#!/usr/bin/env python3
"""
FTP deployment script for Kramnet -> Hostek
Usage: python deploy/upload.py
Set FTP_PASSWORD environment variable before running.
"""

import ftplib
import os
import sys
from pathlib import Path
from getpass import getpass

FTP_HOST = "ls-web1-14.hostek.se"
FTP_USER = "uwlks486930"
LOCAL_ROOT = Path(__file__).parent.parent  # project root (kramnet/)
REMOTE_ROOT = "/public_html"  # adjust to the correct remote path

EXCLUDE = {
    ".env",
    ".git",
    "__pycache__",
    "venv",
    ".venv",
    "deploy",        # skip the deploy script itself
    "*.pyc",
    ".pytest_cache",
    "node_modules",
}


def should_skip(path: Path) -> bool:
    """Return True if the file/directory should be excluded from upload."""
    for part in path.parts:
        if part in EXCLUDE:
            return True
        if part.endswith(".pyc"):
            return True
    name = path.name
    if name in EXCLUDE or name.endswith(".pyc"):
        return True
    return False


def ensure_remote_dir(ftp: ftplib.FTP, remote_dir: str) -> None:
    """Create remote directory (and parents) if it does not exist."""
    parts = [p for p in remote_dir.split("/") if p]
    current = ""
    for part in parts:
        current += f"/{part}"
        try:
            ftp.mkd(current)
        except ftplib.error_perm:
            pass  # directory already exists


def upload_file(ftp: ftplib.FTP, local_path: Path, remote_path: str) -> None:
    with open(local_path, "rb") as f:
        ftp.storbinary(f"STOR {remote_path}", f)
    print(f"  uploaded: {remote_path}")


def deploy(ftp: ftplib.FTP) -> None:
    uploaded = 0
    skipped = 0

    for local_path in sorted(LOCAL_ROOT.rglob("*")):
        rel = local_path.relative_to(LOCAL_ROOT)

        if should_skip(rel):
            skipped += 1
            continue

        remote_path = f"{REMOTE_ROOT}/{rel.as_posix()}"

        if local_path.is_dir():
            ensure_remote_dir(ftp, remote_path)
        elif local_path.is_file():
            ensure_remote_dir(ftp, str(Path(remote_path).parent).replace("\\", "/"))
            upload_file(ftp, local_path, remote_path)
            uploaded += 1

    print(f"\nDone. {uploaded} files uploaded, {skipped} paths skipped.")


def main() -> None:
    password = os.environ.get("FTP_PASSWORD") or getpass(
        f"FTP password for {FTP_USER}@{FTP_HOST}: "
    )

    print(f"Connecting to {FTP_HOST} as {FTP_USER}...")
    try:
        with ftplib.FTP(FTP_HOST) as ftp:
            ftp.login(FTP_USER, password)
            print(f"Connected. Uploading from {LOCAL_ROOT} -> {REMOTE_ROOT}\n")
            deploy(ftp)
    except ftplib.all_errors as exc:
        print(f"FTP error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
