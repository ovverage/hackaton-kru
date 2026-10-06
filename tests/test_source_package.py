import subprocess

from scripts.package_release import source_files


def test_training_images_and_untracked_files_never_enter_source_package(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    files = [
        "training/train_phone_v2.py",
        "training/reports/aggregate.json",
        "training/runs/private-features.json",
        "training/runs/photo.jpg",
        "README.md",
        "docs/secret-untracked.md",
        "data/private.jpg",
    ]
    for name in files:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("test")
    subprocess.run(
        ["git", "add", "README.md", "training", "data"], cwd=tmp_path, check=True
    )
    names = {path.relative_to(tmp_path).as_posix() for path in source_files(tmp_path)}
    assert names == {
        "training/train_phone_v2.py",
        "training/reports/aggregate.json",
        "README.md",
    }
