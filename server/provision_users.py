"""Import verified member names and prepared usernames without starting the app.

Usage: python -m server.provision_users --input members.csv --source UniPat
The default is a read-only dry run. Pass --apply to create accounts.
External ids in this importer are local provenance ids, never Lark open_ids.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

# Support both `python -m server.provision_users` and a direct script invocation.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from server.auth import password_hash
    from server.storage import Store, now, uid
else:
    from .auth import password_hash
    from .storage import Store, now, uid


USERNAME = re.compile(r"[a-z0-9]{1,128}")


@dataclass(frozen=True)
class Member:
    row: int
    name: str
    username: str


class ImportProblem(Exception):
    def __init__(self, conflicts, total=0):
        super().__init__("The import was rejected; no accounts were written.")
        self.conflicts = conflicts
        self.total = total


def problem(code, message, member=None):
    item = {"code": code, "message": message}
    if member is not None:
        item.update({"row": member.row, "name": member.name})
    return item


def read_members(path: Path) -> list[Member]:
    members = []
    errors = []
    seen_names = {}
    seen_usernames = {}
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, strict=True)
            if reader.fieldnames is None or len(reader.fieldnames) != 2 or set(reader.fieldnames) != {"name", "username"}:
                raise ImportProblem([problem("csv_headers", "CSV must contain exactly the name and username columns.")])
            for values in reader:
                member = Member(reader.line_num, (values.get("name") or "").strip(), values.get("username") or "")
                members.append(member)
                if None in values or values.get("name") is None or values.get("username") is None:
                    errors.append(problem("csv_columns", "Every row must have exactly two fields.", member))
                if not member.name or any(ord(char) < 32 for char in member.name):
                    errors.append(problem("invalid_name", "A nonempty member name without control characters is required.", member))
                if not USERNAME.fullmatch(member.username):
                    errors.append(problem("invalid_username", "Username must contain 1–128 lowercase ASCII letters or digits, without spaces.", member))
                name_key = member.name.casefold()
                if name_key in seen_names:
                    errors.append(problem("duplicate_name", f"Member name duplicates CSV row {seen_names[name_key]}.", member))
                else:
                    seen_names[name_key] = member.row
                if member.username in seen_usernames:
                    errors.append(problem("duplicate_username", f"Username duplicates CSV row {seen_usernames[member.username]}.", member))
                else:
                    seen_usernames[member.username] = member.row
    except UnicodeDecodeError as exc:
        raise ImportProblem([problem("csv_encoding", "CSV must use UTF-8 encoding.")]) from exc
    except csv.Error as exc:
        raise ImportProblem([problem("invalid_csv", "CSV could not be parsed; check quoting and column boundaries.")]) from exc
    except OSError as exc:
        raise ImportProblem([problem("input_unreadable", "The CSV input file could not be read.")]) from exc
    if not members:
        errors.append(problem("empty_csv", "CSV must contain at least one member row."))
    if errors:
        raise ImportProblem(errors, len(members))
    return members


def local_identity(source: str, name: str) -> tuple[str, str]:
    name_hash = hashlib.sha256(name.encode("utf-8")).hexdigest()
    external_id = f"imported:{source}:{name_hash}"
    local_part = hashlib.sha256(external_id.encode("utf-8")).hexdigest()
    return external_id, f"{local_part}@accounts.invalid"


def existing_users(db):
    columns = {row[1] for row in db.execute("PRAGMA table_info(users)")}
    required = {"id", "name", "email", "role"}
    if not required <= columns:
        raise ImportProblem([problem("unsupported_database", "The database does not contain a compatible users table.")])
    selected = ["id", "name", "email", "role"]
    selected.extend(field if field in columns else f"NULL AS {field}" for field in ("username", "external_id"))
    rows = db.execute("SELECT " + ",".join(selected) + " FROM users")
    names = [column[0] for column in rows.description]
    return [dict(zip(names, row)) for row in rows]


def read_existing(directory: Path):
    database = directory / "harbor.sqlite3"
    if not database.exists():
        return []
    # A read-only connection sees committed WAL records without initializing Store.
    db = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        return existing_users(db)
    finally:
        db.close()


def plan_import(members: list[Member], source: str, users: list[dict], admin_username: str | None):
    errors = []
    planned = []
    for member in members:
        external_id, email = local_identity(source, member.name)
        matches = [user for user in users if user["external_id"] == external_id]
        existing = matches[0] if len(matches) == 1 else None
        if len(matches) > 1:
            errors.append(problem("external_id_collision", "Multiple existing accounts share this local import identity.", member))
        if existing and (existing["name"] != member.name or existing["username"] != member.username):
            errors.append(problem("existing_identity_mismatch", "This source and member name already exist with different account details; existing credentials will not be changed.", member))
        existing_id = existing["id"] if existing else None
        for user in users:
            if user["id"] == existing_id:
                continue
            if user["username"] is not None and user["username"].casefold() == member.username.casefold():
                errors.append(problem("username_collision", "Username is already assigned to another account.", member))
            if user["name"].strip().casefold() == member.name.casefold():
                errors.append(problem("name_collision", "Member name already belongs to another account or import source.", member))
            if user["email"].casefold() == email.casefold():
                errors.append(problem("reserved_email_collision", "The reserved account address is already assigned to another account.", member))
        planned.append({"member": member, "external_id": external_id, "email": email,
                        "action": "skip" if existing else "create",
                        "role": existing["role"] if existing else "admin" if member.username == admin_username else "member"})
    if errors:
        raise ImportProblem(errors, len(members))
    return planned


def report(source, mode, planned=None, conflicts=None, total=None, applied=False):
    planned = planned or []
    return {"source": source, "mode": mode, "applied": applied,
            "total": len(planned) if total is None else total,
            "create": sum(item["action"] == "create" for item in planned),
            "skip": sum(item["action"] == "skip" for item in planned),
            "conflicts": conflicts or [],
            # Usernames are deliberately omitted: initial passwords equal them.
            "members": [{"row": item["member"].row, "name": item["member"].name,
                         "action": item["action"], "role": item["role"]} for item in planned]}


def provision(input_path: Path, source: str, data_dir: Path, *, apply=False, admin_username=None):
    source = source.strip()
    if not source or any(ord(char) < 32 for char in source):
        raise ImportProblem([problem("invalid_source", "A nonempty source label without control characters is required.")])
    members = read_members(input_path)
    if admin_username is not None:
        if not USERNAME.fullmatch(admin_username) or admin_username not in {member.username for member in members}:
            raise ImportProblem([problem("invalid_admin", "The administrator option must exactly match a valid username in this CSV.")], len(members))
    data_dir = data_dir.resolve()
    planned = plan_import(members, source, read_existing(data_dir), admin_username)
    if not apply:
        return report(source, "dry-run", planned)
    store = Store(data_dir)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        # Recheck under the write lock so concurrent registration cannot produce
        # a partial batch after a successful read-only preflight.
        planned = plan_import(members, source, existing_users(db), admin_username)
        for item in planned:
            if item["action"] == "skip":
                continue
            member = item["member"]
            db.execute("""INSERT INTO users
                (id,name,email,password_hash,role,created_at,username,external_id)
                VALUES (?,?,?,?,?,?,?,?)""", (
                    uid(), member.name, item["email"], password_hash(member.username),
                    item["role"], now(), member.username, item["external_id"],
                ))
    return report(source, "apply", planned, applied=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Provision verified CSV members; defaults to a dry run.")
    parser.add_argument("--input", required=True, type=Path, help="UTF-8 CSV with exactly name,username columns")
    parser.add_argument("--source", required=True, help="Local provenance label; this is not a Lark open_id")
    parser.add_argument("--data-dir", type=Path, default=Path(os.getenv("HARBOR_DATA_DIR", Path(__file__).resolve().parent.parent / "data")))
    parser.add_argument("--apply", action="store_true", help="Create accounts after all checks pass")
    parser.add_argument("--admin-username", help="Grant admin only if this CSV account is newly created")
    args = parser.parse_args(argv)
    try:
        result = provision(args.input, args.source, args.data_dir, apply=args.apply, admin_username=args.admin_username)
        code = 0
    except ImportProblem as exc:
        result = report(args.source.strip(), "apply" if args.apply else "dry-run", conflicts=exc.conflicts, total=exc.total)
        code = 2
    except (sqlite3.Error, OSError):
        result = report(args.source.strip(), "apply" if args.apply else "dry-run", conflicts=[problem("database_error", "The database operation failed; no account batch was committed.")])
        code = 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
