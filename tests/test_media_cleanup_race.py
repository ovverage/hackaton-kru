"""An upload may outlive deletion of its completed exam's history."""

import asyncio

import pytest
from fastapi import HTTPException

from backend.proctor.app import create_app, digest
from backend.proctor.db import decode, encode
from backend.proctor.history import remove_completed_history
from shared.rules import State


@pytest.mark.parametrize("replacement", [None, "other-exam", "other-device"])
def test_upload_revalidates_original_event_after_history_cleanup(tmp_path, replacement):
    app = create_app(tmp_path)
    db = app.state.db
    completed = State(lifecycle="COMPLETED").public()
    unrelated_file = tmp_path / "media" / "unrelated.mp4"
    unrelated_file.write_bytes(b"unrelated recording")
    with db.connect(True) as c:
        device = {"id": "pc", "exam_id": "exam", "state": completed, "capabilities": {}}
        c.execute("INSERT INTO devices VALUES(?,?,?,?)", ("pc", "owner", digest("token"), encode(device)))
        exam = {"id": "exam", "status": "COMPLETED", "participants": {"pc": {"state": completed}}}
        c.execute("INSERT INTO exams VALUES(?,?,?)", ("exam", "owner", encode(exam)))
        c.execute("INSERT INTO events VALUES(?,?,?,?)", ("event", "exam", "pc", encode({"id": "event", "media": []})))
        c.execute("INSERT INTO exams VALUES(?,?,?)", ("unrelated", "other-owner", encode({"id": "unrelated"})))
        c.execute("INSERT INTO events VALUES(?,?,?,?)", ("unrelated-event", "unrelated", "unrelated-pc", encode({"id": "unrelated-event"})))
        c.execute("INSERT INTO media VALUES(?,?,?,?,?)", ("unrelated-media", "unrelated-event", "unrelated-pc", str(unrelated_file), "video/mp4"))
    endpoint = next(route.endpoint for route in app.routes
                    if getattr(route, "path", None) == "/api/agent/media/{event_id}")

    class StreamingRequest:
        headers = {"authorization": "Bearer token", "content-type": "video/mp4"}

        async def stream(self):
            # The endpoint already authenticated the device and original event.
            yield b"\x00\x00\x00\x18ftyp"
            with db.connect(True) as c:
                remove_completed_history(c, tmp_path, "owner", {"exam"}, apply=True)
                if replacement:
                    new_exam = "exam" if replacement == "other-device" else "new-exam"
                    new_device = "other-pc" if replacement == "other-device" else "pc"
                    c.execute("INSERT INTO exams VALUES(?,?,?)", (new_exam, "other-owner", encode({"id": new_exam})))
                    c.execute("INSERT INTO events VALUES(?,?,?,?)",
                              ("event", new_exam, new_device, encode({"id": "event", "media": []})))
            yield b"isom"

    with pytest.raises(HTTPException) as failure:
        asyncio.run(endpoint("event", StreamingRequest()))
    assert failure.value.status_code == 404
    # A rejected late upload must leave no private orphan and must not attach to
    # a replacement row that happens to reuse the old event identifier.
    assert list((tmp_path / "media").iterdir()) == [unrelated_file]
    assert unrelated_file.read_bytes() == b"unrelated recording"
    with db.connect() as c:
        assert [row["id"] for row in c.execute("SELECT id FROM media")] == ["unrelated-media"]
        replacement_row = c.execute("SELECT body FROM events WHERE id='event'").fetchone()
        assert (decode(replacement_row)["media"] == []) if replacement else replacement_row is None
        assert c.execute("SELECT count(*) FROM media_lifetime").fetchone()[0] == 0
