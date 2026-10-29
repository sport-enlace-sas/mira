"""Offline administrative backup of the vault key, never exposed through HTTP."""

from __future__ import annotations

import argparse
import os
import stat
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Protected ai-private/vault.key")
    parser.add_argument("destination", type=Path, help="Offline backup file (must not exist)")
    args = parser.parse_args()
    fd = os.open(args.source, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as source:
        metadata = os.fstat(source.fileno())
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise SystemExit("Source must be a regular file with mode 0600")
        key = source.read(33)
    if len(key) != 32:
        raise SystemExit("Invalid vault key")
    parent = args.destination.parent
    if not parent.is_dir() or parent.stat().st_mode & 0o077:
        raise SystemExit("Destination directory must already exist with mode 0700")
    fd = os.open(args.destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as destination:
        destination.write(key)
        destination.flush()
        os.fsync(destination.fileno())
    print("Vault key backup completed; retain it separately from the database backup.")


if __name__ == "__main__":
    main()
