"""Resumable, source-bound downloads and verified, traversal-safe ZIP extraction.

No model libraries are imported and no training or model downloads are performed.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import time
from urllib.parse import quote, urlsplit, urlunsplit, parse_qsl, urlencode
import zipfile
import zlib

import requests


def log(message: str) -> None:
    print(time.strftime("[%Y-%m-%d %H:%M:%S] ") + str(message), flush=True)


def write_json(path: Path, value: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=path.name + ".",
                                     suffix=".tmp", encoding="utf-8", delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    try:
        for attempt in range(5):
            try:
                temporary.replace(path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def process_lock(path: Path):
    """OS lock released on process exit, including crashes; never delete inode."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if path.stat().st_size == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError(f"Another process is using {path}") from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def hashes(path: Path) -> dict:
    sha, md5 = hashlib.sha256(), hashlib.md5()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            sha.update(block)
            md5.update(block)
    return {"sha256": sha.hexdigest(), "md5": md5.hexdigest()}


def _fresh_url(url: str) -> str:
    # A cached HF redirect may contain an expired signed CDN URL.
    parsed = urlsplit(url)
    if parsed.hostname == "huggingface.co":
        query = dict(parse_qsl(parsed.query))
        query["download"] = "true"
        query["qorgau_request"] = str(time.time_ns())
        return urlunsplit(parsed._replace(query=urlencode(query)))
    return url


def download(url: str, destination: Path, expected_sha256: str | None = None,
             expected_md5: str | None = None, expected_size: int | None = None) -> dict:
    destination = Path(destination)
    with process_lock(destination.with_name(destination.name + ".lock")):
        return _download(url, destination, expected_sha256, expected_md5, expected_size)


def _download(url: str, destination: Path, expected_sha256: str | None = None,
              expected_md5: str | None = None, expected_size: int | None = None) -> dict:
    """Download once, resume only this source, and compare published hashes.

    Partial bytes are retained on network errors. Existing completed downloads
    are hashed again. An HTTP 200 response never gets appended to a Range request.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    source_file = destination.with_name(destination.name + ".source.json")
    provenance = {"url": url, "expected_sha256": expected_sha256,
                  "expected_md5": expected_md5, "expected_size": expected_size}
    if source_file.exists():
        if json.loads(source_file.read_text(encoding="utf-8")) != provenance:
            raise ValueError(f"Refusing to mix different archive sources: {destination}")
    elif partial.exists() or destination.exists():
        raise ValueError(f"Existing download has no source identity: {destination}")
    write_json(source_file, provenance)

    def verify(path: Path) -> dict:
        size = path.stat().st_size
        if expected_size is not None and size != expected_size:
            raise ValueError(f"Size mismatch for {path.name}: {size} != {expected_size}")
        if size == 0:
            raise ValueError(f"Empty download: {path}")
        calculated = hashes(path)
        for key, expected in (("sha256", expected_sha256), ("md5", expected_md5)):
            if expected and calculated[key].lower() != expected.lower():
                raise ValueError(f"{key} mismatch for {path.name}")
        return {"path": str(destination.resolve()), "url": url, "size_bytes": size,
                **calculated, "status": "verified" if expected_sha256 or expected_md5 else "downloaded",
                "published_checksum_verified": bool(expected_sha256 or expected_md5),
                "expected_sha256": expected_sha256, "expected_md5": expected_md5}

    if destination.exists():
        result = verify(destination)
        write_json(destination.with_name(destination.name + ".download.json"), result)
        return result
    last_error = None
    for attempt in range(8):
        offset = partial.stat().st_size if partial.exists() else 0
        if expected_size is not None and offset == expected_size:
            break
        if expected_size is not None and offset > expected_size:
            raise ValueError(f"Oversized partial download: {partial}")
        headers = {"User-Agent": "Qorgau-dataset-preparation/1.0", "Accept-Encoding": "identity"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        try:
            with requests.get(_fresh_url(url), headers=headers, stream=True, timeout=(30, 60)) as response:
                if response.status_code == 416 and offset:
                    # Interrupted after the last write, before final verification/rename.
                    match = re.fullmatch(r"bytes \*/(\d+)", response.headers.get("Content-Range", ""))
                    if match and int(match.group(1)) == offset:
                        if expected_size is not None and offset != expected_size:
                            raise ValueError("HTTP 416 remote size does not match expected size")
                        break
                response.raise_for_status()
                total = expected_size
                response_bytes = None
                if response.status_code == 206:
                    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
                    if not match:
                        raise ValueError("Missing or malformed Content-Range")
                    start, end, actual_total = map(int, match.groups())
                    if start != offset or end < start or end >= actual_total:
                        raise ValueError(f"Incorrect Content-Range: {match.group(0)} at {offset}")
                    if total is not None and total != actual_total:
                        raise ValueError(f"Remote size changed: {actual_total} != {total}")
                    total, response_bytes = actual_total, end - start + 1
                elif response.status_code == 200:
                    # Range unsupported: truncate before reading the entire response.
                    offset = 0
                    length = response.headers.get("Content-Length")
                    if length:
                        response_bytes = int(length)
                        if total is not None and total != response_bytes:
                            raise ValueError(f"Content-Length mismatch: {response_bytes} != {total}")
                        total = response_bytes
                else:
                    raise ValueError(f"Unexpected HTTP status: {response.status_code}")
                if response.headers.get("Content-Encoding", "identity") not in ("", "identity"):
                    raise ValueError("Encoded response cannot be safely resumed")
                if total is not None and shutil.disk_usage(destination.parent).free < total - offset + 1024**3:
                    raise OSError("Insufficient free space for download plus 1 GiB reserve")
                log(f"Downloading {destination.name}: {offset:,}/{total or '?'} bytes (attempt {attempt + 1})")
                received, last_log = 0, time.monotonic()
                with partial.open("ab" if offset else "wb") as stream:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        if response_bytes is not None and received + len(chunk) > response_bytes:
                            raise ValueError("Response exceeded declared byte range")
                        stream.write(chunk)
                        received += len(chunk)
                        if time.monotonic() - last_log > 20:
                            log(f"{destination.name}: {offset + received:,}/{total or '?'} bytes")
                            last_log = time.monotonic()
                if response_bytes is not None and received != response_bytes:
                    raise IOError(f"Incomplete response: {received}/{response_bytes}")
                if total is not None and partial.stat().st_size != total:
                    raise IOError(f"Partial range received: {partial.stat().st_size}/{total}")
                break
        except (requests.RequestException, OSError, ValueError) as error:
            last_error = error
            log(f"{destination.name}: {type(error).__name__}: {error}")
            if attempt == 7:
                raise RuntimeError(f"Download failed for {url}: {error}") from error
            time.sleep(min(2 ** attempt, 20))
    if not partial.exists():
        raise RuntimeError(f"No download received for {url}: {last_error}")
    result = verify(partial)
    partial.replace(destination)
    write_json(destination.with_name(destination.name + ".download.json"), result)
    log(f"SHA-256 {destination.name}: {result['sha256']}")
    return result


def fetch_hf(repo: str, revision: str, filenames: list[str], sources: Path) -> dict:
    """Resolve a dataset revision and persist its published LFS hash metadata."""
    sources = Path(sources)
    sources.mkdir(parents=True, exist_ok=True)
    api = f"https://huggingface.co/api/datasets/{repo}/revision/{quote(revision, safe='')}?blobs=true"
    response = requests.get(api, timeout=(20, 60))
    response.raise_for_status()
    payload = response.json()
    commit = payload["sha"]
    if re.fullmatch(r"[0-9a-f]{40}", revision) and commit != revision:
        raise ValueError(f"HF revision mismatch: {commit} != {revision}")
    write_json(sources / "huggingface-metadata.json", {"metadata_url": api, "response": payload})
    siblings = {item["rfilename"]: item for item in payload["siblings"]}
    result = {}
    for name in filenames:
        entry = siblings[name]
        lfs = entry.get("lfs", {})
        digest = lfs.get("sha256") or lfs.get("oid", "").removeprefix("sha256:")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"No published LFS SHA-256 for {repo}/{name}")
        result[name] = {"url": f"https://huggingface.co/datasets/{repo}/resolve/{commit}/{quote(name)}",
                        "size": lfs.get("size", entry.get("size")), "sha256": digest, "revision": commit}
    return result


def safe_path(raw: Path, name: str) -> Path:
    """Reject paths which escape or are ambiguous on Windows or Unix."""
    if "\\" in name or ":" in name or "\x00" in name:
        raise ValueError(f"Unsafe archive member: {name!r}")
    member = PurePosixPath(name)
    if member.is_absolute() or ".." in member.parts:
        raise ValueError(f"Unsafe archive member: {name!r}")
    root = raw.resolve()
    destination = root.joinpath(*member.parts)
    if not destination.resolve().is_relative_to(root):
        raise ValueError(f"Archive member escaped target: {name!r}")
    for part in member.parts:
        if part.endswith((".", " ")) or part.split(".")[0].upper() in {
            "CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]
        }:
            raise ValueError(f"Unsafe Windows archive member: {name!r}")
    return destination


def extract_zip_verified(archive: Path, raw: Path) -> dict:
    """Read all entries to EOF (CRC verified), preserve the complete ZIP tree."""
    archive, raw = Path(archive), Path(raw)
    raw.mkdir(parents=True, exist_ok=True)
    count = total = 0
    seen = set()
    last_log = time.monotonic()
    with zipfile.ZipFile(archive) as source:
        needed = sum(entry.file_size for entry in source.infolist())
        if shutil.disk_usage(raw).free < needed + 1024**3:
            raise OSError("Insufficient space for archive expansion plus 1 GiB reserve")
        for member in source.infolist():
            target = safe_path(raw, member.filename)
            key = str(target).casefold()
            if key in seen:
                raise ValueError(f"Duplicate archive member: {member.filename}")
            seen.add(key)
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) and not (stat.S_ISDIR(mode) or stat.S_ISREG(mode))):
                raise ValueError(f"Non-regular ZIP member: {member.filename}")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            # Check existing extracted files against ZIP CRC, allowing safe restart.
            existing_ok = False
            if target.is_file() and target.stat().st_size == member.file_size:
                crc = 0
                with target.open("rb") as existing:
                    for block in iter(lambda: existing.read(1024 * 1024), b""):
                        crc = zlib.crc32(block, crc)
                existing_ok = crc == member.CRC
            if existing_ok:
                # Validate archive bytes too: a healthy raw file cannot certify a
                # corrupted ZIP payload whose central directory is still intact.
                with source.open(member) as incoming:
                    for _ in iter(lambda: incoming.read(1024 * 1024), b""):
                        pass
            else:
                temporary = target.with_name(target.name + ".extracting")
                try:
                    with source.open(member) as incoming, temporary.open("wb") as outgoing:
                        shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
                    if temporary.stat().st_size != member.file_size:
                        raise ValueError(f"Uncompressed size mismatch: {member.filename}")
                    temporary.replace(target)
                finally:
                    if temporary.exists():
                        temporary.unlink()
            count += 1
            total += member.file_size
            if time.monotonic() - last_log > 20:
                log(f"Extracting {archive.name}: {count:,} files, {total:,} bytes")
                last_log = time.monotonic()
    return {"archive": str(archive.resolve()), "raw_path": str(raw.resolve()),
            "files": count, "size_bytes": total, "crc_verified": True, "status": "verified"}
