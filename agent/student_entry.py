"""PyInstaller entry point; no console is required for the student app."""

from multiprocessing import freeze_support
import os
import shlex
import sys


def configure_webengine():
    """Disable unused Cast discovery before Chromium starts; keep its sandbox."""
    key = "QTWEBENGINE_CHROMIUM_FLAGS"
    arguments = shlex.split(os.environ.get(key, ""), posix=False)
    others, disabled = [], []
    for argument in arguments:
        if argument.startswith("--disable-features="):
            disabled.extend(argument.split("=", 1)[1].strip('"\'').split(","))
        else:
            others.append(argument)
    disabled.append("MediaRouter")
    others.append("--disable-features=" + ",".join(dict.fromkeys(filter(None, disabled))))
    os.environ[key] = " ".join(others)


if __name__ == "__main__":
    freeze_support()
    configure_webengine()
    try:
        from agent.client import main
        main()
    except Exception:
        if "--self-test" in sys.argv:
            import json
            import traceback
            from pathlib import Path
            output = Path(sys.argv[sys.argv.index("--self-test") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps({"result": "FAIL", "error": traceback.format_exc()}, indent=2), encoding="utf-8")
            sys.exit(1)
        raise
