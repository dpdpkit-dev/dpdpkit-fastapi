#!/usr/bin/env python3
"""Download the dashboard bundle pinned in ``admin-bundle.version`` and unpack it into the package.

The release workflow runs this before building, so the wheel ships the real dashboard. A source checkout keeps the
placeholder page. Usage: ``scripts/fetch_admin_bundle.py <admin_static directory>``
"""

from __future__ import annotations

import hashlib
import io
import sys
import tarfile
import urllib.request
from pathlib import Path

RELEASES = "https://github.com/dpdpkit-dev/dpdpkit-admin/releases/download"


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def main(dest: Path) -> None:
    version = Path("admin-bundle.version").read_text(encoding="utf-8").strip()
    name = f"dpdpkit-admin-{version}.tar.gz"
    data = fetch(f"{RELEASES}/v{version}/{name}")
    expected = fetch(f"{RELEASES}/v{version}/{name}.sha256").split()[0].decode()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        sys.exit(f"{name}: checksum {actual} does not match {expected}")
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        archive.extractall(dest, filter="data")
    print(f"unpacked {name} into {dest}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
