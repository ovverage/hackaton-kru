"""Delete server video two hours after upload, retaining event/review/audit metadata."""
import argparse
import json
from pathlib import Path
import time
from .db import Database, decode, encode


MEDIA_TTL_SECONDS = 2 * 60 * 60


def expire_media(base, days=None, now=None, apply=False):
    # days is retained solely for old maintenance callers; it cannot extend the policy.
    base = Path(base).resolve()
    media_root = (base / "media").resolve()
    database = base / "proctor.sqlite3"
    if not database.is_file():
        raise ValueError("EXISTING_QORGAU_DATABASE_REQUIRED")
    db = Database(database)
    now = time.time() if now is None else now
    removed = []
    with db.connect(write=apply) as c:
        rows = c.execute("SELECT media.*,events.body AS event_body,media_lifetime.expires_at FROM media JOIN events ON media.event_id=events.id LEFT JOIN media_lifetime ON media_lifetime.id=media.id").fetchall()
        for row in rows:
            event = json.loads(row["event_body"])
            # Legacy rows have no upload clock: use the event time, never reset retention.
            expires = row["expires_at"] if row["expires_at"] is not None else event["created_at"] + MEDIA_TTL_SECONDS
            if expires > now:
                continue
            path = Path(row["path"]).resolve()
            if path.parent != media_root:
                raise ValueError("MEDIA_PATH_OUTSIDE_DATA_DIRECTORY")
            removed.append(row["id"])
            if apply:
                # Delete first: a crash can leave metadata, but cannot leave an untracked private file.
                path.unlink(missing_ok=True)
                fresh = decode(c.execute("SELECT body FROM events WHERE id=?", (row["event_id"],)).fetchone())
                fresh["media"] = [x for x in fresh["media"] if x["id"] != row["id"]]
                fresh["media_expired_at"] = now
                c.execute("UPDATE events SET body=? WHERE id=?", (encode(fresh), row["event_id"]))
                c.execute("DELETE FROM media WHERE id=?", (row["id"],))
                c.execute("DELETE FROM media_lifetime WHERE id=?", (row["id"],))
                c.execute("INSERT INTO audit(at,actor,action,body) VALUES(?,?,?,?)", (now, "retention", "MEDIA_EXPIRED", encode({"event_id": row["event_id"], "media_id": row["id"]})))
    return removed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--days", type=int, help="Deprecated; video retention is always two hours after upload")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print({"apply": args.apply, "media_ids": expire_media(args.data, args.days, apply=args.apply)})


if __name__ == "__main__":
    main()
