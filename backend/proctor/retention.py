"""Expire completed-session video only, retaining event/review/audit metadata."""
import argparse
from pathlib import Path
import time
from .db import Database, decode, encode


def expire_media(base, days=7, now=None, apply=False):
    base = Path(base).resolve()
    media_root = (base / "media").resolve()
    database = base / "proctor.sqlite3"
    if not database.is_file():
        raise ValueError("EXISTING_QORGAU_DATABASE_REQUIRED")
    db = Database(database)
    now = time.time() if now is None else now
    removed = []
    with db.connect(write=apply) as c:
        rows = c.execute("SELECT media.*,events.body AS event_body,exams.body AS exam_body FROM media JOIN events ON media.event_id=events.id JOIN exams ON events.exam_id=exams.id").fetchall()
        for row in rows:
            import json
            exam = json.loads(row["exam_body"])
            event = json.loads(row["event_body"])
            expires = max(event.get("retain_until", 0), event["created_at"] + days * 86400)
            if exam.get("status") != "COMPLETED" or expires > now:
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
                c.execute("INSERT INTO audit(at,actor,action,body) VALUES(?,?,?,?)", (now, "retention", "MEDIA_EXPIRED", encode({"event_id": row["event_id"], "media_id": row["id"]})))
    return removed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.days < 1:
        parser.error("Retention must be at least one day")
    print({"apply": args.apply, "media_ids": expire_media(args.data, args.days, apply=args.apply)})


if __name__ == "__main__":
    main()
