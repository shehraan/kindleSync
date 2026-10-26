"""Durable edition uploads and a separate, restartable narration worker."""
import fcntl
import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .store import digest, edition_md5, utcnow


MAX_EPUB_BYTES = 256 * 1024 * 1024
_MESSAGES = {
    'awaiting_epub': 'Position saved. Send this EPUB to prepare its narration.',
    'queued': 'Position saved. Computer narration is queued on your PC.',
    'preparing': 'Position saved. Your PC is preparing narration and captions.',
    'ready': 'Position saved. Narration is ready for the next handoff.',
    'error': 'Position saved. Narration needs attention on your PC; preparation will retry.',
}


def jobs(store):
    with store.connect() as connection:
        return [json.loads(row[0]) for row in connection.execute('SELECT data FROM imports ORDER BY rowid')]


def _job(store, md5):
    with store.connect() as connection:
        row = connection.execute('SELECT data FROM imports WHERE md5=?', (md5,)).fetchone()
    return json.loads(row[0]) if row else None


def _save(store, job):
    with store.connect() as connection:
        connection.execute('INSERT OR REPLACE INTO imports VALUES (?,?)', (job['md5'], json.dumps(job)))


def progress_status(store, observation):
    md5 = edition_md5(observation['epub_md5'])
    job = _job(store, md5)
    book = store.find_edition(md5)
    if book and not book['id'].startswith('auto-'):
        state = 'ready'
    elif job:
        state = job['book_state']
    elif book:
        state = 'ready' if (store.root / 'library' / book['id'] / 'transcript.json').is_file() else 'preparing'
    else:
        state = 'awaiting_epub'
    return dict(needs_epub=state == 'awaiting_epub', book_state=state, message=_MESSAGES[state])


def receive_epub(store, md5, stream, length):
    """Validate exact, bounded bytes before publishing the canonical import input."""
    md5 = edition_md5(md5)
    if type(length) is not int or not 0 < length <= MAX_EPUB_BYTES:
        raise ValueError('EPUB upload must contain 1 byte to 256 MiB')
    inbox = store.root / 'inbox'
    inbox.mkdir(exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=inbox, prefix='.upload-')
    try:
        checksum = hashlib.md5()
        sha256 = hashlib.sha256()
        remaining = length
        with os.fdopen(fd, 'wb') as destination:
            while remaining:
                block = stream.read(min(262144, remaining))
                if not block or len(block) > remaining:
                    raise ValueError('EPUB upload was interrupted or has an incorrect length')
                destination.write(block)
                checksum.update(block)
                sha256.update(block)
                remaining -= len(block)
            destination.flush()
            os.fsync(destination.fileno())
        if checksum.hexdigest() != md5:
            raise ValueError('EPUB upload does not match its whole-file MD5')
        from .narration import read_epub
        try:
            metadata = read_epub(Path(temporary))
        except (ValueError, OSError, KeyError, TypeError) as error:
            raise ValueError('Cannot read this EPUB: ' + str(error)) from error
        book = store.find_edition(md5)
        if book and book['epub_sha256'] != sha256.hexdigest():
            raise ValueError('Different EPUB bytes share a registered MD5; this edition cannot reuse its narration')
        if book and not book['id'].startswith('auto-'):
            return progress_status(store, dict(epub_md5=md5))
        directory = inbox / md5
        directory.mkdir(exist_ok=True, mode=0o700)
        canonical = directory / 'book.epub'
        if (canonical.is_file() and not canonical.is_symlink()
                and digest(canonical, 'md5') == md5 and digest(canonical) != sha256.hexdigest()):
            raise ValueError('Different EPUB bytes share an uploaded MD5; keep the original edition')
        if canonical.is_symlink() or not canonical.is_file() or digest(canonical, 'md5') != md5:
            os.replace(temporary, canonical)
            # The database never acknowledges bytes before their directory entry.
            directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        with store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT data FROM imports WHERE md5=?', (md5,)).fetchone()
            job = json.loads(row[0]) if row else dict(md5=md5, attempts=0, created_at=utcnow())
            if job.get('book_state', 'awaiting_epub') == 'awaiting_epub':
                job.update(book_state='queued', updated_at=utcnow(), next_attempt_at=None, error='',
                           epub_sha256=sha256.hexdigest(),
                           title=metadata.get('title', ''), author=metadata.get('author', ''),
                           language=metadata.get('language', ''))
                connection.execute('INSERT OR REPLACE INTO imports VALUES (?,?)', (md5, json.dumps(job)))
        return progress_status(store, dict(epub_md5=md5))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def process_pending(store, prepare=None):
    """Run eligible imports once; the process lock also owns crash recovery."""
    with (store.root / 'import.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return []
        if prepare is None:
            from .narration import prepare_book
            prepare = prepare_book
        processed = []
        for job in jobs(store):
            state = job['book_state']
            if state not in {'queued', 'error', 'preparing'}:
                continue
            now = datetime.now(timezone.utc)
            if state == 'error' and job.get('next_attempt_at') and datetime.fromisoformat(job['next_attempt_at']) > now:
                continue
            job.update(book_state='preparing', attempts=job.get('attempts', 0) + 1,
                       updated_at=utcnow(), next_attempt_at=None, error='')
            _save(store, job)
            try:
                book = store.find_edition(job['md5'])
                if book is None or book['id'].startswith('auto-'):
                    book = prepare(store, job['md5'])
                    if book['epub_md5'] != job['md5']:
                        raise ValueError('Generated narration belongs to a different EPUB edition')
                    store.transcript(book['id'])
                job.update(book_state='ready', book_id=book['id'], updated_at=utcnow())
            except Exception as error:
                delay = min(3600, 30 * 2 ** min(job['attempts'] - 1, 7))
                job.update(book_state='error', updated_at=utcnow(),
                           error=str(error).replace(store.token, '[redacted]')[:1000],
                           next_attempt_at=(datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat())
            _save(store, job)
            processed.append(job)
        return processed


def loop(store, stop):
    while not stop.is_set():
        try:
            process_pending(store)
        except Exception:
            logging.exception('Narration worker will retry pending imports')
        stop.wait(5)
