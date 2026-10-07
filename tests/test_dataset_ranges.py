"""Local HTTP integration tests for bounded, restart-safe dataset ranges."""

from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import pytest

pytest.importorskip("requests", reason="Install the datasets extra to run acquisition checks")

from training.datasets.common import write_json
from training.datasets.ranges import download_ranges


@contextmanager
def range_server(payload: bytes, mode="good", delay_first=False):
    requests_seen = []
    completed = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_GET(self):
            match = re.fullmatch(r"bytes=(\d+)-(\d+)", self.headers.get("Range", ""))
            if not match:
                self.send_error(400)
                return
            start, end = map(int, match.groups())
            requests_seen.append((start, end))
            body = payload[start:end + 1]
            if delay_first and start == 0:
                time.sleep(0.1)
            self.send_response(200 if mode == "full200" else 206)
            self.send_header("Content-Range", f"bytes {start + (mode == 'bad_range')}-{end}/{len(payload)}")
            self.send_header("Content-Length", str(len(body) - (mode == "bad_length")))
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                if mode == "truncate_once" and requests_seen.count((start, end)) == 1:
                    self.wfile.write(body[:len(body) // 2])
                    self.wfile.flush()
                    return
                self.wfile.write(body)
                self.wfile.flush()
                completed.append(start)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/archive", requests_seen, completed
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class DatasetRangeTests(unittest.TestCase):
    payload = bytes(range(256)) * 193
    chunk_size = 8192

    def setUp(self):
        quiet = patch("training.datasets.ranges.log")
        quiet.start()
        self.addCleanup(quiet.stop)

    def identity(self, url):
        return {"url": url, "expected_sha256": hashlib.sha256(self.payload).hexdigest(),
                "expected_md5": None, "expected_size": len(self.payload)}

    def run_download(self, url, destination, digest=None):
        return download_ranges(url, destination,
                               digest or hashlib.sha256(self.payload).hexdigest(),
                               len(self.payload), workers=4, chunk_size=self.chunk_size)

    def test_out_of_order_ranges_assemble_exactly_and_verify(self):
        with tempfile.TemporaryDirectory() as temporary, range_server(self.payload, delay_first=True) as (url, seen, completed):
            destination = Path(temporary) / "archive.partaa"
            receipt = self.run_download(url, destination)
            self.assertNotEqual(completed[0], 0)
            self.assertEqual(destination.read_bytes(), self.payload)
            self.assertEqual(receipt["status"], "verified")
            self.assertTrue(receipt["published_checksum_verified"])
            self.assertEqual(sum(end - start + 1 for start, end in seen), len(self.payload))
            self.assertFalse(any((destination.parent / ".ranges").rglob("*.chunk")))

    def test_reuses_arbitrary_sequential_prefix_without_fetching_it(self):
        with tempfile.TemporaryDirectory() as temporary, range_server(self.payload) as (url, seen, _):
            destination = Path(temporary) / "archive.partaa"
            prefix = 10037
            destination.with_name(destination.name + ".part").write_bytes(self.payload[:prefix])
            write_json(destination.with_name(destination.name + ".source.json"), self.identity(url))
            self.run_download(url, destination)
            self.assertEqual(destination.read_bytes(), self.payload)
            self.assertEqual(min(start for start, _ in seen), prefix)
            self.assertEqual(sum(end - start + 1 for start, end in seen), len(self.payload) - prefix)

    def test_rejects_http200_wrong_range_and_wrong_length_without_appending(self):
        for mode in ("full200", "bad_range", "bad_length"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary, range_server(self.payload, mode) as (url, _, _):
                destination = Path(temporary) / "archive"
                with self.assertRaises(ValueError):
                    self.run_download(url, destination)
                self.assertFalse(destination.exists())
                self.assertFalse(destination.with_name(destination.name + ".part").exists())
                report = json.loads((destination.parent / ".ranges" / destination.name / "manifest.json").read_text())
                self.assertEqual(report["status"], "incomplete")

    def test_hash_mismatch_never_promotes_and_retains_evidence(self):
        with tempfile.TemporaryDirectory() as temporary, range_server(self.payload) as (url, _, _):
            destination = Path(temporary) / "archive"
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                self.run_download(url, destination, digest="0" * 64)
            self.assertFalse(destination.exists())
            self.assertEqual(destination.with_name("archive.part").read_bytes(), self.payload)
            self.assertTrue(any((destination.parent / ".ranges").rglob("*.chunk")))

    def test_interrupted_mid_chunk_assembly_reuses_cache_without_duplicates(self):
        with tempfile.TemporaryDirectory() as temporary, range_server(self.payload) as (url, seen, _):
            destination = Path(temporary) / "archive"

            def interrupted_append(partial, record):
                # Simulate termination after bytes reach disk but before manifest update.
                with Path(record["path"]).open("rb") as source, partial.open("ab") as target:
                    target.write(source.read(17))
                    target.flush()
                    os.fsync(target.fileno())
                raise OSError("simulated interrupted assembly")

            with patch("training.datasets.ranges._append_chunk", side_effect=interrupted_append):
                with self.assertRaisesRegex(OSError, "interrupted assembly"):
                    self.run_download(url, destination)
            self.assertEqual(destination.with_name("archive.part").stat().st_size, 17)
            previous_requests = len(seen)
            self.run_download(url, destination)
            self.assertEqual(len(seen), previous_requests)
            self.assertEqual(destination.read_bytes(), self.payload)

    def test_source_mismatch_does_not_touch_existing_prefix(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "archive"
            partial = destination.with_name("archive.part")
            partial.write_bytes(b"preserve me")
            write_json(destination.with_name("archive.source.json"), self.identity("https://old.example/data"))
            with self.assertRaisesRegex(ValueError, "different archive sources"):
                self.run_download("https://new.example/data", destination)
            self.assertEqual(partial.read_bytes(), b"preserve me")

    def test_short_response_retries_only_bounded_chunk(self):
        with tempfile.TemporaryDirectory() as temporary, range_server(self.payload, "truncate_once") as (url, seen, _):
            destination = Path(temporary) / "archive"
            with patch("training.datasets.ranges.time.sleep"):
                self.run_download(url, destination)
            self.assertEqual(destination.read_bytes(), self.payload)
            self.assertTrue(all(seen.count(requested) == 2 for requested in set(seen)))

    def test_corrupt_cached_chunk_is_redownloaded_before_assembly(self):
        with tempfile.TemporaryDirectory() as temporary, range_server(self.payload) as (url, seen, _):
            destination = Path(temporary) / "archive"
            with patch("training.datasets.ranges._append_chunk", side_effect=OSError("interrupted")):
                with self.assertRaisesRegex(OSError, "interrupted"):
                    self.run_download(url, destination)
            chunks = sorted((destination.parent / ".ranges" / "archive").glob("*.chunk"))
            damaged = chunks[1]
            content = bytearray(damaged.read_bytes())
            content[0] ^= 1
            damaged.write_bytes(content)
            previous_requests = len(seen)
            self.run_download(url, destination)
            self.assertEqual(destination.read_bytes(), self.payload)
            self.assertEqual(len(seen), previous_requests + 1)
            self.assertEqual(seen[-1], (self.chunk_size, 2 * self.chunk_size - 1))


if __name__ == "__main__":
    unittest.main()
