"""Safety and integrity checks for multi-gigabyte research data acquisition."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import zipfile

import pytest

pytest.importorskip("requests", reason="Install the datasets extra to run acquisition checks")

from training.datasets import common


@pytest.mark.parametrize("name", ["../escape", "/absolute", "C:/escape", "a\\..\\b", "a/CON.txt", "a/foo. "])
def test_archive_rejects_unsafe_paths(tmp_path, name):
    with pytest.raises(ValueError):
        common.safe_path(tmp_path, name)


def test_zip_crc_and_restart_repairs_corrupted_output(tmp_path):
    archive = tmp_path / "data.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as stream:
        stream.writestr("photos/a.jpg", b"correct image bytes")
    raw = tmp_path / "raw"
    first = common.extract_zip_verified(archive, raw)
    assert first["crc_verified"] is True
    (raw / "photos/a.jpg").write_bytes(b"corrupt image bytes")
    common.extract_zip_verified(archive, raw)
    assert (raw / "photos/a.jpg").read_bytes() == b"correct image bytes"


def test_zip_traversal_cannot_write_outside_raw(tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("../escape", b"bad")
    with pytest.raises(ValueError):
        common.extract_zip_verified(archive, tmp_path / "raw")
    assert not (tmp_path / "escape").exists()


def test_valid_raw_file_does_not_hide_corrupt_zip_payload(tmp_path):
    archive = tmp_path / "data.zip"
    payload = b"unique uncompressed payload to corrupt"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as stream:
        stream.writestr("sample.txt", payload)
    raw = tmp_path / "raw"
    common.extract_zip_verified(archive, raw)
    data = archive.read_bytes()
    archive.write_bytes(data.replace(payload, b"X" + payload[1:]))
    with pytest.raises(zipfile.BadZipFile, match="CRC"):
        common.extract_zip_verified(archive, raw)


def test_changed_download_source_cannot_reuse_partial(tmp_path):
    destination = tmp_path / "archive.zip"
    destination.with_name("archive.zip.part").write_bytes(b"old source")
    common.write_json(destination.with_name("archive.zip.source.json"), {"url": "https://old.invalid"})
    with pytest.raises(ValueError, match="different archive sources"):
        common.download("https://new.invalid", destination)


def test_unknown_existing_file_cannot_gain_false_source(tmp_path):
    destination = tmp_path / "archive.zip"
    destination.write_bytes(b"unrelated file")
    with pytest.raises(ValueError, match="no source identity"):
        common.download("https://unrelated.invalid", destination)
    assert not (tmp_path / "archive.zip.source.json").exists()


@pytest.fixture
def http_source():
    payload = b"qorgau dataset test " * 1000
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            offset = int(self.headers.get("Range", "bytes=0-")[6:-1])
            seen.append(offset)
            if offset == len(payload):
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{len(payload)}")
                self.end_headers()
                return
            if self.path == "/ignore-range":
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            actual_offset = offset + 1 if self.path == "/bad-range" else offset
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {actual_offset}-{len(payload)-1}/{len(payload)}")
            self.send_header("Content-Length", str(len(payload) - actual_offset))
            self.end_headers()
            self.wfile.write(payload[actual_offset:])
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", payload, seen
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.mark.parametrize("endpoint", ["/valid", "/ignore-range"])
def test_resume_validated_and_200_does_not_append(tmp_path, http_source, endpoint):
    base, payload, seen = http_source
    target = tmp_path / "data.bin"
    digest = hashlib.sha256(payload).hexdigest()
    url = base + endpoint
    common.write_json(tmp_path / "data.bin.source.json", {"url": url,
                      "expected_sha256": digest, "expected_md5": None, "expected_size": len(payload)})
    (tmp_path / "data.bin.part").write_bytes(payload[:97])
    result = common.download(url, target, expected_sha256=digest, expected_size=len(payload))
    assert seen[0] == 97
    assert target.read_bytes() == payload
    assert result["published_checksum_verified"] is True


def test_wrong_range_is_rejected_without_appending(tmp_path, http_source, monkeypatch):
    base, payload, _ = http_source
    monkeypatch.setattr(common.time, "sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="Incorrect Content-Range"):
        common.download(base + "/bad-range", tmp_path / "bad.bin", expected_size=len(payload))
    assert not (tmp_path / "bad.bin").exists()
    assert not (tmp_path / "bad.bin.part").exists()


def test_checksum_failure_never_promotes_download(tmp_path, http_source):
    base, payload, _ = http_source
    with pytest.raises(ValueError, match="sha256 mismatch"):
        common.download(base + "/valid", tmp_path / "bad.bin", expected_sha256="0" * 64,
                        expected_size=len(payload))
    assert not (tmp_path / "bad.bin").exists()


def test_complete_partial_without_known_size_can_finish_after_416(tmp_path, http_source):
    base, payload, _ = http_source
    url = base + "/valid"
    digest = hashlib.sha256(payload).hexdigest()
    common.write_json(tmp_path / "data.bin.source.json", {"url": url,
                      "expected_sha256": digest, "expected_md5": None, "expected_size": None})
    (tmp_path / "data.bin.part").write_bytes(payload)
    result = common.download(url, tmp_path / "data.bin", expected_sha256=digest)
    assert (tmp_path / "data.bin").read_bytes() == payload
    assert result["sha256"] == digest


def test_live_registry_does_not_rescan_images_or_claim_zero_usage(tmp_path, monkeypatch):
    from training.datasets import prepare
    def unexpected_scan(_):
        raise AssertionError("Live registry must not repeatedly scan large image trees")
    monkeypatch.setattr(prepare, "tree_usage", unexpected_scan)
    result = prepare.inventory(tmp_path, scan_files=False)
    assert result["disk_usage"]["scan_complete"] is False
    assert result["disk_usage"]["total_bytes"] is None
    assert all(d["file_count"] is None for d in result["datasets"].values())


def test_tree_usage_preserves_nested_categories(tmp_path):
    from training.datasets.prepare import tree_usage
    for name, value in {"archives/.ranges/a.chunk": b"abcd", "raw/sub/a.jpg": b"12",
                        "sources/license.txt": b"xyz", "status.json": b"{}"}.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    sizes, count = tree_usage(tmp_path)
    assert count == 4
    assert sizes == {"archives_bytes": 4, "raw_bytes": 2, "sources_bytes": 3, "other_bytes": 2}
