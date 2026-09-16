"""Export a stopped workspace and initialize a fresh deployment from its snapshot."""
from __future__ import annotations

import argparse
import fcntl
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile


def validate_snapshot(directory: Path) -> list[str]:
    database = directory / "harbor.sqlite3"
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise ValueError("Deployment database is corrupt")
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Deployment database has invalid references")
        rows = db.execute("SELECT id,size FROM files").fetchall()
        for file_id, size in rows:
            if not re.fullmatch(r"[0-9a-f]{32}", file_id):
                raise ValueError("Invalid deployment file ID")
            blob = directory / "files" / file_id
            if blob.is_symlink() or not blob.is_file() or blob.stat().st_size != size:
                raise ValueError("Deployment snapshot has a missing or incomplete file")
    return [row[0] for row in rows]


def export_snapshot(source: str | Path, destination: str | Path) -> None:
    """Call with the source service stopped so files and database stay consistent."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination.exists():
        raise FileExistsError("Snapshot destination already exists; use a new directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".snapshot-", dir=destination.parent) as temporary:
        staged = Path(temporary) / "snapshot"
        staged.mkdir(mode=0o700)
        (staged / "files").mkdir(mode=0o700)
        with sqlite3.connect((source / "harbor.sqlite3").as_uri() + "?mode=ro", uri=True) as original:
            with sqlite3.connect(staged / "harbor.sqlite3") as snapshot:
                original.backup(snapshot)
                # Browser sessions belong to the original deployment. Passwords,
                # user roles and project API tokens retain their existing hashes.
                snapshot.execute("DELETE FROM sessions")
                snapshot.commit()
                snapshot.execute("PRAGMA journal_mode=DELETE")
                snapshot.execute("VACUUM")
                for file_id, in snapshot.execute("SELECT id FROM files"):
                    if not re.fullmatch(r"[0-9a-f]{32}", file_id):
                        raise ValueError("Invalid source file ID")
                    shutil.copyfile(source / "files" / file_id, staged / "files" / file_id)
        validate_snapshot(staged)
        staged.rename(destination)


def initialize_data(directory: str | Path, snapshot: str | Path) -> bool:
    """Initialize only a missing database; upgrades never overwrite live data."""
    directory, snapshot = Path(directory).resolve(), Path(snapshot).resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / ".bootstrap.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        database = directory / "harbor.sqlite3"
        if database.exists():
            return False
        file_ids = validate_snapshot(snapshot)
        # Stage everything before publishing. Install the database last so a
        # process interrupted while copying can retry initialization on restart.
        with tempfile.TemporaryDirectory(prefix=".bootstrap-", dir=directory) as temporary:
            staged = Path(temporary)
            for file_id in file_ids:
                shutil.copyfile(snapshot / "files" / file_id, staged / file_id)
                (staged / file_id).chmod(0o600)
            shutil.copyfile(snapshot / "harbor.sqlite3", staged / "harbor.sqlite3")
            (staged / "harbor.sqlite3").chmod(0o600)
            blobs = directory / "files"
            blobs.mkdir(exist_ok=True, mode=0o700)
            for file_id in file_ids:
                os.replace(staged / file_id, blobs / file_id)
            os.replace(staged / "harbor.sqlite3", database)
        return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Stopped workspace data directory")
    parser.add_argument("--destination", required=True, help="New snapshot directory")
    args = parser.parse_args()
    export_snapshot(args.source, args.destination)
    print("Deployment snapshot exported; account details are not printed.")
