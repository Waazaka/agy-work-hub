#!/usr/bin/env python3
"""
Hub Collaboration Guard (PreToolUse)
结构化解析会话 transcript.jsonl 与命令语义，守住人机协作秩序与独立测试边界：
1. 终生角色由出生输入锁定，彻底忽略 MODEL 输出（PLANNER_RESPONSE / GENERIC 等工具返回值）。
2. 总控主会话仅读写 Hub 控制面与临时目录；外部业务源码须独立派单或经人类当轮显式豁免。
3. Swarm / 子代理按各自点名消息隔离解析角色与阶段开关，防止公共看板串台。
4. 跨平台动态探测 Hub 控制面与活跃工作区根，防独立工作区误放行，防 /tmp 模拟工作区穿透。
5. 保护全局共享文件免于整篇覆盖、管控看板体积上限、拦截会破坏人类未提交改动的 Git 命令。
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import shlex
import sys
import tempfile
from typing import Optional

MAX_BOARD_LINES = 220
MAX_BOARD_BYTES = 36864

TEST_FILE_RE = re.compile(
    r"(\.(test|spec)\.[a-zA-Z0-9]+$"
    r"|(^|/)test_[^/]+\.py$"
    r"|_test\.(go|py|rs)$"
    r"|/(?:__tests__|tests)/.+\.(?:py|js|jsx|ts|tsx|mjs|cjs)$)"
)

HUB_ROOT_FILES = frozenset(
    {
        "DASHBOARD.md",
        "AGENTS.md",
        "README.md",
        "ARCHITECTURE_GUIDE.md",
        "LICENSE",
        ".gitignore",
    }
)
HUB_SUBDIRS = ("boards", "playbook", ".agents")
HUB_DIR_NAMES = ("hub", "agy-work-hub")

HUB_PATH_PATTERN_RE = re.compile(
    r"(?:^|/)(?:hub|agy-work-hub)/"
    r"(?:DASHBOARD\.md|AGENTS\.md|README\.md|ARCHITECTURE_GUIDE\.md|LICENSE|\.gitignore"
    r"|(?:boards|playbook|\.agents)(?:/|$))"
)
BOARD_OR_DASHBOARD_PATTERN_RE = re.compile(
    r"(?:^|/)(?:hub|agy-work-hub)/(?:DASHBOARD\.md|boards/.+)"
)
SHARED_GLOBAL_DOC_PATTERN_RE = re.compile(
    r"(?:^|/)(?:hub|agy-work-hub)/(?:DASHBOARD\.md|playbook/.+)"
)

ROLE_TAG_PREFIX = "HUB_ROLE:"
ROLE_IMPL = "IMPL"
ROLE_VERIFIER = "VERIFIER"
ROLE_TESTER = "TESTER"
ROLE_PLANNER = "PLANNER"
ROLE_PROJECT_LEAD = "PROJECT_LEAD"
ROLE_COORDINATOR = "COORDINATOR"


def _tag(body: str) -> str:
    return "[" + body + "]"


def _role_tag(role: str) -> str:
    return _tag(ROLE_TAG_PREFIX + role)


TAG_ALLOW_RED = _tag("ALLOW_RED_TEST")
TAG_REVOKE_RED = _tag("REVOKE_RED_TEST")
TAG_LOCK_TESTS = _tag("LOCK_TESTS")
TAG_UNLOCK_TESTS = _tag("UNLOCK_TESTS")

ROLE_TAG_RE = re.compile(r"\[" + ROLE_TAG_PREFIX + r"([A-Z0-9_]+)\]")
LEADING_ROLE_TAG_RE = re.compile(
    r"^\s*(?:@[a-zA-Z0-9_-]+\s+)?\[" + ROLE_TAG_PREFIX + r"([A-Z0-9_]+)\]"
)
SYSTEM_PREAMBLE_RE = re.compile(
    r"The following is a <SYSTEM_MESSAGE> not actually sent by the user\.[^\n]*\n*"
)
SYSTEM_MSG_BLOCK_RE = re.compile(r"<SYSTEM_MESSAGE>\s*(.*?)\s*</SYSTEM_MESSAGE>", re.DOTALL)
MSG_CONTENT_RE = re.compile(r"^\[Message\][^\n]*?\bcontent=(.*)$", re.DOTALL)
SWARM_ASSIGNMENT_RE = re.compile(
    r"You have just started\.\s*Your assignment\s*"
    r"\(recorded on the board as #\d+ for @([a-zA-Z0-9_-]+)\):\s*(.*)",
    re.DOTALL,
)
SWARM_WORKER_SELF_RE = re.compile(
    r"(?:<swarm_worker>\s*)?You are ([a-zA-Z0-9_-]+),\s*one agent in a swarm\b",
    re.IGNORECASE,
)
BOARD_LINE_PREFIX_RE = re.compile(
    r"^(?:[-*]\s*)?"
    r"(?:"
    r"(?:#\d+|\[#?\d+\]|seq\s*[:=]?\s*\d+)\s*(?:[a-zA-Z0-9_-]+:\s*|\([^)]*\):?\s*|\[[^\]]*\]:?\s*)?"
    r"|\[(?:swarm|board|msg)[^\]]*\]\s*(?:#\d+\s*)?"
    r"|(?:New board message(?:\s+from\s+[^:\n]+)?|Board\s*#\d+(?:\s+from\s+[^:\n]+)?|[a-zA-Z0-9_-]+):\s*"
    r")?"
    r"@([a-zA-Z0-9_-]+)(?=\s|$|:)"
)
EXPLICIT_BOARD_RECORD_RE = re.compile(
    r"^(?:[-*]\s*)?"
    r"(?:#\d+|\[#?\d+\]|seq\s*[:=]?\s*\d+)\s*(?:[a-zA-Z0-9_-]+:\s*|\([^)]*\):?\s*|\[[^\]]*\]:?\s*)?"
    r"@([a-zA-Z0-9_-]+)(?=\s|$|:)"
)
DIRECT_EDIT_PHRASES = ("直接改", "不用走总控", "edit directly", "bypass hub")

SHELL_WRAPPERS = frozenset({"env", "command", "sudo", "time", "nice", "nohup"})
SHELL_RUNNERS = frozenset({"sh", "bash", "zsh", "dash"})
GIT_VALUE_FLAGS = frozenset(
    {
        "-C",
        "-c",
        "--git-dir",
        "--work-tree",
        "--namespace",
        "--exec-path",
        "--super-prefix",
        "--config-env",
    }
)
GIT_NO_VALUE_FLAGS = frozenset(
    {
        "-p",
        "--paginate",
        "-P",
        "--no-pager",
        "--bare",
        "--no-replace-objects",
        "--literal-pathspecs",
        "--glob-pathspecs",
        "--noglob-pathspecs",
        "--icase-pathspecs",
        "--no-optional-locks",
        "--version",
        "--help",
        "-h",
    }
)


def allow():
    print(json.dumps({"decision": "allow"}))
    sys.exit(0)


def deny(reason: str):
    print(json.dumps({"decision": "deny", "reason": reason}, ensure_ascii=False))
    sys.exit(0)


def _is_windows_abs(slash_path: str) -> bool:
    return len(slash_path) >= 3 and slash_path[0].isalpha() and slash_path[1:3] == ":/"


def _norm_path(path: str) -> str:
    if not path:
        return ""
    slash_path = path.replace("\\", "/")
    if _is_windows_abs(slash_path):
        drive, rest = slash_path[:2], slash_path[2:]
        return drive + posixpath.normpath(rest)
    if slash_path.startswith("//"):
        return posixpath.normpath(slash_path)
    return os.path.normpath(os.path.abspath(slash_path)).replace("\\", "/")


def _norm_real(path: str) -> str:
    if not path:
        return ""
    slash_path = path.replace("\\", "/")
    if _is_windows_abs(slash_path) or slash_path.startswith("//"):
        return _norm_path(slash_path)
    return os.path.normpath(os.path.realpath(slash_path)).replace("\\", "/")


def _path_variants(path: str) -> list[str]:
    if not path:
        return []
    n = _norm_path(path)
    r = _norm_real(path)
    return [n] if n == r else [n, r]


def _is_bare_root(norm_p: str) -> bool:
    p = norm_p.rstrip("/")
    if not p:
        return True
    if len(p) == 2 and p[0].isalpha() and p[1] == ":":
        return True
    bare_sys = {
        "/tmp",
        "/private/tmp",
        "/var",
        "/private/var",
    }
    sys_tmp = tempfile.gettempdir().replace("\\", "/").rstrip("/")
    if sys_tmp:
        bare_sys.add(sys_tmp)
        bare_sys.add(_norm_real(sys_tmp).rstrip("/"))
    return p in bare_sys


def _add_unique_variants(target_list: list[str], raw_path: str) -> None:
    for v in _path_variants(raw_path):
        if v and v not in target_list:
            target_list.append(v)


def _script_hub_root() -> str:
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    plugin_dir = os.path.dirname(scripts_dir)
    plugins_dir = os.path.dirname(plugin_dir)
    agents_dir = os.path.dirname(plugins_dir)
    if (
        os.path.basename(scripts_dir) == "scripts"
        and os.path.basename(plugin_dir) == "work-hub"
        and os.path.basename(plugins_dir) == "plugins"
        and os.path.basename(agents_dir) == ".agents"
    ):
        return _norm_path(os.path.dirname(agents_dir))
    return ""


def _looks_like_hub_dir(dir_path: str) -> bool:
    if not dir_path or not os.path.isdir(dir_path):
        return False
    for marker in ("DASHBOARD.md", "boards", "playbook"):
        if os.path.exists(os.path.join(dir_path, marker)):
            return True
    return os.path.isdir(os.path.join(dir_path, ".agents", "plugins", "work-hub"))


def resolve_context_roots(payload: dict, args: dict) -> tuple[list[str], list[str]]:
    """Return (hub_roots, active_roots) with symlinks normalized."""
    hub_roots: list[str] = []
    active_roots: list[str] = []

    def add_active(raw_p: str) -> None:
        if not raw_p:
            return
        for v in _path_variants(raw_p):
            if not _is_bare_root(v) and v not in active_roots:
                active_roots.append(v)

    env_hub = os.environ.get("WORK_HUB_ROOT", "").strip()
    if env_hub:
        _add_unique_variants(hub_roots, env_hub)
        add_active(env_hub)
        norm_env = _norm_path(env_hub).rstrip("/")
        if os.path.basename(norm_env) in HUB_DIR_NAMES:
            add_active(os.path.dirname(norm_env))

    script_root = _script_hub_root()
    if script_root:
        _add_unique_variants(hub_roots, script_root)
        add_active(script_root)
        norm_sr = script_root.rstrip("/")
        if os.path.basename(norm_sr) in HUB_DIR_NAMES:
            add_active(os.path.dirname(norm_sr))

    ws_candidates: list[str] = []
    raw_ws_list = payload.get("workspacePaths")
    if isinstance(raw_ws_list, list):
        for item in raw_ws_list:
            if isinstance(item, str) and item.strip():
                ws_candidates.append(item.strip())
    for key in ("workspacePath", "cwd"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            ws_candidates.append(val.strip())
    for key in ("Cwd", "cwd"):
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            ws_candidates.append(val.strip())

    for ws in ws_candidates:
        add_active(ws)
        norm_ws = _norm_path(ws)
        if _looks_like_hub_dir(norm_ws):
            _add_unique_variants(hub_roots, norm_ws)
        for sub in HUB_DIR_NAMES:
            _add_unique_variants(hub_roots, posixpath.join(norm_ws.rstrip("/"), sub))

    return hub_roots, active_roots


def _rel_under(candidate: str, root: str) -> Optional[str]:
    c = candidate.rstrip("/")
    r = root.rstrip("/")
    if not c or not r or _is_bare_root(r):
        return None
    if c == r:
        return ""
    prefix = r + "/"
    if c.startswith(prefix):
        return c[len(prefix):]
    return None


def is_board_or_dashboard(path: str, hub_roots: list[str] | None = None) -> bool:
    variants = _path_variants(path)
    for cand in variants:
        for root in hub_roots or ():
            rel = _rel_under(cand, root)
            if rel is not None:
                if rel == "DASHBOARD.md" or rel.startswith("boards/"):
                    return True
        if BOARD_OR_DASHBOARD_PATTERN_RE.search(cand):
            return True
    return False


def is_shared_global_doc(path: str, hub_roots: list[str] | None = None) -> bool:
    variants = _path_variants(path)
    for cand in variants:
        for root in hub_roots or ():
            rel = _rel_under(cand, root)
            if rel is not None:
                if rel == "DASHBOARD.md" or rel.startswith("playbook/"):
                    return True
        if SHARED_GLOBAL_DOC_PATTERN_RE.search(cand):
            return True
    return False


def is_hub_path(path: str, hub_roots: list[str] | None = None) -> bool:
    variants = _path_variants(path)
    for cand in variants:
        for root in hub_roots or ():
            rel = _rel_under(cand, root)
            if rel is None or rel == "":
                continue
            parts = rel.split("/")
            if (len(parts) == 1 and parts[0] in HUB_ROOT_FILES) or (parts[0] in HUB_SUBDIRS):
                return True
        if HUB_PATH_PATTERN_RE.search(cand):
            return True
    return False


def is_scratch_path(path: str, active_roots: list[str] | None = None) -> bool:
    if not path:
        return False
    variants = _path_variants(path)

    # /brain/ and /scratch/ subtrees are always scratch (even inside an active root)
    for cand in variants:
        if (
            "/brain/" in cand
            or cand.endswith("/brain")
            or "/scratch/" in cand
            or cand.endswith("/scratch")
        ):
            return True

    # Paths inside an active workspace/Hub root must NOT be bypassed by /tmp prefix
    for cand in variants:
        for root in active_roots or ():
            if _rel_under(cand, root) is not None:
                return False

    for cand in variants:
        if (
            cand == "/tmp"
            or cand.startswith("/tmp/")
            or cand == "/private/tmp"
            or cand.startswith("/private/tmp/")
        ):
            return True
        if os.name == "nt":
            sys_tmp = _norm_path(tempfile.gettempdir()).rstrip("/").lower()
            if sys_tmp and (cand.lower() == sys_tmp or cand.lower().startswith(sys_tmp + "/")):
                return True
    return False


def is_test_file(path: str) -> bool:
    return bool(TEST_FILE_RE.search(_norm_path(path)))


def check_board_size(tool_name: str, args: dict, target_path: str):
    if tool_name == "write_file":
        content = args.get("Content") or ""
        if args.get("Append") and os.path.isfile(target_path):
            try:
                with open(target_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read() + content
            except OSError:
                pass
    elif tool_name == "edit_file" and os.path.isfile(target_path):
        try:
            with open(target_path, "r", encoding="utf-8", errors="ignore") as f:
                existing = f.read()
            target = args.get("Target") or ""
            replacement = args.get("Replacement") or ""
            content = existing.replace(target, replacement) if target else existing
        except OSError:
            return
    else:
        return

    line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
    byte_count = len(content.encode("utf-8"))
    lines_exceeded = line_count > MAX_BOARD_LINES
    bytes_exceeded = byte_count > MAX_BOARD_BYTES
    if not lines_exceeded and not bytes_exceeded:
        return

    if lines_exceeded and bytes_exceeded:
        deny(
            f"Hub 护栏拦截：记忆卡/看板当前将达到 {line_count} 行 / {byte_count} bytes，"
            f"同时超过上限 {MAX_BOARD_LINES} 行与 {MAX_BOARD_BYTES} bytes。"
            "严禁堆砌过程日志，请精简提炼结论以防止上下文膨胀。"
        )
    if lines_exceeded:
        deny(
            f"Hub 护栏拦截：记忆卡/看板当前将达到 {line_count} 行（{byte_count} bytes），"
            f"行数超过上限 {MAX_BOARD_LINES} 行。严禁堆砌过程日志，请精简提炼结论以防止上下文膨胀。"
        )
    deny(
        f"Hub 护栏拦截：记忆卡/看板当前将达到 {byte_count} bytes（{line_count} 行），"
        f"字节数超过上限 {MAX_BOARD_BYTES} bytes。严禁堆砌过程日志，请精简提炼结论以防止上下文膨胀。"
    )


def _unwrap_system_blocks(raw_text: str) -> list[str]:
    cleaned = SYSTEM_PREAMBLE_RE.sub("", raw_text)
    blocks = SYSTEM_MSG_BLOCK_RE.findall(cleaned)
    if not blocks:
        stripped = cleaned.strip()
        m = MSG_CONTENT_RE.match(stripped)
        return [m.group(1).strip()] if m else [raw_text]

    results = []
    outside = SYSTEM_MSG_BLOCK_RE.sub("", cleaned).strip()
    if outside:
        results.append(outside)

    for blk in blocks:
        stripped = blk.strip()
        m = MSG_CONTENT_RE.match(stripped)
        results.append(m.group(1).strip() if m else stripped)
    return results


def _split_board_entries(text: str) -> list[tuple[Optional[str], str]]:
    stripped = text.strip()
    if not stripped:
        return []

    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict) and isinstance(obj.get("messages"), list):
                out = []
                for msg in obj["messages"]:
                    if not isinstance(msg, dict):
                        continue
                    msg_text = msg.get("text")
                    if not isinstance(msg_text, str):
                        continue
                    m = re.match(
                        r"^\s*@([a-zA-Z0-9_-]+)(?=\s|$|:)\s*:?\s*(.*)$", msg_text, re.DOTALL
                    )
                    if m:
                        out.append((m.group(1), m.group(2)))
                    else:
                        out.append((None, msg_text))
                if out:
                    return out
        except json.JSONDecodeError:
            pass

    entries: list[tuple[Optional[str], str]] = []
    current_addr: Optional[str] = None
    current_lines: list[str] = []

    for line in text.splitlines():
        m = BOARD_LINE_PREFIX_RE.match(line)
        if m:
            if current_lines:
                entries.append((current_addr, "\n".join(current_lines).strip()))
            current_addr = m.group(1)
            rest = line[m.end():].lstrip(" :")
            current_lines = [rest]
        else:
            current_lines.append(line)

    if current_lines:
        entries.append((current_addr, "\n".join(current_lines).strip()))
    return entries


def _split_assignment_block(member: str, body: str) -> list[tuple[Optional[str], str]]:
    entries: list[tuple[Optional[str], str]] = []
    current_addr: Optional[str] = member
    current_lines: list[str] = []

    for line in body.splitlines():
        m = EXPLICIT_BOARD_RECORD_RE.match(line)
        if m:
            if current_lines:
                entries.append((current_addr, "\n".join(current_lines).strip()))
            current_addr = m.group(1)
            rest = line[m.end():].lstrip(" :")
            current_lines = [rest]
        else:
            current_lines.append(line)

    if current_lines:
        entries.append((current_addr, "\n".join(current_lines).strip()))
    return entries


def _is_worker_birth_or_wrapped(content: str) -> bool:
    stripped = content.lstrip()
    if LEADING_ROLE_TAG_RE.match(stripped):
        return True
    if stripped.startswith("<SYSTEM_MESSAGE>") or stripped.startswith(
        "The following is a <SYSTEM_MESSAGE>"
    ):
        return True
    if stripped.startswith("<swarm_worker>") or SWARM_ASSIGNMENT_RE.match(stripped):
        return True
    return False


def _looks_like_worker_system_msg(content: str) -> bool:
    if _is_worker_birth_or_wrapped(content):
        return True
    if SWARM_ASSIGNMENT_RE.search(content) or SWARM_WORKER_SELF_RE.search(content):
        return True
    for line in content.splitlines():
        if BOARD_LINE_PREFIX_RE.match(line):
            return True
    return False


def _apply_switches(text: str, allow_red: bool, lock_tests: bool) -> tuple[bool, bool]:
    red_events: list[tuple[int, bool]] = []
    for tag, state in ((TAG_ALLOW_RED, True), (TAG_REVOKE_RED, False)):
        start = 0
        while True:
            idx = text.find(tag, start)
            if idx == -1:
                break
            red_events.append((idx, state))
            start = idx + len(tag)
    for _, state in sorted(red_events, key=lambda item: item[0]):
        allow_red = state

    lock_events: list[tuple[int, bool]] = []
    for tag, state in ((TAG_LOCK_TESTS, True), (TAG_UNLOCK_TESTS, False)):
        start = 0
        while True:
            idx = text.find(tag, start)
            if idx == -1:
                break
            lock_events.append((idx, state))
            start = idx + len(tag)
    for _, state in sorted(lock_events, key=lambda item: item[0]):
        lock_tests = state

    return allow_red, lock_tests


def _read_input_steps(transcript_path: str) -> list[dict]:
    steps: list[dict] = []
    if not transcript_path or not os.path.isfile(transcript_path):
        return steps
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                if obj.get("source") == "MODEL":
                    continue
                if obj.get("type") not in ("USER_INPUT", "SYSTEM_MESSAGE"):
                    continue
                if not isinstance(obj.get("content"), str):
                    continue
                steps.append(obj)
    except OSError:
        return []
    return steps


def analyze_session(payload: dict) -> tuple[Optional[str], bool, bool, bool]:
    transcript_path = payload.get("transcriptPath") or ""
    last_user_input = (payload.get("lastUserInput") or "").strip()
    steps = _read_input_steps(transcript_path)

    if not steps:
        if not last_user_input:
            return None, False, False, False
        steps = [{"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": last_user_input}]

    role: Optional[str] = None
    self_member: Optional[str] = (
        payload.get("agentName").strip()
        if isinstance(payload.get("agentName"), str) and payload.get("agentName").strip()
        else None
    )
    allow_red = False
    lock_tests = False
    explicit_user_inputs: list[str] = []
    seen_contents: list[str] = []

    for step in steps:
        step_type = step.get("type")
        source = step.get("source") or ""
        content = step.get("content") or ""
        seen_contents.append(content.strip())

        if step_type == "USER_INPUT" and source == "USER_EXPLICIT":
            explicit_user_inputs.append(content)

        if role == ROLE_COORDINATOR:
            continue

        if role is None:
            if (
                step_type == "USER_INPUT"
                and source == "USER_EXPLICIT"
                and not _is_worker_birth_or_wrapped(content)
            ):
                role = ROLE_COORDINATOR
                continue

            if step_type == "SYSTEM_MESSAGE" and not _looks_like_worker_system_msg(content):
                continue

        blocks = _unwrap_system_blocks(content)
        entries_for_step: list[tuple[Optional[str], str, bool]] = []

        for blk in blocks:
            m_self = SWARM_WORKER_SELF_RE.search(blk)
            if m_self and not self_member:
                self_member = m_self.group(1)

            m_assign = SWARM_ASSIGNMENT_RE.search(blk)
            if m_assign:
                assigned_member = m_assign.group(1)
                if not self_member:
                    self_member = assigned_member
                prefix_text = blk[:m_assign.start()].strip()
                if prefix_text:
                    for addr, body in _split_board_entries(prefix_text):
                        entries_for_step.append((addr, body, False))
                for addr, body in _split_assignment_block(
                    assigned_member, m_assign.group(2).strip()
                ):
                    entries_for_step.append((addr, body, True))
            else:
                for addr, body in _split_board_entries(blk):
                    entries_for_step.append((addr, body, False))

        has_directed = any(addr is not None for addr, _, _ in entries_for_step)
        matching_bodies: list[str] = []

        for addr, body, from_assignment in entries_for_step:
            if self_member:
                if addr is not None:
                    if addr.lower() == self_member.lower():
                        matching_bodies.append(body)
                elif not has_directed and (from_assignment or step_type == "USER_INPUT"):
                    matching_bodies.append(body)
            else:
                if addr is None:
                    matching_bodies.append(body)
                elif len([1 for a, _, _ in entries_for_step if a is not None]) == 1:
                    self_member = addr
                    matching_bodies.append(body)

        for body in matching_bodies:
            if role is None:
                m_role = LEADING_ROLE_TAG_RE.match(body) or ROLE_TAG_RE.search(body)
                if m_role:
                    role = m_role.group(1)
            if role is not None:
                allow_red, lock_tests = _apply_switches(body, allow_red, lock_tests)

    if role in (ROLE_IMPL, ROLE_VERIFIER, ROLE_TESTER) and last_user_input:
        if not any(last_user_input in s for s in seen_contents):
            for blk in _unwrap_system_blocks(last_user_input):
                for addr, body in _split_board_entries(blk):
                    if self_member:
                        if addr is not None and addr.lower() == self_member.lower():
                            allow_red, lock_tests = _apply_switches(body, allow_red, lock_tests)
                    elif addr is None:
                        allow_red, lock_tests = _apply_switches(body, allow_red, lock_tests)

    direct_edit_exempt = False
    if role == ROLE_COORDINATOR:
        latest_explicit = explicit_user_inputs[-1].strip() if explicit_user_inputs else ""
        active_ui = last_user_input if last_user_input else latest_explicit
        active_lower = active_ui.lower()
        direct_edit_exempt = any(
            phrase in active_ui or phrase in active_lower for phrase in DIRECT_EDIT_PHRASES
        )

    return role, allow_red, lock_tests, direct_edit_exempt


def _split_shell_segments(cmd: str) -> list[str]:
    segments: list[str] = []
    buf: list[str] = []
    in_single = False
    in_double = False
    escape = False
    i = 0
    n = len(cmd)

    while i < n:
        ch = cmd[i]
        if escape:
            buf.append(ch)
            escape = False
            i += 1
            continue
        if in_single:
            if ch == "'":
                in_single = False
            buf.append(ch)
            i += 1
            continue
        if in_double:
            if ch == "\\":
                escape = True
            elif ch == '"':
                in_double = False
            buf.append(ch)
            i += 1
            continue

        if ch == "\\":
            escape = True
            buf.append(ch)
            i += 1
            continue
        if ch == "'":
            in_single = True
            buf.append(ch)
            i += 1
            continue
        if ch == '"':
            in_double = True
            buf.append(ch)
            i += 1
            continue

        if ch in ("&", "|") and i + 1 < n and cmd[i + 1] == ch:
            seg = "".join(buf).strip()
            if seg:
                segments.append(seg)
            buf = []
            i += 2
            continue
        if ch in (";", "|", "&", "\n"):
            seg = "".join(buf).strip()
            if seg:
                segments.append(seg)
            buf = []
            i += 1
            continue

        buf.append(ch)
        i += 1

    seg = "".join(buf).strip()
    if seg:
        segments.append(seg)
    return segments


def _tokenize_segment(seg: str) -> list[str]:
    stripped = seg.strip()
    while stripped.startswith("(") and stripped.endswith(")"):
        stripped = stripped[1:-1].strip()
    if stripped.startswith("("):
        stripped = stripped.lstrip("( ")
    if stripped.endswith(")"):
        stripped = stripped.rstrip(" )")
    if not stripped:
        return []
    try:
        return shlex.split(stripped, posix=True)
    except ValueError:
        return stripped.split()


def _strip_cmd_prefixes(tokens: list[str]) -> list[str]:
    idx = 0
    while idx < len(tokens):
        tok = tokens[idx]
        if "=" in tok and not tok.startswith("-") and tok.split("=", 1)[0].replace("_", "").isalnum():
            idx += 1
            continue
        base = os.path.basename(tok)
        if base in SHELL_WRAPPERS:
            idx += 1
            continue
        break
    return tokens[idx:]


def _check_git_invocation(git_args: list[str], cwd: str, active_roots: list[str]):
    i = 0
    repo_dir = ""
    while i < len(git_args):
        tok = git_args[i]
        if tok == "--":
            i += 1
            break
        if tok in GIT_VALUE_FLAGS:
            if tok in ("-C", "--work-tree") and i + 1 < len(git_args):
                repo_dir = git_args[i + 1]
            i += 2
            continue
        if any(tok.startswith(flag + "=") for flag in GIT_VALUE_FLAGS if flag.startswith("--")):
            if tok.startswith("--work-tree="):
                repo_dir = tok.split("=", 1)[1]
            i += 1
            continue
        if tok.startswith("-C") and len(tok) > 2:
            repo_dir = tok[2:]
            i += 1
            continue
        if tok.startswith("-c") and len(tok) > 2:
            i += 1
            continue
        if tok in GIT_NO_VALUE_FLAGS or tok.startswith("-"):
            i += 1
            continue
        break

    if i >= len(git_args):
        return

    subcmd = git_args[i]
    sub_args = git_args[i + 1:]

    effective_dir = cwd
    if repo_dir:
        if os.path.isabs(repo_dir) or _is_windows_abs(repo_dir.replace("\\", "/")):
            effective_dir = repo_dir
        elif cwd:
            effective_dir = os.path.normpath(os.path.join(cwd, repo_dir))
        else:
            effective_dir = repo_dir

    in_scratch_repo = is_scratch_path(effective_dir, active_roots)
    if in_scratch_repo:
        return

    if "--" in sub_args:
        sep_idx = sub_args.index("--")
        opts = sub_args[:sep_idx]
        has_dash_dash = True
    else:
        opts = sub_args
        has_dash_dash = False

    if subcmd == "reset" and any(a == "--hard" for a in opts):
        deny(
            "Hub 护栏拦截：严禁执行 `git reset --hard`，会直接销毁人类未提交的现场创作与本地改动。"
        )

    if subcmd == "clean":
        for a in opts:
            if a == "--force" or (a.startswith("-") and not a.startswith("--") and "f" in a[1:]):
                deny(
                    "Hub 护栏拦截：严禁执行带 `-f` 的 `git clean`，会永久删除未跟踪的工作区文件。"
                )

    if subcmd == "checkout":
        has_branch_create = any(a in ("-b", "-B", "--orphan") for a in opts)
        positional = [a for a in opts if not a.startswith("-")]
        if has_dash_dash or (not has_branch_create and any(p in (".", "./") for p in positional)):
            deny(
                "Hub 护栏拦截：严禁执行 `git checkout --` / `git checkout .`，会覆盖工作区未提交的修改。"
            )

    if subcmd == "restore":
        if any(a in ("-h", "--help") for a in opts):
            return
        has_staged = any(
            a == "--staged" or (a.startswith("-") and not a.startswith("--") and "S" in a[1:])
            for a in opts
        )
        has_worktree = any(
            a == "--worktree" or (a.startswith("-") and not a.startswith("--") and "W" in a[1:])
            for a in opts
        )
        if not (has_staged and not has_worktree):
            deny(
                "Hub 护栏拦截：严禁对工作区执行破坏性 `git restore`（仅放行纯暂存区 `--staged` 操作），以免丢失人类未暂存修改。"
            )

    if subcmd == "add":
        for a in opts:
            if a == "--all" or (a.startswith("-") and not a.startswith("--") and "A" in a[1:]):
                deny(
                    "Hub 护栏拦截：严禁在业务仓执行 `git add -A` / `git add --all`（易误把人类临时草稿或敏感文件全部暂存），请显式指定文件路径 `git add <file>`。"
                )


def _extract_shell_runner_cmd(tokens: list[str]) -> Optional[str]:
    idx = 1
    has_c = False
    while idx < len(tokens):
        tok = tokens[idx]
        if tok == "--":
            idx += 1
            break
        if tok in ("-o", "+o"):
            idx += 2
            continue
        if not tok.startswith(("-", "+")) or tok in ("-", "+"):
            break
        if tok.startswith("-") and not tok.startswith("--") and tok[1:].isalpha() and "c" in tok[1:]:
            has_c = True
        idx += 1
    if has_c and idx < len(tokens):
        return tokens[idx]
    return None


def _check_shell_command(cmd: str, cwd: str, active_roots: list[str]):
    current_cwd = cwd
    for seg in _split_shell_segments(cmd):
        tokens = _strip_cmd_prefixes(_tokenize_segment(seg))
        if not tokens:
            continue
        base = os.path.basename(tokens[0])

        if base == "cd":
            if len(tokens) == 1:
                current_cwd = ""
            else:
                target = tokens[1]
                if os.path.isabs(target) or _is_windows_abs(target.replace("\\", "/")):
                    current_cwd = _norm_path(target)
                elif current_cwd:
                    current_cwd = _norm_path(os.path.join(current_cwd, target))
                else:
                    current_cwd = ""
            continue

        if base in SHELL_RUNNERS:
            nested_cmd = _extract_shell_runner_cmd(tokens)
            if nested_cmd is not None:
                _check_shell_command(nested_cmd, current_cwd, active_roots)
            continue

        if base == "git":
            _check_git_invocation(tokens[1:], current_cwd, active_roots)


def main():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            allow()
        payload = json.loads(raw)
    except Exception:
        allow()

    tool_call = payload.get("toolCall") or {}
    tool_name = tool_call.get("name") or ""
    args = tool_call.get("args") or {}
    hub_roots, active_roots = resolve_context_roots(payload, args)

    if tool_name == "run_command":
        cmd = args.get("CommandLine") or args.get("command") or ""
        cwd = args.get("Cwd") or args.get("cwd") or payload.get("cwd") or ""
        if cmd:
            _check_shell_command(cmd, cwd, active_roots)
        allow()

    target_path = args.get("Path") or ""
    if tool_name not in ("write_file", "edit_file") or not target_path:
        allow()

    if (
        tool_name == "write_file"
        and args.get("Overwrite")
        and is_shared_global_doc(target_path, hub_roots)
        and os.path.isfile(target_path)
    ):
        deny(
            "Hub 多总控并发护栏：全局共享文件 DASHBOARD.md / playbook 严禁使用 write_file(Overwrite=true) 整文件重写"
            "（会覆盖其他并行总控窗口的修改）。请使用 edit_file 仅局部更新对应行。"
        )

    if is_board_or_dashboard(target_path, hub_roots):
        check_board_size(tool_name, args, target_path)

    if is_scratch_path(target_path, active_roots):
        allow()

    role, allow_red, lock_tests, direct_edit_exempt = analyze_session(payload)

    if role == ROLE_PLANNER:
        deny(
            f"Hub 护栏拦截：主审核查官 {_role_tag(ROLE_PLANNER)}（PLANNER）只读审视现场并在 Swarm 看板输出"
            "《施工蓝图与对抗 DoD》交由 IMPL 执行，严禁直接改码或改看板（仅允许写入 /brain/ 与 /tmp/ 临时路径）。"
        )

    if role in (ROLE_VERIFIER, ROLE_TESTER):
        if (
            allow_red
            and is_test_file(target_path)
            and not is_board_or_dashboard(target_path, hub_roots)
        ):
            allow()
        deny(
            f"Hub 护栏拦截：独立测试/验收专员 {_role_tag(role)} 严禁修改业务源码或看板"
            f"（仅在显式携带 {TAG_ALLOW_RED} 时允许编写非看板测试文件）。请将失败步骤打回由 IMPL 修复。"
        )

    if role == ROLE_IMPL:
        if lock_tests and is_test_file(target_path):
            deny(
                f"Hub 护栏拦截：当前处于对抗式修 Bug 锁死断言阶段（{TAG_LOCK_TESTS}），"
                "严禁 IMPL 修改或放宽测试文件断言！请只修改业务实现源码使现有失败测试变绿。"
            )
        allow()

    if role == ROLE_COORDINATOR:
        if is_hub_path(target_path, hub_roots) or direct_edit_exempt:
            allow()
        deny(
            "Hub 护栏拦截：总控（COORDINATOR）主会话仅可维护 Hub 控制面文档（DASHBOARD.md / boards/ / playbook/）"
            "与 /brain/、/tmp/ 临时文件，严禁直接修改外部业务项目代码。请拉起独立会话执行，"
            "或由人类明确指示「直接改 / 不用走总控 / edit directly」。"
        )

    if role == ROLE_PROJECT_LEAD and not is_hub_path(target_path, hub_roots):
        deny(
            f"Hub 护栏拦截：项目主理人 {_role_tag(ROLE_PROJECT_LEAD)} 仅可维护 Hub 控制面看板（boards/ / DASHBOARD.md），严禁亲自修改业务代码。"
        )

    allow()


if __name__ == "__main__":
    main()
