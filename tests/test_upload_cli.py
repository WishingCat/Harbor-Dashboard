"""The downloadable CLI uses stdlib only; HTTP tests never contact a real API."""
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from email import policy
from email.parser import BytesParser
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import stat
import tempfile
from threading import Thread
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from unittest import mock
import zipfile


SPEC = importlib.util.spec_from_file_location("harbor_upload_cli", Path(__file__).parents[1] / "scripts" / "harbor_upload.py")
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)
TOKEN = "synthetic-qa-bearer-not-a-real-secret"
TASK_FILES = {
    "instruction.md": "# 虚构任务\n\nNormalize whitespace.\n".encode(),
    "task.toml": b'schema_version="1.4"\n[task]\nname="fixture/manifest-name"\n',
    "environment/Dockerfile": b"FROM python:3.12-slim\n",
    "tests/test.sh": b"#!/bin/sh\nexit 0\n",
}
SUCCESS = {
    "task": {"id": "task-qa", "title": "upload-name", "status": "pending", "project_id": "project-aa"},
    "task_url": "http://example.test/?project=project-aa&task=task-qa",
    "api_url": "http://example.test/api/v1/tasks/task-qa?project_id=project-aa",
    "replayed": False,
}


def make_zip(path, entries, *, date=(2020, 1, 1, 0, 0, 0), compression=zipfile.ZIP_STORED):
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in entries.items():
            info = zipfile.ZipInfo(name, date_time=date)
            info.compress_type = compression
            archive.writestr(info, content)
    return path


@contextmanager
def server(replies):
    """A queue of local HTTP replies or a deliberate connection drop."""
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def handle_request(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            received.append({"method": self.command, "path": self.path, "headers": dict(self.headers), "body": body})
            reply = replies.pop(0)
            if reply is None:
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            status, headers, payload = reply
            if not isinstance(payload, bytes):
                payload = json.dumps(payload).encode()
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_POST = handle_request
        do_GET = handle_request

    instance = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=instance.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(instance.server_port), received
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=2)


def unpack_request(captured):
    message = BytesParser(policy=policy.default).parsebytes(
        ("Content-Type: " + captured["headers"]["Content-Type"] + "\r\nMIME-Version: 1.0\r\n\r\n").encode()
        + captured["body"]
    )
    fields, archives = {}, []
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if name == "files":
            with zipfile.ZipFile(io.BytesIO(part.get_payload(decode=True))) as archive:
                archives.append((part.get_filename(), {path: archive.read(path) for path in archive.namelist()}))
        else:
            fields[name] = part.get_payload(decode=True).decode("utf-8")
    return fields, archives


class UploadCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="harbor-cli-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.task = self.root / "上传名称"
        self.task.mkdir()
        for name, data in TASK_FILES.items():
            destination = self.task / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)

    def run_cli(self, argv, *, url=None, token=TOKEN, task_set=""):
        environment = {"HARBOR_API_URL": url or "http://127.0.0.1:1", "HARBOR_API_TOKEN": token, "HARBOR_TASK_SET_ID": task_set}
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, environment), mock.patch.object(cli.time, "sleep"), redirect_stdout(output), redirect_stderr(errors):
            code = cli.main(argv)
        return code, output.getvalue(), errors.getvalue()

    def test_directory_upload_preserves_content_name_and_fields(self):
        (self.task / ".git").mkdir()
        (self.task / ".git" / "config").write_text("ignored fixture")
        (self.task / "__pycache__").mkdir()
        (self.task / "__pycache__" / "noise.pyc").write_bytes(b"noise")
        with server([(200, {}, SUCCESS)]) as (url, captured):
            code, output, errors = self.run_cli(["--project", "paperbenchx", "task", str(self.task), "--description", "虚构上传说明", "--json"], url=url)
        self.assertEqual((code, errors), (0, ""))
        self.assertEqual(json.loads(output), SUCCESS)
        self.assertEqual(captured[0]["headers"]["Authorization"], "Bearer " + TOKEN)
        fields, archives = unpack_request(captured[0])
        self.assertEqual(fields["project_id"], "paperbenchx")
        self.assertEqual(fields["description"], "虚构上传说明")
        self.assertEqual(json.loads(fields["paths"]), ["上传名称.zip"])
        self.assertEqual(archives, [("上传名称.zip", TASK_FILES)])
        self.assertEqual(captured[0]["path"], "/api/v1/tasks")

    def test_task_set_flag_and_environment_scope_upload_and_deterministic_keys(self):
        with server([(200, {}, SUCCESS)] * 4) as (url, captured):
            code, _, errors = self.run_cli(["task", str(self.task)], url=url, task_set="set-one")
            self.assertEqual((code, errors), (0, ""))
            self.assertEqual(self.run_cli(["--task-set", "set-one", "task", str(self.task)], url=url)[0], 0)
            self.assertEqual(self.run_cli(["task", str(self.task), "--task-set", "set-two"], url=url, task_set="set-one")[0], 0)
            self.assertEqual(self.run_cli(["task", str(self.task)], url=url)[0], 0)
        fields = [unpack_request(item)[0] for item in captured]
        self.assertEqual([item.get("task_set_id") for item in fields], ["set-one", "set-one", "set-two", None])
        keys = [item["headers"]["Idempotency-Key"] for item in captured]
        self.assertEqual(keys[0], keys[1])
        self.assertEqual(len(set(keys)), 3)

    def test_task_set_scope_applies_to_status_and_rollout(self):
        trial = make_zip(self.root / "run.zip", {"result.json": b'{"trial_name":"qa"}'})
        task = dict(SUCCESS["task"], task_set_id="set-qa", task_set_name="虚构任务集")
        result = dict(SUCCESS, task=task)
        with server([(200, {}, result), (200, {}, result)]) as (url, captured):
            code, output, errors = self.run_cli(["status", "task-qa", "--project", "project-aa"], url=url, task_set="set-qa")
            self.assertEqual((code, errors), (0, ""))
            self.assertIn("Task set: 虚构任务集", output)
            self.assertIn("Task set ID: set-qa", output)
            self.assertEqual(self.run_cli(["rollout", "task-qa", str(trial), "--task-set", "set-qa"], url=url)[0], 0)
        self.assertEqual(captured[0]["path"], "/api/v1/tasks/task-qa?project_id=project-aa&task_set_id=set-qa")
        self.assertEqual(unpack_request(captured[1])[0]["task_set_id"], "set-qa")

    def test_task_set_list_and_create_return_ids_and_send_only_project_scope(self):
        task_set = {"id": "set-qa", "name": "虚构集合", "project_id": "project-aa", "tasks_count": 2, "rollouts_count": 3}
        with server([(200, {}, {"task_sets": [task_set]}), (200, {}, {"task_sets": []}),
                     (200, {}, {"task_set": task_set}), (200, {}, {"task_set": task_set})]) as (url, captured):
            code, output, errors = self.run_cli(["task-sets"], url=url, task_set="unrelated-env-set")
            self.assertEqual((code, errors), (0, ""))
            self.assertIn("set-qa\t虚构集合\tTasks: 2\tRollouts: 3", output)
            self.assertEqual(self.run_cli(["task-sets", "--project", "project-aa"], url=url)[0], 0)
            code, output, errors = self.run_cli(["create-task-set", "  虚构集合  ", "--project", "project-aa", "--json"], url=url, task_set="unrelated-env-set")
            self.assertEqual((code, errors), (0, ""))
            self.assertEqual(json.loads(output), {"task_set": task_set})
            code, output, errors = self.run_cli(["create-task-set", "虚构集合"], url=url)
            self.assertEqual((code, errors), (0, ""))
            self.assertIn("Task set ID: set-qa", output)
        self.assertEqual(captured[0]["path"], "/api/v1/task-sets")
        self.assertEqual(captured[1]["path"], "/api/v1/task-sets?project_id=project-aa")
        self.assertEqual(captured[2]["headers"]["Content-Type"], "application/json")
        self.assertEqual(json.loads(captured[2]["body"]), {"name": "虚构集合", "project_id": "project-aa"})
        self.assertEqual(json.loads(captured[3]["body"]), {"name": "虚构集合"})
        self.assertNotIn("Idempotency-Key", captured[2]["headers"])

    def test_task_set_creation_never_retries_network_or_gateway_failure(self):
        for response in (None, (502, {}, {"detail": "unavailable"}), (503, {}, {"detail": "unavailable"}), (504, {}, {"detail": "unavailable"})):
            with self.subTest(response=response):
                with server([response]) as (url, captured):
                    code, _, errors = self.run_cli(["create-task-set", "隔离 QA 集合"], url=url)
                self.assertNotEqual(code, 0)
                self.assertFalse(json.loads(errors)["ok"])
                self.assertEqual(len(captured), 1)
                if response is None:
                    self.assertIn("check before retrying", json.loads(errors)["error"])

    def test_task_set_list_can_retry_and_invalid_names_fail_locally(self):
        with server([(503, {}, {"detail": "retry"}), (200, {}, {"task_sets": []})]) as (url, captured):
            code, output, errors = self.run_cli(["task-sets", "--json"], url=url)
        self.assertEqual((code, errors), (0, ""))
        self.assertEqual(json.loads(output), {"task_sets": []})
        self.assertEqual(len(captured), 2)
        with mock.patch.object(cli.Client, "send") as send:
            for name in ("  ", "x" * 81):
                code, _, errors = self.run_cli(["create-task-set", name])
                self.assertNotEqual(code, 0)
                self.assertEqual(json.loads(errors)["code"], "arguments")
            send.assert_not_called()

    def test_nested_git_and_pycache_remain_task_material_in_directories_and_zips(self):
        nested = {"environment/repository/.git/config": b"fixture git configuration",
                  "environment/repository/__pycache__/material.pyc": b"fixture binary"}
        for path, body in nested.items():
            target = self.task / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
        expected = dict(TASK_FILES, **nested)
        self.assertEqual(cli.prepare_task(self.task)[1], expected)
        path = make_zip(self.root / "nested.zip", expected | {".git/config": b"noise", "__pycache__/noise.pyc": b"noise"})
        self.assertEqual(cli.prepare_task(path)[1], expected)

    def test_task_zip_combines_rollouts_without_overwriting_existing_files(self):
        task_zip = make_zip(self.root / "outer-name.zip", {"wrapper/" + path: data for path, data in TASK_FILES.items()} | {"wrapper/rollouts/trial/result.json": b'{"existing":true}'})
        trial_a = self.root / "one" / "trial"
        trial_b = self.root / "two" / "trial"
        for trial, reward in ((trial_a, 1), (trial_b, 0)):
            (trial / "artifacts").mkdir(parents=True)
            (trial / "result.json").write_text(json.dumps({"trial_name": "qa", "verifier_result": {"rewards": {"reward": reward}}}))
            (trial / "artifacts" / "report.md").write_text("# QA report")
        rollout_zip = make_zip(self.root / "trial.zip", {"enclosing/result.json": b'{"trial_name":"zip-qa"}'})
        with server([(200, {}, SUCCESS)]) as (url, captured):
            code, _, errors = self.run_cli(["task", str(task_zip), "--rollout", str(trial_a), "--rollout", str(trial_b), "--rollout", str(rollout_zip)], url=url)
        self.assertEqual((code, errors), (0, ""))
        _, archives = unpack_request(captured[0])
        filename, entries = archives[0]
        self.assertEqual(filename, "outer-name.zip")
        self.assertEqual(entries["rollouts/trial/result.json"], b'{"existing":true}')
        self.assertEqual(json.loads(entries["rollouts/trial-2/result.json"])["verifier_result"]["rewards"]["reward"], 1)
        self.assertEqual(json.loads(entries["rollouts/trial-3/result.json"])["verifier_result"]["rewards"]["reward"], 0)
        self.assertIn("rollouts/trial-4/result.json", entries)
        self.assertIn("rollouts/trial-2/artifacts/report.md", entries)
        self.assertNotIn("wrapper/task.toml", entries)

    def test_stable_key_ignores_zip_timestamp_order_and_compression(self):
        path = self.root / "stable.zip"
        replies = [(200, {}, SUCCESS)] * 5
        with server(replies) as (url, captured):
            make_zip(path, TASK_FILES)
            self.assertEqual(self.run_cli(["task", str(path)], url=url)[0], 0)
            make_zip(path, dict(reversed(list(TASK_FILES.items()))), date=(2025, 3, 4, 5, 6, 8), compression=zipfile.ZIP_DEFLATED)
            self.assertEqual(self.run_cli(["task", str(path)], url=url)[0], 0)
            self.assertEqual(self.run_cli(["task", str(path), "--description", "changed"], url=url)[0], 0)
            changed = dict(TASK_FILES, **{"instruction.md": b"Different real content"})
            make_zip(path, changed)
            self.assertEqual(self.run_cli(["task", str(path)], url=url)[0], 0)
            self.assertEqual(self.run_cli(["task", str(path), "--project", "paperbenchx"], url=url)[0], 0)
        keys = [item["headers"]["Idempotency-Key"] for item in captured]
        self.assertEqual(keys[0], keys[1])
        self.assertEqual(captured[0]["body"], captured[1]["body"])
        self.assertEqual(len(set(keys)), 4)

    def test_explicit_key_and_new_upload_have_intended_behavior(self):
        with server([(200, {}, SUCCESS)] * 3) as (url, captured):
            self.assertEqual(self.run_cli(["task", str(self.task), "--idempotency-key", "explicit-key"], url=url)[0], 0)
            for _ in range(2):
                self.assertEqual(self.run_cli(["task", str(self.task), "--new-upload"], url=url)[0], 0)
        keys = [item["headers"]["Idempotency-Key"] for item in captured]
        self.assertEqual(keys[0], "explicit-key")
        self.assertNotEqual(keys[1], keys[2])
        code, _, errors = self.run_cli(["task", str(self.task), "--idempotency-key", "a", "--new-upload"])
        self.assertNotEqual(code, 0)
        self.assertEqual(json.loads(errors)["code"], "arguments")

    def test_transient_http_retries_preserve_body_and_key(self):
        with server([(502, {}, {"detail": "retry"}), (503, {}, {"detail": "retry"}), (200, {}, SUCCESS)]) as (url, captured):
            code, _, errors = self.run_cli(["task", str(self.task)], url=url)
        self.assertEqual((code, errors), (0, ""))
        self.assertEqual(len(captured), 3)
        self.assertEqual(len({item["headers"]["Idempotency-Key"] for item in captured}), 1)
        self.assertEqual(len({item["body"] for item in captured}), 1)

    def test_network_drop_retries_and_retry_limit_is_three_attempts(self):
        with server([None, (200, {}, SUCCESS)]) as (url, captured):
            self.assertEqual(self.run_cli(["task", str(self.task)], url=url)[0], 0)
        self.assertEqual(len(captured), 2)
        with server([(504, {}, {"detail": "unavailable"})] * 3) as (url, captured):
            code, _, errors = self.run_cli(["task", str(self.task)], url=url)
        self.assertNotEqual(code, 0)
        self.assertEqual(json.loads(errors)["status"], 504)
        self.assertEqual(len(captured), 3)

    def test_redirect_is_not_followed_or_sent_to_another_origin(self):
        with server([]) as (other_url, other_requests), server([(307, {"Location": other_url + "/steal"}, {})]) as (url, captured):
            code, output, errors = self.run_cli(["task", str(self.task)], url=url)
        self.assertNotEqual(code, 0)
        self.assertEqual(output, "")
        self.assertEqual(json.loads(errors)["code"], "redirect_refused")
        self.assertEqual(len(captured), 1)
        self.assertEqual(other_requests, [])
        self.assertNotIn(TOKEN, errors)

    def test_api_error_is_not_retried_and_echoed_token_is_redacted(self):
        with server([(409, {}, {"detail": "Do not expose " + TOKEN})]) as (url, captured):
            code, output, errors = self.run_cli(["task", str(self.task), "--json"], url=url)
        self.assertNotEqual(code, 0)
        self.assertEqual(len(captured), 1)
        self.assertEqual(json.loads(errors)["status"], 409)
        self.assertNotIn(TOKEN, output + errors)
        self.assertIn("[REDACTED]", errors)

    def test_rollout_and_status_routes_and_token_project_default(self):
        trial = make_zip(self.root / "run.zip", {"result.json": b'{"trial_name":"qa"}', "artifacts/report.md": b"# Report"})
        result = dict(SUCCESS, rollouts=[{"id": "rollout-qa", "status": "passed"}])
        with server([(200, {}, result), (200, {}, result), (200, {}, result)]) as (url, captured):
            code, _, errors = self.run_cli(["rollout", "task/with space", str(trial), "--url", url + "/api/v1"], url=url)
            self.assertEqual((code, errors), (0, ""))
            code, output, _ = self.run_cli(["status", "task-qa", "--project", "paperbenchx"], url=url)
            self.assertEqual(code, 0)
            self.assertIn("Review status: pending", output)
            self.assertIn("Task URL: " + SUCCESS["task_url"], output)
            code, output, _ = self.run_cli(["--json", "status", "task-qa"], url=url)
            self.assertEqual(json.loads(output), result)
        self.assertEqual(captured[0]["path"], "/api/v1/tasks/task%2Fwith%20space/rollouts")
        fields, _ = unpack_request(captured[0])
        self.assertNotIn("project_id", fields)
        self.assertEqual(captured[1]["path"], "/api/v1/tasks/task-qa?project_id=paperbenchx")
        self.assertNotIn("Idempotency-Key", captured[1]["headers"])

    def test_missing_token_bad_url_and_bad_key_fail_before_network(self):
        with mock.patch.object(cli.Client, "send") as send:
            cases = [
                (["status", "task-qa"], ""),
                (["status", "task-qa", "--url", "https://user:password@example.test"], TOKEN),
                (["status", "task-qa", "--url", "https://example.test/?key=secret"], TOKEN),
                (["task", str(self.task), "--idempotency-key", "x" * 129], TOKEN),
                (["status", "task-qa", "--token", "bad\nheader"], TOKEN),
            ]
            for args, token in cases:
                with self.subTest(args=args):
                    code, _, errors = self.run_cli(args, token=token)
                    self.assertNotEqual(code, 0)
                    self.assertFalse(json.loads(errors)["ok"])
            send.assert_not_called()

    def test_rejects_traversal_absolute_paths_duplicate_paths_and_zip_links(self):
        for unsafe in ("../escape", "/absolute", "C:/drive", "a/../../escape", "a\\..\\escape", "control\nname"):
            with self.subTest(path=unsafe):
                path = make_zip(self.root / "unsafe.zip", dict(TASK_FILES, **{unsafe: b"bad"}))
                with self.assertRaises(cli.CLIError):
                    cli.read_source(path)
        path = make_zip(self.root / "duplicate.zip", {"a//b": b"one", "a/b": b"two"})
        with self.assertRaisesRegex(cli.CLIError, "Duplicate"):
            cli.read_source(path)
        path = self.root / "link.zip"
        with zipfile.ZipFile(path, "w") as archive:
            member = zipfile.ZipInfo("link")
            member.create_system = 3
            member.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(member, "../outside")
        with self.assertRaisesRegex(cli.CLIError, "link"):
            cli.read_source(path)

    def test_directory_symlinks_and_special_files_are_rejected(self):
        link = self.task / "linked.md"
        link.symlink_to(self.task / "instruction.md")
        with self.assertRaisesRegex(cli.CLIError, "Symbolic"):
            cli.read_source(self.task)
        link.unlink()
        link.symlink_to(self.task / "tests", target_is_directory=True)
        with self.assertRaisesRegex(cli.CLIError, "Symbolic"):
            cli.read_source(self.task)
        if hasattr(os, "mkfifo"):
            fifo = self.root / "fifo.zip"
            os.mkfifo(fifo)
            with self.assertRaisesRegex(cli.CLIError, "regular"):
                cli.read_source(fifo)

    def test_size_limits_apply_to_expanded_files_archive_and_combined_sources(self):
        with mock.patch.object(cli, "MAX_FILE", 3):
            with self.assertRaises(cli.CLIError):
                cli.read_source(self.task)
        with mock.patch.object(cli, "MAX_FILES", 2):
            with self.assertRaises(cli.CLIError):
                cli.read_source(self.task)
        with mock.patch.object(cli, "MAX_EXPANDED", 10):
            with self.assertRaises(cli.CLIError):
                cli.read_source(self.task)
        with mock.patch.object(cli, "MAX_UPLOAD", 10):
            with self.assertRaises(cli.CLIError):
                cli.make_archive(TASK_FILES)
        trial = self.root / "run"
        trial.mkdir()
        (trial / "result.json").write_bytes(b"{}")
        with mock.patch.object(cli, "MAX_FILES", len(TASK_FILES)):
            with self.assertRaises(cli.CLIError):
                cli.prepare_task(self.task, [trial])

    def test_high_compression_input_rejected_but_directory_file_can_be_packaged_safely(self):
        data = b"a" * (2 * 1024 * 1024)
        path = make_zip(self.root / "ratio.zip", {"repeat.txt": data}, compression=zipfile.ZIP_DEFLATED)
        with self.assertRaisesRegex(cli.CLIError, "ratio"):
            cli.read_source(path)
        payload = cli.make_archive({"repeat.txt": data})
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            self.assertEqual(archive.getinfo("repeat.txt").compress_type, zipfile.ZIP_STORED)
            self.assertEqual(archive.read("repeat.txt"), data)

    def test_invalid_task_structure_fails_without_upload(self):
        for entries in ({"instruction.md": b"no manifest"}, {"task.toml": b"no instruction"},
                        {"one/task.toml": b"", "two/task.toml": b"", "one/instruction.md": b""}):
            path = make_zip(self.root / "invalid.zip", entries)
            with self.assertRaises(cli.CLIError):
                cli.prepare_task(path)

    def test_success_payload_and_argument_errors_do_not_disclose_token(self):
        with server([(200, {}, dict(SUCCESS, echo=TOKEN))]) as (url, _):
            code, output, errors = self.run_cli(["status", "task-qa", "--json"], url=url)
        self.assertEqual(code, 0)
        self.assertNotIn(TOKEN, output + errors)
        self.assertEqual(json.loads(output)["echo"], "[REDACTED]")
        code, output, errors = self.run_cli(["--token", TOKEN, "unknown-command", TOKEN])
        self.assertNotEqual(code, 0)
        self.assertNotIn(TOKEN, output + errors)


if __name__ == "__main__":
    unittest.main()
