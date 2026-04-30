#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import contextlib
import dataclasses
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
import textwrap
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import vllm_coding_loop as core

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover - optional import at runtime
    PlaywrightError = RuntimeError
    PlaywrightTimeoutError = TimeoutError
    sync_playwright = None


DEFAULT_DESIGN_BRIEF = textwrap.dedent(
    """
    Build a frontend that is clear, production-ready, and visually intentional.
    Maintain strong information hierarchy, obvious primary actions, consistent spacing,
    readable typography, restrained motion, and clear contrast. Avoid generic placeholder
    aesthetics, muddy CTA emphasis, inconsistent radii/shadows, and layouts that look
    like untouched starter-template output.
    """
).strip()

FRONTEND_PROMPT_APPENDIX = textwrap.dedent(
    """
    Frontend-specific guidance:
    - When the task touches the UI, use review_frontend before and after significant edits.
    - Use browser_actions for focused interactions such as filling forms or clicking tabs.
    - Prefer semantic locators (role, label, placeholder, text) over brittle CSS selectors.
    - Treat browser observations as debugging evidence, but remember the external verifier is still the source of truth.
    - Improve both correctness and polish: hierarchy, spacing, typography, contrast, CTA clarity, and state feedback.
    """
).strip()

DEFAULT_BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-background-networking",
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    "--disable-features=Translate,OptimizationHints,MediaRouter",
]

SEMANTIC_SUMMARY_JS = r"""
() => {
  const clip = (value, n = 140) => {
    if (!value) return "";
    return String(value).replace(/\s+/g, " ").trim().slice(0, n);
  };
  const styleOf = (el) => {
    const s = window.getComputedStyle(el);
    return {
      display: s.display,
      position: s.position,
      fontFamily: clip(s.fontFamily, 160),
      fontSize: s.fontSize,
      fontWeight: s.fontWeight,
      lineHeight: s.lineHeight,
      color: s.color,
      backgroundColor: s.backgroundColor,
      borderRadius: s.borderRadius,
      borderColor: s.borderColor,
      boxShadow: clip(s.boxShadow, 120),
      letterSpacing: s.letterSpacing,
    };
  };
  const textOf = (el) => clip(
    el.innerText ||
    el.getAttribute("aria-label") ||
    el.getAttribute("placeholder") ||
    el.getAttribute("value") ||
    el.textContent ||
    "",
    200,
  );
  const labelFor = (el) => {
    const id = el.id;
    if (!id) return "";
    const label = document.querySelector(`label[for="${CSS.escape(id)}"]`);
    return label ? clip(label.innerText || label.textContent || "", 120) : "";
  };
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const locatorHintFor = (el, fallbackText) => {
    const aria = clip(el.getAttribute("aria-label") || "", 80);
    const placeholder = clip(el.getAttribute("placeholder") || "", 80);
    const label = labelFor(el);
    const text = clip(fallbackText || textOf(el), 80);
    if (label) return { strategy: "label", value: label };
    if (aria) return { strategy: "label", value: aria };
    if (placeholder) return { strategy: "placeholder", value: placeholder };
    if (text) return { strategy: "text", value: text };
    if (el.id) return { strategy: "selector", value: `#${el.id}` };
    if (el.name) return { strategy: "selector", value: `[name="${el.name}"]` };
    return { strategy: "selector", value: el.tagName.toLowerCase() };
  };
  const take = (selector, limit, mapper) => {
    return Array.from(document.querySelectorAll(selector))
      .filter(visible)
      .slice(0, limit)
      .map(mapper);
  };
  const headings = take("h1,h2,h3", 12, (el) => ({
    tag: el.tagName.toLowerCase(),
    text: textOf(el),
    css: styleOf(el),
  }));
  const buttons = take('button,[role="button"],input[type="submit"],input[type="button"]', 16, (el) => ({
    tag: el.tagName.toLowerCase(),
    text: textOf(el),
    disabled: !!el.disabled,
    type: el.getAttribute("type") || "",
    locator_hint: locatorHintFor(el, textOf(el)),
    css: styleOf(el),
  }));
  const links = take("a[href]", 16, (el) => ({
    text: textOf(el),
    href: clip(el.getAttribute("href") || "", 160),
    locator_hint: locatorHintFor(el, textOf(el)),
  }));
  const inputs = take("input,textarea,select", 24, (el) => ({
    tag: el.tagName.toLowerCase(),
    type: el.getAttribute("type") || "",
    name: el.getAttribute("name") || "",
    id: el.id || "",
    placeholder: el.getAttribute("placeholder") || "",
    aria_label: el.getAttribute("aria-label") || "",
    label_text: labelFor(el),
    locator_hint: locatorHintFor(el, labelFor(el) || el.getAttribute("placeholder") || textOf(el)),
  }));
  const forms = take("form", 12, (form) => ({
    action: form.getAttribute("action") || "",
    method: form.getAttribute("method") || "get",
    field_count: form.querySelectorAll("input,textarea,select").length,
    submit_count: form.querySelectorAll('button,[role="button"],input[type="submit"],input[type="button"]').length,
  }));
  const landmarks = take("header,nav,main,footer,aside,section", 16, (el) => ({
    tag: el.tagName.toLowerCase(),
    aria_label: el.getAttribute("aria-label") || "",
    text: textOf(el),
  }));
  const body = document.body;
  return {
    title: document.title || "",
    body_class: body ? body.className || "" : "",
    text_sample: body ? clip(body.innerText || body.textContent || "", 2400) : "",
    headings,
    buttons,
    links,
    inputs,
    forms,
    landmarks,
  };
}
"""

STYLE_SUMMARY_JS = r"""
() => {
  const clip = (value, n = 140) => {
    if (!value) return "";
    return String(value).replace(/\s+/g, " ").trim().slice(0, n);
  };
  const body = document.body;
  const html = document.documentElement;
  const bodyStyle = body ? window.getComputedStyle(body) : null;
  const visibleButtons = Array.from(document.querySelectorAll('button,[role="button"],input[type="submit"],input[type="button"]'))
    .filter((el) => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    });
  const primary = visibleButtons[0] || null;
  const primaryStyle = primary ? window.getComputedStyle(primary) : null;
  return {
    viewport: {
      width: window.innerWidth,
      height: window.innerHeight,
      scroll_height: html ? html.scrollHeight : 0,
    },
    body: bodyStyle ? {
      font_family: clip(bodyStyle.fontFamily, 160),
      font_size: bodyStyle.fontSize,
      line_height: bodyStyle.lineHeight,
      color: bodyStyle.color,
      background_color: bodyStyle.backgroundColor,
      max_width: bodyStyle.maxWidth,
    } : null,
    primary_button: primaryStyle ? {
      text: clip(primary.innerText || primary.value || primary.getAttribute("aria-label") || "", 120),
      color: primaryStyle.color,
      background_color: primaryStyle.backgroundColor,
      border_color: primaryStyle.borderColor,
      border_radius: primaryStyle.borderRadius,
      font_family: clip(primaryStyle.fontFamily, 160),
      font_weight: primaryStyle.fontWeight,
      box_shadow: clip(primaryStyle.boxShadow, 120),
      padding: clip(`${primaryStyle.paddingTop} ${primaryStyle.paddingRight} ${primaryStyle.paddingBottom} ${primaryStyle.paddingLeft}`, 80),
    } : null,
  };
}
"""

PERFORMANCE_SUMMARY_JS = r"""
() => {
  const nav = performance.getEntriesByType("navigation")[0];
  if (!nav) return null;
  return {
    dom_content_loaded_ms: Math.round(nav.domContentLoadedEventEnd || 0),
    load_ms: Math.round(nav.loadEventEnd || 0),
    response_end_ms: Math.round(nav.responseEnd || 0),
    transfer_size: Math.round(nav.transferSize || 0),
    encoded_body_size: Math.round(nav.encodedBodySize || 0),
  };
}
"""

LAYOUT_SUMMARY_JS = r"""
() => {
  const clip = (value, n = 120) => String(value || "").replace(/\s+/g, " ").trim().slice(0, n);
  const rectFor = (el) => {
    const r = el.getBoundingClientRect();
    return {
      x: Math.round(r.x),
      y: Math.round(r.y),
      width: Math.round(r.width),
      height: Math.round(r.height),
      right: Math.round(r.right),
      bottom: Math.round(r.bottom),
    };
  };
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = window.getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none" && Number(s.opacity || 1) > 0.01;
  };
  const labelFor = (el) => {
    const text = clip(el.innerText || el.value || el.getAttribute("aria-label") || el.getAttribute("placeholder") || el.textContent || "");
    const tag = el.tagName.toLowerCase();
    return text ? `${tag}: ${text}` : tag;
  };
  const isAncestor = (a, b) => a !== b && (a.contains(b) || b.contains(a));
  const overlapRatio = (a, b) => {
    const left = Math.max(a.x, b.x);
    const top = Math.max(a.y, b.y);
    const right = Math.min(a.right, b.right);
    const bottom = Math.min(a.bottom, b.bottom);
    const width = Math.max(0, right - left);
    const height = Math.max(0, bottom - top);
    if (!width || !height) return 0;
    const overlapArea = width * height;
    const smallerArea = Math.max(1, Math.min(a.width * a.height, b.width * b.height));
    return overlapArea / smallerArea;
  };
  const important = Array.from(document.querySelectorAll("h1,h2,h3,p,a,button,input,textarea,select,[role='button'],[role='link']"))
    .filter(visible)
    .slice(0, 90)
    .map((el) => ({ el, label: labelFor(el), rect: rectFor(el) }));
  const overlaps = [];
  for (let i = 0; i < important.length; i += 1) {
    for (let j = i + 1; j < important.length; j += 1) {
      const a = important[i];
      const b = important[j];
      if (isAncestor(a.el, b.el)) continue;
      const ratio = overlapRatio(a.rect, b.rect);
      if (ratio > 0.35) {
        overlaps.push({
          a: a.label,
          b: b.label,
          ratio: Number(ratio.toFixed(2)),
          a_rect: a.rect,
          b_rect: b.rect,
        });
      }
      if (overlaps.length >= 12) break;
    }
    if (overlaps.length >= 12) break;
  }
  const clippedText = important
    .filter(({ el }) => {
      const s = window.getComputedStyle(el);
      const mayClip = s.overflow === "hidden" || s.textOverflow === "ellipsis" || s.whiteSpace === "nowrap";
      return mayClip && (el.scrollWidth > el.clientWidth + 2 || el.scrollHeight > el.clientHeight + 2);
    })
    .slice(0, 12)
    .map(({ el, label, rect }) => ({ label, rect, scroll_width: el.scrollWidth, client_width: el.clientWidth }));
  const smallTargets = Array.from(document.querySelectorAll("a,button,input,select,textarea,[role='button'],[role='link']"))
    .filter(visible)
    .map((el) => ({ el, label: labelFor(el), rect: rectFor(el) }))
    .filter(({ rect }) => rect.width < 32 || rect.height < 32)
    .slice(0, 12)
    .map(({ label, rect }) => ({ label, rect }));
  const emptyActions = Array.from(document.querySelectorAll("a,button,[role='button'],[role='link']"))
    .filter(visible)
    .filter((el) => !clip(el.innerText || el.getAttribute("aria-label") || el.textContent || ""))
    .slice(0, 12)
    .map((el) => ({ tag: el.tagName.toLowerCase(), rect: rectFor(el) }));
  return {
    viewport: {
      width: window.innerWidth,
      height: window.innerHeight,
      scroll_width: document.documentElement.scrollWidth,
      scroll_height: document.documentElement.scrollHeight,
    },
    horizontal_overflow_px: Math.max(0, document.documentElement.scrollWidth - window.innerWidth),
    overlaps,
    clipped_text: clippedText,
    small_interactive_targets: smallTargets,
    empty_actions: emptyActions,
  };
}
"""


@dataclasses.dataclass
class FrontendPageSpec:
    path: str
    name: str | None = None
    wait_for_text: str | None = None
    wait_for_selector: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FrontendPageSpec":
        return cls(
            path=str(data.get("path", "/")),
            name=str(data.get("name")) if data.get("name") is not None else None,
            wait_for_text=str(data.get("wait_for_text")) if data.get("wait_for_text") is not None else None,
            wait_for_selector=str(data.get("wait_for_selector")) if data.get("wait_for_selector") is not None else None,
        )


@dataclasses.dataclass
class FrontendViewportSpec:
    name: str
    width: int
    height: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FrontendViewportSpec":
        width = int(data.get("width", data.get("viewport_width", 1440)))
        height = int(data.get("height", data.get("viewport_height", 1024)))
        name = str(data.get("name") or f"{width}x{height}")
        return cls(name=name, width=width, height=height)

    def to_playwright(self) -> dict[str, int]:
        return {"width": self.width, "height": self.height}


@dataclasses.dataclass
class FrontendConfig:
    enabled: bool = False
    base_url: str = "http://127.0.0.1:3000"
    start_command: str | None = None
    cwd: str | None = None
    ready_url: str | None = None
    ready_timeout_sec: int = 120
    poll_interval_sec: float = 1.0
    browser: str = "chromium"
    executable_path: str | None = None
    headless: bool = True
    viewport_width: int = 1440
    viewport_height: int = 1024
    wait_until: str = "load"
    wait_after_load_ms: int = 800
    action_timeout_ms: int = 10_000
    viewports: list[FrontendViewportSpec] = dataclasses.field(default_factory=list)
    trace_enabled: bool = True
    screenshot_enabled: bool = True
    full_page_screenshot: bool = True
    review_each_cycle: bool = True
    require_review_before_finish: bool = False
    default_paths: list[str] = dataclasses.field(default_factory=lambda: ["/"])
    pages: list[FrontendPageSpec] = dataclasses.field(default_factory=list)
    design_brief: str = DEFAULT_DESIGN_BRIEF
    max_console_entries: int = 40
    max_request_failures: int = 20
    max_body_text_chars: int = 4000
    max_html_chars: int = 16_000
    storage_state_path: str | None = None
    extra_headers: dict[str, str] = dataclasses.field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FrontendConfig":
        page_specs = [FrontendPageSpec.from_dict(item) for item in core.coerce_list(data.get("pages")) if isinstance(item, dict)]
        viewport_specs = [FrontendViewportSpec.from_dict(item) for item in core.coerce_list(data.get("viewports")) if isinstance(item, dict)]
        default_paths = [str(x) for x in core.coerce_list(data.get("default_paths")) if str(x).strip()]
        if not default_paths and page_specs:
            default_paths = [page.path for page in page_specs]
        if not default_paths:
            default_paths = ["/"]
        headers = data.get("headers") or {}
        return cls(
            enabled=bool(data.get("enabled", False)),
            base_url=str(data.get("base_url", cls.base_url)),
            start_command=str(data.get("start_command")) if data.get("start_command") is not None else None,
            cwd=str(data.get("cwd")) if data.get("cwd") is not None else None,
            ready_url=str(data.get("ready_url")) if data.get("ready_url") is not None else None,
            ready_timeout_sec=int(data.get("ready_timeout_sec", cls.ready_timeout_sec)),
            poll_interval_sec=float(data.get("poll_interval_sec", cls.poll_interval_sec)),
            browser=str(data.get("browser", cls.browser)),
            executable_path=str(data.get("executable_path")) if data.get("executable_path") is not None else None,
            headless=bool(data.get("headless", cls.headless)),
            viewport_width=int(data.get("viewport_width", cls.viewport_width)),
            viewport_height=int(data.get("viewport_height", cls.viewport_height)),
            wait_until=str(data.get("wait_until", cls.wait_until)),
            wait_after_load_ms=int(data.get("wait_after_load_ms", cls.wait_after_load_ms)),
            action_timeout_ms=int(data.get("action_timeout_ms", cls.action_timeout_ms)),
            viewports=viewport_specs,
            trace_enabled=bool(data.get("trace_enabled", cls.trace_enabled)),
            screenshot_enabled=bool(data.get("screenshot_enabled", cls.screenshot_enabled)),
            full_page_screenshot=bool(data.get("full_page_screenshot", cls.full_page_screenshot)),
            review_each_cycle=bool(data.get("review_each_cycle", cls.review_each_cycle)),
            require_review_before_finish=bool(data.get("require_review_before_finish", cls.require_review_before_finish)),
            default_paths=default_paths,
            pages=page_specs,
            design_brief=str(data.get("design_brief", cls.design_brief)).strip() or DEFAULT_DESIGN_BRIEF,
            max_console_entries=int(data.get("max_console_entries", cls.max_console_entries)),
            max_request_failures=int(data.get("max_request_failures", cls.max_request_failures)),
            max_body_text_chars=int(data.get("max_body_text_chars", cls.max_body_text_chars)),
            max_html_chars=int(data.get("max_html_chars", cls.max_html_chars)),
            storage_state_path=str(data.get("storage_state_path")) if data.get("storage_state_path") is not None else None,
            extra_headers={str(k): str(v) for k, v in headers.items()},
        )

    def effective_viewports(self) -> list[FrontendViewportSpec]:
        if self.viewports:
            return self.viewports
        return [
            FrontendViewportSpec(
                name="default",
                width=self.viewport_width,
                height=self.viewport_height,
            )
        ]


@dataclasses.dataclass
class VisionCriticConfig:
    enabled: bool = False
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    temperature: float = 0.1
    top_p: float = 0.9
    max_tokens: int = 1200
    system_prompt: str = (
        "You are a senior frontend reviewer. Inspect the screenshot and the provided DOM/context. "
        "Call out functional UI bugs, layout problems, confusing states, weak hierarchy, spacing/alignment issues, "
        "contrast/readability concerns, and the 3-7 highest-value improvements. Return strict JSON."
    )

    @classmethod
    def from_dict(cls, data: dict[str, Any], fallback: core.AgentConfig) -> "VisionCriticConfig":
        return cls(
            enabled=bool(data.get("enabled", False)),
            base_url=str(data.get("base_url", fallback.base_url)),
            api_key=str(data.get("api_key", fallback.api_key)),
            model=str(data.get("model")) if data.get("model") is not None else None,
            temperature=float(data.get("temperature", cls.temperature)),
            top_p=float(data.get("top_p", cls.top_p)),
            max_tokens=int(data.get("max_tokens", cls.max_tokens)),
            system_prompt=str(data.get("system_prompt", cls.system_prompt)).strip() or cls.system_prompt,
        )


def load_extended_config(path: pathlib.Path | None) -> tuple[core.AgentConfig, FrontendConfig, VisionCriticConfig]:
    base_config = core.AgentConfig()
    frontend = FrontendConfig()
    vision = VisionCriticConfig()
    if path is None:
        return base_config, frontend, vision
    base_config = core.AgentConfig.from_toml(path)
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    frontend = FrontendConfig.from_dict(core.deep_get(data, "frontend", default={}))
    vision = VisionCriticConfig.from_dict(core.deep_get(data, "vision", default={}), fallback=base_config)
    return base_config, frontend, vision


class VisionVLLMClient(core.VLLMClient):
    def __init__(self, config: VisionCriticConfig) -> None:
        agent_cfg = core.AgentConfig(
            base_url=config.base_url or "http://localhost:8000/v1",
            api_key=config.api_key or "EMPTY",
            model=config.model,
            temperature=config.temperature,
            top_p=config.top_p,
            max_tokens=config.max_tokens,
        )
        super().__init__(agent_cfg)

    def critique_image(self, system_prompt: str, user_prompt: str, image_path: pathlib.Path) -> dict[str, Any]:
        image_bytes = image_path.read_bytes()
        encoded = base64.b64encode(image_bytes).decode("ascii")
        message_content = [
            {"type": "text", "text": user_prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
        ]
        payload: dict[str, Any] = {
            "model": self.ensure_model(),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message_content},
            ],
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_tokens": self.max_tokens,
        }
        payload.update(self.extra_body)
        data = self._request("POST", "/chat/completions", payload)
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("Vision critic returned no choices")
        message = choices[0].get("message") or {}
        content = message.get("content") or ""
        if isinstance(content, list):
            text_parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(str(item.get("text", "")))
            content_text = "\n".join(text_parts)
        else:
            content_text = str(content)
        parsed = parse_json_object(content_text)
        return {
            "raw_text": content_text,
            "parsed": parsed,
        }


class FrontendServer:
    def __init__(
        self,
        workspace: core.Workspace,
        config: FrontendConfig,
        command_env: dict[str, str],
        run_root: pathlib.Path,
        logger: core.JsonlLogger,
    ) -> None:
        self.workspace = workspace
        self.config = config
        self.command_env = dict(command_env)
        self.run_root = run_root
        self.logger = logger
        self.shell = "/bin/bash" if pathlib.Path("/bin/bash").exists() else "/bin/sh"
        self.log_path = run_root / "frontend_server.log"
        self.process: subprocess.Popen[str] | None = None
        self._log_handle: Any = None

    def _resolve_cwd(self) -> pathlib.Path:
        if not self.config.cwd:
            return self.workspace.workdir
        return self.workspace.resolve_path(self.config.cwd)

    def _ping_ready(self) -> bool:
        ready_url = self.config.ready_url or self.config.base_url
        req = urllib.request.Request(ready_url, headers={"User-Agent": "vllm-frontend-loop/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=2) as resp:
                status = getattr(resp, "status", 200)
                return 200 <= int(status) < 500
        except Exception:
            return False

    def ensure_started(self) -> dict[str, Any]:
        if not self.config.enabled:
            return {"ok": True, "skipped": True, "enabled": False}
        if self.process is not None and self.process.poll() is not None:
            self.stop()
        if self.process is not None and self._ping_ready():
            return {"ok": True, "reused": True, "base_url": self.config.base_url}
        if not self.config.start_command:
            if self._ping_ready():
                return {"ok": True, "external_server": True, "base_url": self.config.base_url}
            raise RuntimeError(
                f"Frontend base_url is not reachable and no frontend.start_command is configured: {self.config.base_url}"
            )
        self.stop()
        cwd = self._resolve_cwd()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_handle = self.log_path.open("a", encoding="utf-8")
        env = os.environ.copy()
        env.update(self.command_env)
        self.process = subprocess.Popen(
            [self.shell, "-lc", self.config.start_command],
            cwd=str(cwd),
            env=env,
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.time() + self.config.ready_timeout_sec
        while time.time() < deadline:
            if self.process.poll() is not None:
                tail = tail_text(self.log_path.read_text(encoding="utf-8", errors="ignore"), 4000)
                raise RuntimeError(
                    f"Frontend start command exited with code {self.process.returncode}.\n\nLog tail:\n{tail}"
                )
            if self._ping_ready():
                payload = {
                    "ok": True,
                    "started": True,
                    "pid": self.process.pid,
                    "base_url": self.config.base_url,
                    "log_path": str(self.log_path),
                }
                self.logger.log("frontend_server_started", payload)
                return payload
            time.sleep(self.config.poll_interval_sec)
        raise RuntimeError(
            f"Timed out waiting {self.config.ready_timeout_sec}s for frontend server readiness: {self.config.ready_url or self.config.base_url}"
        )

    def stop(self) -> None:
        if self.process is None:
            if self._log_handle is not None:
                try:
                    self._log_handle.close()
                except Exception:
                    pass
                self._log_handle = None
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except Exception:
                pass
            self._log_handle = None
        self.logger.log(
            "frontend_server_stopped",
            {
                "returncode": self.process.returncode,
                "log_path": str(self.log_path),
            },
        )
        self.process = None


class FrontendHarness:
    def __init__(
        self,
        workspace: core.Workspace,
        frontend: FrontendConfig,
        vision: VisionCriticConfig,
        command_env: dict[str, str],
        run_root: pathlib.Path,
        logger: core.JsonlLogger,
    ) -> None:
        self.workspace = workspace
        self.config = frontend
        self.vision_config = vision
        self.run_root = run_root
        self.logger = logger
        self.server = FrontendServer(workspace, frontend, command_env, run_root, logger)
        self.vision_client = VisionVLLMClient(vision) if vision.enabled else None
        self.runtime_storage_state_path: pathlib.Path | None = None

    def close(self) -> None:
        self.server.stop()

    def configured_page_spec(self, path: str) -> FrontendPageSpec:
        normalized = normalize_route(path)
        for spec in self.config.pages:
            if normalize_route(spec.path) == normalized:
                return spec
        return FrontendPageSpec(path=normalized)

    def resolve_storage_state_path(self) -> pathlib.Path | None:
        if self.runtime_storage_state_path is not None:
            return self.runtime_storage_state_path
        if not self.config.storage_state_path:
            return None
        raw = pathlib.Path(self.config.storage_state_path)
        if raw.is_absolute():
            return raw
        try:
            return self.workspace.resolve_path(self.config.storage_state_path)
        except Exception:
            return (self.run_root / self.config.storage_state_path).resolve()

    def default_paths(self) -> list[str]:
        if self.config.pages:
            return [spec.path for spec in self.config.pages]
        return list(self.config.default_paths)

    def review_pages(
        self,
        paths: list[str] | None,
        focus: str | None,
        cycle_idx: int,
        step: int,
        source: str,
        viewport: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any]
        try:
            self._ensure_playwright_available()
            server_info = self.server.ensure_started()
            selected_paths = [normalize_route(p) for p in (paths or self.default_paths())]
            viewport_specs = (
                [FrontendViewportSpec(name=f"{viewport['width']}x{viewport['height']}", width=viewport["width"], height=viewport["height"])]
                if viewport
                else self.config.effective_viewports()
            )
            review_id = f"cycle-{cycle_idx:02d}-step-{step:02d}-{slugify(source)}"
            artifact_root = self.run_root / "frontend_artifacts" / review_id
            artifact_root.mkdir(parents=True, exist_ok=True)
            observations: list[dict[str, Any]] = []
            page_index = 0
            for viewport_spec in viewport_specs:
                for route in selected_paths:
                    page_index += 1
                    spec = self.configured_page_spec(route)
                    observations.append(
                        self._observe_single_page(
                            route=route,
                            spec=spec,
                            focus=focus,
                            cycle_idx=cycle_idx,
                            step=step,
                            page_index=page_index,
                            artifact_root=artifact_root,
                            viewport=viewport_spec.to_playwright(),
                            viewport_name=viewport_spec.name,
                        )
                    )
            aggregate = self._aggregate_review(observations)
            result = {
                "ok": True,
                "source": source,
                "focus": focus or "",
                "paths": selected_paths,
                "viewports": [dataclasses.asdict(item) for item in viewport_specs],
                "artifact_root": str(artifact_root),
                "server": server_info,
                "aggregate": aggregate,
                "observations": observations,
            }
            (artifact_root / "review.json").write_text(core.pretty_json(result), encoding="utf-8")
            self.logger.log("frontend_review", result)
            return result
        except Exception as exc:
            result = {
                "ok": False,
                "source": source,
                "focus": focus or "",
                "error": str(exc),
                "base_url": self.config.base_url,
                "server_log_tail": tail_text(
                    self.server.log_path.read_text(encoding="utf-8", errors="ignore")
                    if self.server.log_path.exists()
                    else "",
                    4000,
                ),
            }
            self.logger.log("frontend_review_error", result)
            return result

    def run_actions(
        self,
        path: str,
        actions: list[dict[str, Any]],
        focus: str | None,
        cycle_idx: int,
        step: int,
        capture_after: bool,
        save_storage_state: bool,
        viewport: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        try:
            self._ensure_playwright_available()
            server_info = self.server.ensure_started()
            route = normalize_route(path)
            action_id = f"cycle-{cycle_idx:02d}-step-{step:02d}-actions-{slugify(route)}"
            artifact_root = self.run_root / "frontend_artifacts" / action_id
            artifact_root.mkdir(parents=True, exist_ok=True)
            with self._browser_session(artifact_root=artifact_root, viewport=viewport) as session:
                page = session["page"]
                context = session["context"]
                collectors = session["collectors"]
                page.goto(
                    absolutize_url(self.config.base_url, route),
                    wait_until=self.config.wait_until,
                    timeout=self.config.action_timeout_ms * 3,
                )
                page.wait_for_timeout(self.config.wait_after_load_ms)
                execution_log: list[dict[str, Any]] = []
                for index, action in enumerate(actions, start=1):
                    execution_log.append(self._execute_action(page, action, index=index))
                    if not execution_log[-1].get("ok"):
                        break
                saved_state_path = None
                if save_storage_state:
                    target = self.resolve_storage_state_path()
                    if target is None:
                        target = artifact_root / "storage_state.json"
                        self.runtime_storage_state_path = target
                    target.parent.mkdir(parents=True, exist_ok=True)
                    context.storage_state(path=str(target))
                    saved_state_path = str(target)
                observation = None
                if capture_after:
                    spec = self.configured_page_spec(route)
                    observation = self._capture_page_state(
                        page=page,
                        spec=spec,
                        focus=focus,
                        artifact_dir=artifact_root,
                        route=route,
                        collectors=collectors,
                        viewport_name="action",
                    )
                trace_path = artifact_root / "trace.zip"
                if self.config.trace_enabled:
                    try:
                        context.tracing.stop(path=str(trace_path))
                    except Exception as exc:
                        trace_path = artifact_root / "trace-stop-error.txt"
                        trace_path.write_text(str(exc), encoding="utf-8")
                result = {
                    "ok": all(item.get("ok") for item in execution_log),
                    "path": route,
                    "focus": focus or "",
                    "artifact_root": str(artifact_root),
                    "server": server_info,
                    "actions": execution_log,
                    "saved_storage_state": saved_state_path,
                    "observation": observation,
                }
                (artifact_root / "actions.json").write_text(core.pretty_json(result), encoding="utf-8")
                self.logger.log("frontend_actions", result)
                return result
        except Exception as exc:
            result = {
                "ok": False,
                "path": normalize_route(path),
                "focus": focus or "",
                "error": str(exc),
                "server_log_tail": tail_text(
                    self.server.log_path.read_text(encoding="utf-8", errors="ignore")
                    if self.server.log_path.exists()
                    else "",
                    4000,
                ),
            }
            self.logger.log("frontend_actions_error", result)
            return result

    def render_review_for_model(self, review: dict[str, Any], max_chars: int = 12_000) -> str:
        if not review.get("ok"):
            return core.pretty_json(review)
        slim = {
            "ok": True,
            "source": review.get("source"),
            "focus": review.get("focus"),
            "paths": review.get("paths"),
            "viewports": review.get("viewports"),
            "aggregate": review.get("aggregate"),
            "observations": [],
        }
        for obs in review.get("observations", []):
            if not isinstance(obs, dict):
                continue
            vision_review = obs.get("vision_review") or {}
            if not isinstance(vision_review, dict):
                vision_review = {"raw_text": str(vision_review)}
            slim["observations"].append(
                {
                    "name": obs.get("name"),
                    "path": obs.get("path"),
                    "url": obs.get("url"),
                    "title": obs.get("title"),
                    "viewport": obs.get("viewport"),
                    "navigation_error": obs.get("navigation_error"),
                    "console_errors": obs.get("console_errors", [])[:6],
                    "page_errors": obs.get("page_errors", [])[:6],
                    "failed_requests": obs.get("failed_requests", [])[:6],
                    "http_errors": obs.get("http_errors", [])[:6],
                    "issues": obs.get("issues", [])[:10],
                    "style_summary": obs.get("style_summary"),
                    "layout_summary": {
                        "horizontal_overflow_px": core.deep_get(obs, "layout_summary", "horizontal_overflow_px", default=0),
                        "overlaps": core.coerce_list(core.deep_get(obs, "layout_summary", "overlaps", default=[]))[:4],
                        "clipped_text": core.coerce_list(core.deep_get(obs, "layout_summary", "clipped_text", default=[]))[:4],
                        "small_interactive_targets": core.coerce_list(core.deep_get(obs, "layout_summary", "small_interactive_targets", default=[]))[:4],
                        "empty_actions": core.coerce_list(core.deep_get(obs, "layout_summary", "empty_actions", default=[]))[:4],
                    },
                    "semantic_summary": {
                        "headings": core.coerce_list(core.deep_get(obs, "semantic_summary", "headings", default=[]))[:10],
                        "buttons": core.coerce_list(core.deep_get(obs, "semantic_summary", "buttons", default=[]))[:10],
                        "inputs": core.coerce_list(core.deep_get(obs, "semantic_summary", "inputs", default=[]))[:10],
                        "forms": core.coerce_list(core.deep_get(obs, "semantic_summary", "forms", default=[]))[:6],
                        "links": core.coerce_list(core.deep_get(obs, "semantic_summary", "links", default=[]))[:8],
                    },
                    "body_text_excerpt": obs.get("body_text_excerpt"),
                    "vision_review": vision_review.get("parsed") or vision_review.get("raw_text"),
                    "artifacts": obs.get("artifacts"),
                }
            )
        return core.shorten(core.pretty_json(slim), max_chars)

    def _ensure_playwright_available(self) -> None:
        if sync_playwright is None:
            raise RuntimeError(
                "Playwright is not installed. Install it with 'pip install playwright' and a browser binary via 'playwright install chromium', or provide frontend.executable_path."
            )

    @contextlib.contextmanager
    def _browser_session(self, artifact_root: pathlib.Path, viewport: dict[str, int] | None):
        playwright = sync_playwright().start()
        browser = None
        try:
            launcher = getattr(playwright, self.config.browser)
            launch_kwargs: dict[str, Any] = {
                "headless": self.config.headless,
                "args": list(DEFAULT_BROWSER_ARGS),
            }
            if self.config.executable_path:
                launch_kwargs["executable_path"] = self.config.executable_path
            browser = launcher.launch(**launch_kwargs)
            context_kwargs: dict[str, Any] = {
                "viewport": viewport or {"width": self.config.viewport_width, "height": self.config.viewport_height},
                "ignore_https_errors": True,
                "extra_http_headers": self.config.extra_headers or None,
            }
            storage_state = self.resolve_storage_state_path()
            if storage_state and storage_state.exists():
                context_kwargs["storage_state"] = str(storage_state)
            context = browser.new_context(**{k: v for k, v in context_kwargs.items() if v is not None})
            if self.config.trace_enabled:
                context.tracing.start(screenshots=True, snapshots=True, sources=True)
            page = context.new_page()
            page.set_default_timeout(self.config.action_timeout_ms)
            collectors = {
                "console_errors": [],
                "console_entries": [],
                "page_errors": [],
                "failed_requests": [],
                "http_errors": [],
            }

            def on_console(msg: Any) -> None:
                entry = {
                    "type": getattr(msg, "type", "log"),
                    "text": getattr(msg, "text", ""),
                }
                if len(collectors["console_entries"]) < self.config.max_console_entries:
                    collectors["console_entries"].append(entry)
                if entry["type"] == "error" and len(collectors["console_errors"]) < self.config.max_console_entries:
                    collectors["console_errors"].append(entry)

            def on_page_error(exc: Exception) -> None:
                if len(collectors["page_errors"]) < self.config.max_console_entries:
                    collectors["page_errors"].append(str(exc))

            def on_request_failed(request: Any) -> None:
                if len(collectors["failed_requests"]) >= self.config.max_request_failures:
                    return
                failure = request.failure or {}
                collectors["failed_requests"].append(
                    {
                        "url": request.url,
                        "method": request.method,
                        "failure_text": failure.get("errorText", "") if isinstance(failure, dict) else str(failure),
                    }
                )

            def on_response(response: Any) -> None:
                if len(collectors["http_errors"]) >= self.config.max_request_failures:
                    return
                try:
                    status = int(response.status)
                except Exception:
                    return
                if status >= 400:
                    collectors["http_errors"].append(
                        {"url": response.url, "status": status}
                    )

            page.on("console", on_console)
            page.on("pageerror", on_page_error)
            page.on("requestfailed", on_request_failed)
            page.on("response", on_response)
            yield {
                "playwright": playwright,
                "browser": browser,
                "context": context,
                "page": page,
                "collectors": collectors,
            }
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass
            try:
                playwright.stop()
            except Exception:
                pass

    def _observe_single_page(
        self,
        route: str,
        spec: FrontendPageSpec,
        focus: str | None,
        cycle_idx: int,
        step: int,
        page_index: int,
        artifact_root: pathlib.Path,
        viewport: dict[str, int] | None,
        viewport_name: str,
    ) -> dict[str, Any]:
        page_name = spec.name or route.strip("/") or "home"
        page_slug = f"{page_index:02d}-{slugify(page_name)}-{slugify(viewport_name)}"
        artifact_dir = artifact_root / page_slug
        artifact_dir.mkdir(parents=True, exist_ok=True)
        with self._browser_session(artifact_root=artifact_dir, viewport=viewport) as session:
            page = session["page"]
            context = session["context"]
            collectors = session["collectors"]
            navigation_error = None
            try:
                page.goto(
                    absolutize_url(self.config.base_url, route),
                    wait_until=self.config.wait_until,
                    timeout=self.config.action_timeout_ms * 3,
                )
                if spec.wait_for_selector:
                    page.locator(spec.wait_for_selector).first.wait_for(
                        state="visible", timeout=self.config.action_timeout_ms
                    )
                if spec.wait_for_text:
                    page.get_by_text(spec.wait_for_text, exact=False).first.wait_for(
                        timeout=self.config.action_timeout_ms
                    )
                page.wait_for_timeout(self.config.wait_after_load_ms)
            except Exception as exc:
                navigation_error = str(exc)
            observation = self._capture_page_state(
                page=page,
                spec=spec,
                focus=focus,
                artifact_dir=artifact_dir,
                route=route,
                collectors=collectors,
                viewport_name=viewport_name,
            )
            observation["navigation_error"] = navigation_error
            if navigation_error:
                existing_issues = [str(x) for x in core.coerce_list(observation.get("issues")) if str(x).strip()]
                observation["issues"] = dedupe_strings([f"Navigation error: {navigation_error}", *existing_issues])
            if self.config.trace_enabled:
                trace_path = artifact_dir / "trace.zip"
                try:
                    context.tracing.stop(path=str(trace_path))
                    observation.setdefault("artifacts", {})["trace_path"] = str(trace_path)
                except Exception as exc:
                    error_path = artifact_dir / "trace-stop-error.txt"
                    error_path.write_text(str(exc), encoding="utf-8")
                    observation.setdefault("artifacts", {})["trace_error_path"] = str(error_path)
            return observation

    def _capture_page_state(
        self,
        page: Any,
        spec: FrontendPageSpec,
        focus: str | None,
        artifact_dir: pathlib.Path,
        route: str,
        collectors: dict[str, list[Any]],
        viewport_name: str,
    ) -> dict[str, Any]:
        title = safe_call(lambda: page.title(), default="")
        url = safe_call(lambda: page.url, default=absolutize_url(self.config.base_url, route))
        body_text = safe_call(
            lambda: page.locator("body").inner_text(timeout=self.config.action_timeout_ms),
            default="",
        )
        body_text_excerpt = core.shorten(body_text or "", self.config.max_body_text_chars)
        html = safe_call(lambda: page.content(), default="")
        html_excerpt = core.shorten(html or "", self.config.max_html_chars)
        semantic_summary = safe_call(lambda: page.evaluate(SEMANTIC_SUMMARY_JS), default={}) or {}
        style_summary = safe_call(lambda: page.evaluate(STYLE_SUMMARY_JS), default={}) or {}
        performance_summary = safe_call(lambda: page.evaluate(PERFORMANCE_SUMMARY_JS), default=None)
        layout_summary = safe_call(lambda: page.evaluate(LAYOUT_SUMMARY_JS), default={}) or {}
        screenshot_path = None
        screenshot_error = None
        if self.config.screenshot_enabled:
            screenshot_path = artifact_dir / "page.png"
            try:
                page.screenshot(
                    path=str(screenshot_path),
                    full_page=self.config.full_page_screenshot,
                    animations="disabled",
                )
            except Exception as exc:
                screenshot_error = str(exc)
                screenshot_path = None
        issues = self._heuristic_issues(
            body_text=body_text or "",
            semantic_summary=semantic_summary,
            style_summary=style_summary,
            layout_summary=layout_summary,
            console_errors=collectors["console_errors"],
            page_errors=collectors["page_errors"],
            failed_requests=collectors["failed_requests"],
            http_errors=collectors["http_errors"],
            navigation_error=None,
        )
        vision_review = None
        if screenshot_path is not None and self.vision_client is not None:
            vision_review = self._vision_review(
                screenshot_path=screenshot_path,
                focus=focus,
                title=title,
                url=url,
                semantic_summary=semantic_summary,
                style_summary=style_summary,
                layout_summary=layout_summary,
                current_issues=issues,
            )
            parsed = vision_review.get("parsed") if isinstance(vision_review, dict) else None
            if isinstance(parsed, dict):
                issues.extend(str(x) for x in core.coerce_list(parsed.get("ui_bugs")) if str(x).strip())
                issues.extend(str(x) for x in core.coerce_list(parsed.get("design_issues")) if str(x).strip())
        issues = dedupe_strings(issues)[:20]
        observation = {
            "name": spec.name or (route.strip("/") or "home"),
            "path": normalize_route(route),
            "url": url,
            "title": title,
            "viewport": {
                "name": viewport_name,
                "width": core.deep_get(layout_summary, "viewport", "width", default=None),
                "height": core.deep_get(layout_summary, "viewport", "height", default=None),
            },
            "focus": focus or "",
            "body_text_excerpt": body_text_excerpt,
            "html_excerpt": html_excerpt,
            "semantic_summary": semantic_summary,
            "style_summary": style_summary,
            "layout_summary": layout_summary,
            "performance_summary": performance_summary,
            "console_entries": collectors["console_entries"],
            "console_errors": collectors["console_errors"],
            "page_errors": collectors["page_errors"],
            "failed_requests": collectors["failed_requests"],
            "http_errors": collectors["http_errors"],
            "issues": issues,
            "vision_review": vision_review,
            "artifacts": {
                "screenshot_path": str(screenshot_path) if screenshot_path is not None else None,
                "screenshot_error": screenshot_error,
                "artifact_dir": str(artifact_dir),
            },
        }
        return observation

    def _vision_review(
        self,
        screenshot_path: pathlib.Path,
        focus: str | None,
        title: str,
        url: str,
        semantic_summary: dict[str, Any],
        style_summary: dict[str, Any],
        layout_summary: dict[str, Any],
        current_issues: list[str],
    ) -> dict[str, Any]:
        if self.vision_client is None:
            return {}
        prompt = textwrap.dedent(
            f"""
            Review this frontend screenshot along with the structured context.

            Focus:
            {focus or 'Overall correctness and polish.'}

            Design brief:
            {self.config.design_brief or DEFAULT_DESIGN_BRIEF}

            Title: {title}
            URL: {url}

            Existing non-visual issues already detected:
            {json.dumps(current_issues[:10], ensure_ascii=False)}

            Structured summary:
            {json.dumps({'semantic_summary': semantic_summary, 'style_summary': style_summary, 'layout_summary': layout_summary}, ensure_ascii=False)[:11000]}

            Return strict JSON with keys:
            - summary: string
            - ui_bugs: string[]
            - design_issues: string[]
            - recommended_changes: string[]
            - confidence: string
            """
        ).strip()
        try:
            return self.vision_client.critique_image(
                system_prompt=self.vision_config.system_prompt,
                user_prompt=prompt,
                image_path=screenshot_path,
            )
        except Exception as exc:
            return {"error": str(exc)}

    def _heuristic_issues(
        self,
        body_text: str,
        semantic_summary: dict[str, Any],
        style_summary: dict[str, Any],
        layout_summary: dict[str, Any],
        console_errors: list[Any],
        page_errors: list[Any],
        failed_requests: list[Any],
        http_errors: list[Any],
        navigation_error: str | None,
    ) -> list[str]:
        issues: list[str] = []
        if navigation_error:
            issues.append(f"Navigation error: {navigation_error}")
        if console_errors:
            issues.append("Browser console reported JavaScript errors.")
        if page_errors:
            issues.append("Unhandled page errors occurred during rendering.")
        if failed_requests:
            issues.append("One or more network requests failed.")
        if http_errors:
            issues.append("One or more HTTP responses returned status >= 400.")
        if not (body_text or "").strip():
            issues.append("The page body text is empty or not readable.")
        headings = core.coerce_list(semantic_summary.get("headings"))
        forms = core.coerce_list(semantic_summary.get("forms"))
        buttons = core.coerce_list(semantic_summary.get("buttons"))
        inputs = core.coerce_list(semantic_summary.get("inputs"))
        links = core.coerce_list(semantic_summary.get("links"))
        if not headings and len((body_text or "").strip()) > 120:
            issues.append("No visible headings were detected, which weakens information hierarchy.")
        if forms and not buttons:
            issues.append("Forms are present but no visible action button was detected.")
        if inputs and not forms and len(inputs) >= 3:
            issues.append("Several input fields exist outside a visible form container.")
        primary_button = core.deep_get(style_summary, "primary_button", default=None)
        body_style = core.deep_get(style_summary, "body", default=None)
        if isinstance(primary_button, dict) and isinstance(body_style, dict):
            if primary_button.get("background_color") == body_style.get("background_color"):
                issues.append("The primary button background matches the page background, so the main CTA may not stand out.")
        horizontal_overflow = int(layout_summary.get("horizontal_overflow_px") or 0)
        if horizontal_overflow > 2:
            issues.append(f"The layout overflows horizontally by {horizontal_overflow}px at this viewport.")
        overlaps = core.coerce_list(layout_summary.get("overlaps"))
        if overlaps:
            issues.append("Important visible elements appear to overlap each other.")
        clipped_text = core.coerce_list(layout_summary.get("clipped_text"))
        if clipped_text:
            issues.append("Some visible text appears clipped or truncated inside its container.")
        small_targets = core.coerce_list(layout_summary.get("small_interactive_targets"))
        if len(small_targets) >= 3:
            issues.append("Several interactive targets are smaller than 32px in one dimension.")
        empty_actions = core.coerce_list(layout_summary.get("empty_actions"))
        if empty_actions:
            issues.append("Some visible interactive elements have no readable text or accessible label.")
        if not buttons and not links and len((body_text or "").strip()) < 80:
            issues.append("The page has very little visible interactive or textual content.")
        return dedupe_strings(issues)

    def _aggregate_review(self, observations: list[dict[str, Any]]) -> dict[str, Any]:
        issues: list[str] = []
        console_error_count = 0
        page_error_count = 0
        failed_request_count = 0
        http_error_count = 0
        navigation_failures = 0
        for obs in observations:
            issues.extend(str(x) for x in core.coerce_list(obs.get("issues")) if str(x).strip())
            console_error_count += len(core.coerce_list(obs.get("console_errors")))
            page_error_count += len(core.coerce_list(obs.get("page_errors")))
            failed_request_count += len(core.coerce_list(obs.get("failed_requests")))
            http_error_count += len(core.coerce_list(obs.get("http_errors")))
            if obs.get("navigation_error"):
                navigation_failures += 1
        return {
            "page_count": len(observations),
            "navigation_failures": navigation_failures,
            "console_error_count": console_error_count,
            "page_error_count": page_error_count,
            "failed_request_count": failed_request_count,
            "http_error_count": http_error_count,
            "issues": dedupe_strings(issues)[:30],
        }

    def _execute_action(self, page: Any, action: dict[str, Any], index: int) -> dict[str, Any]:
        kind = str(action.get("kind", "")).strip()
        if not kind:
            return {"ok": False, "index": index, "error": "Action missing kind"}
        timeout_ms = int(action.get("timeout_ms") or self.config.action_timeout_ms)
        started = time.time()
        try:
            if kind == "wait_ms":
                value = int(action.get("value") or action.get("ms") or 500)
                page.wait_for_timeout(value)
                return {"ok": True, "index": index, "kind": kind, "ms": value, "duration_sec": round(time.time() - started, 3)}
            if kind == "wait_for_text":
                text = str(action.get("text", "")).strip()
                if not text:
                    raise ValueError("wait_for_text requires text")
                page.get_by_text(text, exact=False).first.wait_for(timeout=timeout_ms)
                return {"ok": True, "index": index, "kind": kind, "text": text, "duration_sec": round(time.time() - started, 3)}
            locator = self._resolve_locator(page, action)
            if kind == "click":
                locator.first.click(timeout=timeout_ms)
            elif kind == "fill":
                value = str(action.get("value", ""))
                locator.first.fill(value, timeout=timeout_ms)
            elif kind == "press":
                key = str(action.get("key", action.get("value", ""))).strip()
                if not key:
                    raise ValueError("press requires key")
                locator.first.press(key, timeout=timeout_ms)
            elif kind == "check":
                locator.first.check(timeout=timeout_ms)
            elif kind == "uncheck":
                locator.first.uncheck(timeout=timeout_ms)
            elif kind == "hover":
                locator.first.hover(timeout=timeout_ms)
            elif kind == "select":
                value = str(action.get("value", ""))
                if not value:
                    raise ValueError("select requires value")
                locator.first.select_option(value=value, timeout=timeout_ms)
            elif kind == "wait_for_selector":
                locator.first.wait_for(state="visible", timeout=timeout_ms)
            else:
                raise ValueError(f"Unsupported browser action kind: {kind}")
            return {
                "ok": True,
                "index": index,
                "kind": kind,
                "locator": summarize_action_locator(action),
                "duration_sec": round(time.time() - started, 3),
            }
        except Exception as exc:
            return {
                "ok": False,
                "index": index,
                "kind": kind,
                "locator": summarize_action_locator(action),
                "error": str(exc),
                "duration_sec": round(time.time() - started, 3),
            }

    def _resolve_locator(self, page: Any, action: dict[str, Any]) -> Any:
        role = action.get("role")
        name = action.get("name")
        label = action.get("label")
        placeholder = action.get("placeholder")
        text = action.get("text")
        selector = action.get("selector")
        if role is not None:
            if name is not None:
                return page.get_by_role(str(role), name=str(name), exact=False)
            return page.get_by_role(str(role))
        if label is not None:
            return page.get_by_label(str(label), exact=False)
        if placeholder is not None:
            return page.get_by_placeholder(str(placeholder), exact=False)
        if selector is not None:
            return page.locator(str(selector))
        if text is not None:
            return page.get_by_text(str(text), exact=False)
        raise ValueError("Action requires one locator strategy: role/name, label, placeholder, selector, or text")


class VisualCodingAgent(core.CodingAgent):
    def __init__(
        self,
        config: core.AgentConfig,
        workspace: core.Workspace,
        task_text: str,
        run_root: pathlib.Path,
        logger: core.JsonlLogger,
        frontend_config: FrontendConfig,
        vision_config: VisionCriticConfig,
    ) -> None:
        super().__init__(config, workspace, task_text, run_root, logger)
        self.frontend_config = frontend_config
        self.vision_config = vision_config
        self.frontend = FrontendHarness(
            workspace=workspace,
            frontend=frontend_config,
            vision=vision_config,
            command_env=config.env,
            run_root=run_root,
            logger=logger,
        )
        self.frontend_reviews: list[dict[str, Any]] = []
        self.frontend_actions: list[dict[str, Any]] = []
        self._frontend_review_cycles: set[int] = set()
        if self.frontend_config.enabled:
            self.tools = self.tools + build_frontend_tools()

    def run(self) -> dict[str, Any]:
        original_prompt = core.SYSTEM_PROMPT
        if self.frontend_config.enabled:
            core.SYSTEM_PROMPT = original_prompt + "\n\n" + FRONTEND_PROMPT_APPENDIX
        try:
            return super().run()
        finally:
            core.SYSTEM_PROMPT = original_prompt
            self.frontend.close()

    def _build_cycle_user_message(self, cycle_idx: int, last_verify_results: list[core.CommandResult]) -> str:
        message = super()._build_cycle_user_message(cycle_idx, last_verify_results)
        if not self.frontend_config.enabled or not self.frontend_config.review_each_cycle:
            return message
        auto_review = self.frontend.review_pages(
            paths=None,
            focus="Automatic cycle-start UI review. Look for rendering bugs, missing states, and design regressions.",
            cycle_idx=cycle_idx,
            step=0,
            source="auto_cycle_review",
        )
        self.frontend_reviews.append(auto_review)
        self._frontend_review_cycles.add(cycle_idx)
        review_text = self.frontend.render_review_for_model(
            auto_review,
            max_chars=min(6_000, self.config.max_context_chars // 4),
        )
        return (
            message
            + "\n\nFrontend design brief:\n"
            + (self.frontend_config.design_brief or DEFAULT_DESIGN_BRIEF)
            + "\n\nLatest automatic frontend review:\n"
            + review_text
        )

    def _handle_tool(self, name: str | None, args: dict[str, Any], cycle_idx: int, step: int) -> str:
        if name == "review_frontend":
            review = self.frontend.review_pages(
                paths=[str(x) for x in core.coerce_list(args.get("paths")) if str(x).strip()] or None,
                focus=str(args.get("focus")) if args.get("focus") is not None else None,
                cycle_idx=cycle_idx,
                step=step,
                source="tool_review_frontend",
                viewport=parse_viewport(args),
            )
            self.frontend_reviews.append(review)
            self._frontend_review_cycles.add(cycle_idx)
            return self.frontend.render_review_for_model(
                review,
                max_chars=min(6_000, self.config.max_context_chars // 4),
            )

        if name == "browser_actions":
            action_result = self.frontend.run_actions(
                path=str(args.get("path", "/")),
                actions=[x for x in core.coerce_list(args.get("actions")) if isinstance(x, dict)],
                focus=str(args.get("focus")) if args.get("focus") is not None else None,
                cycle_idx=cycle_idx,
                step=step,
                capture_after=bool(args.get("capture_after", True)),
                save_storage_state=bool(args.get("save_storage_state", False)),
                viewport=parse_viewport(args),
            )
            self.frontend_actions.append(action_result)
            self._frontend_review_cycles.add(cycle_idx)
            return core.shorten(core.pretty_json(action_result), 8_000)

        if name == "finish_iteration" and self.frontend_config.enabled and self.frontend_config.require_review_before_finish:
            if cycle_idx not in self._frontend_review_cycles:
                return core.pretty_json(
                    {
                        "ok": False,
                        "tool": name,
                        "error": "A frontend review is required in this cycle before finish_iteration. Use review_frontend or browser_actions first.",
                    }
                )
        return super()._handle_tool(name, args, cycle_idx, step)

    def _finalize(
        self,
        status: str,
        verify_results: list[core.CommandResult],
        reason: str,
    ) -> dict[str, Any]:
        summary = super()._finalize(status, verify_results, reason)
        summary["frontend"] = {
            "enabled": self.frontend_config.enabled,
            "base_url": self.frontend_config.base_url,
            "reviews": self.frontend_reviews,
            "actions": self.frontend_actions,
            "server_log_path": str(self.frontend.server.log_path),
            "runtime_storage_state_path": str(self.frontend.runtime_storage_state_path) if self.frontend.runtime_storage_state_path else None,
        }
        summary_path = self.run_root / "summary.json"
        summary_path.write_text(core.pretty_json(summary), encoding="utf-8")
        report_path = pathlib.Path(summary["report_path"])
        report = report_path.read_text(encoding="utf-8")
        frontend_section = self._render_frontend_report_section()
        report_path.write_text(report + "\n\n" + frontend_section, encoding="utf-8")
        return summary

    def _render_frontend_report_section(self) -> str:
        if not self.frontend_config.enabled:
            return "## Frontend review\n\n- Disabled\n"
        lines = [
            "## Frontend review",
            "",
            f"- Base URL: `{self.frontend_config.base_url}`",
            f"- Design brief: {self.frontend_config.design_brief or DEFAULT_DESIGN_BRIEF}",
            f"- Review count: {len(self.frontend_reviews)}",
            f"- Action count: {len(self.frontend_actions)}",
            f"- Frontend server log: `{self.frontend.server.log_path}`",
        ]
        if self.frontend.runtime_storage_state_path is not None:
            lines.append(f"- Runtime storage state: `{self.frontend.runtime_storage_state_path}`")
        recent_review = self.frontend_reviews[-1] if self.frontend_reviews else None
        if recent_review:
            lines.extend(
                [
                    "",
                    "### Latest review summary",
                    "",
                    "```json",
                    core.shorten(self.frontend.render_review_for_model(recent_review, max_chars=12_000), 12_000),
                    "```",
                ]
            )
        return "\n".join(lines)


def build_frontend_tools() -> list[dict[str, Any]]:
    return [
        core.tool(
            "review_frontend",
            "Open one or more frontend routes in a real browser, capture structured observations, console/network errors, screenshots, and optional vision critique.",
            {
                "type": "object",
                "properties": {
                    "paths": {"type": ["array", "null"], "items": {"type": "string"}},
                    "focus": {"type": ["string", "null"]},
                    "viewport_width": {"type": ["integer", "null"], "minimum": 240, "maximum": 4000},
                    "viewport_height": {"type": ["integer", "null"], "minimum": 240, "maximum": 4000},
                },
            },
        ),
        core.tool(
            "browser_actions",
            "Run a short interactive browser script using semantic locators, then optionally capture a fresh UI observation.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "focus": {"type": ["string", "null"]},
                    "capture_after": {"type": "boolean"},
                    "save_storage_state": {"type": "boolean"},
                    "viewport_width": {"type": ["integer", "null"], "minimum": 240, "maximum": 4000},
                    "viewport_height": {"type": ["integer", "null"], "minimum": 240, "maximum": 4000},
                    "actions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "kind": {
                                    "type": "string",
                                    "enum": [
                                        "click",
                                        "fill",
                                        "press",
                                        "check",
                                        "uncheck",
                                        "hover",
                                        "select",
                                        "wait_for_text",
                                        "wait_for_selector",
                                        "wait_ms",
                                    ],
                                },
                                "selector": {"type": ["string", "null"]},
                                "role": {"type": ["string", "null"]},
                                "name": {"type": ["string", "null"]},
                                "label": {"type": ["string", "null"]},
                                "placeholder": {"type": ["string", "null"]},
                                "text": {"type": ["string", "null"]},
                                "value": {"type": ["string", "null"]},
                                "key": {"type": ["string", "null"]},
                                "timeout_ms": {"type": ["integer", "null"], "minimum": 1, "maximum": 120000},
                            },
                            "required": ["kind"],
                        },
                    },
                },
                "required": ["path", "actions"],
            },
        ),
    ]


def parse_viewport(args: dict[str, Any]) -> dict[str, int] | None:
    width = args.get("viewport_width")
    height = args.get("viewport_height")
    if width is None or height is None:
        return None
    return {"width": int(width), "height": int(height)}


def summarize_action_locator(action: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key in ["selector", "role", "name", "label", "placeholder", "text"]:
        if action.get(key) is not None:
            summary[key] = action.get(key)
    return summary


def normalize_route(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return "/"
    if re.match(r"^https?://", text, flags=re.IGNORECASE):
        return text
    if not text.startswith("/"):
        return "/" + text
    return text


def absolutize_url(base_url: str, route: str) -> str:
    if re.match(r"^https?://", route, flags=re.IGNORECASE):
        return route
    return urllib.parse.urljoin(base_url.rstrip("/") + "/", normalize_route(route).lstrip("/"))


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip().lower())
    slug = slug.strip("-._")
    return slug or "item"


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


def safe_call(fn: Any, default: Any = None) -> Any:
    try:
        return fn()
    except Exception:
        return default


def parse_json_object(text: str) -> dict[str, Any] | None:
    raw = text.strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        return None
    candidate = match.group(0)
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def tail_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def default_config_toml() -> str:
    return textwrap.dedent(
        '''
        [vllm]
        base_url = "http://localhost:8000/v1"
        api_key = "dev"
        model = "Qwen/Qwen3-Coder-30B-A3B-Instruct"
        temperature = 0.2
        top_p = 0.95
        max_tokens = 4096
        request_timeout_sec = 600

        [agent]
        max_cycles = 8
        max_tool_steps = 60
        apply_patch_on_success = false
        read_chunk_lines = 220
        max_file_bytes = 120000
        max_context_chars = 90000
        tool_command_timeout_sec = 120
        verify_timeout_sec = 1200

        [repo]
        ignore_globs = [
          ".git/**",
          ".agent_runs/**",
          ".venv/**",
          "venv/**",
          "node_modules/**",
          "dist/**",
          "build/**",
          ".next/**",
          ".turbo/**",
          ".pytest_cache/**",
          "__pycache__/**",
        ]
        include_files = [
          "README.md",
          "AGENTS.md",
          "pyproject.toml",
          "requirements.txt",
          "package.json",
          "package-lock.json",
          "pnpm-lock.yaml",
          "yarn.lock",
          "vite.config.*",
          "next.config.*",
          "src/**",
          "app/**",
          "components/**",
          "pages/**",
          "public/**",
        ]

        [commands]
        setup = []
        verify = [
          "npm run lint",
          "npm run build",
          "npx playwright test --reporter=line"
        ]
        allow_command_prefixes = []
        blocked_command_patterns = [
          "(^|[;&|])\\\\s*sudo\\\\b",
          "(^|[;&|])\\\\s*(shutdown|reboot|halt|poweroff)\\\\b",
          "(^|[;&|])\\\\s*rm\\\\s+-rf\\\\s+/",
          "(^|[;&|])\\\\s*(mkfs|fdisk|dd)\\\\b",
          "(^|[;&|])\\\\s*(curl|wget)\\\\b.*\\\\|\\\\s*(sh|bash|zsh)\\\\b",
          "(^|[;&|])\\\\s*git\\\\s+(push|reset\\\\s+--hard|clean\\\\s+-fdx|tag\\\\b)\\\\b",
        ]

        [commands.env]
        PYTHONUNBUFFERED = "1"

        [frontend]
        enabled = true
        base_url = "http://127.0.0.1:3000"
        start_command = "npm run dev -- --host 127.0.0.1 --port 3000"
        ready_url = "http://127.0.0.1:3000"
        ready_timeout_sec = 120
        browser = "chromium"
        # executable_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        # Leave this unset when using `playwright install chromium`.
        headless = true
        viewport_width = 1440
        viewport_height = 1024
        wait_until = "load"
        wait_after_load_ms = 1000
        action_timeout_ms = 10000
        # The automatic review opens every configured page at these viewports.
        # Keep this small until the loop is stable, then add tablet/wide views as needed.
        viewports = [
          { name = "desktop", width = 1440, height = 1024 },
          { name = "mobile", width = 390, height = 844 },
        ]
        trace_enabled = true
        screenshot_enabled = true
        full_page_screenshot = true
        review_each_cycle = true
        require_review_before_finish = true
        default_paths = ["/", "/login", "/dashboard"]
        design_brief = """
        Create a product-quality UI with strong hierarchy, obvious CTA emphasis, generous spacing,
        readable typography, consistent radii/shadows, and clear loading/error/empty states.
        Avoid generic starter-template visuals.
        """

        [[frontend.pages]]
        path = "/"
        name = "home"

        [[frontend.pages]]
        path = "/login"
        name = "login"
        wait_for_text = "Sign in"

        [[frontend.pages]]
        path = "/dashboard"
        name = "dashboard"

        [vision]
        enabled = false
        base_url = "http://localhost:8001/v1"
        api_key = "dev"
        model = "Qwen/Qwen2.5-VL-7B-Instruct"
        temperature = 0.1
        top_p = 0.9
        max_tokens = 1200
        '''
    ).strip() + "\n"

def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Repository-aware autonomous coding loop with a frontend browser review layer for vLLM."
    )
    parser.add_argument("--repo", help="Repository root or project directory")
    parser.add_argument("--config", help="Path to TOML config file")
    parser.add_argument("--task-file", help="Path to markdown or text task file")
    parser.add_argument("--task-text", help="Inline task description")
    parser.add_argument(
        "--runs-dir",
        help="Directory to store run artifacts (default: REPO/.<repo_name>_agent_runs)",
    )
    parser.add_argument(
        "--print-default-config",
        action="store_true",
        help="Print a sample config and exit",
    )
    parser.add_argument(
        "--apply-on-success",
        action="store_true",
        help="Apply the verified final patch back to the source Git repository.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.print_default_config:
        print(default_config_toml())
        return 0

    if not args.repo:
        raise SystemExit("--repo is required unless --print-default-config is used")

    repo = pathlib.Path(args.repo).resolve()
    if not repo.exists() or not repo.is_dir():
        raise SystemExit(f"Repository path does not exist or is not a directory: {repo}")

    config_path = pathlib.Path(args.config) if args.config else None
    base_config, frontend_config, vision_config = load_extended_config(config_path)
    if args.apply_on_success:
        base_config.apply_patch_on_success = True
    task_text = core.read_task(
        task_file=pathlib.Path(args.task_file) if args.task_file else None,
        task_text=args.task_text,
    )
    run_base = pathlib.Path(args.runs_dir) if args.runs_dir else (repo.parent / f'.{repo.name}_agent_runs')
    run_root = core.make_run_root(run_base)
    logger = core.JsonlLogger(run_root / "events.jsonl")
    logger.log(
        "run_started",
        {
            "repo": str(repo),
            "run_root": str(run_root),
            "config": dataclasses.asdict(base_config),
            "frontend": dataclasses.asdict(frontend_config),
            "vision": dataclasses.asdict(vision_config),
        },
    )
    workspace = core.Workspace.create(repo, run_root, base_config.ignore_globs, logger)
    agent = VisualCodingAgent(
        config=base_config,
        workspace=workspace,
        task_text=task_text,
        run_root=run_root,
        logger=logger,
        frontend_config=frontend_config,
        vision_config=vision_config,
    )
    summary = agent.run()
    print(core.pretty_json(summary))
    return 0 if summary["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
