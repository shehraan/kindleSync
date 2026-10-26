# Automatic new-book preparation

The user expects newly opened Kobo EPUBs to work without manual registration.
They approved free computer-generated narration as the fallback on October 21.
Their earlier delegation to choose and implement the simplest method still applies.

KOReader will save progress for unknown editions on the PC, then upload the exact
EPUB once over the paired LAN API. Local capture remains nonblocking and retains
failed uploads. Existing paired human narration remains available. New EPUBs use
local English Piper speech, encoded as MP3, and the existing local transcription,
caption, morning handoff and Android download pipeline. No paid services or
automatic purchase/borrowing is involved. Protected EPUBs and PDFs remain unsupported.

The import protocol is POST /v1/ebooks/<whole-file-md5> with raw EPUB bytes and
Content-Length, authenticated using the existing bearer token. Maximum upload
size is 256 MiB. The preceding /v1/progress response includes needs_epub and a
human-readable message. Unknown positions are durably accepted, never claimed
playable before preparation. Upload hash and EPUB structure must validate before
queuing generation; duplicate uploads are idempotent. Filename changes must not
force regeneration, and editions sharing a filename must stay distinct.

Preparation runs in a separate single-worker loop with a process lock, durable
jobs, restart recovery, chapter caches and bounded retry delays. It must not
block progress requests, reader input, or the morning scheduler. A completed
generation registers immutable EPUB/audio assets and transcribes locally before
marking ready. Publication always uses the matching edition. Old phone audio
remains usable while a new book is preparing. Readiness and failures are visible
through status/CLI and the Kobo receives a clear queued/preparing message.

Verify unknown-book position persistence; exact-file/hash/size validation;
duplicate upload and filename/edition identity; EPUB spine ordering and bounded
text extraction; generation recovery; caption/handoff integration; Lua upload
retry behavior; and real locally synthesized audio. Deploy the coordinator and
updated plugin when available; preserve pairing, saved progress and existing audio.
