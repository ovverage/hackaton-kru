"""Source-bound bounded HTTP Range downloads with restart-safe assembly."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import re
import shutil
import time

import requests

from .common import _fresh_url, hashes, log, process_lock, write_json


def _chunk_paths(cache: Path, start: int, end: int) -> tuple[Path, Path]:
    stem = f"{start:020d}-{end:020d}"
    return cache / (stem + ".chunk"), cache / (stem + ".json")


def _cached_chunks(cache: Path, identity: dict) -> list[dict]:
    records = []
    for path in cache.glob("[0-9]*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("source") != identity:
            raise ValueError(f"Foreign source in range cache: {path}")
        start, end = record["start"], record["end"]
        if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start <= end < identity["expected_size"]:
            raise ValueError(f"Invalid cached range: {path}")
        payload, expected_path = _chunk_paths(cache, start, end)
        if path != expected_path:
            raise ValueError(f"Unexpected range metadata name: {path}")
        if record.get("status") == "verified" and payload.is_file():
            records.append(record)
    return records


def _get_chunk(url: str, identity: dict, cache: Path, start: int, end: int,
               cached: list[dict]) -> dict:
    for record in cached:
        if record["start"] <= start and record["end"] >= end:
            payload, _ = _chunk_paths(cache, record["start"], record["end"])
            if (payload.stat().st_size == record["end"] - record["start"] + 1
                    and hashes(payload)["sha256"] == record.get("sha256")):
                return {**record, "path": str(payload), "requested_start": start, "requested_end": end}
    payload, metadata = _chunk_paths(cache, start, end)
    temporary = payload.with_name(payload.name + ".downloading")
    record = {"source": identity, "start": start, "end": end, "status": "downloading"}
    write_json(metadata, record)
    length = end - start + 1
    for attempt in range(5):
        try:
            headers = {
                "User-Agent": "Qorgau-dataset-preparation/1.0",
                "Accept-Encoding": "identity", "Range": f"bytes={start}-{end}",
            }
            with requests.get(_fresh_url(url), headers=headers, stream=True, timeout=(30, 60)) as response:
                response.raise_for_status()
                if response.status_code != 206:
                    raise ValueError(f"Range {start}-{end}: expected HTTP 206, got {response.status_code}")
                expected_range = f"bytes {start}-{end}/{identity['expected_size']}"
                if response.headers.get("Content-Range") != expected_range:
                    raise ValueError(f"Range {start}-{end}: incorrect Content-Range {response.headers.get('Content-Range')!r}")
                if response.headers.get("Content-Length") != str(length):
                    raise ValueError(f"Range {start}-{end}: incorrect Content-Length")
                if response.headers.get("Content-Encoding", "identity") not in ("", "identity"):
                    raise ValueError("Encoded range response is not safe to assemble")
                received = 0
                with temporary.open("wb") as stream:
                    for block in response.iter_content(chunk_size=1024 * 1024):
                        if received + len(block) > length:
                            raise ValueError(f"Range {start}-{end}: response exceeded requested size")
                        stream.write(block)
                        received += len(block)
                    stream.flush()
                    os.fsync(stream.fileno())
                if received != length:
                    raise IOError(f"Range {start}-{end}: incomplete response {received}/{length}")
            calculated = hashes(temporary)
            temporary.replace(payload)
            record.update(status="verified", size_bytes=length, sha256=calculated["sha256"])
            write_json(metadata, record)
            return {**record, "path": str(payload), "requested_start": start, "requested_end": end}
        except (requests.RequestException, OSError) as error:
            # Restart only this bounded chunk; no unverified bytes enter .part.
            if attempt == 4:
                raise RuntimeError(f"Range {start}-{end} failed: {error}") from error
            time.sleep(min(2 ** attempt, 8))
    raise AssertionError("Unreachable retry state")


def _verify_complete(path: Path, destination: Path, identity: dict) -> dict:
    if path.stat().st_size != identity["expected_size"]:
        raise ValueError(f"Assembled size mismatch for {destination.name}")
    calculated = hashes(path)
    if calculated["sha256"] != identity["expected_sha256"]:
        raise ValueError(f"SHA-256 mismatch for {destination.name}; incomplete file retained, not promoted")
    return {"path": str(destination.resolve()), "url": identity["url"],
            "size_bytes": path.stat().st_size, **calculated, "status": "verified",
            "published_checksum_verified": True,
            "expected_sha256": identity["expected_sha256"], "expected_md5": None,
            "transport": "bounded_parallel_ranges"}


def _append_chunk(partial: Path, record: dict) -> None:
    """Treat actual durable prefix length as authority after any interruption."""
    offset = partial.stat().st_size if partial.exists() else 0
    start, end = record["requested_start"], record["requested_end"]
    if offset > end:
        return
    if offset < start or offset < record["start"]:
        raise ValueError(f"Non-contiguous range assembly: prefix={offset}, range={start}-{end}")
    payload = Path(record["path"])
    remaining = end - offset + 1
    with payload.open("rb") as source, partial.open("ab") as target:
        source.seek(offset - record["start"])
        while remaining:
            block = source.read(min(1024 * 1024, remaining))
            if not block:
                raise IOError(f"Cached range truncated during assembly: {payload}")
            target.write(block)
            remaining -= len(block)
        target.flush()
        os.fsync(target.fileno())


def _cleanup_verified_chunks(cache: Path, identity: dict) -> None:
    # Only exact source-bound files created by this module; never recursively delete.
    for record in _cached_chunks(cache, identity):
        payload, metadata = _chunk_paths(cache, record["start"], record["end"])
        payload.unlink(missing_ok=True)
        payload.with_name(payload.name + ".downloading").unlink(missing_ok=True)
        metadata.unlink(missing_ok=True)


def download_ranges(url: str, destination: Path, expected_sha256: str,
                    expected_size: int, workers: int = 4,
                    chunk_size: int = 64 * 1024 ** 2) -> dict:
    """Resume common.download's prefix, download exact ranges, verify then promote.

    Chunks may complete in any order. Assembly always follows increasing byte
    offsets. A crash between append and manifest update resumes from .part's
    actual length, and a partially appended chunk is reused from that offset.
    """
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
        raise ValueError("A published SHA-256 is required for range downloading")
    if not isinstance(expected_size, int) or expected_size <= 0:
        raise ValueError("A positive expected byte size is required")
    if not 1 <= workers <= 8 or chunk_size <= 0:
        raise ValueError("Use 1-8 workers and a positive chunk size")
    destination = Path(destination)
    identity = {"url": url, "expected_sha256": expected_sha256.lower(),
                "expected_md5": None, "expected_size": expected_size}
    with process_lock(destination.with_name(destination.name + ".lock")):
        return _download_locked(destination, identity, workers, chunk_size)


def _download_locked(destination: Path, identity: dict, workers: int, chunk_size: int) -> dict:
    partial = destination.with_name(destination.name + ".part")
    source_file = destination.with_name(destination.name + ".source.json")
    cache = destination.parent / ".ranges" / destination.name
    source = cache / "identity.json"
    manifest_path = cache / "manifest.json"
    if source_file.exists():
        if json.loads(source_file.read_text(encoding="utf-8")) != identity:
            raise ValueError(f"Refusing to mix different archive sources: {destination}")
    elif partial.exists() or destination.exists():
        raise ValueError(f"Existing download has no source identity: {destination}")
    if source.exists():
        if json.loads(source.read_text(encoding="utf-8")) != identity:
            raise ValueError(f"Refusing to mix different cached range sources: {cache}")
    elif cache.exists() and any(cache.iterdir()):
        raise ValueError(f"Existing range cache has no source identity: {cache}")
    cache.mkdir(parents=True, exist_ok=True)
    write_json(source_file, identity)
    write_json(source, identity)
    manifest = {"source": identity, "status": "downloading", "workers": workers,
                "chunk_size": chunk_size, "assembled_prefix_bytes": partial.stat().st_size if partial.exists() else 0}
    write_json(manifest_path, manifest)
    try:
        if destination.exists():
            result = _verify_complete(destination, destination, identity)
        else:
            offset = partial.stat().st_size if partial.exists() else 0
            total = identity["expected_size"]
            if offset > total:
                raise ValueError(f"Oversized sequential prefix: {partial}")
            if offset < total:
                # Retain verified cache through final whole-file verification.
                if shutil.disk_usage(destination.parent).free < 2 * (total - offset) + 1024 ** 3:
                    raise OSError("Insufficient space for range cache, assembly, and 1 GiB reserve")
                cached = _cached_chunks(cache, identity)
                ranges = []
                while offset < total:
                    end = min(((offset // chunk_size) + 1) * chunk_size, total) - 1
                    ranges.append((offset, end))
                    offset = end + 1
                completed = {}
                log(f"Range downloading {destination.name}: {len(ranges)} chunks with {workers} workers")
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = {executor.submit(_get_chunk, identity["url"], identity, cache, start, end, cached): start
                               for start, end in ranges}
                    try:
                        for future in as_completed(futures):
                            record = future.result()
                            completed[record["requested_start"]] = record
                            count = len(completed)
                            if count % 8 == 0 or count == len(ranges):
                                log(f"{destination.name}: {count}/{len(ranges)} ranges downloaded")
                    except BaseException:
                        for future in futures:
                            future.cancel()
                        raise
                manifest["status"] = "assembling"
                write_json(manifest_path, manifest)
                for start, _ in ranges:
                    _append_chunk(partial, completed[start])
                    manifest["assembled_prefix_bytes"] = partial.stat().st_size
                    write_json(manifest_path, manifest)
            manifest["status"] = "verifying"
            write_json(manifest_path, manifest)
            result = _verify_complete(partial, destination, identity)
            partial.replace(destination)
        write_json(destination.with_name(destination.name + ".download.json"), result)
        manifest.update(status="verified", assembled_prefix_bytes=identity["expected_size"], sha256=result["sha256"])
        write_json(manifest_path, manifest)
        _cleanup_verified_chunks(cache, identity)
        log(f"SHA-256 {destination.name}: {result['sha256']}")
        return result
    except BaseException as error:
        manifest.update(status="incomplete", error=f"{type(error).__name__}: {error}",
                        assembled_prefix_bytes=partial.stat().st_size if partial.exists() else 0)
        write_json(manifest_path, manifest)
        raise
