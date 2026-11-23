#!/usr/bin/env python3
"""Zero-dependency cross-platform unit tests for hub_guard.py running via subprocess."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from typing import Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GUARD_PATH = os.path.join(SCRIPT_DIR, "hub_guard.py")


def make_tag(body: str) -> str:
    return "[" + body + "]"


ROLE_IMPL = make_tag("HUB_ROLE:IMPL")
ROLE_VERIFIER = make_tag("HUB_ROLE:VERIFIER")
ROLE_TESTER = make_tag("HUB_ROLE:TESTER")
ROLE_PLANNER = make_tag("HUB_ROLE:PLANNER")
ALLOW_RED = make_tag("ALLOW_RED_TEST")
REVOKE_RED = make_tag("REVOKE_RED_TEST")
LOCK_TESTS = make_tag("LOCK_TESTS")
UNLOCK_TESTS = make_tag("UNLOCK_TESTS")


def run_guard(payload: dict, env_override: Optional[dict[str, str]] = None) -> dict:
    env = os.environ.copy()
    env.pop("WORK_HUB_ROOT", None)
    if env_override:
        env.update(env_override)
    proc = subprocess.run(
        [sys.executable, GUARD_PATH],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"hub_guard.py exited with {proc.returncode}: stderr={proc.stderr!r}"
        )
    try:
        return json.loads(proc.stdout.strip())
    except json.JSONDecodeError as exc:
        raise AssertionError(f"Invalid JSON stdout: {proc.stdout!r}") from exc


def write_jsonl(path: str, steps: list[dict]):
    with open(path, "w", encoding="utf-8") as f:
        for idx, step in enumerate(steps):
            row = {"step_index": idx, "status": "DONE"}
            row.update(step)
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def wrap_system_message(body: str) -> str:
    return (
        "The following is a <SYSTEM_MESSAGE> not actually sent by the user. "
        "It is provided by the system as important information to pay attention to.\n\n"
        "<SYSTEM_MESSAGE>\n"
        "[Message] timestamp=2026-11-23T09:00:00Z sender=coord priority=MESSAGE_PRIORITY_HIGH "
        f"content={body}\n"
        "</SYSTEM_MESSAGE>"
    )


class HubGuardTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = self._tmp.name
        self.transcript_path = os.path.join(self.tmp_dir, "transcript.jsonl")

    def tearDown(self):
        self._tmp.cleanup()

    def test_01_coordinator_ignores_tool_outputs_and_swarm_add_allows_board_edit(self):
        write_jsonl(
            self.transcript_path,
            [
                {
                    "source": "USER_EXPLICIT",
                    "type": "USER_INPUT",
                    "content": "请盘点项目进度并派单给 impl 和 verifier",
                },
                {
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "tool_calls": [
                        {
                            "name": "swarm",
                            "args": {
                                "action": "add",
                                "name": "verifier",
                                "brief": f"{ROLE_VERIFIER} {ALLOW_RED} 验收",
                            },
                        },
                        {
                            "name": "swarm",
                            "args": {
                                "action": "add",
                                "name": "impl",
                                "brief": f"{ROLE_IMPL} {LOCK_TESTS} 实现",
                            },
                        },
                    ],
                },
                {
                    "source": "MODEL",
                    "type": "GENERIC",
                    "content": f"SKILL.md content: {ROLE_VERIFIER} {ALLOW_RED} {LOCK_TESTS}",
                },
                {
                    "source": "SYSTEM",
                    "type": "SYSTEM_MESSAGE",
                    "content": wrap_system_message(
                        f"@coordinator @verifier {ROLE_VERIFIER} 已完成验收"
                    ),
                },
            ],
        )

        res = run_guard(
            {
                "transcriptPath": self.transcript_path,
                "lastUserInput": "请盘点项目进度并派单给 impl 和 verifier",
                "toolCall": {
                    "name": "edit_file",
                    "args": {
                        "Path": "/workspace/hub/boards/demo.md",
                        "Target": "old",
                        "Replacement": "new",
                    },
                },
            }
        )
        self.assertEqual(res["decision"], "allow")

    def test_02_coordinator_external_code_default_deny_and_single_turn_direct_exempt(self):
        write_jsonl(
            self.transcript_path,
            [
                {
                    "source": "USER_EXPLICIT",
                    "type": "USER_INPUT",
                    "content": "帮我看看 demo-app 的登录 bug",
                }
            ],
        )
        payload = {
            "transcriptPath": self.transcript_path,
            "lastUserInput": "帮我看看 demo-app 的登录 bug",
            "toolCall": {
                "name": "edit_file",
                "args": {
                    "Path": "/workspace/demo-app/src/auth.py",
                    "Target": "a",
                    "Replacement": "b",
                },
            },
        }
        res = run_guard(payload)
        self.assertEqual(res["decision"], "deny")
        self.assertIn("直接改", res["reason"])
        self.assertIn("不用走总控", res["reason"])

        # Turn with "不用走总控" followed by a read_file step still allows edit_file
        write_jsonl(
            self.transcript_path,
            [
                {
                    "source": "USER_EXPLICIT",
                    "type": "USER_INPUT",
                    "content": "帮我看看 demo-app 的登录 bug",
                },
                {
                    "source": "USER_EXPLICIT",
                    "type": "USER_INPUT",
                    "content": "不用走总控，你先把 auth.py 改了",
                },
                {
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "tool_calls": [
                        {"name": "read_file", "args": {"Path": "/workspace/demo-app/src/auth.py"}}
                    ],
                },
                {
                    "source": "MODEL",
                    "type": "GENERIC",
                    "content": "def login(): pass",
                },
            ],
        )
        payload["lastUserInput"] = "不用走总控，你先把 auth.py 改了"
        self.assertEqual(run_guard(payload)["decision"], "allow")

        # English direct edit phrases work too when transcript not flushed yet
        for phrase in ("直接改这个文件吧", "Please edit directly", "bypass hub for this quick fix"):
            unflushed_payload = {
                "transcriptPath": os.path.join(self.tmp_dir, "missing.jsonl"),
                "lastUserInput": phrase,
                "toolCall": payload["toolCall"],
            }
            self.assertEqual(run_guard(unflushed_payload)["decision"], "allow", phrase)

        # Next human turn without exemption automatically revokes direct edit exemption
        write_jsonl(
            self.transcript_path,
            [
                {
                    "source": "USER_EXPLICIT",
                    "type": "USER_INPUT",
                    "content": "不用走总控，直接改 auth.py",
                },
                {
                    "source": "USER_EXPLICIT",
                    "type": "USER_INPUT",
                    "content": "下一轮新任务：顺便分析一下 order.py",
                },
            ],
        )
        payload["lastUserInput"] = "下一轮新任务：顺便分析一下 order.py"
        self.assertEqual(run_guard(payload)["decision"], "deny")

        # Unflushed new lastUserInput revokes immediately
        write_jsonl(
            self.transcript_path,
            [
                {
                    "source": "USER_EXPLICIT",
                    "type": "USER_INPUT",
                    "content": "直接改 auth.py",
                }
            ],
        )
        payload["lastUserInput"] = "下一轮新任务：别改代码"
        self.assertEqual(run_guard(payload)["decision"], "deny")

        # Repeated earlier phrase ("看看看板") before flush must NOT leak Turn 2 exemption
        write_jsonl(
            self.transcript_path,
            [
                {"source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "看看看板"},
                {"source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "不用走总控，直接改 a.py"},
            ],
        )
        res_repeat_phrase = run_guard(
            {
                "transcriptPath": self.transcript_path,
                "lastUserInput": "看看看板",
                "toolCall": {
                    "name": "edit_file",
                    "args": {
                        "Path": "/workspace/demo/b.py",
                        "Target": "a",
                        "Replacement": "b",
                    },
                },
            }
        )
        self.assertEqual(res_repeat_phrase["decision"], "deny")

    def test_03_swarm_multi_brief_isolation_no_cross_contamination(self):
        # Case A: Real SYSTEM_MESSAGE structure for impl + subsequent @verifier message
        write_jsonl(
            self.transcript_path,
            [
                {
                    "source": "SYSTEM",
                    "type": "SYSTEM_MESSAGE",
                    "content": wrap_system_message("[swarm] new board message #1"),
                },
                {
                    "source": "SYSTEM",
                    "type": "SYSTEM_MESSAGE",
                    "content": wrap_system_message(
                        "You have just started. Your assignment (recorded on the board as #1 for @impl):\n\n"
                        f"{ROLE_IMPL} 请修改 /workspace/demo-app/src/service.py（注意不要带 LOCK_TESTS 字样误伤）"
                    ),
                },
                {
                    "source": "SYSTEM",
                    "type": "SYSTEM_MESSAGE",
                    "content": wrap_system_message(
                        f"@verifier {ROLE_VERIFIER} {ALLOW_RED} 请构造边界测试用例"
                    ),
                },
                {
                    "source": "MODEL",
                    "type": "GENERIC",
                    "content": f'{{"messages":[{{"seq":2,"text":"@verifier {ROLE_VERIFIER} {ALLOW_RED}"}}]}}',
                },
            ],
        )
        res_impl = run_guard(
            {
                "transcriptPath": self.transcript_path,
                "lastUserInput": "",
                "toolCall": {
                    "name": "edit_file",
                    "args": {
                        "Path": "/workspace/demo-app/src/service.py",
                        "Target": "x",
                        "Replacement": "y",
                    },
                },
            }
        )
        self.assertEqual(res_impl["decision"], "allow")

        # Case B: Single birth message containing <swarm_worker> and multiple board records
        write_jsonl(
            self.transcript_path,
            [
                {
                    "source": "SYSTEM",
                    "type": "USER_INPUT",
                    "content": (
                        "<swarm_worker>\n"
                        "You are impl, one agent in a swarm working together on one task.\n"
                        "</swarm_worker>\n"
                        f"#1 coordinator: @verifier {ROLE_VERIFIER} {ALLOW_RED} 验收登录\n"
                        f"#2 coordinator: @impl {ROLE_IMPL} 修复登录源码\n"
                    ),
                }
            ],
        )
        self.assertEqual(
            run_guard(
                {
                    "transcriptPath": self.transcript_path,
                    "lastUserInput": "",
                    "toolCall": {
                        "name": "write_file",
                        "args": {"Path": "/workspace/demo-app/src/login.py", "Content": "pass"},
                    },
                }
            )["decision"],
            "allow",
        )

        # Case C: Verifier in same multi-record board must stay VERIFIER and be denied on source code
        write_jsonl(
            self.transcript_path,
            [
                {
                    "source": "SYSTEM",
                    "type": "SYSTEM_MESSAGE",
                    "content": wrap_system_message(
                        "You have just started. Your assignment (recorded on the board as #2 for @verifier):\n\n"
                        f"{ROLE_VERIFIER} 盲测验收\n"
                        f"#1 coordinator: @impl {ROLE_IMPL} 修改源码"
                    ),
                }
            ],
        )
        res_ver = run_guard(
            {
                "transcriptPath": self.transcript_path,
                "lastUserInput": "",
                "toolCall": {
                    "name": "edit_file",
                    "args": {
                        "Path": "/workspace/demo-app/src/login.py",
                        "Target": "a",
                        "Replacement": "b",
                    },
                },
            }
        )
        self.assertEqual(res_ver["decision"], "deny")

    def test_04_impl_lock_tests_blocks_all_test_paths_allows_source_and_unlock(self):
        write_jsonl(
            self.transcript_path,
            [
                {
                    "source": "SYSTEM",
                    "type": "SYSTEM_MESSAGE",
                    "content": wrap_system_message(
                        "You have just started. Your assignment (recorded on the board as #1 for @impl):\n\n"
                        f"{ROLE_IMPL} {LOCK_TESTS} 只修改业务实现让测试通过"
                    ),
                }
            ],
        )

        blocked_test_paths = [
            "/workspace/demo-app/src/auth.test.ts",
            "/workspace/demo-app/src/auth.spec.jsx",
            "/workspace/demo-app/test_auth.py",
            "/workspace/demo-app/pkg/auth_test.go",
            "/workspace/demo-app/pkg/auth_test.py",
            "/workspace/demo-app/crates/auth_test.rs",
            "/workspace/demo-app/__tests__/login.ts",
            "/workspace/demo-app/src/__tests__/helper.tsx",
            "/workspace/demo-app/tests/integration/flow.py",
            "/workspace/demo-app/tests/utils.mjs",
            "/workspace/demo-app/tests/setup.cjs",
            "/workspace/demo-app/web/tests/App.js",
            r"C:\workspace\demo-app\tests\win_flow.py",
        ]
        for test_path in blocked_test_paths:
            res = run_guard(
                {
                    "transcriptPath": self.transcript_path,
                    "lastUserInput": "",
                    "toolCall": {
                        "name": "edit_file",
                        "args": {"Path": test_path, "Target": "a", "Replacement": "b"},
                    },
                }
            )
            self.assertEqual(res["decision"], "deny", f"Expected deny for {test_path}")

        for allowed_path in (
            "/workspace/demo-app/src/auth.py",
            "/workspace/demo-app/tests/fixture.json",
            "/tmp/scratch_test.py",
            "/workspace/.gemini/antigravity/brain/conv-1/scratch/test_tmp.py",
        ):
            res = run_guard(
                {
                    "transcriptPath": self.transcript_path,
                    "lastUserInput": "",
                    "toolCall": {
                        "name": "write_file",
                        "args": {"Path": allowed_path, "Content": "ok"},
                    },
                }
            )
            self.assertEqual(res["decision"], "allow", f"Expected allow for {allowed_path}")

        # UNLOCK_TESTS directed to @impl unlocks test files, while @other UNLOCK_TESTS does not
        write_jsonl(
            self.transcript_path,
            [
                {
                    "source": "SYSTEM",
                    "type": "SYSTEM_MESSAGE",
                    "content": wrap_system_message(
                        "You have just started. Your assignment (recorded on the board as #1 for @impl):\n\n"
                        f"{ROLE_IMPL} {LOCK_TESTS} 修 bug"
                    ),
                },
                {
                    "source": "SYSTEM",
                    "type": "SYSTEM_MESSAGE",
                    "content": wrap_system_message(f"@other {UNLOCK_TESTS}"),
                },
            ],
        )
        self.assertEqual(
            run_guard(
                {
                    "transcriptPath": self.transcript_path,
                    "lastUserInput": "",
                    "toolCall": {
                        "name": "edit_file",
                        "args": {
                            "Path": "/workspace/demo-app/tests/flow.py",
                            "Target": "a",
                            "Replacement": "b",
                        },
                    },
                }
            )["decision"],
            "deny",
        )

        with open(self.transcript_path, "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "step_index": 2,
                        "source": "SYSTEM",
                        "type": "SYSTEM_MESSAGE",
                        "content": wrap_system_message(f"@impl {UNLOCK_TESTS} 允许补单测"),
                    }
                )
                + "\n"
            )
        self.assertEqual(
            run_guard(
                {
                    "transcriptPath": self.transcript_path,
                    "lastUserInput": "",
                    "toolCall": {
                        "name": "edit_file",
                        "args": {
                            "Path": "/workspace/demo-app/tests/flow.py",
                            "Target": "a",
                            "Replacement": "b",
                        },
                    },
                }
            )["decision"],
            "allow",
        )

    def test_05_verifier_and_tester_red_test_permissions_and_revoke(self):
        write_jsonl(
            self.transcript_path,
            [
                {
                    "source": "SYSTEM",
                    "type": "SYSTEM_MESSAGE",
                    "content": wrap_system_message(
                        "You have just started. Your assignment (recorded on the board as #2 for @verifier):\n\n"
                        f"{ROLE_VERIFIER} 独立验收"
                    ),
                }
            ],
        )
        for p in (
            "/workspace/demo-app/src/app.py",
            "/workspace/demo-app/tests/test_app.py",
            "/workspace/hub/boards/demo.md",
        ):
            self.assertEqual(
                run_guard(
                    {
                        "transcriptPath": self.transcript_path,
                        "lastUserInput": "",
                        "toolCall": {"name": "write_file", "args": {"Path": p, "Content": "x"}},
                    }
                )["decision"],
                "deny",
                f"Default verifier should deny {p}",
            )

        # With ALLOW_RED_TEST: allows test files, still denies source & boards
        write_jsonl(
            self.transcript_path,
            [
                {
                    "source": "SYSTEM",
                    "type": "SYSTEM_MESSAGE",
                    "content": wrap_system_message(
                        "You have just started. Your assignment (recorded on the board as #2 for @verifier):\n\n"
                        f"{ROLE_VERIFIER} {ALLOW_RED} 编写复现红测"
                    ),
                }
            ],
        )
        for test_p in (
            "/workspace/demo-app/tests/repro.ts",
            "/workspace/demo-app/__tests__/repro.test.tsx",
            "/workspace/demo-app/test_repro.py",
        ):
            self.assertEqual(
                run_guard(
                    {
                        "transcriptPath": self.transcript_path,
                        "lastUserInput": "",
                        "toolCall": {"name": "write_file", "args": {"Path": test_p, "Content": "x"}},
                    }
                )["decision"],
                "allow",
                f"Red-test verifier should allow {test_p}",
            )
        for non_test_p in (
            "/workspace/demo-app/src/app.py",
            "/workspace/hub/boards/test_demo.md",
            "/workspace/hub/DASHBOARD.md",
        ):
            self.assertEqual(
                run_guard(
                    {
                        "transcriptPath": self.transcript_path,
                        "lastUserInput": "",
                        "toolCall": {
                            "name": "write_file",
                            "args": {"Path": non_test_p, "Content": "x"},
                        },
                    }
                )["decision"],
                "deny",
                f"Red-test verifier should deny {non_test_p}",
            )

        # REVOKE_RED_TEST returns verifier to strict read-only
        with open(self.transcript_path, "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "step_index": 1,
                        "source": "SYSTEM",
                        "type": "SYSTEM_MESSAGE",
                        "content": wrap_system_message(f"@verifier {REVOKE_RED} 切回只读盲审"),
                    }
                )
                + "\n"
            )
        self.assertEqual(
            run_guard(
                {
                    "transcriptPath": self.transcript_path,
                    "lastUserInput": "",
                    "toolCall": {
                        "name": "write_file",
                        "args": {
                            "Path": "/workspace/demo-app/tests/repro.ts",
                            "Content": "x",
                        },
                    },
                }
            )["decision"],
            "deny",
        )

        # TESTER role alias works identically
        write_jsonl(
            self.transcript_path,
            [
                {
                    "source": "SYSTEM",
                    "type": "USER_INPUT",
                    "content": f"{ROLE_TESTER} {ALLOW_RED} 写红测",
                }
            ],
        )
        self.assertEqual(
            run_guard(
                {
                    "transcriptPath": self.transcript_path,
                    "lastUserInput": "",
                    "toolCall": {
                        "name": "write_file",
                        "args": {"Path": "/workspace/demo-app/tests/e2e.py", "Content": "x"},
                    },
                }
            )["decision"],
            "allow",
        )

    def test_06_shared_global_docs_overwrite_protection(self):
        hub_root = os.path.join(self.tmp_dir, "work", "hub")
        playbook_dir = os.path.join(hub_root, "playbook")
        os.makedirs(playbook_dir, exist_ok=True)
        dashboard = os.path.join(hub_root, "DASHBOARD.md")
        workflow = os.path.join(playbook_dir, "workflow-gates.md")
        with open(dashboard, "w", encoding="utf-8") as f:
            f.write("# DASHBOARD\n")
        with open(workflow, "w", encoding="utf-8") as f:
            f.write("# Workflow\n")

        write_jsonl(
            self.transcript_path,
            [{"source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "更新全局看板"}],
        )

        for target in (dashboard, workflow):
            res_overwrite = run_guard(
                {
                    "transcriptPath": self.transcript_path,
                    "lastUserInput": "更新全局看板",
                    "toolCall": {
                        "name": "write_file",
                        "args": {"Path": target, "Content": "overwrite", "Overwrite": True},
                    },
                }
            )
            self.assertEqual(res_overwrite["decision"], "deny")

            res_edit = run_guard(
                {
                    "transcriptPath": self.transcript_path,
                    "lastUserInput": "更新全局看板",
                    "toolCall": {
                        "name": "edit_file",
                        "args": {"Path": target, "Target": "#", "Replacement": "##"},
                    },
                }
            )
            self.assertEqual(res_edit["decision"], "allow")

    def test_07_board_size_gate_lines_vs_bytes(self):
        boards_dir = os.path.join(self.tmp_dir, "work", "hub", "boards")
        os.makedirs(boards_dir, exist_ok=True)
        board_file = os.path.join(boards_dir, "demo.md")

        write_jsonl(
            self.transcript_path,
            [{"source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "更新记忆卡"}],
        )

        line_zh = "- 记忆卡条目：完成认证模块边界校验与回归验证，记录关键结论与下一步动作。\n"
        content_200 = line_zh * 200
        byte_len_200 = len(content_200.encode("utf-8"))
        self.assertGreater(byte_len_200, 16384)
        self.assertLessEqual(byte_len_200, 36864)
        res_ok = run_guard(
            {
                "transcriptPath": self.transcript_path,
                "lastUserInput": "更新记忆卡",
                "toolCall": {
                    "name": "write_file",
                    "args": {"Path": board_file, "Content": content_200},
                },
            }
        )
        self.assertEqual(res_ok["decision"], "allow")

        content_221 = "x\n" * 221
        res_lines = run_guard(
            {
                "transcriptPath": self.transcript_path,
                "lastUserInput": "更新记忆卡",
                "toolCall": {
                    "name": "write_file",
                    "args": {"Path": board_file, "Content": content_221},
                },
            }
        )
        self.assertEqual(res_lines["decision"], "deny")
        self.assertIn("行数超过上限 220 行", res_lines["reason"])
        self.assertNotIn("字节数超过上限", res_lines["reason"])

        long_zh_line = ("中文状态明细记录" * 20) + "\n"
        content_big_bytes = long_zh_line * 100
        self.assertGreater(len(content_big_bytes.encode("utf-8")), 36864)
        res_bytes = run_guard(
            {
                "transcriptPath": self.transcript_path,
                "lastUserInput": "更新记忆卡",
                "toolCall": {
                    "name": "write_file",
                    "args": {"Path": board_file, "Content": content_big_bytes},
                },
            }
        )
        self.assertEqual(res_bytes["decision"], "deny")
        self.assertIn("字节数超过上限 36864 bytes", res_bytes["reason"])
        self.assertNotIn("行数超过上限", res_bytes["reason"])

    def test_08_run_command_high_risk_git_guard(self):
        deny_cmds = [
            "git reset --hard",
            "git reset --hard HEAD~1",
            "git clean -f",
            "git clean -fd",
            "git clean -fx",
            "git clean --force",
            "git checkout -- .",
            "git checkout -- src/app.py",
            "git checkout HEAD -- src/app.py",
            "git checkout .",
            "git restore .",
            "git restore src/app.py",
            "git restore --worktree src/app.py",
            "git restore -W .",
            "git restore --staged --worktree src/app.py",
            "git restore -SW src/app.py",
            "git add -A",
            "git add --all",
            "git status && git reset --hard",
            'sh -c "git clean -fd"',
            'bash -lc "git reset --hard"',
            'zsh -lc "git clean -fd"',
            'sh -ec "git checkout -- ."',
            'bash -lc -- "git add -A"',
            'bash -o pipefail -xc "git restore ."',
            "FOO=1 git -C /workspace/demo-app add -A",
        ]
        for cmd in deny_cmds:
            for key in ("CommandLine", "command"):
                res = run_guard(
                    {
                        "toolCall": {
                            "name": "run_command",
                            "args": {key: cmd, "Cwd": "/workspace/demo-app"},
                        }
                    }
                )
                self.assertEqual(res["decision"], "deny", f"Expected deny for {key}={cmd!r}")

        allow_cmds = [
            "git status",
            "git diff",
            "git diff --stat",
            "git add src/app.py",
            "git reset --soft HEAD~1",
            "git clean -nd",
            "git checkout -b feat/test",
            "git checkout main",
            "git restore --staged src/app.py",
            "git restore -S .",
            "git commit -m 'do not run git reset --hard or git add -A'",
            'echo "git reset --hard && git clean -fd"',
            'grep -rn "git checkout --" /workspace/hub',
            'bash -lc "git status && git diff --stat"',
            """zsh -lc 'echo "git reset --hard"'""",
            'bash script.sh -c "git reset --hard"',
            "python3 -m unittest discover -v -s /workspace/agy-work-hub/.agents/plugins/work-hub/scripts",
            "cd /tmp/test-repo && git init && git add -A && git reset --hard HEAD",
            "git -C /tmp/test-repo clean -fd",
            "cd /workspace/.gemini/antigravity/brain/conv-1/scratch && git add --all",
        ]
        for cmd in allow_cmds:
            res = run_guard(
                {
                    "toolCall": {
                        "name": "run_command",
                        "args": {"CommandLine": cmd},
                    }
                }
            )
            self.assertEqual(res["decision"], "allow", f"Expected allow for {cmd!r}")

    def test_09_planner_role_permissions_and_three_way_swarm_isolation(self):
        write_jsonl(
            self.transcript_path,
            [
                {
                    "source": "SYSTEM",
                    "type": "SYSTEM_MESSAGE",
                    "content": wrap_system_message(
                        "You have just started. Your assignment (recorded on the board as #1 for @planner):\n\n"
                        f"{ROLE_PLANNER} {ALLOW_RED} 核查现场并在看板输出蓝图与 DoD"
                    ),
                }
            ],
        )

        for scratch_path in (
            "/tmp/planner_blueprint.md",
            "/workspace/.gemini/antigravity/brain/conv-99/implementation_plan.md",
            "/workspace/.gemini/antigravity/brain/conv-99/scratch/dod.md",
        ):
            for tool in ("write_file", "edit_file"):
                args = (
                    {"Path": scratch_path, "Content": "blueprint"}
                    if tool == "write_file"
                    else {"Path": scratch_path, "Target": "a", "Replacement": "b"}
                )
                res = run_guard(
                    {
                        "transcriptPath": self.transcript_path,
                        "lastUserInput": "",
                        "toolCall": {"name": tool, "args": args},
                    }
                )
                self.assertEqual(
                    res["decision"], "allow", f"PLANNER should allow {tool} on {scratch_path}"
                )

        denied_paths = (
            "/workspace/demo-app/src/app.py",
            "/workspace/demo-app/tests/test_app.py",
            "/workspace/hub/DASHBOARD.md",
            "/workspace/hub/boards/demo.md",
            "/workspace/hub/AGENTS.md",
        )
        for denied_path in denied_paths:
            for tool in ("write_file", "edit_file"):
                args = (
                    {"Path": denied_path, "Content": "x"}
                    if tool == "write_file"
                    else {"Path": denied_path, "Target": "a", "Replacement": "b"}
                )
                res = run_guard(
                    {
                        "transcriptPath": self.transcript_path,
                        "lastUserInput": "",
                        "toolCall": {"name": tool, "args": args},
                    }
                )
                self.assertEqual(
                    res["decision"], "deny", f"PLANNER should deny {tool} on {denied_path}"
                )
                self.assertIn("PLANNER", res["reason"])
                self.assertIn("《施工蓝图与对抗 DoD》", res["reason"])
                self.assertIn("IMPL", res["reason"])
                self.assertIn("严禁直接改码或改看板", res["reason"])

        res_git = run_guard(
            {
                "transcriptPath": self.transcript_path,
                "lastUserInput": "",
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": "git status && git diff --stat", "Cwd": "/workspace/demo-app"},
                },
            }
        )
        self.assertEqual(res_git["decision"], "allow")

        board_transcript_steps = [
            (
                "planner",
                "You have just started. Your assignment (recorded on the board as #1 for @planner):\n\n"
                f"{ROLE_PLANNER} 审视现场并下达蓝图",
            ),
            (
                "impl-2",
                "You have just started. Your assignment (recorded on the board as #2 for @impl-2):\n\n"
                f"{ROLE_IMPL} {LOCK_TESTS} 按蓝图实现业务源码",
            ),
            (
                "verifier-2",
                "You have just started. Your assignment (recorded on the board as #3 for @verifier-2):\n\n"
                f"{ROLE_VERIFIER} {ALLOW_RED} 独立测试验收",
            ),
        ]
        shared_followup = [
            {
                "source": "SYSTEM",
                "type": "SYSTEM_MESSAGE",
                "content": wrap_system_message(
                    f"#4 coordinator: @planner {ROLE_PLANNER} 请先看现网\n"
                    f"#5 planner: @impl-2 {ROLE_IMPL} {LOCK_TESTS} 仅改 src/service.py\n"
                    f"#6 coordinator: @verifier-2 {ROLE_VERIFIER} {ALLOW_RED} 编写边界红测"
                ),
            },
            {
                "source": "MODEL",
                "type": "GENERIC",
                "content": json.dumps(
                    {
                        "messages": [
                            {"seq": 4, "text": f"@planner {ROLE_PLANNER}"},
                            {"seq": 5, "text": f"@impl-2 {ROLE_IMPL} {LOCK_TESTS}"},
                            {"seq": 6, "text": f"@verifier-2 {ROLE_VERIFIER} {ALLOW_RED}"},
                        ]
                    },
                    ensure_ascii=False,
                ),
            },
        ]

        results_by_member = {}
        for member, birth_msg in board_transcript_steps:
            member_jsonl = os.path.join(self.tmp_dir, f"{member}.jsonl")
            write_jsonl(
                member_jsonl,
                [{"source": "SYSTEM", "type": "SYSTEM_MESSAGE", "content": wrap_system_message(birth_msg)}]
                + shared_followup,
            )
            results_by_member[member] = {
                "src": run_guard(
                    {
                        "transcriptPath": member_jsonl,
                        "lastUserInput": "",
                        "toolCall": {
                            "name": "edit_file",
                            "args": {"Path": "/workspace/demo-app/src/service.py", "Target": "a", "Replacement": "b"},
                        },
                    }
                )["decision"],
                "test": run_guard(
                    {
                        "transcriptPath": member_jsonl,
                        "lastUserInput": "",
                        "toolCall": {
                            "name": "write_file",
                            "args": {"Path": "/workspace/demo-app/tests/test_service.py", "Content": "x"},
                        },
                    }
                )["decision"],
                "board": run_guard(
                    {
                        "transcriptPath": member_jsonl,
                        "lastUserInput": "",
                        "toolCall": {
                            "name": "edit_file",
                            "args": {"Path": "/workspace/hub/boards/demo.md", "Target": "a", "Replacement": "b"},
                        },
                    }
                )["decision"],
                "tmp": run_guard(
                    {
                        "transcriptPath": member_jsonl,
                        "lastUserInput": "",
                        "toolCall": {"name": "write_file", "args": {"Path": "/tmp/check.md", "Content": "ok"}},
                    }
                )["decision"],
            }

        self.assertEqual(
            results_by_member["planner"],
            {"src": "deny", "test": "deny", "board": "deny", "tmp": "allow"},
        )
        self.assertEqual(
            results_by_member["impl-2"],
            {"src": "allow", "test": "deny", "board": "allow", "tmp": "allow"},
        )
        self.assertEqual(
            results_by_member["verifier-2"],
            {"src": "deny", "test": "allow", "board": "deny", "tmp": "allow"},
        )

        multi_record_board = (
            f"#1 coordinator: @planner {ROLE_PLANNER} 定蓝图\n"
            f"#2 planner: @impl {ROLE_IMPL} 改源码\n"
            f"#3 coordinator: @verifier {ROLE_VERIFIER} 盲测验收\n"
        )
        for member, expected_src in (("planner", "deny"), ("impl", "allow"), ("verifier", "deny")):
            member_jsonl = os.path.join(self.tmp_dir, f"multi_{member}.jsonl")
            write_jsonl(
                member_jsonl,
                [
                    {
                        "source": "SYSTEM",
                        "type": "USER_INPUT",
                        "content": (
                            "<swarm_worker>\n"
                            f"You are {member}, one agent in a swarm working together on one task.\n"
                            "</swarm_worker>\n"
                            + multi_record_board
                        ),
                    }
                ],
            )
            res_src = run_guard(
                {
                    "transcriptPath": member_jsonl,
                    "lastUserInput": "",
                    "toolCall": {
                        "name": "edit_file",
                        "args": {"Path": "/workspace/demo-app/src/app.py", "Target": "a", "Replacement": "b"},
                    },
                }
            )
            self.assertEqual(res_src["decision"], expected_src, f"Multi-record mismatch for {member}")
            res_dash = run_guard(
                {
                    "transcriptPath": member_jsonl,
                    "lastUserInput": "",
                    "toolCall": {
                        "name": "edit_file",
                        "args": {"Path": "/workspace/hub/DASHBOARD.md", "Target": "a", "Replacement": "b"},
                    },
                }
            )
            expected_dash = "allow" if member == "impl" else "deny"
            self.assertEqual(res_dash["decision"], expected_dash, f"Dashboard mismatch for {member}")

        plain_board_jsonl = os.path.join(self.tmp_dir, "plain_board.jsonl")
        write_jsonl(
            plain_board_jsonl,
            [
                {
                    "source": "SYSTEM",
                    "type": "SYSTEM_MESSAGE",
                    "content": wrap_system_message(multi_record_board),
                }
            ],
        )
        for agent_name, expected_src in (("planner", "deny"), ("impl", "allow"), ("verifier", "deny")):
            res_agent = run_guard(
                {
                    "agentName": agent_name,
                    "transcriptPath": plain_board_jsonl,
                    "lastUserInput": "",
                    "toolCall": {
                        "name": "edit_file",
                        "args": {"Path": "/workspace/demo-app/src/app.py", "Target": "a", "Replacement": "b"},
                    },
                }
            )
            self.assertEqual(res_agent["decision"], expected_src, f"agentName mismatch for {agent_name}")

        unflushed_planner = run_guard(
            {
                "transcriptPath": os.path.join(self.tmp_dir, "missing_planner.jsonl"),
                "lastUserInput": f"{ROLE_PLANNER} 审视现场",
                "toolCall": {
                    "name": "edit_file",
                    "args": {"Path": "/workspace/hub/boards/demo.md", "Target": "a", "Replacement": "b"},
                },
            }
        )
        self.assertEqual(unflushed_planner["decision"], "deny")

    def test_10_custom_work_hub_root_and_standalone_workspace_root_isolation(self):
        # Custom WORK_HUB_ROOT (not named "hub") sharing a standalone root with src/
        standalone_ws = os.path.join(self.tmp_dir, "my-custom-hub")
        os.makedirs(os.path.join(standalone_ws, "boards"), exist_ok=True)
        os.makedirs(os.path.join(standalone_ws, "src"), exist_ok=True)
        dashboard = os.path.join(standalone_ws, "DASHBOARD.md")
        board_file = os.path.join(standalone_ws, "boards", "app.md")
        src_file = os.path.join(standalone_ws, "src", "main.py")
        with open(dashboard, "w", encoding="utf-8") as f:
            f.write("# Dashboard\n")

        write_jsonl(
            self.transcript_path,
            [{"source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "整理看板"}],
        )
        env = {"WORK_HUB_ROOT": standalone_ws}

        # Coordinator allowed to edit Hub control plane assets under custom WORK_HUB_ROOT
        res_dash_edit = run_guard(
            {
                "transcriptPath": self.transcript_path,
                "lastUserInput": "整理看板",
                "toolCall": {
                    "name": "edit_file",
                    "args": {"Path": dashboard, "Target": "Dashboard", "Replacement": "Hub"},
                },
            },
            env_override=env,
        )
        self.assertEqual(res_dash_edit["decision"], "allow")

        # Overwrite on DASHBOARD.md under custom WORK_HUB_ROOT is denied
        res_dash_overwrite = run_guard(
            {
                "transcriptPath": self.transcript_path,
                "lastUserInput": "整理看板",
                "toolCall": {
                    "name": "write_file",
                    "args": {"Path": dashboard, "Content": "wipe", "Overwrite": True},
                },
            },
            env_override=env,
        )
        self.assertEqual(res_dash_overwrite["decision"], "deny")

        # Board > 220 lines under custom WORK_HUB_ROOT is denied
        res_board_big = run_guard(
            {
                "transcriptPath": self.transcript_path,
                "lastUserInput": "整理看板",
                "toolCall": {
                    "name": "write_file",
                    "args": {"Path": board_file, "Content": "line\n" * 221},
                },
            },
            env_override=env,
        )
        self.assertEqual(res_board_big["decision"], "deny")

        # Standalone workspace sibling source code src/main.py must STILL be denied for COORDINATOR
        res_sibling_src = run_guard(
            {
                "transcriptPath": self.transcript_path,
                "lastUserInput": "整理看板",
                "toolCall": {
                    "name": "write_file",
                    "args": {"Path": src_file, "Content": "print(1)"},
                },
            },
            env_override=env,
        )
        self.assertEqual(res_sibling_src["decision"], "deny")

    def test_11_tmp_simulated_workspace_cannot_bypass_guard(self):
        # Simulate user/CI workspace explicitly located under /tmp/
        with tempfile.TemporaryDirectory(dir="/tmp") as sim_ws:
            hub_dir = os.path.join(sim_ws, "agy-work-hub")
            app_dir = os.path.join(sim_ws, "sample-app")
            os.makedirs(os.path.join(hub_dir, "boards"), exist_ok=True)
            os.makedirs(os.path.join(app_dir, "tests"), exist_ok=True)
            os.makedirs(os.path.join(app_dir, "src"), exist_ok=True)

            # 1) IMPL [LOCK_TESTS] editing test under /tmp/sim_ws must be DENIED
            impl_jsonl = os.path.join(sim_ws, "impl.jsonl")
            write_jsonl(
                impl_jsonl,
                [
                    {
                        "source": "SYSTEM",
                        "type": "SYSTEM_MESSAGE",
                        "content": wrap_system_message(
                            "You have just started. Your assignment (recorded on the board as #1 for @impl):\n\n"
                            f"{ROLE_IMPL} {LOCK_TESTS} 修复业务逻辑"
                        ),
                    }
                ],
            )
            res_test_deny = run_guard(
                {
                    "transcriptPath": impl_jsonl,
                    "workspacePaths": [sim_ws],
                    "lastUserInput": "",
                    "toolCall": {
                        "name": "edit_file",
                        "args": {
                            "Path": os.path.join(app_dir, "tests", "test_core.py"),
                            "Target": "a",
                            "Replacement": "b",
                        },
                    },
                }
            )
            self.assertEqual(res_test_deny["decision"], "deny")

            res_src_allow = run_guard(
                {
                    "transcriptPath": impl_jsonl,
                    "workspacePaths": [sim_ws],
                    "lastUserInput": "",
                    "toolCall": {
                        "name": "write_file",
                        "args": {
                            "Path": os.path.join(app_dir, "src", "core.py"),
                            "Content": "def ok(): return True\n",
                        },
                    },
                }
            )
            self.assertEqual(res_src_allow["decision"], "allow")

            # 2) Same lock check via WORK_HUB_ROOT only (even without workspacePaths)
            res_env_deny = run_guard(
                {
                    "transcriptPath": impl_jsonl,
                    "lastUserInput": "",
                    "toolCall": {
                        "name": "write_file",
                        "args": {
                            "Path": os.path.join(app_dir, "tests", "test_core.py"),
                            "Content": "assert True",
                        },
                    },
                },
                env_override={"WORK_HUB_ROOT": hub_dir},
            )
            self.assertEqual(res_env_deny["decision"], "deny")

            # 3) git reset --hard inside /tmp simulated workspace Cwd must be DENIED
            res_git_deny = run_guard(
                {
                    "workspacePaths": [sim_ws],
                    "toolCall": {
                        "name": "run_command",
                        "args": {"CommandLine": "git reset --hard", "Cwd": app_dir},
                    },
                }
            )
            self.assertEqual(res_git_deny["decision"], "deny")

            # 4) Scratch paths inside /scratch/ or outside active sim_ws in /tmp/ remain ALLOWED
            for scratch in (
                os.path.join(sim_ws, "scratch", "test_note.py"),
                "/tmp/unrelated_scratch_file.py",
            ):
                res_scratch = run_guard(
                    {
                        "transcriptPath": impl_jsonl,
                        "workspacePaths": [sim_ws],
                        "lastUserInput": "",
                        "toolCall": {
                            "name": "write_file",
                            "args": {"Path": scratch, "Content": "tmp"},
                        },
                    }
                )
                self.assertEqual(res_scratch["decision"], "allow", scratch)

            res_scratch_git = run_guard(
                {
                    "workspacePaths": [sim_ws],
                    "toolCall": {
                        "name": "run_command",
                        "args": {
                            "CommandLine": "cd /tmp/outside_scratch_repo && git reset --hard",
                            "Cwd": app_dir,
                        },
                    },
                }
            )
            self.assertEqual(res_scratch_git["decision"], "allow")


if __name__ == "__main__":
    unittest.main()
