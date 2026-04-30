#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import json
import mimetypes
import os
import pathlib
import sys
import textwrap
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

import vllm_coding_loop as core
import vllm_frontend_loop as frontend


@dataclasses.dataclass
class PaperclipConfig:
    enabled: bool = True
    api_url: str | None = None
    api_key: str | None = None
    success_status: str = "done"
    failure_status: str = "blocked"
    upload_artifacts: bool = True
    upload_review_assets: bool = False
    update_plan_document: bool = True
    report_cost_events: bool = True
    cost_provider: str = "vllm"
    cost_cents: int = 0
    max_comment_chars: int = 12000
    issue_statuses: str = "todo,in_progress,in_review"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PaperclipConfig":
        return cls(
            enabled=bool(data.get("enabled", True)),
            api_url=str(data.get("api_url")).strip() if data.get("api_url") else None,
            api_key=str(data.get("api_key")).strip() if data.get("api_key") else None,
            success_status=str(data.get("success_status", cls.success_status)),
            failure_status=str(data.get("failure_status", cls.failure_status)),
            upload_artifacts=bool(data.get("upload_artifacts", cls.upload_artifacts)),
            upload_review_assets=bool(data.get("upload_review_assets", cls.upload_review_assets)),
            update_plan_document=bool(data.get("update_plan_document", cls.update_plan_document)),
            report_cost_events=bool(data.get("report_cost_events", cls.report_cost_events)),
            cost_provider=str(data.get("cost_provider", cls.cost_provider)),
            cost_cents=int(data.get("cost_cents", cls.cost_cents)),
            max_comment_chars=int(data.get("max_comment_chars", cls.max_comment_chars)),
            issue_statuses=str(data.get("issue_statuses", cls.issue_statuses)),
        )


def load_config(path: pathlib.Path | None) -> tuple[core.AgentConfig, frontend.FrontendConfig, frontend.VisionCriticConfig, PaperclipConfig]:
    base_config = core.AgentConfig()
    frontend_config = frontend.FrontendConfig()
    vision_config = frontend.VisionCriticConfig()
    paperclip_config = PaperclipConfig()
    if path is None:
        return base_config, frontend_config, vision_config, paperclip_config
    base_config, frontend_config, vision_config = frontend.load_extended_config(path)
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    paperclip_config = PaperclipConfig.from_dict(core.deep_get(data, "paperclip", default={}))
    return base_config, frontend_config, vision_config, paperclip_config


class CheckoutConflict(RuntimeError):
    pass


class CheckoutBlocked(RuntimeError):
    def __init__(self, message: str, unresolved_blocker_issue_ids: list[str] | None = None) -> None:
        super().__init__(message)
        self.unresolved_blocker_issue_ids = unresolved_blocker_issue_ids or []


def valid_uuid_or_none(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        return None


def resolve_current_run_id(api_url: str, api_key: str, company_id: str, agent_id: str) -> str | None:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "paperclip-vllm-frontend-agent/0.1",
    }

    def fetch(path: str) -> Any:
        req = urllib.request.Request(f"{api_url.rstrip('/')}{path}", headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else None

    def rows(payload: Any) -> list[Any]:
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in ("data", "runs", "items", "results"):
                if isinstance(payload.get(key), list):
                    return payload[key]
            if payload.get("id"):
                return [payload]
        return []

    candidates: list[Any] = []
    for path in (
        f"/companies/{company_id}/live-runs",
        f"/companies/{company_id}/heartbeat-runs?agentId={urllib.parse.quote(agent_id)}&limit=20",
    ):
        try:
            candidates.extend(rows(fetch(path)))
        except Exception:
            continue

    for run in candidates:
        if not isinstance(run, dict):
            continue
        if run.get("agentId") != agent_id:
            continue
        if str(run.get("status") or "").lower() in {"running", "queued"}:
            resolved = valid_uuid_or_none(str(run.get("id") or ""))
            if resolved:
                return resolved

    for run in candidates:
        if isinstance(run, dict) and run.get("agentId") == agent_id:
            resolved = valid_uuid_or_none(str(run.get("id") or ""))
            if resolved:
                return resolved
    return None


class PaperclipClient:
    def __init__(self, api_url: str, api_key: str, run_id: str | None = None) -> None:
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.run_id = run_id

    def _headers(self, mutate: bool = False, extra_headers: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "User-Agent": "paperclip-vllm-frontend-agent/0.1",
            "Authorization": f"Bearer {self.api_key}",
        }
        if mutate:
            headers["Content-Type"] = "application/json"
        if self.run_id and mutate:
            headers["X-Paperclip-Run-Id"] = self.run_id
        if extra_headers:
            headers.update(extra_headers)
        return headers

    def request_json(
        self,
        method: str,
        path: str,
        payload: Any | None = None,
        mutate: bool | None = None,
        expected_statuses: tuple[int, ...] = (200, 201),
    ) -> Any:
        is_mutate = mutate if mutate is not None else method.upper() in {"POST", "PATCH", "PUT", "DELETE"}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url=f"{self.api_url}{path}",
            data=body,
            headers=self._headers(mutate=is_mutate),
            method=method.upper(),
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8")
                if expected_statuses and getattr(resp, "status", 200) not in expected_statuses:
                    raise RuntimeError(f"Unexpected HTTP status {getattr(resp, 'status', 200)} calling {path}: {raw}")
                if not raw.strip():
                    return None
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            if exc.code == 422 and path.endswith("/checkout"):
                try:
                    error_payload = json.loads(detail) if detail.strip() else {}
                except json.JSONDecodeError:
                    error_payload = {}
                if not isinstance(error_payload, dict):
                    error_payload = {}
                message = str(error_payload.get("error") or detail or exc.reason)
                details = error_payload.get("details") if isinstance(error_payload, dict) else {}
                unresolved = []
                if isinstance(details, dict):
                    unresolved = [
                        str(item)
                        for item in details.get("unresolvedBlockerIssueIds", [])
                        if str(item).strip()
                    ]
                if "blocked by unresolved blockers" in message.lower() or unresolved:
                    raise CheckoutBlocked(message, unresolved) from exc
            if exc.code == 409 and path.endswith("/checkout"):
                raise CheckoutConflict(detail or exc.reason) from exc
            raise RuntimeError(f"HTTP {exc.code} calling {path}: {detail or exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Failed to reach Paperclip API {self.api_url}: {exc}") from exc

    def upload_attachment(self, company_id: str, issue_id: str, file_path: pathlib.Path) -> Any:
        boundary = f"paperclip-{uuid.uuid4().hex}"
        mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        file_bytes = file_path.read_bytes()
        parts = [
            f"--{boundary}\r\n".encode("utf-8"),
            (
                "Content-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\n" % file_path.name
            ).encode("utf-8"),
            ("Content-Type: %s\r\n\r\n" % mime_type).encode("utf-8"),
            file_bytes,
            b"\r\n",
            f"--{boundary}--\r\n".encode("utf-8"),
        ]
        body = b"".join(parts)
        headers = self._headers(mutate=False, extra_headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        })
        if self.run_id:
            headers["X-Paperclip-Run-Id"] = self.run_id
        req = urllib.request.Request(
            url=f"{self.api_url}/companies/{company_id}/issues/{issue_id}/attachments",
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
                return json.loads(raw) if raw.strip() else None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"HTTP {exc.code} uploading {file_path.name}: {detail or exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Failed to upload attachment {file_path.name}: {exc}") from exc


@dataclasses.dataclass
class UsageEntry:
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


class UsageTracker:
    def __init__(self) -> None:
        self.entries: dict[tuple[str, str], UsageEntry] = {}

    def add(self, provider: str, model: str, input_tokens: int, output_tokens: int) -> None:
        key = (provider, model)
        current = self.entries.get(key)
        if current is None:
            current = UsageEntry(provider=provider, model=model)
            self.entries[key] = current
        current.input_tokens += max(0, int(input_tokens))
        current.output_tokens += max(0, int(output_tokens))

    def snapshot(self) -> list[dict[str, Any]]:
        rows = []
        for entry in self.entries.values():
            rows.append(
                {
                    "provider": entry.provider,
                    "model": entry.model,
                    "inputTokens": entry.input_tokens,
                    "outputTokens": entry.output_tokens,
                }
            )
        return rows


_USAGE_PATCHED = False
_ORIGINAL_REQUEST = None


def install_usage_tracking(tracker: UsageTracker, provider: str) -> None:
    global _USAGE_PATCHED, _ORIGINAL_REQUEST
    if _USAGE_PATCHED:
        return
    _ORIGINAL_REQUEST = core.VLLMClient._request

    def wrapped(self: core.VLLMClient, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = _ORIGINAL_REQUEST(self, method, path, payload)
        if method.upper() == "POST" and path == "/chat/completions" and isinstance(data, dict):
            usage = data.get("usage") or {}
            prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
            completion_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
            model_name = ""
            if isinstance(payload, dict) and payload.get("model"):
                model_name = str(payload["model"])
            if not model_name:
                model_name = getattr(self, "model", "") or "unknown"
            tracker.add(provider=provider, model=model_name, input_tokens=prompt_tokens, output_tokens=completion_tokens)
        return data

    core.VLLMClient._request = wrapped  # type: ignore[assignment]
    _USAGE_PATCHED = True


def maybe_restore_usage_tracking() -> None:
    global _USAGE_PATCHED, _ORIGINAL_REQUEST
    if _USAGE_PATCHED and _ORIGINAL_REQUEST is not None:
        core.VLLMClient._request = _ORIGINAL_REQUEST  # type: ignore[assignment]
        _USAGE_PATCHED = False
        _ORIGINAL_REQUEST = None


@dataclasses.dataclass
class HeartbeatContext:
    api_url: str
    api_key: str
    company_id: str
    agent_id: str | None
    run_id: str | None
    issue_id: str | None
    wake_reason: str | None
    wake_comment_id: str | None
    approval_id: str | None
    approval_status: str | None


def redacted_context(context: HeartbeatContext) -> dict[str, Any]:
    payload = dataclasses.asdict(context)
    if payload.get("api_key"):
        payload["api_key"] = "<redacted>"
    return payload


@dataclasses.dataclass
class PaperclipRunArtifacts:
    uploaded: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    plan_document: dict[str, Any] | None = None
    cost_events: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    final_issue_update: dict[str, Any] | None = None
    picked_issue_id: str | None = None


class PaperclipRunner:
    def __init__(
        self,
        repo: pathlib.Path,
        config_path: pathlib.Path | None,
        runs_dir: pathlib.Path | None,
        context: HeartbeatContext,
    ) -> None:
        self.repo = repo.resolve()
        self.config_path = config_path
        self.runs_dir = runs_dir
        self.context = context
        self.base_config, self.frontend_config, self.vision_config, self.paperclip_config = load_config(config_path)
        self.client = PaperclipClient(context.api_url, context.api_key, run_id=context.run_id)
        self.artifacts = PaperclipRunArtifacts()

    def run(self) -> dict[str, Any]:
        me = self.client.request_json("GET", "/agents/me", mutate=False, expected_statuses=(200,))
        company_id = str(first_non_empty(me.get("companyId"), self.context.company_id))
        agent_id = str(first_non_empty(me.get("id"), self.context.agent_id))
        self.context.company_id = company_id
        self.context.agent_id = agent_id
        issue_id = self._pick_issue_id(company_id=company_id, agent_id=agent_id)
        self.artifacts.picked_issue_id = issue_id
        if not issue_id:
            return {
                "status": "idle",
                "reason": "No Paperclip issue selected for this heartbeat",
                "company_id": company_id,
                "agent_id": agent_id,
            }

        issue_before = self.client.request_json("GET", f"/issues/{issue_id}", mutate=False, expected_statuses=(200,))
        checkout_statuses = ["todo", "backlog", "blocked", "in_review"]
        current_status = str(issue_before.get("status") or "")
        if current_status == "in_progress":
            checkout_statuses.append("in_progress")
        self.client.request_json(
            "POST",
            f"/issues/{issue_id}/checkout",
            {
                "agentId": agent_id,
                "expectedStatuses": dedupe_strings(checkout_statuses),
            },
            mutate=True,
            expected_statuses=(200, 201),
        )
        issue = self.client.request_json("GET", f"/issues/{issue_id}", mutate=False, expected_statuses=(200,))
        comments = self.client.request_json("GET", f"/issues/{issue_id}/comments", mutate=False, expected_statuses=(200,)) or []
        approval_context = self._load_approval_context()
        task_text = self._build_task_text(issue=issue, comments=comments, approval_context=approval_context)
        summary = self._run_agent(task_text)
        self._sync_plan_document(issue_id, summary)
        self._report_costs(company_id=company_id, agent_id=agent_id, summary=summary)
        self._upload_artifacts(company_id=company_id, issue_id=issue_id, summary=summary)
        final_status = self._map_issue_status(summary.get("status"))
        final_comment = self._build_final_comment(summary=summary)
        final_issue_update = self.client.request_json(
            "PATCH",
            f"/issues/{issue_id}",
            {"status": final_status, "comment": final_comment},
            mutate=True,
            expected_statuses=(200, 201),
        )
        self.artifacts.final_issue_update = final_issue_update
        result = {
            "status": "ok",
            "paperclip_issue_id": issue_id,
            "paperclip_issue_status": final_status,
            "agent_status": summary.get("status"),
            "summary": summary,
            "paperclip": dataclasses.asdict(self.artifacts),
        }
        summary_path = pathlib.Path(summary["workspace_root"]).parent / "paperclip_result.json"
        summary_path.write_text(core.pretty_json(result), encoding="utf-8")
        return result

    def _pick_issue_id(self, company_id: str, agent_id: str) -> str | None:
        if self.context.issue_id:
            return self.context.issue_id
        query = urllib.parse.urlencode(
            {
                "assigneeAgentId": agent_id,
                "status": self.paperclip_config.issue_statuses,
            }
        )
        issues = self.client.request_json(
            "GET",
            f"/companies/{company_id}/issues?{query}",
            mutate=False,
            expected_statuses=(200,),
        ) or []
        if not isinstance(issues, list) or not issues:
            return None
        wake_reason = (self.context.wake_reason or "").lower()
        status_rank = {
            "in_progress": 0,
            "in_review": 1 if "comment" in wake_reason else 2,
            "todo": 3,
            "backlog": 4,
            "blocked": 8,
        }
        ranked = sorted(
            issues,
            key=lambda item: status_rank.get(str(item.get("status") or ""), 99),
        )
        chosen = ranked[0]
        return str(chosen.get("id")) if chosen.get("id") else None

    def _load_approval_context(self) -> dict[str, Any] | None:
        if not self.context.approval_id:
            return None
        approval = self.client.request_json(
            "GET",
            f"/approvals/{self.context.approval_id}",
            mutate=False,
            expected_statuses=(200,),
        )
        linked_issues = self.client.request_json(
            "GET",
            f"/approvals/{self.context.approval_id}/issues",
            mutate=False,
            expected_statuses=(200,),
        )
        return {
            "approval": approval,
            "issues": linked_issues,
        }

    def _build_task_text(self, issue: dict[str, Any], comments: list[Any], approval_context: dict[str, Any] | None) -> str:
        lines: list[str] = []
        lines.append("Paperclip issue context")
        lines.append("========================")
        lines.append(f"Issue ID: {issue.get('id', '')}")
        identifier = first_non_empty(issue.get("identifier"), issue.get("shortId"), issue.get("key"))
        if identifier:
            lines.append(f"Identifier: {identifier}")
        lines.append(f"Title: {issue.get('title', '')}")
        lines.append(f"Status: {issue.get('status', '')}")
        priority = issue.get("priority")
        if priority:
            lines.append(f"Priority: {priority}")
        if self.context.wake_reason:
            lines.append(f"Wake reason: {self.context.wake_reason}")
        if self.context.wake_comment_id:
            lines.append(f"Wake comment ID: {self.context.wake_comment_id}")
        if issue.get("project"):
            project = issue.get("project") or {}
            lines.append(f"Project: {project.get('name') or project.get('title') or project.get('id')}")
        if issue.get("goal"):
            goal = issue.get("goal") or {}
            goal_name = goal.get("name") or goal.get("title") or goal.get("id")
            if goal_name:
                lines.append(f"Goal: {goal_name}")
        ancestors = issue.get("ancestors") or []
        if isinstance(ancestors, list) and ancestors:
            lines.append("")
            lines.append("Ancestors:")
            for ancestor in ancestors:
                if not isinstance(ancestor, dict):
                    continue
                title = ancestor.get("title") or ancestor.get("name") or ancestor.get("id")
                aid = first_non_empty(ancestor.get("identifier"), ancestor.get("shortId"), ancestor.get("id"))
                lines.append(f"- {aid}: {title}")
        description = issue.get("description") or ""
        if description:
            lines.append("")
            lines.append("Description:")
            lines.append(str(description))
        plan_document = issue.get("planDocument") or issue.get("legacyPlanDocument")
        if plan_document:
            lines.append("")
            lines.append("Existing plan document:")
            lines.append(str(plan_document))
        if approval_context:
            lines.append("")
            lines.append("Approval context:")
            lines.append(core.pretty_json(approval_context))
        if comments:
            lines.append("")
            lines.append("Recent issue comments:")
            selected = comments[-20:] if len(comments) > 20 else comments
            for comment in selected:
                if not isinstance(comment, dict):
                    continue
                prefix = "-"
                comment_id = str(comment.get("id") or "")
                if self.context.wake_comment_id and comment_id == self.context.wake_comment_id:
                    prefix = "*"
                author = first_non_empty(comment.get("authorName"), comment.get("author"), comment.get("agentName"), "unknown")
                body = str(comment.get("body") or "").strip()
                body = core.shorten(body, 2500)
                lines.append(f"{prefix} [{comment_id}] {author}")
                lines.append(body)
                lines.append("")
        lines.append("Operational rules:")
        lines.append("- You are running under Paperclip. Keep work tightly scoped to this issue.")
        lines.append("- The external verifier remains the source of truth for completion.")
        lines.append("- Leave code and UI in a state that matches the issue goal and current project direction.")
        lines.append("- Prefer coherent changes over broad refactors.")
        return "\n".join(lines).strip() + "\n"

    def _run_agent(self, task_text: str) -> dict[str, Any]:
        if not self.repo.exists() or not self.repo.is_dir():
            raise RuntimeError(f"Repository path does not exist or is not a directory: {self.repo}")
        run_base = self.runs_dir if self.runs_dir is not None else self._default_run_base()
        run_root = core.make_run_root(run_base)
        logger = core.JsonlLogger(run_root / "events.jsonl")
        logger.log(
            "paperclip_run_started",
            {
                "repo": str(self.repo),
                "run_root": str(run_root),
                "paperclip_context": redacted_context(self.context),
                "agent_config": dataclasses.asdict(self.base_config),
                "frontend_config": dataclasses.asdict(self.frontend_config),
                "vision_config": dataclasses.asdict(self.vision_config),
                "paperclip_config": dataclasses.asdict(self.paperclip_config),
            },
        )
        usage_tracker = UsageTracker()
        install_usage_tracking(usage_tracker, provider=self.paperclip_config.cost_provider)
        try:
            workspace = core.Workspace.create(self.repo, run_root, self.base_config.ignore_globs, logger)
            agent = frontend.VisualCodingAgent(
                config=self.base_config,
                workspace=workspace,
                task_text=task_text,
                run_root=run_root,
                logger=logger,
                frontend_config=self.frontend_config,
                vision_config=self.vision_config,
            )
            summary = agent.run()
            summary["paperclip_context"] = redacted_context(self.context)
            summary["usage"] = usage_tracker.snapshot()
            summary_path = pathlib.Path(summary["workspace_root"]).parent / "summary.json"
            summary_path.write_text(core.pretty_json(summary), encoding="utf-8")
            return summary
        finally:
            maybe_restore_usage_tracking()

    def _default_run_base(self) -> pathlib.Path:
        configured = os.environ.get("PAPERCLIP_AGENT_RUNS_DIR")
        if configured:
            return pathlib.Path(configured)
        agent_id = self.context.agent_id or "default"
        paperclip_workspaces = pathlib.Path("/paperclip/instances/default/workspaces")
        if paperclip_workspaces.exists():
            return paperclip_workspaces / agent_id / "paperclip-agent-runs"
        return self.repo.parent / f".{self.repo.name}_paperclip_agent_runs"

    def _sync_plan_document(self, issue_id: str, summary: dict[str, Any]) -> None:
        if not self.paperclip_config.update_plan_document:
            return
        plans = summary.get("plans") or []
        if not isinstance(plans, list) or not plans:
            return
        latest = plans[-1] if isinstance(plans[-1], dict) else None
        if not latest:
            return
        body = self._render_plan_document(summary)
        doc_path = f"/issues/{issue_id}/documents/plan"
        current = None
        try:
            current = self.client.request_json("GET", doc_path, mutate=False, expected_statuses=(200,))
        except Exception:
            current = None
        payload: dict[str, Any] = {
            "title": "Implementation plan",
            "format": "markdown",
            "body": body,
        }
        if isinstance(current, dict):
            revision_id = first_non_empty(
                current.get("currentRevisionId"),
                current.get("latestRevisionId"),
                current.get("revisionId"),
                core.deep_get(current, "latestRevision", "id", default=None),
                core.deep_get(current, "revision", "id", default=None),
            )
            if revision_id:
                payload["baseRevisionId"] = revision_id
        try:
            response = self.client.request_json("PUT", doc_path, payload, mutate=True, expected_statuses=(200, 201))
            self.artifacts.plan_document = response if isinstance(response, dict) else {"ok": True}
        except Exception as exc:
            self.artifacts.plan_document = {"ok": False, "error": str(exc)}

    def _render_plan_document(self, summary: dict[str, Any]) -> str:
        plans = summary.get("plans") or []
        latest = plans[-1] if plans else {}
        summary_line = str(latest.get("summary") or summary.get("reason") or "")
        steps = [str(x) for x in core.coerce_list(latest.get("steps")) if str(x).strip()]
        target_files = [str(x) for x in core.coerce_list(latest.get("target_files")) if str(x).strip()]
        risks = [str(x) for x in core.coerce_list(latest.get("risks")) if str(x).strip()]
        changed_files = [str(x) for x in core.coerce_list(summary.get("changed_files")) if str(x).strip()]
        lines = [
            "# Implementation plan",
            "",
            f"Updated: {core.now_iso()}",
            "",
            "## Summary",
            "",
            summary_line or "No summary recorded.",
            "",
            "## Steps",
        ]
        if steps:
            for idx, step in enumerate(steps, start=1):
                lines.append(f"{idx}. {step}")
        else:
            lines.append("1. No explicit steps recorded.")
        lines.extend(["", "## Target files"])
        if target_files:
            for path in target_files:
                lines.append(f"- `{path}`")
        else:
            lines.append("- None recorded")
        lines.extend(["", "## Risks"])
        if risks:
            for risk in risks:
                lines.append(f"- {risk}")
        else:
            lines.append("- None recorded")
        lines.extend(["", "## Changed files"])
        if changed_files:
            for path in changed_files:
                lines.append(f"- `{path}`")
        else:
            lines.append("- None")
        return "\n".join(lines).strip() + "\n"

    def _report_costs(self, company_id: str, agent_id: str, summary: dict[str, Any]) -> None:
        if not self.paperclip_config.report_cost_events:
            return
        usage_rows = summary.get("usage") or []
        for row in usage_rows:
            if not isinstance(row, dict):
                continue
            payload = {
                "agentId": agent_id,
                "provider": str(row.get("provider") or self.paperclip_config.cost_provider),
                "model": str(row.get("model") or "unknown"),
                "inputTokens": int(row.get("inputTokens") or 0),
                "outputTokens": int(row.get("outputTokens") or 0),
                "costCents": int(self.paperclip_config.cost_cents),
            }
            try:
                response = self.client.request_json(
                    "POST",
                    f"/companies/{company_id}/cost-events",
                    payload,
                    mutate=True,
                    expected_statuses=(200, 201),
                )
                self.artifacts.cost_events.append(response if isinstance(response, dict) else payload)
            except Exception as exc:
                self.artifacts.cost_events.append({"ok": False, "payload": payload, "error": str(exc)})

    def _upload_artifacts(self, company_id: str, issue_id: str, summary: dict[str, Any]) -> None:
        if not self.paperclip_config.upload_artifacts:
            return
        for path in collect_artifact_paths(summary, include_review_assets=self.paperclip_config.upload_review_assets):
            try:
                response = self.client.upload_attachment(company_id=company_id, issue_id=issue_id, file_path=path)
                payload = response if isinstance(response, dict) else {"file": path.name}
                payload.setdefault("local_path", str(path))
                self.artifacts.uploaded.append(payload)
            except Exception as exc:
                self.artifacts.uploaded.append({"ok": False, "file": path.name, "local_path": str(path), "error": str(exc)})

    def _map_issue_status(self, agent_status: Any) -> str:
        if str(agent_status) == "success":
            return self.paperclip_config.success_status
        if str(agent_status) == "blocked":
            return "blocked"
        return self.paperclip_config.failure_status

    def _build_final_comment(self, summary: dict[str, Any]) -> str:
        lines = [
            "## Update",
            "",
        ]
        status = str(summary.get("status") or "unknown")
        reason = str(summary.get("reason") or "")
        if status == "success":
            lines.append("Completed the local vLLM frontend heartbeat and passed the configured verifier.")
        elif status == "blocked":
            lines.append("Heartbeat ended in a blocked state.")
        else:
            lines.append("Heartbeat ended without a clean verifier pass.")
        lines.extend(
            [
                "",
                f"- Agent result: `{status}`",
                f"- Issue status set to: `{self._map_issue_status(status)}`",
                f"- Reason: {reason}",
            ]
        )
        wake_reason = self.context.wake_reason or "manual"
        lines.append(f"- Wake reason: `{wake_reason}`")
        changed = [str(x) for x in core.coerce_list(summary.get("changed_files")) if str(x).strip()]
        if changed:
            lines.append("- Changed files:")
            for path in changed[:20]:
                lines.append(f"  - `{path}`")
            if len(changed) > 20:
                lines.append(f"  - ... and {len(changed) - 20} more")
        else:
            lines.append("- Changed files: none")
        verify_results = summary.get("verify_results") or []
        if verify_results:
            lines.append("- Verify results:")
            for result in verify_results[:10]:
                if not isinstance(result, dict):
                    continue
                command = str(result.get("command") or "")
                exit_code = result.get("exit_code")
                if "ok" in result:
                    ok = bool(result.get("ok"))
                else:
                    ok = (exit_code == 0) and (not result.get("timed_out")) and (not result.get("error"))
                marker = "OK" if ok else "FAIL"
                lines.append(f"  - `{command}` -> {marker} (exit {exit_code})")
        uploaded_ok = [item for item in self.artifacts.uploaded if isinstance(item, dict) and item.get("ok", True) is not False]
        if uploaded_ok:
            lines.append("- Uploaded artifacts:")
            for item in uploaded_ok[:12]:
                name = pathlib.Path(str(item.get("local_path") or item.get("filename") or item.get("file") or "artifact")).name
                lines.append(f"  - `{name}`")
        failed_uploads = [item for item in self.artifacts.uploaded if isinstance(item, dict) and item.get("ok") is False]
        if failed_uploads:
            lines.append("- Artifact upload issues:")
            for item in failed_uploads[:8]:
                lines.append(f"  - `{item.get('file')}`: {item.get('error')}")
        lines.append("- Next action: review the attached report and patch, then leave a comment on this issue if another heartbeat should refine the UI or behavior.")
        body = "\n".join(lines).strip() + "\n"
        return core.shorten(body, self.paperclip_config.max_comment_chars)


def collect_artifact_paths(summary: dict[str, Any], include_review_assets: bool) -> list[pathlib.Path]:
    paths: list[pathlib.Path] = []
    for key in ["patch_path", "report_path"]:
        value = summary.get(key)
        if value:
            p = pathlib.Path(str(value))
            if p.exists() and p.is_file():
                paths.append(p)
    summary_path = pathlib.Path(summary.get("workspace_root", ".")).parent / "summary.json"
    if summary_path.exists() and summary_path.is_file():
        paths.append(summary_path)
    frontend_payload = summary.get("frontend") or {}
    if include_review_assets and isinstance(frontend_payload, dict):
        for review in frontend_payload.get("reviews") or []:
            if not isinstance(review, dict):
                continue
            for observation in review.get("observations") or []:
                if not isinstance(observation, dict):
                    continue
                artifacts = observation.get("artifacts") or {}
                for key in ["screenshot_path", "trace_path"]:
                    raw = artifacts.get(key)
                    if raw:
                        p = pathlib.Path(str(raw))
                        if p.exists() and p.is_file():
                            paths.append(p)
    deduped: list[pathlib.Path] = []
    seen: set[str] = set()
    for path in paths:
        resolved = str(path.resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(path)
    return deduped


def first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            if value.strip():
                return value
            continue
        return value
    return None


def dedupe_strings(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Paperclip process-adapter bridge for the vLLM frontend coding loop."
    )
    parser.add_argument("--repo", help="Repository root. Defaults to the current working directory.")
    parser.add_argument("--config", help="Path to the agent TOML config file.")
    parser.add_argument("--runs-dir", help="Optional directory for run artifacts.")
    parser.add_argument("--issue-id", help="Paperclip issue ID. Defaults to PAPERCLIP_TASK_ID.")
    parser.add_argument("--company-id", help="Paperclip company ID. Defaults to PAPERCLIP_COMPANY_ID.")
    parser.add_argument("--agent-id", help="Paperclip agent ID. Defaults to PAPERCLIP_AGENT_ID.")
    parser.add_argument("--run-id", help="Paperclip run ID. Defaults to PAPERCLIP_RUN_ID.")
    parser.add_argument("--api-url", help="Paperclip API base URL. Defaults to PAPERCLIP_API_URL.")
    parser.add_argument("--api-key", help="Paperclip API key or run JWT. Defaults to PAPERCLIP_API_KEY.")
    parser.add_argument("--wake-reason", help="Override PAPERCLIP_WAKE_REASON.")
    parser.add_argument("--wake-comment-id", help="Override PAPERCLIP_WAKE_COMMENT_ID.")
    parser.add_argument("--apply-on-success", action="store_true", help="Apply the verified final patch back to the source Git repository.")
    parser.add_argument("--print-example-process-adapter", action="store_true", help="Print a sample Paperclip process adapter config and exit.")
    return parser.parse_args(argv)


def example_process_adapter() -> str:
    return textwrap.dedent(
        """
        {
          "adapterType": "process",
          "adapterConfig": {
            "command": "python3 /absolute/path/to/paperclip_frontend_agent.py --config /absolute/path/to/paperclip_agent.frontend.toml",
            "cwd": "/absolute/path/to/your/repo",
            "timeoutSec": 7200,
            "env": {
              "PYTHONUNBUFFERED": "1"
            }
          }
        }
        """
    ).strip() + "\n"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.print_example_process_adapter:
        print(example_process_adapter())
        return 0

    repo = pathlib.Path(args.repo).resolve() if args.repo else pathlib.Path.cwd().resolve()
    config_path = pathlib.Path(args.config).resolve() if args.config else None
    runs_dir = pathlib.Path(args.runs_dir).resolve() if args.runs_dir else None
    configured_paperclip = PaperclipConfig()
    if config_path is not None:
        try:
            _, _, _, configured_paperclip = load_config(config_path)
        except Exception:
            configured_paperclip = PaperclipConfig()

    api_url = args.api_url or os.environ.get("PAPERCLIP_API_URL") or configured_paperclip.api_url
    api_key = args.api_key or os.environ.get("PAPERCLIP_API_KEY") or configured_paperclip.api_key
    company_id = args.company_id or os.environ.get("PAPERCLIP_COMPANY_ID")
    raw_run_id = (
        args.run_id
        or os.environ.get("PAPERCLIP_RUN_ID")
        or os.environ.get("PAPERCLIP_HEARTBEAT_RUN_ID")
        or os.environ.get("PAPERCLIP_HEARTBEAT_RUN_UUID")
    )
    run_id = valid_uuid_or_none(raw_run_id)
    issue_id = args.issue_id or os.environ.get("PAPERCLIP_TASK_ID")
    agent_id = args.agent_id or os.environ.get("PAPERCLIP_AGENT_ID")
    if run_id is None and api_url and api_key and company_id and agent_id:
        run_id = resolve_current_run_id(api_url, api_key, company_id, agent_id)
    wake_reason = args.wake_reason or os.environ.get("PAPERCLIP_WAKE_REASON")
    wake_comment_id = args.wake_comment_id or os.environ.get("PAPERCLIP_WAKE_COMMENT_ID")
    approval_id = os.environ.get("PAPERCLIP_APPROVAL_ID")
    approval_status = os.environ.get("PAPERCLIP_APPROVAL_STATUS")

    if not api_url:
        raise SystemExit("Paperclip API URL is required via --api-url or PAPERCLIP_API_URL")
    if not api_key:
        raise SystemExit("Paperclip API key is required via --api-key or PAPERCLIP_API_KEY")
    if not company_id:
        raise SystemExit("Paperclip company ID is required via --company-id or PAPERCLIP_COMPANY_ID")

    context = HeartbeatContext(
        api_url=api_url,
        api_key=api_key,
        company_id=company_id,
        agent_id=agent_id,
        run_id=run_id,
        issue_id=issue_id,
        wake_reason=wake_reason,
        wake_comment_id=wake_comment_id,
        approval_id=approval_id,
        approval_status=approval_status,
    )

    try:
        runner = PaperclipRunner(repo=repo, config_path=config_path, runs_dir=runs_dir, context=context)
        if args.apply_on_success:
            runner.base_config.apply_patch_on_success = True
        result = runner.run()
        print(core.pretty_json(result))
        return 0
    except CheckoutConflict as exc:
        result = {
            "status": "skipped",
            "reason": f"Checkout conflict: {exc}",
            "issue_id": context.issue_id,
            "run_id": context.run_id,
        }
        print(core.pretty_json(result))
        return 0
    except CheckoutBlocked as exc:
        result = {
            "status": "skipped",
            "reason": f"Checkout blocked: {exc}",
            "issue_id": context.issue_id,
            "run_id": context.run_id,
            "unresolved_blocker_issue_ids": exc.unresolved_blocker_issue_ids,
        }
        print(core.pretty_json(result))
        return 0
    except Exception as exc:
        error_payload = {
            "status": "error",
            "error": str(exc),
            "paperclip_context": redacted_context(context),
        }
        print(core.pretty_json(error_payload))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
