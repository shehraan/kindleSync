"""Read Kobo state without changing the source database."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def read_latest(path: str | Path) -> dict:
    path = Path(path).resolve(strict=True)
    source = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    snapshot = sqlite3.connect(":memory:")
    try:
        source.backup(snapshot)
        snapshot.row_factory = sqlite3.Row
        cols = {r[1] for r in snapshot.execute("PRAGMA table_info(content)")}
        required = {"ContentID", "BookID", "ChapterIDBookmarked", "DateLastRead", "Title"}
        if not required <= cols:
            raise ValueError("Unsupported Kobo content schema; missing: " + ", ".join(sorted(required-cols)))
        rows = snapshot.execute("SELECT * FROM content WHERE BookID IS NULL OR BookID = ''")
        observations = []
        for row in rows:
            row = dict(row)
            if "ContentType" in cols and str(row["ContentType"]) != "6":
                continue
            if not row.get("DateLastRead") or not row.get("ChapterIDBookmarked"):
                continue
            try:
                when = datetime.fromisoformat(row["DateLastRead"].replace("Z", "+00:00"))
                when = when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when
            except (ValueError, AttributeError) as e:
                raise ValueError("Invalid Kobo DateLastRead for " + str(row["ContentID"])) from e
            observations.append((when, row))
        if not observations:
            raise ValueError("No saved book reading location in Kobo content table")
        when, row = max(observations, key=lambda r: r[0])
        return dict(content_id=row["ContentID"], title=row["Title"],
                    author=row.get("Attribution") or "", locator=row["ChapterIDBookmarked"],
                    source_time=when.astimezone(timezone.utc).isoformat(),
                    percent=row.get("___PercentRead"), reader="kobo")
    finally:
        source.close()
        snapshot.close()
