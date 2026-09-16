#!/usr/bin/env python3
"""Upload Harbor tasks and existing rollouts using only Python's standard library.

Configuration comes from flags or environment variables HARBOR_API_URL,
HARBOR_API_TOKEN and HARBOR_TASK_SET_ID. No dotenv file is loaded.
Only top-level .git and __pycache__ entries are omitted; nested
directories with these names remain task material and are preserved.
Nothing in an uploaded task is executed. Python 3.9 or later is supported.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import stat
import sys
import time
from urllib import error, parse, request
import uuid
import zipfile
import zlib


MAX_UPLOAD = 50 * 1024 * 1024
MAX_EXPANDED = 100 * 1024 * 1024
MAX_FILE = 20 * 1024 * 1024
MAX_FILES = 2000
MAX_RESPONSE = 8 * 1024 * 1024
IGNORED_PARTS = {".git", "__pycache__"}
HARBOR_DIRECTORIES = {"environment", "tests", "solution", "steps", "agent", "verifier", "artifacts"}
RETRY_STATUSES = {502, 503, 504}


class CLIError(Exception):
    def __init__(self, message, *, status=None, code="client_error"):
        super().__init__(message)
        self.status = status
        self.code = code


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise CLIError(message, code="arguments")


def safe_path(value):
    value = value.replace("\\", "/")
    if (not value or value.startswith("/") or ":" in value
            or ".." in value.split("/") or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise CLIError("Unsafe archive path: " + repr(value), code="unsafe_path")
    normalized = str(PurePosixPath(value))
    if normalized == "." or len(normalized) > 1000:
        raise CLIError("Archive path is empty or exceeds 1000 characters", code="unsafe_path")
    return normalized


def ignored(path):
    return PurePosixPath(path).parts[0] in IGNORED_PARTS


def add_entry(entries, path, data):
    path = safe_path(path)
    if path in entries:
        raise CLIError("Duplicate archive path: " + path, code="unsafe_path")
    if len(data) > MAX_FILE:
        raise CLIError("A file exceeds the 20 MB limit: " + path, code="size_limit")
    if len(entries) >= MAX_FILES or sum(map(len, entries.values())) + len(data) > MAX_EXPANDED:
        raise CLIError("Expanded upload exceeds 100 MB or 2000 files", code="size_limit")
    entries[path] = data


def read_regular_file(path, limit):
    # O_NOFOLLOW also rejects a link substituted between the directory scan and open.
    if not stat.S_ISREG(os.lstat(path).st_mode):
        raise CLIError("Only regular files are supported: " + str(path), code="unsafe_path")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(descriptor, "rb") as handle:
        metadata = os.fstat(handle.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise CLIError("Only regular files are supported: " + str(path), code="unsafe_path")
        if metadata.st_size > limit:
            raise CLIError("File exceeds the upload size limit: " + str(path), code="size_limit")
        body = handle.read(limit + 1)
        if len(body) > limit:
            raise CLIError("File exceeds the upload size limit: " + str(path), code="size_limit")
        return body


def read_source(source):
    """Read a directory or ZIP without extraction, execution, or symlink traversal."""
    source = Path(source)
    if source.is_symlink():
        raise CLIError("Symbolic links are not supported: " + str(source), code="unsafe_path")
    entries = {}
    if source.is_dir():
        visited = 0

        def walk(directory, prefix=""):
            nonlocal visited
            with os.scandir(directory) as scanner:
                children = sorted(scanner, key=lambda child: child.name)
            for child in children:
                path = safe_path(prefix + child.name)
                if ignored(path):
                    continue
                visited += 1
                if visited > MAX_FILES * 2:
                    raise CLIError("Directory contains too many entries", code="size_limit")
                if child.is_symlink():
                    raise CLIError("Symbolic links are not supported: " + path, code="unsafe_path")
                if child.is_dir(follow_symlinks=False):
                    walk(child.path, path + "/")
                elif child.is_file(follow_symlinks=False):
                    add_entry(entries, path, read_regular_file(child.path, MAX_FILE))
                else:
                    raise CLIError("Special files are not supported: " + path, code="unsafe_path")

        walk(source)
    else:
        if source.suffix.lower() != ".zip":
            raise CLIError("PATH must be a directory or a .zip file", code="arguments")
        payload = read_regular_file(source, MAX_UPLOAD)
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                members = archive.infolist()
                if len(members) > MAX_FILES * 2:
                    raise CLIError("ZIP contains too many entries", code="size_limit")
                for member in members:
                    path = safe_path(member.filename)
                    mode = stat.S_IFMT(member.external_attr >> 16)
                    if mode not in (0, stat.S_IFREG, stat.S_IFDIR):
                        raise CLIError("ZIP contains a link or special file: " + path, code="unsafe_path")
                    if member.flag_bits & 1:
                        raise CLIError("Encrypted ZIP files are not supported", code="unsafe_path")
                    if member.is_dir() or ignored(path):
                        continue
                    if member.file_size > MAX_FILE or sum(map(len, entries.values())) + member.file_size > MAX_EXPANDED:
                        raise CLIError("Expanded ZIP exceeds the file or total size limit", code="size_limit")
                    if member.file_size > 1024 * 1024 and member.file_size / max(member.compress_size, 1) > 200:
                        raise CLIError("ZIP compression ratio exceeds the server limit", code="size_limit")
                    with archive.open(member) as handle:
                        add_entry(entries, path, handle.read(MAX_FILE + 1))
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError, zlib.error) as exc:
            raise CLIError("Cannot read ZIP: " + str(exc), code="invalid_archive") from exc
    if not entries:
        raise CLIError("Upload contains no readable files", code="arguments")
    return entries


def strip_wrapper(entries):
    """Match the server's removal of a single enclosing upload directory."""
    roots = {path.split("/", 1)[0] for path in entries}
    if len(roots) == 1 and all("/" in path for path in entries) and next(iter(roots)) not in HARBOR_DIRECTORIES:
        return {path.split("/", 1)[1]: data for path, data in entries.items()}
    return entries


def source_name(source):
    source = Path(source)
    # Resolve '.' for the display name only; read_source still checks the original path.
    name = Path(os.path.abspath(source)).name
    if source.is_file() and name.lower().endswith(".zip"):
        name = name[:-4]
    name = safe_path(name)
    if "/" in name:
        raise CLIError("Upload name must not contain a path separator", code="unsafe_path")
    return name


def prepare_task(source, rollout_sources=()):
    entries = strip_wrapper(read_source(source))
    manifests = [path for path in entries if PurePosixPath(path).name == "task.toml"]
    if len(manifests) != 1:
        raise CLIError("A task upload must contain exactly one task.toml", code="invalid_task")
    prefix = manifests[0][:-len("task.toml")]
    if prefix.count("/") > 1 or not (
        prefix + "instruction.md" in entries
        or any(path.startswith(prefix + "steps/") and path.endswith("/instruction.md") for path in entries)
    ):
        raise CLIError("task.toml must be at the root or one enclosing directory, with instruction.md or steps/**/instruction.md", code="invalid_task")
    for source_path in rollout_sources:
        rollout_entries = strip_wrapper(read_source(source_path))
        name = source_name(source_path)
        suffix = 1
        destination = prefix + "rollouts/" + name
        while any(path == destination or path.startswith(destination + "/") for path in entries):
            suffix += 1
            destination = prefix + "rollouts/" + name + "-" + str(suffix)
        # Prevent a preexisting file named "rollouts" from also becoming a directory.
        if any(part in entries for part in (prefix + "rollouts", destination)):
            raise CLIError("Rollout destination conflicts with a task file", code="unsafe_path")
        for path, data in rollout_entries.items():
            add_entry(entries, destination + "/" + path, data)
    return source_name(source) + ".zip", entries


def make_archive(entries):
    """Stable content, timestamps, permissions and ordering make retries reproducible."""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path, data in sorted(entries.items()):
            member = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            member.create_system = 3
            member.external_attr = (stat.S_IFREG | 0o644) << 16
            member.compress_type = zipfile.ZIP_DEFLATED
            # Do not manufacture a ZIP bomb rejection from a legitimate local text file.
            if len(data) > 1024 * 1024 and len(data) / max(len(zlib.compress(data, 6)) - 6, 1) > 200:
                member.compress_type = zipfile.ZIP_STORED
            archive.writestr(member, data, compresslevel=6)
    payload = output.getvalue()
    if len(payload) > MAX_UPLOAD:
        raise CLIError("Upload ZIP exceeds the 50 MB limit", code="size_limit")
    return payload


def canonical_key(base_url, endpoint, fields, filename, entries):
    identity = {
        "version": 1, "url": base_url, "endpoint": endpoint, "fields": fields,
        "filename": filename,
        "files": [[path, len(data), hashlib.sha256(data).hexdigest()] for path, data in sorted(entries.items())],
    }
    payload = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "harbor-cli-v1-" + hashlib.sha256(payload).hexdigest()


def multipart(fields, filename, payload):
    fields = dict(fields, paths=json.dumps([filename], ensure_ascii=False))
    boundary = "harbor-cli-" + hashlib.sha256(payload + json.dumps(fields, sort_keys=True).encode()).hexdigest()
    pieces = []
    for name, value in sorted(fields.items()):
        pieces.append(("--" + boundary + '\r\nContent-Disposition: form-data; name="' + name + '"\r\n\r\n' + value + "\r\n").encode("utf-8"))
    escaped = filename.replace("\\", "\\\\").replace('"', '\\"')
    pieces.extend([
        ("--" + boundary + '\r\nContent-Disposition: form-data; name="files"; filename="' + escaped + '"\r\nContent-Type: application/zip\r\n\r\n').encode("utf-8"),
        payload, ("\r\n--" + boundary + "--\r\n").encode("ascii"),
    ])
    return b"".join(pieces), "multipart/form-data; boundary=" + boundary


def normalize_url(value):
    if not value:
        raise CLIError("Set HARBOR_API_URL or pass --url with the dashboard URL", code="configuration")
    if any(ord(char) < 33 or ord(char) == 127 for char in value):
        raise CLIError("--url contains whitespace or control characters", code="configuration")
    try:
        parsed = parse.urlsplit(value)
        valid = parsed.scheme in {"http", "https"} and parsed.hostname and parsed.port != 0
    except ValueError as exc:
        raise CLIError("Invalid --url", code="configuration") from exc
    if not valid or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise CLIError("--url must be an HTTP(S) server URL without credentials, query or fragment", code="configuration")
    base = value.rstrip("/")
    return base[:-len("/api/v1")] if base.endswith("/api/v1") else base


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, base_url, token, timeout=60):
        self.base_url, self.token, self.timeout = base_url, token, timeout
        self.opener = request.build_opener(NoRedirect())

    def send(self, method, endpoint, *, body=None, content_type=None, idempotency_key=None):
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json", "User-Agent": "harbor-upload-cli/1"}
        if content_type:
            headers["Content-Type"] = content_type
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        # Creation endpoints without idempotency must never be blindly replayed.
        attempts = 3 if method in {"GET", "HEAD"} or idempotency_key else 1
        for attempt in range(attempts):
            req = request.Request(self.base_url + endpoint, data=body, method=method, headers=headers)
            try:
                with self.opener.open(req, timeout=self.timeout) as response:
                    raw = response.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise CLIError("API response exceeds 8 MB", code="invalid_response")
                try:
                    result = json.loads(raw)
                except (ValueError, UnicodeDecodeError) as exc:
                    raise CLIError("API returned a non-JSON response", code="invalid_response") from exc
                if not isinstance(result, dict):
                    raise CLIError("API response must be a JSON object", code="invalid_response")
                return result
            except error.HTTPError as exc:
                status = exc.code
                try:
                    raw = exc.read(65536)
                finally:
                    exc.close()
                if status in RETRY_STATUSES and attempt + 1 < attempts:
                    time.sleep(0.25 * (2 ** attempt))
                    continue
                if 300 <= status < 400:
                    raise CLIError("Redirect refused; use --url with the final dashboard origin", status=status, code="redirect_refused") from None
                try:
                    result = json.loads(raw)
                    detail = result.get("detail", result.get("error", "Request failed")) if isinstance(result, dict) else "Request failed"
                    message = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)
                except (ValueError, UnicodeDecodeError):
                    message = "Server returned HTTP " + str(status)
                raise CLIError(message, status=status, code="api_error") from None
            except (error.URLError, TimeoutError, socket.timeout, OSError, http.client.HTTPException) as exc:
                if attempt + 1 < attempts:
                    time.sleep(0.25 * (2 ** attempt))
                    continue
                message = "Network request failed after " + str(attempts) + (" attempt" if attempts == 1 else " attempts")
                if attempts == 1:
                    message += "; the server may have accepted the request, so check before retrying"
                raise CLIError(message + ": " + str(exc), code="network_error") from None


def parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--url", default=argparse.SUPPRESS, help="Dashboard URL (default: HARBOR_API_URL); a trailing /api/v1 is accepted")
    common.add_argument("--token", default=argparse.SUPPRESS, help="Bearer token (prefer HARBOR_API_TOKEN to keep it out of shell history)")
    common.add_argument("--project", default=argparse.SUPPRESS, help="Project ID; omit to use the token's project")
    common.add_argument("--task-set", default=argparse.SUPPRESS, metavar="ID", help="Task set for task/rollout/status (default: HARBOR_TASK_SET_ID); omit both to use legacy defaults")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Print the API JSON response")
    common.add_argument("--timeout", type=float, default=argparse.SUPPRESS, help="HTTP timeout in seconds (default: 60)")
    root = Parser(description=__doc__, parents=[common], formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = root.add_subparsers(dest="command", required=True)
    task = commands.add_parser("task", parents=[common], help="Upload a Harbor task directory or ZIP")
    task.add_argument("path")
    task.add_argument("--description", default=None)
    task.add_argument("--rollout", action="append", default=[], metavar="PATH", help="Include existing trial/job files; may be repeated")
    rollout = commands.add_parser("rollout", parents=[common], help="Append trial/job files to an existing task")
    rollout.add_argument("task_id")
    rollout.add_argument("path")
    status = commands.add_parser("status", parents=[common], help="Read review status, task URL and rollout information")
    status.add_argument("task_id")
    commands.add_parser("task-sets", parents=[common], help="List task sets in the token's project")
    create_task_set = commands.add_parser("create-task-set", parents=[common], help="Create a task set; this request is never automatically retried")
    create_task_set.add_argument("name", metavar="NAME")
    for command in (task, rollout):
        group = command.add_mutually_exclusive_group()
        group.add_argument("--idempotency-key", help="Override the deterministic content key (1-128 visible ASCII characters)")
        group.add_argument("--new-upload", action="store_true", help="Use a fresh key to deliberately create another upload")
    return root


def scrub(value, secrets):
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value
    if isinstance(value, dict):
        return {scrub(key, secrets): scrub(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub(item, secrets) for item in value]
    return value


def display(result):
    task_set = result.get("task_set")
    if task_set:
        print("Task set: " + str(task_set.get("name", "")))
        print("Task set ID: " + str(task_set.get("id", "")))
        print("Project ID: " + str(task_set.get("project_id", "")))
    if "task_sets" in result:
        print("Task sets: " + str(len(result["task_sets"])))
        for item in result["task_sets"]:
            print(str(item["id"]) + "\t" + str(item.get("name", ""))
                  + "\tTasks: " + str(item.get("tasks_count", 0))
                  + "\tRollouts: " + str(item.get("rollouts_count", 0)))
    task = result.get("task", {})
    if task:
        print("Task: " + str(task.get("title", task.get("id", ""))))
        print("Task ID: " + str(task.get("id", "")))
        if task.get("task_set_id"):
            print("Task set: " + str(task.get("task_set_name", task["task_set_id"])))
            print("Task set ID: " + str(task["task_set_id"]))
        print("Review status: " + str(task.get("status", "unknown")))
    if "rollouts" in result:
        print("Rollouts: " + str(len(result["rollouts"])))
    for key, label in (("task_url", "Task URL"), ("api_url", "API URL"), ("replayed", "Replayed")):
        if key in result:
            print(label + ": " + str(result[key]))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    secrets = [os.environ.get("HARBOR_API_TOKEN", "")]
    for index, value in enumerate(argv):
        if value == "--token" and index + 1 < len(argv):
            secrets.append(argv[index + 1])
        elif value.startswith("--token="):
            secrets.append(value.partition("=")[2])
    try:
        args = parser().parse_args(argv)
        token = getattr(args, "token", os.environ.get("HARBOR_API_TOKEN", ""))
        if not token or any(ord(char) < 33 or ord(char) > 126 for char in token):
            raise CLIError("Set a valid HARBOR_API_TOKEN or pass --token", code="configuration")
        base_url = normalize_url(getattr(args, "url", os.environ.get("HARBOR_API_URL", "")))
        timeout = getattr(args, "timeout", 60)
        if not 0 < timeout <= 3600:
            raise CLIError("--timeout must be between 0 and 3600 seconds", code="arguments")
        client = Client(base_url, token, timeout)
        project = getattr(args, "project", None)
        fields = {"project_id": project} if project else {}
        task_set = getattr(args, "task_set", os.environ.get("HARBOR_TASK_SET_ID", "")).strip()
        if task_set and args.command in {"task", "rollout", "status"}:
            fields["task_set_id"] = task_set
        if args.command == "task-sets":
            endpoint = "/api/v1/task-sets"
            if fields:
                endpoint += "?" + parse.urlencode(fields)
            result = client.send("GET", endpoint)
        elif args.command == "create-task-set":
            name = args.name.strip()
            if not 1 <= len(name) <= 80:
                raise CLIError("Task set NAME must contain 1-80 characters after trimming", code="arguments")
            payload = json.dumps(dict(fields, name=name), ensure_ascii=False).encode("utf-8")
            result = client.send("POST", "/api/v1/task-sets", body=payload, content_type="application/json")
        elif args.command == "status":
            endpoint = "/api/v1/tasks/" + parse.quote(args.task_id, safe="")
            if fields:
                endpoint += "?" + parse.urlencode(fields)
            result = client.send("GET", endpoint)
        else:
            if args.command == "task":
                endpoint = "/api/v1/tasks"
                filename, entries = prepare_task(args.path, args.rollout)
                if args.description is not None:
                    fields["description"] = args.description
            else:
                endpoint = "/api/v1/tasks/" + parse.quote(args.task_id, safe="") + "/rollouts"
                filename, entries = source_name(args.path) + ".zip", strip_wrapper(read_source(args.path))
            key = args.idempotency_key
            if key is not None and (not 1 <= len(key) <= 128 or any(ord(char) < 33 or ord(char) > 126 for char in key)):
                raise CLIError("--idempotency-key must contain 1-128 visible ASCII characters", code="arguments")
            key = key or ("harbor-cli-" + uuid.uuid4().hex if args.new_upload else canonical_key(base_url, endpoint, fields, filename, entries))
            body, content_type = multipart(fields, filename, make_archive(entries))
            result = client.send("POST", endpoint, body=body, content_type=content_type, idempotency_key=key)
        result = scrub(result, secrets)
        if getattr(args, "json", False):
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        else:
            display(result)
        return 0
    except (CLIError, OSError, ValueError) as exc:
        result = {"ok": False, "error": str(exc), "code": getattr(exc, "code", "local_error")}
        if getattr(exc, "status", None) is not None:
            result["status"] = exc.status
        print(json.dumps(scrub(result, secrets), ensure_ascii=False), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(json.dumps({"ok": False, "error": "Interrupted", "code": "interrupted"}), file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
