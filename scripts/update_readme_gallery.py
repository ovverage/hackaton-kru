"""Render only approved real-photo slots; absent files stay explicit placeholders."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PHOTO_DIR = ROOT / "docs/images/photos"
START = "<!-- REAL_PHOTOS_START -->"
END = "<!-- REAL_PHOTOS_END -->"


def render():
    entries = json.loads((PHOTO_DIR / "manifest.json").read_text(encoding="utf-8"))
    lines = []
    for entry in entries:
        filename = entry["file"]
        if Path(filename).name != filename or Path(filename).suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            raise ValueError(f"Invalid photo filename: {filename}")
        path = PHOTO_DIR / filename
        if path.resolve().parent != PHOTO_DIR.resolve():
            raise ValueError("Photo must stay inside the gallery directory")
        lines += [f"### {entry['title']}", "", entry["caption"], ""]
        if path.is_file():
            relative = path.relative_to(ROOT).as_posix()
            lines += [f"![{entry['title']}]({relative})", ""]
        else:
            lines += [f"> Ожидается реальный прогон. Файл: `{path.relative_to(ROOT).as_posix()}`.", ""]
    return "\n" + "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if README gallery needs regeneration")
    args = parser.parse_args()
    path = ROOT / "README.md"
    before = path.read_text(encoding="utf-8-sig")
    if before.count(START) != 1 or before.count(END) != 1:
        raise ValueError("README must contain exactly one ordered pair of gallery markers")
    prefix, tail = before.split(START)
    _, suffix = tail.split(END)
    after = prefix + START + render() + END + suffix
    if args.check:
        if before != after:
            raise SystemExit("README gallery is stale; run scripts/update_readme_gallery.py")
        print("README gallery is current")
    else:
        path.write_text(after, encoding="utf-8")
        print("README gallery updated; missing photos remain labelled placeholders")


if __name__ == "__main__":
    main()
