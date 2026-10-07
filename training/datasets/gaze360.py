"""Download and verify the pinned Gaze360 mirror, without training models.

Run with ``python -m training.datasets.gaze360 --root data/datasets``.
The six files are one TAR stream; no additional 30 GB concatenated copy is made.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import shutil
import tarfile
import traceback
from collections import Counter
from datetime import datetime, timezone
from functools import partial
from pathlib import Path, PurePosixPath
from typing import Callable

from .common import download, fetch_hf, log, process_lock, safe_path, write_json


HF_REPO = "immediately/Gaze360-split"
HF_REVISION = "063f11c19f3d1a58610ec17e77a49f16fe793c5c"
OFFICIAL_REVISION = "546762ef1373dae13569afdfbe501a834040e8c8"
OFFICIAL_BASE = f"https://raw.githubusercontent.com/erkil1452/gaze360/{OFFICIAL_REVISION}"
PART_NAMES = [f"Gaze360.tar.part{suffix}" for suffix in ("aa", "ab", "ac", "ad", "ae", "af")]
EXPECTED_ARCHIVE_BYTES = 30_548_756_480


class SequentialParts(io.RawIOBase):
    """A read-only concatenated stream with strict caller-supplied part order."""

    def __init__(self, paths: list[Path]):
        super().__init__()
        self.paths = paths
        self.index = 0
        self.current = None
        self.bytes_read = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        view = memoryview(buffer).cast("B")
        position = 0
        while position < len(view):
            if self.current is None:
                if self.index >= len(self.paths):
                    break
                self.current = self.paths[self.index].open("rb")
                self.index += 1
            size = self.current.readinto(view[position:])
            if not size:
                self.current.close()
                self.current = None
                continue
            position += size
            self.bytes_read += size
        return position

    def close(self) -> None:
        if self.current is not None:
            self.current.close()
            self.current = None
        super().close()


def selected_member(name: str) -> str | None:
    """Return a safe relative path for only metadata and requested head crops."""
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
        raise ValueError(f"Unsafe TAR member: {name!r}")
    if path.parts == ("metadata.mat",):
        return "metadata.mat"
    if (
        len(path.parts) == 5
        and path.parts[0] == "imgs"
        and path.parts[2] == "head"
        and path.suffix.lower() == ".jpg"
    ):
        return path.as_posix()
    return None


def extract_heads(
    parts: list[Path], raw: Path, progress: Callable[[dict], None] | None = None
) -> dict:
    """Validate every TAR header and decode every selected JPEG during extraction."""
    from PIL import Image

    raw.mkdir(parents=True, exist_ok=True)
    stats = {
        "method": "sequential TAR stream, aa -> ab -> ac -> ad -> ae -> af",
        "tar_members": 0,
        "head_images": 0,
        "body_images_skipped": 0,
        "metadata_files": 0,
        "extracted_bytes": 0,
        "jpeg_decode_failures": 0,
        "jpeg_failure_examples": [],
    }
    written = set()
    with SequentialParts(parts) as source:
        with tarfile.open(fileobj=source, mode="r|", bufsize=1024 * 1024) as archive:
            for member in archive:
                stats["tar_members"] += 1
                selected = selected_member(member.name)
                if selected is None or member.isdir():
                    if member.isfile() and "/body/" in member.name:
                        stats["body_images_skipped"] += 1
                    continue
                if not member.isfile():
                    raise ValueError(f"Selected TAR member is not a regular file: {member.name}")
                if selected in written:
                    raise ValueError(f"Duplicate selected TAR member: {selected}")
                written.add(selected)
                destination = safe_path(raw, selected)
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_name(destination.name + ".extracting")
                reader = archive.extractfile(member)
                if reader is None:
                    raise ValueError(f"Could not read {selected}")
                with reader, temporary.open("wb") as output:
                    shutil.copyfileobj(reader, output, length=1024 * 1024)
                if temporary.stat().st_size != member.size:
                    raise ValueError(f"Extracted size mismatch: {selected}")
                temporary.replace(destination)
                stats["extracted_bytes"] += member.size
                if selected == "metadata.mat":
                    stats["metadata_files"] += 1
                else:
                    stats["head_images"] += 1
                    try:
                        with Image.open(destination) as image:
                            if image.format != "JPEG":
                                raise ValueError(f"Expected JPEG, found {image.format}")
                            image.load()
                    except Exception as error:
                        stats["jpeg_decode_failures"] += 1
                        if len(stats["jpeg_failure_examples"]) < 20:
                            stats["jpeg_failure_examples"].append(f"{selected}: {error}")
                if stats["head_images"] % 5000 == 0 and progress:
                    progress(dict(stats))
        # TAR terminators may leave zero padding after the final header. Hashes
        # already cover all six parts; nevertheless consume the full stream and
        # reject a non-zero trailing stream rather than silently ignoring it.
        while block := source.read(1024 * 1024):
            if any(block):
                raise ValueError("Non-zero bytes follow the TAR end marker")
        stats["stream_bytes_read"] = source.bytes_read
    stats["tar_headers_verified"] = True
    stats["selected_paths_unique"] = True
    stats["body_images_extracted"] = sum(1 for _ in raw.glob("imgs/*/body/*/*.jpg"))
    if stats["metadata_files"] != 1 or not stats["head_images"]:
        raise ValueError(f"Required contents missing: {stats}")
    if stats["jpeg_decode_failures"] or stats["body_images_extracted"]:
        raise ValueError(f"Image verification failed: {stats}")
    return stats


def verify_annotations(raw: Path) -> dict:
    """Check all metadata rows, unused frames, and official split labels."""
    import numpy as np
    from scipy.io import loadmat

    metadata = loadmat(raw / "metadata.mat", simplify_cells=True)
    recordings = np.atleast_1d(metadata["recordings"])
    recording = np.asarray(metadata["recording"]).reshape(-1)
    identities = np.asarray(metadata["person_identity"]).reshape(-1)
    frames = np.asarray(metadata["frame"]).reshape(-1)
    assigned = np.asarray(metadata["split"]).reshape(-1)
    gaze = np.asarray(metadata["gaze_dir"])
    count = len(recording)
    if not all(len(array) == count for array in (identities, frames, assigned, gaze)):
        raise ValueError("Metadata field lengths disagree")
    if gaze.shape != (count, 3) or not np.isfinite(gaze).all():
        raise ValueError("Metadata gaze directions must be finite three-vectors")
    if not set(np.unique(assigned)).issubset({0, 1, 2, 3}):
        raise ValueError("Unexpected metadata split ID")
    row_by_path = {}
    missing = []
    for index in range(count):
        relative = (
            f"{str(recordings[int(recording[index])])}/head/"
            f"{int(identities[index]):06d}/{int(frames[index]):06d}.jpg"
        )
        if relative in row_by_path:
            raise ValueError(f"Duplicate metadata image reference: {relative}")
        row_by_path[relative] = index
        if not (raw / "imgs" / relative).is_file():
            if len(missing) < 20:
                missing.append(relative)
    if missing:
        raise ValueError(f"Metadata references missing head images, examples: {missing}")
    splits = {}
    all_split_paths = set()
    for split_id, filename in enumerate(("train.txt", "validation.txt", "test.txt")):
        seen = set()
        max_difference = 0.0
        with (raw / "splits" / filename).open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                columns = line.split()
                if len(columns) != 4:
                    raise ValueError(f"Invalid split row {filename}:{line_number}")
                relative = columns[0]
                safe = selected_member(f"imgs/{relative}")
                if safe is None or relative not in row_by_path:
                    raise ValueError(f"Unknown split image {filename}:{line_number}: {relative}")
                if relative in seen or relative in all_split_paths:
                    raise ValueError(f"Duplicate or overlapping split image: {relative}")
                seen.add(relative)
                row = row_by_path[relative]
                if int(assigned[row]) != split_id:
                    raise ValueError(f"Split assignment differs from metadata: {relative}")
                direction = np.asarray(columns[1:], dtype=float)
                if not np.isfinite(direction).all():
                    raise ValueError(f"Non-finite split gaze direction: {relative}")
                difference = float(np.max(np.abs(direction - gaze[row])))
                max_difference = max(max_difference, difference)
                if difference > 1e-5:
                    raise ValueError(f"Split gaze differs from metadata: {relative}: {difference}")
        expected = int(np.count_nonzero(assigned == split_id))
        if len(seen) != expected:
            raise ValueError(f"{filename}: {len(seen)} rows versus {expected} metadata entries")
        all_split_paths.update(seen)
        splits[filename] = {
            "rows": len(seen),
            "all_images_exist": True,
            "metadata_split_and_gaze_match": True,
            "maximum_gaze_component_difference": max_difference,
        }
    images = {
        path.relative_to(raw / "imgs").as_posix()
        for path in (raw / "imgs").glob("*/head/*/*.jpg")
    }
    if set(row_by_path) - images:
        raise ValueError("Metadata references images absent from the extracted inventory")
    with (raw / "metadata.mat").open("rb") as metadata_file:
        metadata_sha256 = hashlib.file_digest(metadata_file, "sha256").hexdigest()
    return {
        "metadata_rows": count,
        "recordings": len(recordings),
        "all_metadata_images_exist": True,
        "head_images_on_disk": len(images),
        "head_images_without_metadata": len(images - set(row_by_path)),
        "unused_frames_preserved": int(np.count_nonzero(assigned == 3)),
        "metadata_split_counts": {str(int(k)): v for k, v in Counter(assigned).items()},
        "official_splits": splits,
        "official_splits_are_disjoint": True,
        "metadata_sha256": metadata_sha256,
    }


def prepare(root: Path, range_workers: int = 0) -> dict:
    archive_download = download
    if range_workers:
        from .ranges import download_ranges

        archive_download = partial(download_ranges, workers=range_workers)
    destination = root / "gaze360"
    archives, raw, sources = (destination / name for name in ("archives", "raw", "sources"))
    for path in (archives, raw, sources, raw / "splits"):
        path.mkdir(parents=True, exist_ok=True)
    status = {
        "dataset": "Gaze360",
        "status": "preparing",
        "source": "Public community Hugging Face mirror, not endorsed or verified by dataset authors",
        "mirror_author_endorsement": False,
        "repository": HF_REPO,
        "revision": HF_REVISION,
        "official_repository_revision": OFFICIAL_REVISION,
        "extraction_path": str(raw.resolve()),
        "archives_path": str(archives.resolve()),
        "sources_path": str(sources.resolve()),
        "expected_archive_bytes": EXPECTED_ARCHIVE_BYTES,
        "download_strategy": "bounded HTTP Range chunks" if range_workers else "resumable HTTP stream",
        "download_connections": range_workers or 1,
        "license": {
            "name": "Gaze360 Research License",
            "path": str((sources / "LICENSE.md").resolve()),
            "research_only": True,
            "commercial_use": "Not permitted, including trained models and other derivatives",
            "redistribution": "Not permitted; consult retained license for limited backup/colleague terms",
            "citation_required": True,
        },
        "notes": [
            "3D gaze dataset does not define the boundaries of the user's monitor.",
            "Preserve unused frames for temporal neighbours; only head crops are extracted.",
            "Gaze direction alone does not establish phone or cheat-sheet use.",
        ],
        "downloads": [],
    }

    def save(stage: str | None = None) -> None:
        if stage:
            status["status"] = stage
        status["updated_at"] = datetime.now(timezone.utc).isoformat()
        write_json(destination / "status.json", status)

    def progress(extraction: dict) -> None:
        status["extraction"] = extraction
        save("extracting")
        log(f"Gaze360: {extraction['head_images']:,} heads extracted and decoded")

    save()
    try:
        metadata = fetch_hf(HF_REPO, HF_REVISION, PART_NAMES, sources)
        total = sum(metadata[name]["size"] for name in PART_NAMES)
        if total != EXPECTED_ARCHIVE_BYTES:
            raise ValueError(f"Pinned archive size changed: {total}")
        status["pinned_lfs_metadata"] = metadata
        save("downloading_sources")
        for remote, local in (
            ("dataset/README.md", sources / "README.md"),
            ("LICENSE.md", sources / "LICENSE.md"),
            ("code/train.txt", raw / "splits" / "train.txt"),
            ("code/validation.txt", raw / "splits" / "validation.txt"),
            ("code/test.txt", raw / "splits" / "test.txt"),
        ):
            status["downloads"].append(download(f"{OFFICIAL_BASE}/{remote}", local))
            save()
        save("downloading_archives")
        for name in PART_NAMES:
            entry = metadata[name]
            status["active_archive"] = name
            save()
            status["downloads"].append(
                archive_download(
                    entry["url"], archives / name,
                    expected_sha256=entry["sha256"], expected_size=entry["size"],
                )
            )
            save()
        status.pop("active_archive", None)
        status["archive_bytes"] = sum((archives / name).stat().st_size for name in PART_NAMES)
        save("extracting")
        status["extraction"] = extract_heads([archives / name for name in PART_NAMES], raw, progress)
        save("verifying_annotations")
        status["verification"] = verify_annotations(raw)
        if status["extraction"]["head_images"] != status["verification"]["head_images_on_disk"]:
            raise ValueError("Disk head-image inventory differs from extracted archive inventory")
        status["archive_integrity"] = "All six SHA-256 values and sizes match pinned HF LFS metadata"
        status["occupied_bytes"] = sum(path.stat().st_size for path in destination.rglob("*") if path.is_file())
        save("ready")
        log(f"Gaze360 ready: {status['verification']['head_images_on_disk']:,} head images")
    except Exception as error:
        status["failed_stage"] = status["status"]
        status["error"] = f"{type(error).__name__}: {error}"
        save("incomplete")
        raise
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/datasets"))
    parser.add_argument("--range-workers", type=int, choices=range(5), default=0,
                        help="Use up to four parallel bounded HTTP Range requests (0: streaming)")
    arguments = parser.parse_args()
    try:
        with process_lock(arguments.root / "gaze360" / ".prepare.lock"):
            prepare(arguments.root, range_workers=arguments.range_workers)
    except Exception:
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
