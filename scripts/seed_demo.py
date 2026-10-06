"""Seed an explicitly selected disposable localhost QA server, never the classroom DB."""

import argparse
import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    with httpx.Client(
        base_url=f"http://127.0.0.1:{args.port}", headers={"X-Requested-With": "Qorgau"}
    ) as client:
        credentials = {"name": "Qorgau QA", "password": "qorgau-disposable-qa-2026"}
        result = client.post("/api/auth/setup", json=credentials)
        if result.status_code == 409:
            result = client.post("/api/auth/login", json=credentials)
        result.raise_for_status()
        client.post("/api/demo/devices", json={}).raise_for_status()
    print("Disposable QA server ready. Login: Qorgau QA / qorgau-disposable-qa-2026")


if __name__ == "__main__":
    main()
