"""Import the private training package without committing personal media."""
import argparse
from pathlib import Path, PurePosixPath
import stat
import zipfile


def extract(archive, destination):
    destination = Path(destination).resolve()
    target = destination / "qorgau_dataset"
    if target.exists():
        raise ValueError("Dataset already exists; choose a new destination")
    with zipfile.ZipFile(archive) as bundle:
        items = bundle.infolist()
        if sum(item.file_size for item in items) > 10 * 1024**3:
            raise ValueError("Bundle is larger than the supported 10 GiB")
        for item in items:
            path = PurePosixPath(item.filename)
            if (path.is_absolute() or ".." in path.parts or "\\" in item.filename or
                not path.parts or path.parts[0] != "qorgau_dataset" or
                any(":" in part for part in path.parts) or stat.S_ISLNK(item.external_attr >> 16)):
                raise ValueError("Unsafe archive path")
        if bundle.testzip() is not None:
            raise ValueError("ZIP CRC validation failed")
        bundle.extractall(destination)
    return target


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("archive", type=Path)
    p.add_argument("--destination", type=Path, default=Path("data"))
    args = p.parse_args()
    print(extract(args.archive, args.destination))


if __name__ == "__main__":
    main()
