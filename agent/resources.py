from pathlib import Path
import sys


def model_path(name):
    root = (
        Path(sys._MEIPASS)
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parents[1]
    )
    return root / "models" / name
