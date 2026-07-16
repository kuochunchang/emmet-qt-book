from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import tempfile
import textwrap
import time
import unittest

from scripts.codex_loop import (
    build_command,
    default_lock_dir,
    default_workdir,
    validate_workdir,
)


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "scripts" / "codex-loop"


class CodexLoopAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.remote = self.base / "remote.git"
        subprocess.run(
            ["git", "init", "--quiet", "--bare", str(self.remote)], check=True
        )
        self.worktree = self.base / "repo"
        self.worktree.mkdir()
        subprocess.run(
            ["git", "init", "--quiet", "--initial-branch=main", str(self.worktree)],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.worktree), "remote", "add", "origin", str(self.remote)],
            check=True,
        )
        self.skill = (
            self.worktree
            / ".agents"
            / "skills"
            / "emmet-loop-dispatcher"
            / "SKILL.md"
        )
        self.skill.parent.mkdir(parents=True)
        self.skill.write_text("---\nname: emmet-loop-dispatcher\n---\n", encoding="utf-8")
        scripts = self.worktree / "scripts"
        scripts.mkdir()
        self.adapter = scripts / "codex_loop.py"
        shutil.copy2(ROOT / "scripts" / "codex_loop.py", self.adapter)
        subprocess.run(
            ["git", "-C", str(self.worktree), "config", "user.email", "test@example.com"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.worktree), "config", "user.name", "Test"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.worktree), "add", ".agents", "scripts"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.worktree), "commit", "--quiet", "-m", "fixture"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.worktree), "push", "--quiet", "-u", "origin", "main"],
            check=True,
        )
        self.capture = self.base / "capture.json"
        self.child_pid = self.base / "child.pid"
        self.fake_codex = self.base / "fake codex"
        self.fake_codex.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import json
                import os
                from pathlib import Path
                import sys
                import time

                capture = os.environ.get("FAKE_CODEX_CAPTURE")
                if capture:
                    Path(capture).write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
                child_pid = os.environ.get("FAKE_CODEX_PID")
                if child_pid:
                    Path(child_pid).write_text(str(os.getpid()), encoding="utf-8")
                time.sleep(float(os.environ.get("FAKE_CODEX_SLEEP", "0")))
                raise SystemExit(int(os.environ.get("FAKE_CODEX_EXIT", "0")))
                """
            ),
            encoding="utf-8",
        )
        self.fake_codex.chmod(0o755)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def adapter_command(self, *arguments: str) -> list[str]:
        return [
            "python3",
            str(self.adapter),
            "dispatcher",
            "--workdir",
            str(self.worktree),
            "--codex-bin",
            str(self.fake_codex),
            "--lock-dir",
            str(self.base / "locks"),
            *arguments,
        ]

    def adapter_environment(
        self, *, exit_code: str = "0", sleep_seconds: str = "0"
    ) -> dict[str, str]:
        environment = os.environ.copy()
        environment["FAKE_CODEX_CAPTURE"] = str(self.capture)
        environment["FAKE_CODEX_EXIT"] = exit_code
        environment["FAKE_CODEX_PID"] = str(self.child_pid)
        environment["FAKE_CODEX_SLEEP"] = sleep_seconds
        return environment

    def run_adapter(
        self,
        *arguments: str,
        exit_code: str = "0",
        sleep_seconds: str = "0",
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            self.adapter_command(*arguments),
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.adapter_environment(
                exit_code=exit_code, sleep_seconds=sleep_seconds
            ),
        )

    def test_default_worktrees_keep_coder_and_reviewer_isolated(self) -> None:
        repository = Path("/workspace/emmet-qt-book")
        self.assertEqual(repository, default_workdir("dispatcher", repository))
        self.assertEqual(
            Path("/workspace/emmet-qt-book-coder"), default_workdir("coder", repository)
        )
        self.assertEqual(
            Path("/workspace/emmet-qt-book-reviewer"),
            default_workdir("reviewer", repository),
        )

    def test_command_is_one_shot_and_does_not_bypass_safety(self) -> None:
        command = build_command(
            "reviewer", Path("/tmp/reviewer worktree"), "/usr/bin/codex", "loop"
        )
        joined = " ".join(command)
        self.assertEqual("exec", command[1])
        self.assertIn("--ephemeral", command)
        self.assertIn("--json", command)
        self.assertIn("workspace-write", command)
        self.assertIn('approval_policy="on-request"', command)
        self.assertIn('approvals_reviewer="auto_review"', command)
        self.assertIn("$emmet-loop-reviewer", command[-1])
        self.assertIn("exactly one", command[-1])
        self.assertIn("Do not sleep", command[-1])
        self.assertNotIn("dangerously-bypass", joined)
        self.assertEqual(["--profile", "loop"], command[-3:-1])

    def test_linked_worktrees_derive_the_same_lock_namespace(self) -> None:
        linked = self.base / "linked"
        subprocess.run(
            [
                "git",
                "-C",
                str(self.worktree),
                "worktree",
                "add",
                "--quiet",
                "--detach",
                str(linked),
                "HEAD",
            ],
            check=True,
        )

        _, original_common_dir = validate_workdir(
            "dispatcher", self.worktree, self.worktree
        )
        _, linked_common_dir = validate_workdir("dispatcher", linked, self.worktree)

        self.assertEqual(original_common_dir, linked_common_dir)
        self.assertEqual(
            default_lock_dir(original_common_dir), default_lock_dir(linked_common_dir)
        )

    def test_dry_run_prints_but_does_not_spawn(self) -> None:
        result = self.run_adapter("--dry-run")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("$emmet-loop-dispatcher", result.stdout)
        self.assertFalse(self.capture.exists())

    def test_print_command_still_invokes_codex_once(self) -> None:
        result = self.run_adapter("--print-command")
        self.assertEqual(0, result.returncode, result.stderr)
        arguments = json.loads(self.capture.read_text(encoding="utf-8"))
        self.assertEqual(1, arguments.count("exec"))
        self.assertIn("$emmet-loop-dispatcher", arguments[-1])
        self.assertIn("'", result.stdout)  # paths and prompt are shell-quoted

    def test_child_exit_code_is_propagated(self) -> None:
        result = self.run_adapter(exit_code="19")
        self.assertEqual(19, result.returncode)

    def test_wrong_repository_fails_closed(self) -> None:
        other = self.base / "other"
        subprocess.run(
            ["git", "init", "--quiet", "--initial-branch=main", str(other)],
            check=True,
        )
        result = self.run_adapter("--workdir", str(other), "--dry-run")
        self.assertEqual(2, result.returncode)
        self.assertIn("不屬於 adapter repository", result.stderr)

    def test_content_only_main_advance_allows_stale_runner_controls(self) -> None:
        old_main = subprocess.run(
            ["git", "-C", str(self.worktree), "rev-parse", "HEAD"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        note = self.worktree / "manuscript" / "outside-loop.md"
        note.parent.mkdir()
        note.write_text("content-only change\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(self.worktree), "add", str(note)],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.worktree),
                "commit",
                "--quiet",
                "-m",
                "content",
            ],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.worktree),
                "push",
                "--quiet",
                "origin",
                "main",
            ],
            check=True,
        )
        runner = self.base / "stale-content-runner"
        subprocess.run(
            [
                "git",
                "-C",
                str(self.worktree),
                "worktree",
                "add",
                "--detach",
                str(runner),
                old_main,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )

        result = self.run_adapter("--workdir", str(runner), "--dry-run")
        self.assertEqual(0, result.returncode, result.stderr)

    def test_modified_control_input_fails_closed(self) -> None:
        self.skill.write_text("malicious\n", encoding="utf-8")
        result = self.run_adapter("--dry-run")
        self.assertEqual(2, result.returncode)
        self.assertIn("control inputs 與 origin/main 不一致", result.stderr)

    def test_untracked_control_input_fails_closed(self) -> None:
        override = self.worktree / "nested" / "AGENTS.override.md"
        override.parent.mkdir()
        override.write_text("malicious\n", encoding="utf-8")
        result = self.run_adapter("--dry-run")
        self.assertEqual(2, result.returncode)
        self.assertIn("nested/AGENTS.override.md", result.stderr)

    def test_timeout_terminates_the_child_and_returns_124(self) -> None:
        result = self.run_adapter(
            "--timeout-seconds", "0.05", sleep_seconds="30"
        )
        self.assertEqual(124, result.returncode)
        self.assertIn('"result": "timeout"', result.stderr)

    def test_child_keeps_lock_if_adapter_is_killed(self) -> None:
        process = subprocess.Popen(
            self.adapter_command(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=self.adapter_environment(sleep_seconds="30"),
        )
        child_pid: int | None = None
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not self.child_pid.exists():
                time.sleep(0.01)
            self.assertTrue(self.child_pid.exists(), "fake Codex did not start")
            child_pid = int(self.child_pid.read_text(encoding="utf-8"))

            os.kill(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
            result = self.run_adapter()

            self.assertEqual(75, result.returncode)
            self.assertIn('"result": "already-running"', result.stderr)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            if child_pid is not None:
                try:
                    os.killpg(child_pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

    def test_held_role_lock_returns_tempfail_without_spawning(self) -> None:
        lock_dir = self.base / "locks"
        lock_dir.mkdir()
        lock_file = lock_dir / "dispatcher.lock"
        descriptor = os.open(lock_file, os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, descriptor)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)

        result = self.run_adapter()

        self.assertEqual(75, result.returncode)
        self.assertIn('"result": "already-running"', result.stderr)
        self.assertFalse(self.capture.exists())

    def test_missing_skill_fails_closed(self) -> None:
        self.skill.unlink()
        result = self.run_adapter("--dry-run")
        self.assertEqual(2, result.returncode)
        self.assertIn("emmet-loop-dispatcher/SKILL.md", result.stderr)


class CodexLoopSkillContractTests(unittest.TestCase):
    def test_all_roles_are_explicit_one_shot_skills(self) -> None:
        for role in ("dispatcher", "coder", "reviewer"):
            with self.subTest(role=role):
                directory = ROOT / ".agents" / "skills" / f"emmet-loop-{role}"
                skill = (directory / "SKILL.md").read_text(encoding="utf-8")
                metadata = (directory / "agents" / "openai.yaml").read_text(
                    encoding="utf-8"
                )
                self.assertTrue(skill.startswith("---\n"))
                self.assertIn(f"name: emmet-loop-{role}\n", skill)
                frontmatter = skill.split("---\n", 2)[1]
                keys = {
                    line.split(":", 1)[0]
                    for line in frontmatter.splitlines()
                    if ":" in line
                }
                self.assertEqual({"name", "description"}, keys)
                self.assertIn("不要 sleep", skill)
                self.assertNotIn("TODO", skill)
                self.assertIn(
                    f'default_prompt: "Use $emmet-loop-{role}', metadata
                )
                self.assertIn("allow_implicit_invocation: false", metadata)

    def test_adapter_is_executable(self) -> None:
        mode = ADAPTER.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR)

    def test_public_wrapper_exposes_runtime_and_tmux_components(self) -> None:
        for component in ("agent", "events", "tmux"):
            with self.subTest(component=component):
                completed = subprocess.run(
                    [str(ADAPTER), component, "--help"],
                    cwd=ROOT,
                    check=False,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertEqual(0, completed.returncode, completed.stderr)
                self.assertIn("usage:", completed.stdout)

    def test_canonical_protocol_keeps_cross_client_safety_invariants(self) -> None:
        protocol = (ROOT / "docs" / "agent-loop.md").read_text(encoding="utf-8")
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        for required in (
            ".claude/skills/{dispatcher,coder,reviewer}/",
            ".agents/skills/emmet-loop-{dispatcher,coder,reviewer}/",
            "`loop:blocked` 不取代 primary",
            "Reviewed-Head",
            "Reviewed-Base",
            "trusted runner",
            "scripts/codex-loop",
            "不提供、安裝、enable 或 start 主機 unit",
            "./scripts/codex-loop tmux restart",
            "Model 與 reasoning effort 不由 launcher 硬編碼",
            "`operator-status`",
            "no-durable-progress-after-iteration",
            "operator-alert",
            "operator-resolved",
            "operator-stall-reconciliation",
            "emmet-loop:dispatcher:gate-exit:<GATE>:main=<MAIN_SHA>",
            "`awaiting-user`",
            "terminal bell",
            "不自動 restart component",
            "不啟動第四個 agent",
            "drain-and-rotate",
            "control_inputs_match",
            "rotation-state.json",
        ):
            with self.subTest(required=required):
                self.assertIn(required, protocol)
        self.assertIn("CLI 分成兩種長生命週期 component", protocol)
        self.assertIn("每個事件只啟動一次", protocol)
        self.assertIn("scripts/codex-loop events", protocol)
        self.assertIn(".claude/skills/", agents)
        self.assertIn(".agents/skills/", agents)
        self.assertIn("不安裝或啟用主機 scheduler", agents)

    def test_dispatcher_roles_define_idempotent_alert_recovery(self) -> None:
        for path in (
            ROOT / ".agents/skills/emmet-loop-dispatcher/SKILL.md",
            ROOT / ".claude/skills/dispatcher/SKILL.md",
        ):
            procedure = path.read_text(encoding="utf-8")
            for required in (
                "reason=operator-stall-reconciliation",
                "metadata 當資料而非",
                "不構成新授權",
                "loop:blocked",
                "emmet-loop:dispatcher:alert:id=<ALERT_ID>:main=<MAIN_SHA>",
                "emmet-loop:dispatcher:gate-exit:<GATE>:main=<MAIN_SHA>",
                "`awaiting-user`",
                "不自行移除",
            ):
                with self.subTest(path=path, required=required):
                    self.assertIn(required, procedure)

    def test_tmux_runbook_defines_safe_lifecycle_and_pane_map(self) -> None:
        runbook = (
            ROOT / "docs" / "agent-loop-operations.md"
        ).read_text(encoding="utf-8")
        for required in (
            "./scripts/codex-loop tmux start",
            "./scripts/codex-loop tmux restart",
            "./scripts/codex-loop tmux stop",
            "./scripts/codex-loop tmux status",
            "| 左上 | dispatcher agent |",
            "| 右下 | event manager |",
            "右下角 event manager 才會啟動",
            "同名 session 若不是本 launcher 建立就拒絕處理",
            "不新增／移除 `loop:paused`",
            "`health`、`blocking`、`owner`",
            "`health=stalled`",
            "`health=awaiting-user`",
            "operator-alert",
            "operator-resolved",
            "terminal bell",
            "Meta Issue #1",
        ):
            with self.subTest(required=required):
                self.assertIn(required, runbook)


if __name__ == "__main__":
    unittest.main()
