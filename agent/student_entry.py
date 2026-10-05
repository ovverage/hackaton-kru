"""PyInstaller entry point; no console is required for the student app."""

from multiprocessing import freeze_support
from agent.client import main

if __name__ == "__main__":
    freeze_support()
    main()
