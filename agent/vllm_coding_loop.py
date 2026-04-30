#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import fnmatch
import hashlib
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
import time
import tomllib
import urllib.error
import urllib.request
from typing import Any, Iterable


DEFAULT_IGNORE_GLOBS = [
    ".git/**",
    ".agent_runs/**",
    ".idea/**",
    ".vscode/**",
    ".venv/**",
    "venv/**",
    "node_modules/**",
    "dist/**",
    "build/**",
    ".next/**",
    ".turbo/**",
    ".pytest_cache/**",
    ".mypy_cache/**",
    "__pycache__/**",
    "target/**",
    ".DS_Store",
]

DEFAULT_INCLUDE_FILES = [
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
    ".continue/rules",
    ".continue/rules/**",
    ".openhands/**",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "go.mod",
    "Cargo.toml",
    "Makefile",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
]

DEFAULT_BLOCKED_COMMAND_PATTERNS = [
    r"(^|[;&|])\s*sudo\b",
    r"(^|[;&|])\s*(shutdown|reboot|halt|poweroff)\b",
    r"(^|[;&|])\s*rm\s+-rf\s+/",
    r"(^|[;&|])\s*(mkfs|fdisk|dd)\b",
    r"(^|[;&|])\s*(curl|wget)\b.*\|\s*(sh|bash|zsh)\b",
    r"(^|[;&|])\s*git\s+(push|reset\s+--hard|clean\s+-fdx|tag\b)\b",
]

TEXT_EXTENSIONS = {
    ".py",
    ".pyi",
    ".md",
    ".txt",
    ".json",
    ".jsonl",
    ".toml",
    ".yaml",
    ".yml",
    ".ini",
    ".cfg",
    ".env",
    ".sh",
    ".bash",
    ".zsh",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".mjs",
    ".cjs",
    ".css",
    ".scss",
    ".html",
    ".xml",
    ".sql",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".swift",
    ".rb",
    ".php",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".cs",
}

BINARY_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".pdf",
    ".zip",
    ".gz",
    ".tar",
    ".bz2",
    ".7z",
    ".jar",
    ".war",
    ".so",
    ".dylib",
    ".dll",
    ".exe",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".ico",
    ".mp4",
    ".mov",
    ".mp3",
    ".wav",
}

STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "then",
    "into",
    "your",
    "will",
    "have",
    "when",
    "what",
    "want",
    "make",
    "code",
    "file",
    "files",
    "test",
    "tests",
    "app",
    "repo",
    "repository",
    "task",
    "agent",
    "plan",
    "loop",
    "python",
}


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def shorten(text: str, limit: int = 12000) -> str:
    if len(text) <= limit:
        return text
    head = int(limit * 0.6)
    tail = limit - head - 40
    return f"{text[:head]}\n\n...[truncated {len(text) - limit} chars]...\n\n{text[-tail:]}"


def pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False)


def coerce_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def deep_get(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return default
        if key not in current:
            return default
        current = current[key]
    return current


@dataclasses.dataclass
class CommandResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_sec: float
    timed_out: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and self.error is None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    def render_for_model(self, max_chars: int = 12000) -> str:
        payload = {
            "command": self.command,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "duration_sec": round(self.duration_sec, 3),
            "error": self.error,
            "stdout": shorten(self.stdout, max_chars // 2),
            "stderr": shorten(self.stderr, max_chars // 2),
        }
        return pretty_json(payload)


@dataclasses.dataclass
class AgentConfig:
    base_url: str = "http://localhost:8000/v1"
    api_key: str = "EMPTY"
    model: str | None = None
    temperature: float = 0.2
    top_p: float = 0.95
    max_tokens: int = 4096
    request_timeout_sec: int = 600
    max_cycles: int = 6
    max_tool_steps: int = 40
    read_chunk_lines: int = 220
    max_file_bytes: int = 120_000
    max_context_chars: int = 60_000
    tool_command_timeout_sec: int = 120
    verify_timeout_sec: int = 900
    allow_command_prefixes: list[str] | None = None
    blocked_command_patterns: list[str] = dataclasses.field(
        default_factory=lambda: list(DEFAULT_BLOCKED_COMMAND_PATTERNS)
    )
    ignore_globs: list[str] = dataclasses.field(
        default_factory=lambda: list(DEFAULT_IGNORE_GLOBS)
    )
    include_files: list[str] = dataclasses.field(
        default_factory=lambda: list(DEFAULT_INCLUDE_FILES)
    )
    setup_commands: list[str] = dataclasses.field(default_factory=list)
    verify_commands: list[str] = dataclasses.field(default_factory=list)
    env: dict[str, str] = dataclasses.field(default_factory=dict)
    model_extra_body: dict[str, Any] = dataclasses.field(default_factory=dict)

    @classmethod
    def from_toml(cls, path: pathlib.Path) -> "AgentConfig":
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        vllm = deep_get(data, "vllm", default={})
        agent = deep_get(data, "agent", default={})
        repo = deep_get(data, "repo", default={})
        commands = deep_get(data, "commands", default={})
        return cls(
            base_url=deep_get(vllm, "base_url", default=cls.base_url),
            api_key=deep_get(vllm, "api_key", default=cls.api_key),
            model=deep_get(vllm, "model", default=None),
            temperature=float(deep_get(vllm, "temperature", default=cls.temperature)),
            top_p=float(deep_get(vllm, "top_p", default=cls.top_p)),
            max_tokens=int(deep_get(vllm, "max_tokens", default=cls.max_tokens)),
            request_timeout_sec=int(
                deep_get(vllm, "request_timeout_sec", default=cls.request_timeout_sec)
            ),
            max_cycles=int(deep_get(agent, "max_cycles", default=cls.max_cycles)),
            max_tool_steps=int(
                deep_get(agent, "max_tool_steps", default=cls.max_tool_steps)
            ),
            read_chunk_lines=int(
                deep_get(agent, "read_chunk_lines", default=cls.read_chunk_lines)
            ),
            max_file_bytes=int(
                deep_get(agent, "max_file_bytes", default=cls.max_file_bytes)
            ),
            max_context_chars=int(
                deep_get(agent, "max_context_chars", default=cls.max_context_chars)
            ),
            tool_command_timeout_sec=int(
                deep_get(
                    agent,
                    "tool_command_timeout_sec",
                    default=cls.tool_command_timeout_sec,
                )
            ),
            verify_timeout_sec=int(
                deep_get(agent, "verify_timeout_sec", default=cls.verify_timeout_sec)
            ),
            allow_command_prefixes=deep_get(
                commands, "allow_command_prefixes", default=None
            ),
            blocked_command_patterns=list(
                deep_get(
                    commands,
                    "blocked_command_patterns",
                    default=list(DEFAULT_BLOCKED_COMMAND_PATTERNS),
                )
            ),
            ignore_globs=list(
                deep_get(repo, "ignore_globs", default=list(DEFAULT_IGNORE_GLOBS))
            ),
            include_files=list(
                deep_get(repo, "include_files", default=list(DEFAULT_INCLUDE_FILES))
            ),
            setup_commands=list(deep_get(commands, "setup", default=[])),
            verify_commands=list(deep_get(commands, "verify", default=[])),
            env={str(k): str(v) for k, v in deep_get(commands, "env", default={}).items()},
            model_extra_body=dict(deep_get(vllm, "extra_body", default={})),
        )


class JsonlLogger:
    def __init__(self, path: pathlib.Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, event_type: str, payload: dict[str, Any]) -> None:
        record = {
            "ts": now_iso(),
            "event": event_type,
            **payload,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


class ToolError(RuntimeError):
    pass


class VLLMClient:
    def __init__(self, config: AgentConfig) -> None:
        self.base_url = config.base_url.rstrip("/")
        self.api_key = config.api_key
        self.model = config.model
        self.temperature = config.temperature
        self.top_p = config.top_p
        self.max_tokens = config.max_tokens
        self.request_timeout_sec = config.request_timeout_sec
        self.extra_body = dict(config.model_extra_body)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "vllm-coding-loop/0.1",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url=f"{self.base_url}{path}",
            data=body,
            headers=self._headers(),
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.request_timeout_sec) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(
                f"HTTP {exc.code} calling {path}: {detail or exc.reason}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Failed to reach vLLM server {self.base_url}: {exc}") from exc

    def ensure_model(self) -> str:
        if self.model:
            return self.model
        data = self._request("GET", "/models")
        models = data.get("data") or []
        if not models:
            raise RuntimeError("vLLM /models returned no available models")
        self.model = str(models[0]["id"])
        return self.model

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, tool_choice: str | dict[str, Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.ensure_model(),
            "messages": messages,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_tokens": self.max_tokens,
        }
        if tools is not None:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        payload.update(self.extra_body)
        data = self._request("POST", "/chat/completions", payload)
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("No choices returned from vLLM /chat/completions")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise RuntimeError(f"Unexpected message payload from vLLM: {choices[0]!r}")
        return message


class Workspace:
    def __init__(
        self,
        source_root: pathlib.Path,
        workdir: pathlib.Path,
        workspace_root: pathlib.Path,
        mode: str,
        logger: JsonlLogger,
        ignore_globs: list[str],
    ) -> None:
        self.source_root = source_root
        self.workdir = workdir
        self.workspace_root = workspace_root
        self.mode = mode
        self.logger = logger
        self.ignore_globs = ignore_globs

    @classmethod
    def create(
        cls,
        repo_path: pathlib.Path,
        run_root: pathlib.Path,
        ignore_globs: list[str],
        logger: JsonlLogger,
    ) -> "Workspace":
        repo_path = repo_path.resolve()
        workspace_root = run_root / "workspace"
        git_top = git_toplevel(repo_path)
        if git_top is not None:
            worktree_root = workspace_root
            run_checked(["git", "-C", str(git_top), "worktree", "add", "--detach", str(worktree_root), "HEAD"])
            tracked_diff = run_captured(
                ["git", "-C", str(git_top), "diff", "--binary", "HEAD"],
                cwd=git_top,
            )
            if tracked_diff.stdout.strip():
                apply_patch_to_worktree(worktree_root, tracked_diff.stdout)
            copy_untracked_files(git_top, worktree_root, ignore_globs)
            rel = repo_path.relative_to(git_top)
            workdir = worktree_root / rel
            logger.log(
                "workspace_created",
                {
                    "mode": "git_worktree",
                    "source_root": str(git_top),
                    "workspace_root": str(worktree_root),
                    "workdir": str(workdir),
                },
            )
            return cls(git_top, workdir, worktree_root, "git_worktree", logger, ignore_globs)

        shutil.copytree(
            repo_path,
            workspace_root,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(*default_copy_ignore(ignore_globs)),
        )
        logger.log(
            "workspace_created",
            {
                "mode": "copy",
                "source_root": str(repo_path),
                "workspace_root": str(workspace_root),
                "workdir": str(workspace_root),
            },
        )
        return cls(repo_path, workspace_root, workspace_root, "copy", logger, ignore_globs)

    def resolve_path(self, relative_path: str) -> pathlib.Path:
        rel = pathlib.PurePosixPath(relative_path)
        if rel.is_absolute() or ".." in rel.parts:
            raise ToolError(f"Path must stay inside workspace: {relative_path}")
        full = (self.workdir / pathlib.Path(*rel.parts)).resolve()
        if self.workdir not in full.parents and full != self.workdir:
            raise ToolError(f"Resolved path escaped workspace: {relative_path}")
        return full

    def write_patch(self, patch_path: pathlib.Path) -> None:
        if self.mode == "git_worktree":
            subprocess.run(
                ["git", "-C", str(self.workspace_root), "add", "-N", "."],
                capture_output=True,
                text=True,
            )
            result = run_captured(["git", "-C", str(self.workspace_root), "diff", "--binary"])
            patch_path.write_text(result.stdout, encoding="utf-8")
            return
        patch_text = build_text_patch(self.source_root, self.workdir, self.ignore_globs)
        patch_path.write_text(patch_text, encoding="utf-8")

    def changed_files(self) -> list[str]:
        if self.mode == "git_worktree":
            result = run_captured(["git", "-C", str(self.workspace_root), "status", "--porcelain"])
            changed: list[str] = []
            for line in result.stdout.splitlines():
                if not line.strip():
                    continue
                rel_path = line[3:]
                if matches_any_glob(rel_path, self.ignore_globs):
                    continue
                changed.append(rel_path)
            return sorted(changed)
        changed: list[str] = []
        seen = set()
        for path in iter_files(self.workdir, self.ignore_globs):
            rel = path.relative_to(self.workdir).as_posix()
            src = self.source_root / rel
            if not src.exists():
                changed.append(rel)
                seen.add(rel)
                continue
            if path.is_file() and src.is_file():
                try:
                    if path.read_bytes() != src.read_bytes():
                        changed.append(rel)
                        seen.add(rel)
                except OSError:
                    continue
        for path in iter_files(self.source_root, self.ignore_globs):
            rel = path.relative_to(self.source_root).as_posix()
            if rel in seen:
                continue
            dst = self.workdir / rel
            if not dst.exists():
                changed.append(rel)
        return sorted(changed)


def default_copy_ignore(ignore_globs: list[str]) -> list[str]:
    patterns = set()
    for pattern in ignore_globs:
        first = pattern.split("/")[0]
        if first:
            patterns.add(first)
    patterns.add(".git")
    return sorted(patterns)


def git_toplevel(path: pathlib.Path) -> pathlib.Path | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        return pathlib.Path(result.stdout.strip()).resolve()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def run_checked(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def run_captured(cmd: list[str], cwd: pathlib.Path | None = None) -> CommandResult:
    started = time.time()
    result = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True)
    return CommandResult(
        command=shlex.join(cmd),
        exit_code=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        duration_sec=time.time() - started,
    )


def apply_patch_to_worktree(worktree_root: pathlib.Path, patch_text: str) -> None:
    if not patch_text.strip():
        return
    proc = subprocess.run(
        ["git", "-C", str(worktree_root), "apply", "--whitespace=nowarn", "-"],
        input=patch_text,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"Failed to apply local diff into worktree: {proc.stderr or proc.stdout}"
        )


def copy_untracked_files(source_root: pathlib.Path, worktree_root: pathlib.Path, ignore_globs: list[str]) -> None:
    proc = subprocess.run(
        ["git", "-C", str(source_root), "ls-files", "--others", "--exclude-standard", "-z"],
        capture_output=True,
        check=True,
    )
    entries = [e for e in proc.stdout.decode("utf-8", errors="ignore").split("\0") if e]
    for rel in entries:
        if matches_any_glob(rel, ignore_globs):
            continue
        src = source_root / rel
        dst = worktree_root / rel
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        elif src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def build_text_patch(original_root: pathlib.Path, modified_root: pathlib.Path, ignore_globs: list[str] | None = None) -> str:
    import difflib

    lines: list[str] = []
    original_map = {p.relative_to(original_root).as_posix(): p for p in iter_files(original_root, ignore_globs)}
    modified_map = {p.relative_to(modified_root).as_posix(): p for p in iter_files(modified_root, ignore_globs)}
    all_paths = sorted(set(original_map) | set(modified_map))
    for rel in all_paths:
        src = original_map.get(rel)
        dst = modified_map.get(rel)
        if src and not dst:
            try:
                before = src.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)
            except OSError:
                continue
            diff = difflib.unified_diff(before, [], fromfile=f"a/{rel}", tofile=f"b/{rel}")
            lines.extend(diff)
            continue
        if dst and not src:
            try:
                after = dst.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)
            except OSError:
                continue
            diff = difflib.unified_diff([], after, fromfile=f"a/{rel}", tofile=f"b/{rel}")
            lines.extend(diff)
            continue
        if not src or not dst:
            continue
        try:
            before_text = src.read_text(encoding="utf-8", errors="ignore")
            after_text = dst.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if before_text == after_text:
            continue
        diff = difflib.unified_diff(
            before_text.splitlines(keepends=True),
            after_text.splitlines(keepends=True),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
        )
        lines.extend(diff)
    return "".join(lines)


class RepoInspector:
    def __init__(self, root: pathlib.Path, ignore_globs: list[str], max_file_bytes: int) -> None:
        self.root = root
        self.ignore_globs = ignore_globs
        self.max_file_bytes = max_file_bytes

    def iter_files(self) -> Iterable[pathlib.Path]:
        return iter_files(self.root, self.ignore_globs)

    def list_files(self, pattern: str | None = None, limit: int = 200) -> list[str]:
        files: list[str] = []
        for path in self.iter_files():
            rel = path.relative_to(self.root).as_posix()
            if pattern and not fnmatch.fnmatch(rel, pattern):
                continue
            files.append(rel)
            if len(files) >= limit:
                break
        return files

    def render_tree(self, max_depth: int = 3, limit: int = 400) -> str:
        lines: list[str] = []
        count = 0
        for current_root, dirs, files in os.walk(self.root):
            rel_dir = pathlib.Path(current_root).relative_to(self.root)
            depth = len(rel_dir.parts)
            if depth > max_depth:
                dirs[:] = []
                continue
            dirs[:] = sorted(
                d
                for d in dirs
                if not matches_any_glob((rel_dir / d).as_posix().lstrip("."), self.ignore_globs)
            )
            prefix = "  " * depth
            dirname = "." if rel_dir == pathlib.Path(".") else rel_dir.name
            lines.append(f"{prefix}{dirname}/")
            count += 1
            if count >= limit:
                lines.append("... [tree truncated] ...")
                break
            for filename in sorted(files):
                rel = (rel_dir / filename).as_posix().replace("./", "")
                if matches_any_glob(rel, self.ignore_globs):
                    continue
                lines.append(f"{prefix}  {filename}")
                count += 1
                if count >= limit:
                    lines.append("... [tree truncated] ...")
                    break
            if count >= limit:
                break
        return "\n".join(lines)

    def read_file(self, rel_path: str, start_line: int = 1, end_line: int | None = None) -> str:
        path = self.root / rel_path
        if not path.exists():
            raise ToolError(f"File not found: {rel_path}")
        if path.is_dir():
            raise ToolError(f"Path is a directory, not a file: {rel_path}")
        if path.stat().st_size > self.max_file_bytes:
            raise ToolError(
                f"File is too large ({path.stat().st_size} bytes) for direct read: {rel_path}"
            )
        if is_binary_file(path):
            raise ToolError(f"Refusing to read binary file as text: {rel_path}")
        text = path.read_text(encoding="utf-8", errors="ignore")
        lines = text.splitlines()
        if end_line is None:
            end_line = len(lines)
        start_line = max(1, start_line)
        end_line = max(start_line, end_line)
        selected = lines[start_line - 1 : end_line]
        numbered = [f"{idx:5d}: {line}" for idx, line in enumerate(selected, start=start_line)]
        return "\n".join(numbered)

    def search_repo(
        self,
        query: str,
        glob_pattern: str | None = None,
        case_sensitive: bool = False,
        max_matches: int = 50,
    ) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        if not case_sensitive:
            query_cmp = query.lower()
        else:
            query_cmp = query
        for path in self.iter_files():
            rel = path.relative_to(self.root).as_posix()
            if glob_pattern and not fnmatch.fnmatch(rel, glob_pattern):
                continue
            if is_binary_file(path) or path.stat().st_size > self.max_file_bytes:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for idx, line in enumerate(text.splitlines(), start=1):
                hay = line if case_sensitive else line.lower()
                if query_cmp in hay:
                    matches.append({"path": rel, "line": idx, "text": line})
                    if len(matches) >= max_matches:
                        return matches
        return matches

    def detect_verify_commands(self) -> list[str]:
        commands: list[str] = []
        if (self.root / "pyproject.toml").exists() or (self.root / "setup.py").exists():
            if (self.root / "tests").exists() or (self.root / "pytest.ini").exists():
                commands.append("python -m pytest -q")
        package_json = self.root / "package.json"
        if package_json.exists():
            try:
                data = json.loads(package_json.read_text(encoding="utf-8"))
                scripts = data.get("scripts") or {}
                if "test" in scripts:
                    commands.append("npm test -- --runInBand")
                elif "lint" in scripts:
                    commands.append("npm run lint")
            except Exception:
                commands.append("npm test -- --runInBand")
        if (self.root / "go.mod").exists():
            commands.append("go test ./...")
        if (self.root / "Cargo.toml").exists():
            commands.append("cargo test")
        if (self.root / "pom.xml").exists():
            commands.append("mvn test")
        return dedupe_preserve_order(commands)

    def initial_context(
        self,
        task_text: str,
        changed_files: list[str],
        include_files: list[str],
        max_chars: int,
    ) -> str:
        budget = max_chars
        chunks: list[str] = []

        def add(section: str) -> None:
            nonlocal budget
            if budget <= 0:
                return
            clipped = shorten(section, budget)
            chunks.append(clipped)
            budget -= len(clipped) + 2

        add("# Repository tree\n" + self.render_tree())

        if changed_files:
            add("# Current changed files\n" + "\n".join(f"- {p}" for p in changed_files[:100]))

        selected_paths = select_context_files(
            root=self.root,
            task_text=task_text,
            include_files=include_files,
            ignore_globs=self.ignore_globs,
            limit=12,
        )
        for rel in selected_paths:
            path = self.root / rel
            if not path.exists() or path.is_dir() or is_binary_file(path):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            snippet = shorten(text, 5000)
            add(f"# File: {rel}\n```\n{snippet}\n```")
            if budget <= 0:
                break

        return "\n\n".join(chunks)


def iter_files(root: pathlib.Path, ignore_globs: list[str] | None = None) -> Iterable[pathlib.Path]:
    ignore_globs = ignore_globs or []
    for current_root, dirs, files in os.walk(root):
        rel_dir = pathlib.Path(current_root).relative_to(root)
        dirs[:] = [
            d
            for d in dirs
            if not matches_any_glob((rel_dir / d).as_posix().replace("./", ""), ignore_globs)
        ]
        for filename in files:
            rel = (rel_dir / filename).as_posix().replace("./", "")
            if matches_any_glob(rel, ignore_globs):
                continue
            yield pathlib.Path(current_root) / filename


def matches_any_glob(path: str, patterns: list[str]) -> bool:
    normalized = path[2:] if path.startswith("./") else path.lstrip("/")
    for pattern in patterns:
        if fnmatch.fnmatch(normalized, pattern):
            return True
        if pattern.endswith("/**"):
            prefix = pattern[:-3].rstrip("/")
            if normalized == prefix or normalized.startswith(prefix + "/"):
                return True
    return False


def is_binary_file(path: pathlib.Path) -> bool:
    if path.suffix.lower() in BINARY_EXTENSIONS:
        return True
    try:
        sample = path.read_bytes()[:4096]
    except OSError:
        return True
    if b"\x00" in sample:
        return True
    return False


def is_probably_text(path: pathlib.Path) -> bool:
    if path.suffix.lower() in TEXT_EXTENSIONS:
        return True
    return not is_binary_file(path)


def select_context_files(
    root: pathlib.Path,
    task_text: str,
    include_files: list[str],
    ignore_globs: list[str],
    limit: int,
) -> list[str]:
    tokens = [
        token
        for token in re.findall(r"[A-Za-z0-9_./-]{3,}", task_text.lower())
        if token not in STOPWORDS
    ]
    scored: list[tuple[int, str]] = []
    for path in iter_files(root, ignore_globs):
        rel = path.relative_to(root).as_posix()
        if not is_probably_text(path):
            continue
        score = 0
        basename = path.name.lower()
        if any(fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(basename, pattern) for pattern in include_files):
            score += 60
        if path.suffix.lower() in {".py", ".ts", ".tsx", ".js", ".go", ".rs", ".java", ".toml", ".json", ".yml", ".yaml"}:
            score += 8
        lowered_rel = rel.lower()
        for token in tokens:
            if token in lowered_rel:
                score += 15
        try:
            head = path.read_text(encoding="utf-8", errors="ignore")[:2500].lower()
        except OSError:
            continue
        for token in tokens:
            if token in head:
                score += 3
        if score > 0:
            scored.append((score, rel))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [rel for _, rel in scored[:limit]]


def dedupe_preserve_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


class CommandRunner:
    def __init__(self, workdir: pathlib.Path, config: AgentConfig, logger: JsonlLogger) -> None:
        self.workdir = workdir
        self.config = config
        self.logger = logger
        self.shell = "/bin/bash" if pathlib.Path("/bin/bash").exists() else "/bin/sh"

    def _validate(self, command: str) -> None:
        stripped = command.strip()
        if not stripped:
            raise ToolError("Command cannot be empty")
        if self.config.allow_command_prefixes:
            allowed = any(stripped.startswith(prefix) for prefix in self.config.allow_command_prefixes)
            if not allowed:
                raise ToolError(
                    "Command rejected because it does not match allow_command_prefixes"
                )
        for pattern in self.config.blocked_command_patterns:
            if re.search(pattern, stripped):
                raise ToolError(f"Command rejected by safety policy: {pattern}")

    def run(self, command: str, timeout_sec: int) -> CommandResult:
        self._validate(command)
        env = os.environ.copy()
        env.update(self.config.env)
        started = time.time()
        try:
            proc = subprocess.run(
                [self.shell, "-lc", command],
                cwd=str(self.workdir),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
            result = CommandResult(
                command=command,
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                duration_sec=time.time() - started,
            )
        except subprocess.TimeoutExpired as exc:
            result = CommandResult(
                command=command,
                exit_code=124,
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
                duration_sec=time.time() - started,
                timed_out=True,
                error=f"Timed out after {timeout_sec} seconds",
            )
        self.logger.log("command_result", result.to_dict())
        return result


SYSTEM_PROMPT = textwrap.dedent(
    """
    You are an autonomous coding agent working inside a disposable repository workspace.

    Your job in each cycle is:
    1. Understand the task and the repository.
    2. Create or update a concrete plan using update_plan.
    3. Inspect files before editing them whenever practical.
    4. Make the smallest coherent code changes needed.
    5. Use run_command for exploratory checks if needed.
    6. Call finish_iteration when the attempt is ready for the external verifier.

    Rules:
    - Never claim success only because you think the code is correct. The external verifier is the source of truth.
    - Stay inside the workspace. Paths are relative to the repository workdir.
    - Prefer replace_in_file for narrow edits and write_file for full rewrites.
    - If a replacement fails, inspect the file and try again.
    - Keep changes aligned with the task and avoid unrelated refactors.
    - At the start of every cycle, your first tool call should be update_plan.
    - Before finish_iteration, update_plan again if the plan changed.
    - If blocked, call finish_iteration with status='blocked' and explain why.
    - Think through failures using the provided verifier logs, then adapt the plan.
    """
).strip()


def build_tools() -> list[dict[str, Any]]:
    return [
        tool(
            "update_plan",
            "Record the current plan, intended files, and main risks.",
            {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "steps": {"type": "array", "items": {"type": "string"}},
                    "target_files": {"type": "array", "items": {"type": "string"}},
                    "risks": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["summary", "steps", "target_files", "risks"],
            },
        ),
        tool(
            "list_files",
            "List files in the repo, optionally filtered by a glob pattern.",
            {
                "type": "object",
                "properties": {
                    "glob": {"type": ["string", "null"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                },
                "required": ["limit"],
            },
        ),
        tool(
            "search_repo",
            "Search literal text in repository files.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "glob": {"type": ["string", "null"]},
                    "case_sensitive": {"type": "boolean"},
                    "max_matches": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                "required": ["query"],
            },
        ),
        tool(
            "read_file",
            "Read a text file with line numbers.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": ["integer", "null"], "minimum": 1},
                },
                "required": ["path"],
            },
        ),
        tool(
            "write_file",
            "Write or overwrite a full text file.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        ),
        tool(
            "replace_in_file",
            "Replace exact text within a file. Use expected_replacements to catch drift.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old": {"type": "string"},
                    "new": {"type": "string"},
                    "expected_replacements": {"type": ["integer", "null"], "minimum": 1},
                },
                "required": ["path", "old", "new"],
            },
        ),
        tool(
            "delete_file",
            "Delete a file.",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        ),
        tool(
            "run_command",
            "Run a shell command inside the workspace. Use this for focused checks only.",
            {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout_sec": {"type": ["integer", "null"], "minimum": 1, "maximum": 3600},
                },
                "required": ["command"],
            },
        ),
        tool(
            "finish_iteration",
            "Declare the iteration ready for the external verifier or blocked.",
            {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["ready_for_verify", "blocked"],
                    },
                    "summary": {"type": "string"},
                    "remaining_risks": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["status", "summary", "remaining_risks"],
            },
        ),
    ]


def tool(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }


class CodingAgent:
    def __init__(
        self,
        config: AgentConfig,
        workspace: Workspace,
        task_text: str,
        run_root: pathlib.Path,
        logger: JsonlLogger,
    ) -> None:
        self.config = config
        self.workspace = workspace
        self.task_text = task_text.strip()
        self.run_root = run_root
        self.logger = logger
        self.client = VLLMClient(config)
        self.runner = CommandRunner(workspace.workdir, config, logger)
        self.inspector = RepoInspector(workspace.workdir, config.ignore_globs, config.max_file_bytes)
        self.tools = build_tools()
        self.plans: list[dict[str, Any]] = []
        self.cycle_summaries: list[dict[str, Any]] = []
        self.verify_commands = (
            config.verify_commands or self.inspector.detect_verify_commands() or ["python -m pytest -q"]
        )

    def run(self) -> dict[str, Any]:
        setup_results = self._run_setup_commands()
        if any(not result.ok for result in setup_results):
            return self._finalize(
                status="setup_failed",
                verify_results=[],
                reason="One or more setup commands failed",
            )

        last_verify_results: list[CommandResult] = []
        for cycle_idx in range(1, self.config.max_cycles + 1):
            self.logger.log("cycle_start", {"cycle": cycle_idx})
            finish_payload = self._run_one_cycle(cycle_idx, last_verify_results)
            if finish_payload.get("status") == "blocked":
                return self._finalize(
                    status="blocked",
                    verify_results=last_verify_results,
                    reason=finish_payload.get("summary", "Agent reported blocked"),
                )

            verify_results = self._run_verifier(cycle_idx)
            last_verify_results = verify_results
            if all(result.ok for result in verify_results):
                return self._finalize(
                    status="success",
                    verify_results=verify_results,
                    reason=finish_payload.get("summary", "Verifier passed"),
                )

            self.cycle_summaries.append(
                {
                    "cycle": cycle_idx,
                    "finish": finish_payload,
                    "verify": [result.to_dict() for result in verify_results],
                }
            )

        return self._finalize(
            status="max_cycles_exceeded",
            verify_results=last_verify_results,
            reason=f"Reached max_cycles={self.config.max_cycles} without passing verifier",
        )

    def _run_setup_commands(self) -> list[CommandResult]:
        results: list[CommandResult] = []
        for command in self.config.setup_commands:
            result = self.runner.run(command, timeout_sec=self.config.verify_timeout_sec)
            results.append(result)
        return results

    def _build_cycle_user_message(self, cycle_idx: int, last_verify_results: list[CommandResult]) -> str:
        changed = self.workspace.changed_files()
        context = self.inspector.initial_context(
            task_text=self.task_text,
            changed_files=changed,
            include_files=self.config.include_files,
            max_chars=self.config.max_context_chars,
        )
        verify_text = "\n".join(f"- {cmd}" for cmd in self.verify_commands)
        verify_feedback = "No previous verifier output."
        if last_verify_results:
            verify_feedback = "\n\n".join(
                f"## Verifier command\n{result.render_for_model(8000)}" for result in last_verify_results
            )
        previous_plans = pretty_json(self.plans[-3:]) if self.plans else "[]"
        return textwrap.dedent(
            f"""
            Cycle: {cycle_idx}/{self.config.max_cycles}

            Task:
            {self.task_text}

            External verifier commands (the only source of truth):
            {verify_text}

            Previous plan snapshots:
            {previous_plans}

            Latest verifier feedback:
            {verify_feedback}

            Workspace information:
            - Repository workdir: {self.workspace.workdir}
            - Changed files right now: {len(changed)}

            Repository context:
            {context}
            """
        ).strip()

    def _run_one_cycle(self, cycle_idx: int, last_verify_results: list[CommandResult]) -> dict[str, Any]:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": self._build_cycle_user_message(cycle_idx, last_verify_results)},
        ]
        finish_payload: dict[str, Any] | None = None
        for step in range(1, self.config.max_tool_steps + 1):
            assistant_message = self.client.chat(
                messages=messages,
                tools=self.tools,
                tool_choice="required",
            )
            tool_calls = assistant_message.get("tool_calls") or []
            if not tool_calls:
                raise RuntimeError(
                    "Model returned no tool_calls even though tool_choice='required'"
                )
            assistant_record = {
                "role": "assistant",
                "content": assistant_message.get("content") or "",
                "tool_calls": tool_calls,
            }
            messages.append(assistant_record)
            self.logger.log(
                "assistant_tool_calls",
                {
                    "cycle": cycle_idx,
                    "step": step,
                    "tool_calls": tool_calls,
                },
            )
            for call in tool_calls:
                name = deep_get(call, "function", "name")
                raw_args = deep_get(call, "function", "arguments", default="{}")
                if isinstance(raw_args, str):
                    try:
                        args = json.loads(raw_args)
                    except json.JSONDecodeError as exc:
                        raise RuntimeError(
                            f"Invalid JSON in tool arguments for {name}: {raw_args}"
                        ) from exc
                elif isinstance(raw_args, dict):
                    args = raw_args
                else:
                    args = {}
                tool_output = self._handle_tool(name, args, cycle_idx=cycle_idx, step=step)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id", "unknown"),
                        "name": name,
                        "content": tool_output,
                    }
                )
                if name == "finish_iteration":
                    finish_payload = json.loads(tool_output)
                    break
            if finish_payload is not None:
                break
        if finish_payload is None:
            raise RuntimeError(
                f"Agent failed to call finish_iteration within max_tool_steps={self.config.max_tool_steps}"
            )
        return finish_payload

    def _handle_tool(self, name: str | None, args: dict[str, Any], cycle_idx: int, step: int) -> str:
        if not name:
            raise RuntimeError("Tool call missing function name")
        self.logger.log(
            "tool_invocation",
            {"cycle": cycle_idx, "step": step, "tool": name, "args": args},
        )
        try:
            if name == "update_plan":
                payload = {
                    "summary": str(args.get("summary", "")).strip(),
                    "steps": [str(x) for x in coerce_list(args.get("steps"))],
                    "target_files": [str(x) for x in coerce_list(args.get("target_files"))],
                    "risks": [str(x) for x in coerce_list(args.get("risks"))],
                }
                self.plans.append({"cycle": cycle_idx, **payload})
                return pretty_json({"ok": True, "recorded_plan": payload})

            if name == "list_files":
                glob = args.get("glob")
                limit = int(args.get("limit", 200))
                files = self.inspector.list_files(pattern=glob, limit=limit)
                return pretty_json({"files": files})

            if name == "search_repo":
                query = str(args["query"])
                glob = args.get("glob")
                case_sensitive = bool(args.get("case_sensitive", False))
                max_matches = int(args.get("max_matches", 50))
                matches = self.inspector.search_repo(
                    query=query,
                    glob_pattern=glob,
                    case_sensitive=case_sensitive,
                    max_matches=max_matches,
                )
                return pretty_json({"matches": matches})

            if name == "read_file":
                rel_path = str(args["path"])
                start_line = int(args.get("start_line", 1))
                end_line = args.get("end_line")
                if end_line is None:
                    end_line = start_line + self.config.read_chunk_lines - 1
                else:
                    end_line = int(end_line)
                path = self.workspace.resolve_path(rel_path)
                rel = path.relative_to(self.workspace.workdir).as_posix()
                content = self.inspector.read_file(rel, start_line=start_line, end_line=end_line)
                return content

            if name == "write_file":
                rel_path = str(args["path"])
                content = str(args["content"])
                path = self.workspace.resolve_path(rel_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
                return pretty_json(
                    {
                        "ok": True,
                        "path": rel_path,
                        "bytes": len(content.encode("utf-8")),
                        "sha256": sha256_text(content),
                    }
                )

            if name == "replace_in_file":
                rel_path = str(args["path"])
                old = str(args["old"])
                new = str(args["new"])
                expected = args.get("expected_replacements")
                path = self.workspace.resolve_path(rel_path)
                if not path.exists():
                    raise ToolError(f"File not found: {rel_path}")
                text = path.read_text(encoding="utf-8", errors="ignore")
                count = text.count(old)
                if count == 0:
                    raise ToolError(
                        f"Old text not found in {rel_path}. Inspect the file and retry with exact text."
                    )
                if expected is not None and count != int(expected):
                    raise ToolError(
                        f"Expected {expected} replacements but found {count} matches in {rel_path}"
                    )
                replaced = text.replace(old, new)
                path.write_text(replaced, encoding="utf-8")
                return pretty_json({"ok": True, "path": rel_path, "replacements": count})

            if name == "delete_file":
                rel_path = str(args["path"])
                path = self.workspace.resolve_path(rel_path)
                if not path.exists():
                    return pretty_json({"ok": True, "path": rel_path, "already_missing": True})
                path.unlink()
                return pretty_json({"ok": True, "path": rel_path, "deleted": True})

            if name == "run_command":
                command = str(args["command"])
                timeout = int(args.get("timeout_sec") or self.config.tool_command_timeout_sec)
                result = self.runner.run(command, timeout_sec=timeout)
                return result.render_for_model()

            if name == "finish_iteration":
                payload = {
                    "status": str(args.get("status", "blocked")),
                    "summary": str(args.get("summary", "")).strip(),
                    "remaining_risks": [str(x) for x in coerce_list(args.get("remaining_risks"))],
                }
                return pretty_json(payload)

            raise RuntimeError(f"Unknown tool: {name}")
        except Exception as exc:
            error_payload = {
                "ok": False,
                "tool": name,
                "error": str(exc),
            }
            return pretty_json(error_payload)

    def _run_verifier(self, cycle_idx: int) -> list[CommandResult]:
        results: list[CommandResult] = []
        for command in self.verify_commands:
            result = self.runner.run(command, timeout_sec=self.config.verify_timeout_sec)
            results.append(result)
        self.logger.log(
            "verifier_results",
            {"cycle": cycle_idx, "results": [result.to_dict() for result in results]},
        )
        return results

    def _finalize(
        self,
        status: str,
        verify_results: list[CommandResult],
        reason: str,
    ) -> dict[str, Any]:
        patch_path = self.run_root / "final.patch"
        self.workspace.write_patch(patch_path)
        changed_files = self.workspace.changed_files()
        report_path = self.run_root / "final_report.md"
        report = self._render_report(status, reason, verify_results, changed_files, patch_path)
        report_path.write_text(report, encoding="utf-8")
        summary = {
            "status": status,
            "reason": reason,
            "verify_results": [result.to_dict() for result in verify_results],
            "changed_files": changed_files,
            "patch_path": str(patch_path),
            "report_path": str(report_path),
            "workspace_root": str(self.workspace.workspace_root),
            "workdir": str(self.workspace.workdir),
            "plans": self.plans,
            "verify_commands": self.verify_commands,
        }
        (self.run_root / "summary.json").write_text(
            pretty_json(summary), encoding="utf-8"
        )
        return summary

    def _render_report(
        self,
        status: str,
        reason: str,
        verify_results: list[CommandResult],
        changed_files: list[str],
        patch_path: pathlib.Path,
    ) -> str:
        verify_section = "\n\n".join(
            f"### `{result.command}`\n\n```text\n{shorten(result.stdout or result.stderr or '(no output)', 12000)}\n```\n\nExit code: {result.exit_code}"
            for result in verify_results
        )
        plan_section = pretty_json(self.plans[-10:]) if self.plans else "[]"
        return textwrap.dedent(
            f"""
            # vLLM Coding Loop Report

            - Status: **{status}**
            - Reason: {reason}
            - Workdir: `{self.workspace.workdir}`
            - Workspace root: `{self.workspace.workspace_root}`
            - Patch: `{patch_path}`

            ## Task

            ```text
            {self.task_text}
            ```

            ## Verify commands

            {os.linesep.join(f'- `{cmd}`' for cmd in self.verify_commands)}

            ## Changed files

            {os.linesep.join(f'- `{path}`' for path in changed_files) if changed_files else '- None'}

            ## Recent plans

            ```json
            {plan_section}
            ```

            ## Verifier results

            {verify_section or 'No verifier output.'}
            """
        ).strip() + "\n"


def read_task(task_file: pathlib.Path | None, task_text: str | None) -> str:
    if task_file is not None:
        return task_file.read_text(encoding="utf-8")
    if task_text:
        return task_text
    raise ValueError("Provide either --task-file or --task-text")


def make_run_root(base_dir: pathlib.Path | None = None) -> pathlib.Path:
    base_dir = base_dir or (pathlib.Path.cwd() / ".agent_runs")
    run_id = time.strftime("run-%Y%m%d-%H%M%S")
    path = base_dir / run_id
    suffix = 0
    while path.exists():
        suffix += 1
        path = base_dir / f"{run_id}-{suffix}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Repository-aware autonomous coding loop using a vLLM OpenAI-compatible server."
    )
    parser.add_argument("--repo", required=True, help="Repository root or project directory")
    parser.add_argument("--config", help="Path to TOML config file")
    parser.add_argument("--task-file", help="Path to markdown or text task file")
    parser.add_argument("--task-text", help="Inline task description")
    parser.add_argument(
        "--runs-dir",
        help="Directory to store run artifacts (default: REPO/.agent_runs)",
    )
    parser.add_argument(
        "--print-default-config",
        action="store_true",
        help="Print a sample config and exit",
    )
    return parser.parse_args(argv)


def default_config_toml() -> str:
    return textwrap.dedent(
        """
        [vllm]
        base_url = "http://localhost:8000/v1"
        api_key = "dev"
        model = "Qwen/Qwen2.5-Coder-32B-Instruct"
        temperature = 0.2
        top_p = 0.95
        max_tokens = 4096
        request_timeout_sec = 600

        [agent]
        max_cycles = 6
        max_tool_steps = 40
        read_chunk_lines = 220
        max_file_bytes = 120000
        max_context_chars = 60000
        tool_command_timeout_sec = 120
        verify_timeout_sec = 900

        [repo]
        ignore_globs = [
          ".git/**",
          ".agent_runs/**",
          ".venv/**",
          "venv/**",
          "node_modules/**",
          "dist/**",
          "build/**",
          ".pytest_cache/**",
          "__pycache__/**",
        ]
        include_files = [
          "README.md",
          "AGENTS.md",
          "pyproject.toml",
          "requirements.txt",
          "package.json",
          "go.mod",
          "Cargo.toml",
          "Makefile",
        ]

        [commands]
        setup = []
        verify = ["python -m pytest -q"]
        allow_command_prefixes = null
        blocked_command_patterns = [
          "(^|[;&|])\\s*sudo\\b",
          "(^|[;&|])\\s*(shutdown|reboot|halt|poweroff)\\b",
          "(^|[;&|])\\s*rm\\s+-rf\\s+/",
          "(^|[;&|])\\s*(mkfs|fdisk|dd)\\b",
          "(^|[;&|])\\s*(curl|wget)\\b.*\\|\\s*(sh|bash|zsh)\\b",
          "(^|[;&|])\\s*git\\s+(push|reset\\s+--hard|clean\\s+-fdx|tag\\b)\\b",
        ]

        [commands.env]
        PYTHONUNBUFFERED = "1"
        """
    ).strip() + "\n"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.print_default_config:
        print(default_config_toml())
        return 0

    repo = pathlib.Path(args.repo).resolve()
    if not repo.exists() or not repo.is_dir():
        raise SystemExit(f"Repository path does not exist or is not a directory: {repo}")

    config = AgentConfig()
    if args.config:
        config = AgentConfig.from_toml(pathlib.Path(args.config))

    task_text = read_task(
        task_file=pathlib.Path(args.task_file) if args.task_file else None,
        task_text=args.task_text,
    )

    run_base = pathlib.Path(args.runs_dir) if args.runs_dir else (repo.parent / f'.{repo.name}_agent_runs')
    run_root = make_run_root(run_base)
    logger = JsonlLogger(run_root / "events.jsonl")
    logger.log(
        "run_started",
        {
            "repo": str(repo),
            "run_root": str(run_root),
            "config": dataclasses.asdict(config),
        },
    )

    workspace = Workspace.create(repo, run_root, config.ignore_globs, logger)
    agent = CodingAgent(config, workspace, task_text, run_root, logger)
    summary = agent.run()
    print(pretty_json(summary))
    return 0 if summary["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
