"""Bounded, non-executing import of Harbor task and trial artifacts."""
from __future__ import annotations

import io
import json
import math
import re
import stat
import tomllib
import zipfile
import zlib
from datetime import datetime
from pathlib import PurePosixPath

from fastapi import HTTPException, UploadFile

MAX_UPLOAD = 50 * 1024 * 1024
MAX_EXPANDED = 100 * 1024 * 1024
MAX_FILE = 20 * 1024 * 1024
MAX_FILES = 2000
TEXT_LIMIT = 300 * 1024
TASK_DIRECTORIES = {"environment", "tests", "solution", "steps"}


def safe_path(value: str) -> str:
    value = value.replace("\\", "/")
    if not value or value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise HTTPException(400, "上传文件包含不安全的绝对路径")
    parts = value.split("/")
    if ".." in parts or any(ord(char) < 32 for char in value) or ":" in value:
        raise HTTPException(400, "上传文件包含不安全的路径")
    normalized = str(PurePosixPath(value))
    if normalized == "." or len(normalized) > 1000:
        raise HTTPException(400, "上传文件路径为空或过长")
    return normalized


def upload_paths(files: list[UploadFile], paths_json: str) -> list[str]:
    try:
        paths = json.loads(paths_json) if paths_json else []
    except (TypeError, ValueError):
        raise HTTPException(400, "paths 必须是 JSON 数组")
    if not isinstance(paths, list) or (paths and len(paths) != len(files)) or any(not isinstance(x, str) for x in paths):
        raise HTTPException(400, "paths 必须与上传文件一一对应")
    return [safe_path(paths[index] if paths else upload.filename or "unnamed") for index, upload in enumerate(files)]


def upload_title(files: list[UploadFile], paths_json: str) -> str:
    """Prefer the selected archive name, then the selected folder's root name."""
    paths = upload_paths(files, paths_json)
    for path in paths:
        if path.lower().endswith(".zip"):
            name = PurePosixPath(path).name[:-4].strip()
            if name:
                return name
    roots = {path.split("/", 1)[0] for path in paths}
    if len(roots) == 1 and all("/" in path for path in paths):
        return next(iter(roots)).strip()
    return ""


async def read_uploads(files: list[UploadFile], paths_json: str) -> dict[str, bytes]:
    if not files:
        raise HTTPException(400, "请上传任务文件、文件夹或 ZIP 压缩包")
    paths = upload_paths(files, paths_json)
    if len(files) > MAX_FILES:
        raise HTTPException(413, "单次最多上传 2000 个文件")
    expanded: dict[str, bytes] = {}
    compressed_total = 0
    expanded_total = 0

    def add(path, data):
        nonlocal expanded_total
        path = safe_path(path)
        if path in expanded:
            raise HTTPException(400, f"重复的文件路径：{path}")
        if len(data) > MAX_FILE:
            raise HTTPException(413, "单个文件不能超过 20 MB")
        expanded_total += len(data)
        if expanded_total > MAX_EXPANDED or len(expanded) >= MAX_FILES:
            raise HTTPException(413, "展开后的文件不能超过 100 MB 或 2000 个文件")
        expanded[path] = data

    try:
        for index, upload in enumerate(files):
            path = paths[index]
            chunks = []
            while chunk := await upload.read(1024 * 1024):
                compressed_total += len(chunk)
                if compressed_total > MAX_UPLOAD:
                    raise HTTPException(413, "单次上传总大小不能超过 50 MB")
                chunks.append(chunk)
            data = b"".join(chunks)
            if path.lower().endswith(".zip"):
                try:
                    with zipfile.ZipFile(io.BytesIO(data)) as archive:
                        entries = archive.infolist()
                        if len(entries) > MAX_FILES * 2:
                            raise HTTPException(413, "ZIP 包含过多条目")
                        for info in entries:
                            archive_path = safe_path(info.filename)
                            mode = info.external_attr >> 16
                            file_type = stat.S_IFMT(mode)
                            if file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
                                raise HTTPException(400, "ZIP 不支持符号链接或特殊文件")
                            if info.is_dir():
                                continue
                            if info.flag_bits & 1:
                                raise HTTPException(400, "不支持加密 ZIP")
                            if info.file_size > MAX_FILE or expanded_total + info.file_size > MAX_EXPANDED:
                                raise HTTPException(413, "ZIP 展开后超过文件大小限制")
                            if info.file_size > 1024 * 1024 and info.file_size / max(info.compress_size, 1) > 200:
                                raise HTTPException(413, "ZIP 压缩比过高，请拆分后上传")
                            with archive.open(info) as member:
                                body = member.read(MAX_FILE + 1)
                            add(archive_path, body)
                except (zipfile.BadZipFile, zlib.error, RuntimeError, NotImplementedError, EOFError) as exc:
                    raise HTTPException(400, "无法读取 ZIP，请检查压缩包是否完整") from exc
            else:
                add(path, data)
    finally:
        for upload in files:
            await upload.close()
    if not expanded:
        raise HTTPException(400, "上传内容中没有可读取的文件")
    # Ignore the directory selected by the user, keeping Harbor's internal paths.
    roots = {p.split("/")[0] for p in expanded}
    if len(roots) == 1 and all("/" in p for p in expanded):
        root = next(iter(roots))
        if root not in {"environment", "tests", "solution", "steps", "agent", "verifier", "artifacts"}:
            expanded = {p.split("/", 1)[1]: value for p, value in expanded.items()}
    return expanded


def json_object(body: bytes):
    if len(body) > TEXT_LIMIT * 5:
        return None
    try:
        value = json.loads(body)
        return value if isinstance(value, dict) else None
    except (ValueError, UnicodeDecodeError):
        return None


def is_task_material(path: str, task_prefix: str) -> bool:
    if not path.startswith(task_prefix):
        return False
    relative = path[len(task_prefix):]
    return relative in {"task.toml", "instruction.md"} or relative.split("/", 1)[0] in TASK_DIRECTORIES


def trial_groups(files: dict[str, bytes], *, task_prefix: str | None = None):
    candidates = []
    for path, data in files.items():
        if PurePosixPath(path).name != "result.json":
            continue
        if task_prefix is not None and is_task_material(path, task_prefix):
            # Results inside task code, verifier fixtures or solutions remain task
            # files even when their JSON resembles a Harbor trial result.
            continue
        result = json_object(data)
        if not result or "stats" in result or "n_total_trials" in result or "trial_results" in result:
            continue
        trial_name = result.get("trial_name")
        agent_info = result.get("agent_info")
        has_identity = isinstance(trial_name, str) and bool(trial_name.strip())
        has_agent = isinstance(agent_info, dict) and isinstance(agent_info.get("name"), str) and bool(agent_info["name"].strip())
        has_execution = isinstance(result.get("verifier_result"), dict) or isinstance(result.get("exception_info"), dict) or isinstance(result.get("started_at"), str)
        if (has_identity and (has_agent or has_execution)) or (has_agent and has_execution):
            prefix = path.rsplit("/", 1)[0] + "/" if "/" in path else ""
            candidates.append((prefix, result))
    # Nested step results belong to the surrounding trial, not separate rollouts.
    candidates.sort(key=lambda pair: len(pair[0]))
    groups = []
    for prefix, result in candidates:
        if any(prefix.startswith(existing[0]) for existing in groups):
            continue
        groups.append((prefix, result))
    return groups


def finite_reward(value):
    """Only finite real numbers can be summarized in SQLite's REAL column."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def parse_rollout(files: dict[str, bytes], supplied: dict | None = None, result: dict | None = None):
    supplied = supplied or {}
    result = result or json_object(files.get("result.json", b"")) or {}
    config = json_object(files.get("config.json", b"")) or {}
    info = result.get("agent_info") or {}
    info = info if isinstance(info, dict) else {}
    model_info = info.get("model_info") or {}
    model_info = model_info if isinstance(model_info, dict) else {}
    config_agent = config.get("agent") or {}
    config_agent = config_agent if isinstance(config_agent, dict) else {}
    verifier = result.get("verifier_result") or {}
    rewards = verifier.get("rewards") if isinstance(verifier, dict) else None
    if not isinstance(rewards, dict):
        rewards = json_object(files.get("verifier/reward.json", b""))
    reward = None
    binary_reward = None
    if isinstance(rewards, dict):
        values = [number for value in rewards.values() if (number := finite_reward(value)) is not None]
        reward = finite_reward(rewards.get("reward"))
        binary_reward = reward
        if reward is None:
            reward = values[0] if len(values) == 1 else None
    if reward is None and "verifier/reward.txt" in files:
        try:
            candidate = float(files["verifier/reward.txt"].decode().strip())
            if math.isfinite(candidate):
                reward = candidate
                binary_reward = candidate
        except (ValueError, UnicodeDecodeError):
            pass
    # Only a binary reward has an unambiguous success/failure interpretation.
    status = "failed" if result.get("exception_info") else "passed" if binary_reward == 1 else "failed" if binary_reward == 0 else "unknown"
    duration = None
    try:
        start = datetime.fromisoformat(result["started_at"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(result["finished_at"].replace("Z", "+00:00"))
        elapsed = (end - start).total_seconds()
        if elapsed >= 0:
            duration = elapsed
    except (KeyError, ValueError, TypeError, AttributeError):
        pass
    return {
        "name": str(supplied.get("name") or result.get("trial_name") or "Imported rollout")[:200],
        "agent": str(supplied.get("agent") or info.get("name") or config_agent.get("name") or "unknown")[:200],
        "model": str(supplied.get("model") or model_info.get("name") or config_agent.get("model_name") or "unknown")[:200],
        "status": status, "reward": reward, "duration_seconds": duration,
    }


def task_metadata(files):
    candidates = [p for p in files if PurePosixPath(p).name == "task.toml"]
    if not candidates:
        return {}
    try:
        data = tomllib.loads(files[min(candidates, key=len)].decode("utf-8-sig"))
        task = data.get("task", {})
        metadata = data.get("metadata", {})
        task = task if isinstance(task, dict) else {}
        metadata = metadata if isinstance(metadata, dict) else {}
        title = task.get("name", metadata.get("name", ""))
        category = metadata.get("category", "software-engineering")
        difficulty = metadata.get("difficulty", "medium")
        tags = task.get("keywords", metadata.get("tags", []))
        clean_tags = []
        if isinstance(tags, list):
            for tag in tags:
                if isinstance(tag, str) and tag.strip():
                    value = tag.strip()[:50]
                    if value not in clean_tags:
                        clean_tags.append(value)
        return {"title": title.strip() if isinstance(title, str) else "",
                "category": category.strip()[:100] if isinstance(category, str) and category.strip() else "software-engineering",
                "difficulty": difficulty.strip().lower() if isinstance(difficulty, str) and difficulty.strip().lower() in {"easy", "medium", "hard"} else "medium",
                "tags": clean_tags[:20]}
    except (ValueError, UnicodeDecodeError, AttributeError):
        return {}


def validate_task_bundle(files):
    manifests = [p for p in files if PurePosixPath(p).name == "task.toml"]
    if len(manifests) > 1:
        raise HTTPException(400, "检测到多个 Harbor 任务，请分别上传每个任务目录")
    if not manifests:
        raise HTTPException(400, "Harbor 任务必须包含 task.toml 和 instruction.md（或 steps 下的 instruction.md）")
    manifest = manifests[0]
    prefix = manifest.rsplit("/", 1)[0] + "/" if "/" in manifest else ""
    if prefix.count("/") > 1:
        raise HTTPException(400, "请选择任务目录本身，task.toml 应位于根目录或单一父目录下")
    instructions = prefix + "instruction.md" in files or any(
        p.startswith(prefix + "steps/") and p.endswith("/instruction.md") for p in files
    )
    if not instructions:
        raise HTTPException(400, "任务缺少 instruction.md；多步骤任务需在 steps 下提供 instruction.md")
    try:
        tomllib.loads(files[manifest].decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(400, "task.toml 不是有效的 UTF-8 TOML 配置") from exc
    return prefix
