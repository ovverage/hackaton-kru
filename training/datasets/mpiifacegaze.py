"""Download the original MPIIFaceGaze archive and audit its full contents.

Run with ``python -m training.datasets.mpiifacegaze --root data/datasets``.
This module prepares data only; it never trains a model or fetches weights.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path, PurePosixPath
import shutil
import traceback

import numpy as np
from scipy.io import loadmat

from .common import download, extract_zip_verified, log, process_lock, write_json


OFFICIAL_URL = "https://datasets.d2.mpi-inf.mpg.de/MPIIGaze/MPIIFaceGaze.zip"
DARUS_URL = "https://darus.uni-stuttgart.de/api/access/datafile/198951"
DARUS_MD5 = "dcd051b577c55701e143957edac8be51"
OFFICIAL_PAGE = (
    "https://www.mpi-inf.mpg.de/departments/computer-vision-and-machine-learning/"
    "research/gaze-based-human-computer-interaction/"
    "its-written-all-over-your-face-full-face-appearance-based-gaze-estimation"
)
DARUS_METADATA = (
    "https://darus.uni-stuttgart.de/api/datasets/:persistentId/versions/1.0"
    "?persistentId=doi:10.18419/DARUS-3240"
)
LICENSE_URL = "https://creativecommons.org/licenses/by-nc-sa/4.0/legalcode.txt"
SUBJECTS = [f"p{number:02d}" for number in range(15)]


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def verify_raw(raw: Path) -> dict:
    """Check all annotation references and parse every participant calibration."""
    roots = [path.parent for path in raw.rglob("p00") if path.is_dir()]
    if len(roots) != 1:
        raise ValueError(f"Expected exactly one participant root; found {roots}")
    data_root = roots[0]
    issues: list[str] = []
    participants: dict = {}
    for subject in SUBJECTS:
        directory = data_root / subject
        result: dict = {"path": str(directory.resolve()), "issues": []}
        subject_issues: list[str] = result["issues"]
        participants[subject] = result
        if not directory.is_dir():
            subject_issues.append("Participant directory missing")
            continue
        images = {
            str(path.relative_to(directory)).replace("\\", "/")
            for path in directory.rglob("*")
            if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png"}
        }
        result["image_count"] = len(images)
        if not images:
            subject_issues.append("Participant has no images")
        annotation = directory / f"{subject}.txt"
        result["annotation_path"] = str(annotation.resolve())
        references: set[str] = set()
        annotation_rows = 0
        missing = []
        malformed = []
        if not annotation.is_file():
            subject_issues.append("Participant annotation missing")
        else:
            for line_number, line in enumerate(
                annotation.read_text(encoding="utf-8-sig").splitlines(), start=1
            ):
                fields = line.split()
                if not fields:
                    continue
                annotation_rows += 1
                # 1 path, 26 numeric coordinates, and the evaluation eye label.
                if len(fields) != 28:
                    malformed.append({"line": line_number, "field_count": len(fields)})
                    continue
                reference = PurePosixPath(fields[0].replace("\\", "/"))
                if reference.is_absolute() or ".." in reference.parts:
                    malformed.append({"line": line_number, "unsafe_path": str(reference)})
                    continue
                normalized = reference.as_posix()
                if normalized.startswith(f"{subject}/"):
                    normalized = normalized[len(subject) + 1 :]
                references.add(normalized)
                if normalized not in images:
                    missing.append({"line": line_number, "image": normalized})
                try:
                    numbers = np.asarray(fields[1:27], dtype=float)
                    if not np.isfinite(numbers).all():
                        raise ValueError("non-finite coordinates")
                except ValueError as error:
                    malformed.append({"line": line_number, "error": str(error)})
            if not annotation_rows:
                subject_issues.append("Participant annotation is empty")
        result.update(
            annotation_rows=annotation_rows,
            unique_referenced_images=len(references),
            duplicate_reference_rows=annotation_rows - len(references),
            unreferenced_images=len(images - references),
            missing_image_references_count=len(missing),
            missing_image_references_examples=missing[:20],
            malformed_annotation_rows_count=len(malformed),
            malformed_annotation_rows_examples=malformed[:20],
        )
        if missing:
            subject_issues.append(f"{len(missing)} annotation image references are missing")
        if malformed:
            subject_issues.append(f"{len(malformed)} malformed annotation rows")
        calibration = directory / "Calibration"
        result["calibration_path"] = str(calibration.resolve())
        calibration_results = {}
        result["calibration"] = calibration_results
        for name, expected_keys in (
            ("Camera.mat", {"cameraMatrix", "distCoeffs"}),
            ("monitorPose.mat", {"rvecs", "tvecs"}),
            ("screenSize.mat", {"height_pixel", "width_pixel", "height_mm", "width_mm"}),
        ):
            path = calibration / name
            # The authors' web page spells the screen-size filename "creenSize.mat".
            if name == "screenSize.mat" and not path.is_file():
                alternate = calibration / "creenSize.mat"
                if alternate.is_file():
                    path = alternate
            item: dict = {"path": str(path.resolve()), "exists": path.is_file()}
            calibration_results[name] = item
            if not path.is_file():
                subject_issues.append(f"Missing calibration {name}")
                continue
            try:
                contents = loadmat(path)
                keys = {key for key in contents if not key.startswith("__")}
                item["keys"] = sorted(keys)
                if name == "monitorPose.mat" and "rvecs" not in keys and "rvects" in keys:
                    # The distributed files spell this variable differently from
                    # the README. Preserve the file and record the observed alias.
                    contents["rvecs"] = contents["rvects"]
                    keys.add("rvecs")
                    item["rotation_variable"] = "rvects"
                    item["documentation_note"] = "Archive uses rvects; README calls it rvecs."
                absent = sorted(expected_keys - keys)
                if absent:
                    subject_issues.append(f"{name} missing variables: {absent}")
                for key in expected_keys & keys:
                    value = np.asarray(contents[key])
                    if not np.isfinite(value).all():
                        subject_issues.append(f"{name}/{key} contains non-finite values")
                    if name == "screenSize.mat":
                        if value.size != 1 or float(value.reshape(-1)[0]) <= 0:
                            subject_issues.append(f"{name}/{key} must be a positive scalar")
                        else:
                            item[key] = float(value.reshape(-1)[0])
                item["mat_file_readable"] = True
            except Exception as error:
                item["error"] = str(error)
                subject_issues.append(f"Unreadable {name}: {error}")
        log(f"MPIIFaceGaze {subject}: {len(images)} images, {annotation_rows} annotations")
    for subject, result in participants.items():
        issues.extend(f"{subject}: {issue}" for issue in result["issues"])
    raw_files = [path for path in raw.rglob("*") if path.is_file()]
    return {
        "status": "passed" if not issues else "failed",
        "data_root": str(data_root.resolve()),
        "expected_participants": SUBJECTS,
        "participants": participants,
        "image_count": sum(item.get("image_count", 0) for item in participants.values()),
        "annotation_rows": sum(item.get("annotation_rows", 0) for item in participants.values()),
        "all_annotation_image_references_exist": not any(
            item.get("missing_image_references_count", 0) for item in participants.values()
        ) and all(item.get("annotation_rows", 0) for item in participants.values()),
        "raw_file_count": len(raw_files),
        "raw_size_bytes": sum(path.stat().st_size for path in raw_files),
        "issues": issues,
    }


def prepare(root: Path) -> dict:
    directory = root.resolve() / "mpiifacegaze"
    archives, raw, sources = (directory / name for name in ("archives", "raw", "sources"))
    for path in (archives, raw, sources):
        path.mkdir(parents=True, exist_ok=True)
    status = {
        "dataset": "MPIIFaceGaze",
        "status": "preparing",
        "started_at": timestamp(),
        "extraction_path": str(raw),
        "source_attempts": [],
        "archives": [],
        "sources": [],
        "errors": [],
        "license": {
            "spdx": "CC-BY-NC-SA-4.0",
            "url": LICENSE_URL,
            "restriction": "Only for non-commercial scientific purposes; cite the authors.",
            "evidence_url": OFFICIAL_PAGE,
        },
    }

    def save() -> None:
        status["updated_at"] = timestamp()
        write_json(directory / "status.json", status)

    save()
    try:
        for url, name in (
            (OFFICIAL_PAGE, "official-description.html"),
            (DARUS_METADATA, "darus-version-1.0.json"),
            (LICENSE_URL, "CC-BY-NC-SA-4.0.txt"),
        ):
            try:
                status["sources"].append(download(url, sources / name))
            except Exception as error:
                # Keep downloading the dataset if one redundant evidence page is offline.
                status["errors"].append({"phase": "source_evidence", "url": url, "error": str(error)})
            save()
        status["status"] = "downloading"
        save()
        selected = None
        for url, name, md5 in (
            (OFFICIAL_URL, "MPIIFaceGaze-official.zip", None),
            (DARUS_URL, "Data-darus-198951.zip", DARUS_MD5),
        ):
            attempt = {"url": url, "path": str(archives / name), "status": "downloading"}
            status["source_attempts"].append(attempt)
            save()
            try:
                result = download(url, archives / name, expected_md5=md5)
                result["source_kind"] = "official" if url == OFFICIAL_URL else "author_deposited_alternative"
                result["publisher_checksum"] = (
                    {"algorithm": "md5", "value": DARUS_MD5}
                    if md5 else {"available": False, "note": "No publisher checksum supplied for the official ZIP; local SHA-256 is recorded and ZIP CRC validation is required before readiness."}
                )
                attempt["status"] = "downloaded"
                status["archives"].append(result)
                selected = archives / name
                break
            except Exception as error:
                attempt["status"] = "failed"
                attempt["error"] = str(error)
                status["errors"].append({"phase": "download", "url": url, "error": str(error)})
                save()
        if selected is None:
            raise RuntimeError("Both official and author-deposited archive downloads failed")
        status["selected_source"] = status["archives"][-1]["source_kind"]
        status["status"] = "extracting"
        save()
        status["archive_verification"] = extract_zip_verified(selected, raw)
        status["status"] = "verifying"
        save()
        status["verification"] = verify_raw(raw)
        # Retain the authors' original README/changelog as readily accessible evidence.
        for pattern in ("readme*", "README*", "changelog*"):
            for path in raw.rglob(pattern):
                if path.is_file():
                    destination = sources / ("archive-" + path.name)
                    if not destination.exists():
                        shutil.copy2(path, destination)
        if status["verification"]["status"] != "passed":
            status["status"] = "verification_failed"
        elif not (sources / "CC-BY-NC-SA-4.0.txt").is_file() or not (sources / "official-description.html").is_file():
            status["status"] = "data_verified_source_evidence_incomplete"
        else:
            status["status"] = "ready"
        status["completed_at"] = timestamp()
    except Exception as error:
        status["status"] = "failed"
        status["errors"].append({"phase": "prepare", "error": str(error), "traceback": traceback.format_exc()})
        log(f"MPIIFaceGaze failed: {error}")
    finally:
        save()
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/datasets"))
    args = parser.parse_args()
    try:
        with process_lock(args.root.resolve() / "mpiifacegaze" / ".prepare.lock"):
            status = prepare(args.root)
    except RuntimeError as error:
        log(f"MPIIFaceGaze preparation did not start: {error}")
        return 1
    print(json.dumps({"dataset": status["dataset"], "status": status["status"]}, indent=2))
    return 0 if status["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
