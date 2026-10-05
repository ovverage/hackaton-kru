import runpy
from contextlib import closing
import sqlite3
import tarfile
from pathlib import Path


def test_backup_restores_committed_wal_data_and_referenced_media(tmp_path):
    backup = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "deploy" / "qorgau-backup.py")
    )["backup"]
    data = tmp_path / "data"
    media = data / "media"
    media.mkdir(parents=True)
    clip = media / "clip.webm"
    clip.write_bytes(b"test evidence content")
    with closing(sqlite3.connect(data / "proctor.sqlite3")) as live:
        live.execute("PRAGMA journal_mode=WAL")
        live.execute("CREATE TABLE media(path TEXT)")
        live.execute("CREATE TABLE evidence(id TEXT)")
        live.execute("INSERT INTO media VALUES(?)", (str(clip),))
        live.execute("INSERT INTO evidence VALUES('committed in WAL')")
        live.commit()
        backup(data, tmp_path / "backups")
    archive = next((tmp_path / "backups").glob("*.tar.gz"))
    restored = tmp_path / "restored"
    with tarfile.open(archive) as source:
        source.extractall(restored, filter="data")
    with closing(sqlite3.connect(restored / "proctor.sqlite3")) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT id FROM evidence").fetchone()[0] == "committed in WAL"
    assert (restored / "media/clip.webm").read_bytes() == clip.read_bytes()
