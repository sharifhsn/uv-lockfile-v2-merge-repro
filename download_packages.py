"""Download the pinned PyPI wheels and verify their SHA-256 values."""

import hashlib
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    catalog = ROOT / "catalog"
    catalog.mkdir(exist_ok=True)
    packages = json.loads((ROOT / "package-manifest.json").read_text())
    for package in packages:
        destination = catalog / package["filename"]
        if not destination.exists():
            with urllib.request.urlopen(package["url"], timeout=30) as response:
                destination.write_bytes(response.read())
        actual = hashlib.sha256(destination.read_bytes()).hexdigest()
        if actual != package["sha256"]:
            raise ValueError(f"Checksum mismatch: {destination.name}")
        print(f"{package['name']} {package['version']}: verified")


if __name__ == "__main__":
    main()
