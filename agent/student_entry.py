"""PyInstaller entry point; no console is required for the student app."""

from multiprocessing import freeze_support
import sys

if __name__ == "__main__":
    freeze_support()
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
