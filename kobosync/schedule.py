"""Durable 06:00 local-date scheduling with restart catch-up and retry."""
import logging
import hashlib
import json
import threading
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo
from .pipeline import run_once
from .kobo import read_latest
from .store import utcnow


def due_date(now, zone="America/Toronto", hour=6):
    local = now.astimezone(ZoneInfo(zone))
    return local.date().isoformat() if local.time() >= time(hour) else None


def tick(store, database=None, now=None, zone="America/Toronto", hour=6):
    day = due_date(now or datetime.now(timezone.utc), zone, hour)
    if day is None:
        return False
    key = f"{zone}:{hour}:{day}"
    try:
        if database:
            store.observe(read_latest(database))
        cutoff = datetime.combine(datetime.fromisoformat(day).date(), time(hour), ZoneInfo(zone)).astimezone(timezone.utc).isoformat()
        observation = store.observation(cutoff)
        fingerprint = hashlib.sha256(json.dumps(observation,sort_keys=True).encode()).hexdigest()
        with store.connect() as c:
            done = c.execute("SELECT completed FROM days WHERE date=?", (key,)).fetchone()
            if done and done[0] == fingerprint:
                return False
        manifest = run_once(store, before=cutoff)
    except Exception as e:
        store.set_value("last_error", str(e))
        raise
    with store.connect() as c:
        c.execute("INSERT OR REPLACE INTO days VALUES (?,?)", (key, fingerprint))
        c.execute("INSERT OR REPLACE INTO state VALUES ('last_scheduled_run',?)",
                  (json.dumps(dict(scheduled_for=cutoff, completed_at=utcnow(),
                                   source_time=manifest["source_time"], revision=manifest["revision"])),))
    return True


def loop(store, database=None, zone="America/Toronto", hour=6, stop=None):
    stop = stop or threading.Event()
    while not stop.is_set():
        try:
            tick(store, database, zone=zone, hour=hour)
        except Exception:
            logging.exception("Morning handoff pending; retrying in 60 seconds")
        stop.wait(60)
