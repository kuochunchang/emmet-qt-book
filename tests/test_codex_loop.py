from __future__ import annotations

import fcntl
import io
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import tempfile
import textwrap
import threading
import time
import unittest
from unittest import mock

from scripts.codex_loop import (
    EX_TIMEOUT,
    build_command,
    default_lock_dir,
    default_workdir,
    role_execution_config,
    run_child,
    validate_workdir,
)
from scripts.codex_loop_output import LoopOutput


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
        shutil.copy2(ROOT / "scripts" / "codex_loop_output.py", scripts)
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
                stdout = os.environ.get("FAKE_CODEX_STDOUT", "")
                if stdout:
                    sys.stdout.write(stdout)
                    sys.stdout.flush()
                stderr = os.environ.get("FAKE_CODEX_STDERR", "")
                if stderr:
                    sys.stderr.write(stderr)
                    sys.stderr.flush()
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
        self,
        *,
        exit_code: str = "0",
        sleep_seconds: str = "0",
        stdout: str = "",
        stderr: str = "",
    ) -> dict[str, str]:
        environment = os.environ.copy()
        environment["FAKE_CODEX_CAPTURE"] = str(self.capture)
        environment["FAKE_CODEX_EXIT"] = exit_code
        environment["FAKE_CODEX_PID"] = str(self.child_pid)
        environment["FAKE_CODEX_SLEEP"] = sleep_seconds
        environment["FAKE_CODEX_STDOUT"] = stdout
        environment["FAKE_CODEX_STDERR"] = stderr
        return environment

    def run_adapter(
        self,
        *arguments: str,
        exit_code: str = "0",
        sleep_seconds: str = "0",
        stdout: str = "",
        stderr: str = "",
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            self.adapter_command(*arguments),
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.adapter_environment(
                exit_code=exit_code,
                sleep_seconds=sleep_seconds,
                stdout=stdout,
                stderr=stderr,
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
        self.assertNotIn("--model", command)
        self.assertNotIn("model_reasoning_effort", joined)
        self.assertNotIn("model_verbosity", joined)

    def test_gate_auditor_prompt_requires_live_stdin_transport(self) -> None:
        command = build_command(
            "gate-auditor", Path("/tmp/gate-auditor"), "/usr/bin/codex"
        )

        self.assertIn("do not launch a bare --body-file -", command[-1])
        self.assertIn("live PTY session with echo disabled", command[-1])
        self.assertIn("follow-up stdin", command[-1])
        self.assertIn("terminate it with EOF", command[-1])

        dispatcher = build_command(
            "dispatcher", Path("/tmp/dispatcher"), "/usr/bin/codex"
        )
        self.assertNotIn("--body-file -", dispatcher[-1])

    def test_repo_defaults_bound_each_role_without_a_profile(self) -> None:
        expected = {
            "dispatcher": ("gpt-5.6-sol", "high"),
            "coder": ("gpt-5.6-sol", "high"),
            "reviewer": ("gpt-5.6-sol", "xhigh"),
        }
        for role, (model, effort) in expected.items():
            with self.subTest(role=role):
                config = role_execution_config(role)
                command = build_command(
                    role, Path(f"/tmp/{role}"), "/usr/bin/codex"
                )
                joined = " ".join(command)
                self.assertEqual(model, config["model"])
                self.assertEqual(effort, config["model_reasoning_effort"])
                self.assertIn("--model", command)
                self.assertIn(model, command)
                self.assertIn(
                    f'model_reasoning_effort="{effort}"', command
                )
                self.assertIn('model_verbosity="low"', command)
                self.assertNotIn("--profile", command)
                self.assertIn('approval_policy="on-request"', joined)
                self.assertIn('approvals_reviewer="auto_review"', joined)

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

    def test_pretty_output_renders_text_and_preserves_raw_jsonl(self) -> None:
        raw = (
            '{ "type": "item.completed", "item": '
            '{"type": "agent_message", "text": "可讀事件🙂"} }\n'
        )

        result = self.run_adapter(
            "--output-format",
            "pretty",
            stdout=raw,
            stderr="progress line\n",
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("可讀事件🙂", result.stdout)
        self.assertNotIn('"type": "item.completed"', result.stdout)
        self.assertIn("progress line", result.stderr)
        logs = self.base / "locks" / "logs"
        stdout_logs = list(logs.glob("dispatcher-*.stdout.jsonl"))
        stderr_logs = list(logs.glob("dispatcher-*.stderr.log"))
        component_logs = list(logs.glob("dispatcher-*.component.jsonl"))
        self.assertEqual(1, len(stdout_logs))
        self.assertEqual(1, len(stderr_logs))
        self.assertEqual(1, len(component_logs))
        self.assertEqual(raw.encode(), stdout_logs[0].read_bytes())
        self.assertEqual(b"progress line\n", stderr_logs[0].read_bytes())
        self.assertIn(b'"result": "output-ready"', component_logs[0].read_bytes())
        self.assertIn(str(stdout_logs[0]), result.stdout)
        self.assertIn(str(stderr_logs[0]), result.stdout)
        self.assertIn(str(component_logs[0]), result.stdout)
        self.assertEqual(0o700, logs.stat().st_mode & 0o777)
        self.assertEqual(0o600, stdout_logs[0].stat().st_mode & 0o777)
        self.assertEqual(0o600, stderr_logs[0].stat().st_mode & 0o777)

    def test_explicit_jsonl_output_is_byte_for_byte_compatible(self) -> None:
        raw = '{ "type": "future.event", "value": "值🙂" }\n'

        result = self.run_adapter(
            "--output-format", "jsonl", stdout=raw
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(raw, result.stdout)
        self.assertFalse((self.base / "locks" / "logs").exists())

    def test_unsafe_pretty_log_path_falls_back_without_blocking_child(self) -> None:
        raw = '{"type":"turn.started"}\n'
        unsafe_lock_dir = self.worktree / "runtime"

        result = subprocess.run(
            [
                "python3",
                str(self.adapter),
                "dispatcher",
                "--workdir",
                str(self.worktree),
                "--codex-bin",
                str(self.fake_codex),
                "--lock-dir",
                str(unsafe_lock_dir),
                "--output-format",
                "pretty",
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.adapter_environment(stdout=raw),
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(raw, result.stdout)
        self.assertIn("reverting to JSONL", result.stderr)
        self.assertFalse((unsafe_lock_dir / "logs").exists())

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

    def test_pretty_timeout_does_not_block_on_silent_child(self) -> None:
        result = self.run_adapter(
            "--output-format",
            "pretty",
            "--timeout-seconds",
            "0.05",
            sleep_seconds="30",
        )

        self.assertEqual(124, result.returncode)
        self.assertIn("timeout", result.stdout.lower())

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


class _BlockingOperatorStream(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def write(self, value: str) -> int:
        self.started.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("operator display remained blocked")
        return super().write(value)

    def flush(self) -> None:
        self.started.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("operator display flush remained blocked")
        super().flush()


class _BlockingRawStream:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def write(self, value: bytes) -> int:
        self.started.set()
        if not self.release.wait(timeout=10):
            raise TimeoutError("raw stream remained blocked")
        return len(value)

    def flush(self) -> None:
        if not self.release.wait(timeout=10):
            raise TimeoutError("raw stream flush remained blocked")

    def close(self) -> None:
        return None


class _FailingRawStream:
    def write(self, _value: bytes) -> int:
        raise OSError("raw disk full")

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None


class RunChildPrettyBackpressureTests(unittest.TestCase):
    def test_timeout_reaps_child_while_operator_display_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock_descriptor = os.open(
                root / "dispatcher.lock", os.O_RDWR | os.O_CREAT, 0o600
            )
            blocked = _BlockingOperatorStream()
            renderer = LoopOutput(
                "pretty",
                "dispatcher",
                raw_log_dir=root / "logs",
                stdout=blocked,
                stderr=io.StringIO(),
            )
            command = [
                "python3",
                "-c",
                (
                    "import json,time; "
                    "print(json.dumps({'type':'turn.started'}), flush=True); "
                    "time.sleep(30)"
                ),
            ]
            try:
                started_at = time.monotonic()
                result = run_child(
                    command,
                    root,
                    lock_descriptor,
                    timeout_seconds=0.5,
                    output=renderer,
                )
                elapsed = time.monotonic() - started_at

                self.assertEqual(EX_TIMEOUT, result)
                self.assertLess(elapsed, 2.0)
                self.assertTrue(blocked.started.wait(timeout=1))
            finally:
                blocked.release.set()
                renderer.close()
                os.close(lock_descriptor)

    def test_blocked_raw_sink_degrades_without_overriding_child_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock_descriptor = os.open(
                root / "dispatcher.lock", os.O_RDWR | os.O_CREAT, 0o600
            )
            renderer = LoopOutput(
                "pretty",
                "dispatcher",
                raw_log_dir=root / "logs",
                stdout=io.BytesIO(),
                stderr=io.BytesIO(),
            )
            original_raw_stdout = renderer._raw_stdout
            blocked_raw = _BlockingRawStream()
            renderer._raw_stdout = blocked_raw  # type: ignore[assignment]
            try:
                started_at = time.monotonic()
                result = run_child(
                    [
                        "python3",
                        "-c",
                        (
                            "import json,sys; "
                            "print(json.dumps({'type':'turn.started'}), flush=True); "
                            "sys.exit(19)"
                        ),
                    ],
                    root,
                    lock_descriptor,
                    timeout_seconds=5,
                    output=renderer,
                )

                self.assertEqual(19, result)
                self.assertLess(time.monotonic() - started_at, 3.5)
                self.assertTrue(blocked_raw.started.is_set())
                self.assertFalse(renderer.pretty)

                started_at = time.monotonic()
                with self.assertRaisesRegex(TimeoutError, "raw stream"):
                    renderer.close()
                self.assertLess(time.monotonic() - started_at, 2.0)
            finally:
                blocked_raw.release.set()
                time.sleep(0.05)
                if original_raw_stdout is not None:
                    original_raw_stdout.close()
                for stream in (renderer._raw_stderr, renderer._raw_component):
                    if stream is not None:
                        stream.close()
                os.close(lock_descriptor)

    def test_raw_write_failure_retries_triggering_chunk_as_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock_descriptor = os.open(
                root / "dispatcher.lock", os.O_RDWR | os.O_CREAT, 0o600
            )
            visible_stdout = io.BytesIO()
            renderer = LoopOutput(
                "pretty",
                "dispatcher",
                raw_log_dir=root / "logs",
                stdout=visible_stdout,
                stderr=io.BytesIO(),
            )
            original_raw_stdout = renderer._raw_stdout
            renderer._raw_stdout = _FailingRawStream()  # type: ignore[assignment]
            try:
                result = run_child(
                    [
                        "python3",
                        "-c",
                        (
                            "import json; "
                            "print(json.dumps({'type':'turn.started'}), flush=True)"
                        ),
                    ],
                    root,
                    lock_descriptor,
                    timeout_seconds=5,
                    output=renderer,
                )

                self.assertEqual(0, result)
                self.assertFalse(renderer.pretty)
                self.assertEqual(
                    b'{"type": "turn.started"}\n', visible_stdout.getvalue()
                )
            finally:
                renderer.close()
                if original_raw_stdout is not None:
                    original_raw_stdout.close()
                os.close(lock_descriptor)

    def test_reader_thread_start_failure_terminates_and_reaps_child(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock_descriptor = os.open(
                root / "dispatcher.lock", os.O_RDWR | os.O_CREAT, 0o600
            )
            renderer = LoopOutput(
                "pretty",
                "dispatcher",
                raw_log_dir=root / "logs",
                stdout=io.StringIO(),
                stderr=io.StringIO(),
            )
            children: list[subprocess.Popen[bytes]] = []
            original_popen = subprocess.Popen
            original_start = threading.Thread.start
            starts = 0

            def capture_child(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
                child = original_popen(*args, **kwargs)
                children.append(child)
                return child

            def fail_second_start(thread: threading.Thread) -> None:
                nonlocal starts
                starts += 1
                if starts == 2:
                    raise RuntimeError("reader thread unavailable")
                original_start(thread)

            try:
                with (
                    mock.patch(
                        "scripts.codex_loop.subprocess.Popen",
                        side_effect=capture_child,
                    ),
                    mock.patch(
                        "scripts.codex_loop.threading.Thread.start",
                        new=fail_second_start,
                    ),
                ):
                    with self.assertRaisesRegex(
                        RuntimeError, "reader thread unavailable"
                    ):
                        run_child(
                            ["python3", "-c", "import time; time.sleep(30)"],
                            root,
                            lock_descriptor,
                            timeout_seconds=30,
                            output=renderer,
                        )

                self.assertEqual(1, len(children))
                self.assertIsNotNone(children[0].poll())
            finally:
                renderer.close()
                os.close(lock_descriptor)


class CodexLoopSkillContractTests(unittest.TestCase):
    @staticmethod
    def common_contract(path: Path) -> str:
        text = path.read_text(encoding="utf-8")
        start = "<!-- loop-common-contract:start -->"
        end = "<!-- loop-common-contract:end -->"
        return text.split(start, 1)[1].split(end, 1)[0].strip()

    def test_all_roles_are_explicit_one_shot_skills(self) -> None:
        for role in ("dispatcher", "coder", "reviewer", "gate-auditor"):
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
            ".claude/skills/{dispatcher,coder,reviewer,gate-auditor}/",
            ".agents/skills/emmet-loop-{dispatcher,coder,reviewer,gate-auditor}/",
            "`loop:blocked` 不取代 primary",
            "Reviewed-Head",
            "Reviewed-Base",
            "trusted runner",
            "scripts/codex-loop",
            "不提供、安裝、enable 或 start 主機 unit",
            "./scripts/codex-loop tmux restart",
            "repo 受控的角色預設",
            "bounded preflight",
            "`operator-status`",
            "no-durable-progress-after-iteration",
            "operator-alert",
            "operator-resolved",
            "operator-stall-reconciliation",
            "emmet-loop:dispatcher:gate-exit:<GATE>:main=<MAIN_SHA>",
            "emmet-loop:gate-auditor:audit:v1:gate=<GATE>:main=<SHA>:checkpoint=<ID>",
            "reason=gate-audit-requested",
            "窄版「Gate Auditor」結果卡",
            "audit-time snapshot",
            "result=stale-snapshot-no-publish",
            "result=publication-state-unknown",
            "mutations=unknown",
            "`manual-diagnostic-no-publish` 與 `invalid-wake-no-publish`",
            "只有 matching audit 可投影既有 durable verdict",
            "heading 表示 role",
            "`head_sha=none`",
            "不得為滿足 generic 摘要再追加一段 machine sentinel",
            "`evidence-incomplete-no-publish` 只用於 transport／pagination",
            "必須發佈 durable `verdict=unknown`",
            "`awaiting-user`",
            "terminal bell",
            "不自動 restart component",
            "不建立或喚醒未定義的第五角色",
            "drain-and-rotate",
            "control_inputs_match",
            "rotation-state.json",
            "emmet-qt-book-loop-control",
            "Git common-dir",
        ):
            with self.subTest(required=required):
                self.assertIn(required, protocol)
        self.assertIn("CLI 分成兩種長生命週期 component", protocol)
        self.assertIn("每個事件只啟動一次", protocol)
        self.assertIn("scripts/codex-loop events", protocol)
        self.assertIn(".claude/skills/", agents)
        self.assertIn(".agents/skills/", agents)
        self.assertIn("不安裝或啟用主機 scheduler", agents)
        self.assertIn("dedicated `*-loop-control` worktree", agents)

    def test_operations_explains_gate_auditor_card_and_staleness(self) -> None:
        operations = (ROOT / "docs" / "agent-loop-operations.md").read_text(
            encoding="utf-8"
        )
        for required in (
            "### 右上角：Gate Auditor 結果卡",
            "判定：等待你決定",
            "Gate：目前 <active>",
            "問題：無",
            "下一步（使用者）",
            "診斷：published / exit-ready",
            "<AUDIT_COMMENT_ID>",
            "<CHECKPOINT_ID>",
            "immutable permalink",
            "`有效：過期`",
            "舊 report 是 `exit-ready` 也",
            "Dispatcher 先對 current main reconciliation",
            "全部仍成立才建 fresh checkpoint",
            "第二、三欄固定是 `none / unknown`",
            "interactive PTY／session",
            "follow-up",
            "`write_stdin`",
            "送 EOF",
            "audit-time snapshot",
            "右下角 Events pane 的 current `operator-status`",
        ):
            with self.subTest(required=required):
                self.assertIn(required, operations)

    def test_cross_client_roles_share_the_bounded_context_contract(self) -> None:
        procedures = []
        for role in ("dispatcher", "coder", "reviewer", "gate-auditor"):
            codex = (
                ROOT
                / ".agents"
                / "skills"
                / f"emmet-loop-{role}"
                / "SKILL.md"
            )
            claude = ROOT / ".claude" / "skills" / role / "SKILL.md"
            for path in (codex, claude):
                with self.subTest(path=path):
                    contract = self.common_contract(path)
                    for required in (
                        "已注入",
                        "不得再次輸出整份",
                        "`AGENTS.md`",
                        "bounded preflight",
                        "不是授權",
                        "mutation 前",
                        "compact summary",
                        "預設禁止完整 comments/history",
                    ):
                        self.assertIn(required, contract)
                procedures.append(self.common_contract(path))
        self.assertEqual(1, len(set(procedures)))

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

    def test_gate_auditor_routing_and_human_checkpoint_are_canonical(self) -> None:
        protocol = (ROOT / "docs" / "agent-loop.md").read_text(encoding="utf-8")
        for required in (
            "reason=gate-audit-requested",
            "尚無 matching audit",
            "matching audit verdict 是 `not-ready`",
            "matching audit verdict 是 `unknown`",
            "matching audit verdict 是 `exit-ready`",
            "exit-ready` 只讓流程進入 `awaiting-user`",
            "新的 gate-exit\ncheckpoint comment",
            "舊 audit 只保留為歷史",
        ):
            with self.subTest(required=required):
                self.assertIn(required, protocol)

    def test_dispatcher_can_recover_from_checkpoint_bound_not_ready(self) -> None:
        for path in (
            ROOT / ".agents/skills/emmet-loop-dispatcher/SKILL.md",
            ROOT / ".claude/skills/dispatcher/SKILL.md",
        ):
            procedure = path.read_text(encoding="utf-8")
            for required in (
                "`not-ready` 只 supersede",
                "恢復或派目前 active-gate 工作",
                "新的 checkpoint comment",
                "新 comment ID",
                "舊 audit",
                "沒有 superseding `not-ready` audit",
                "duplicate suppression",
                "matching `exit-ready`",
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
            "| 左中 | coder agent |",
            "| 左下 | reviewer agent |",
            "| 右上 | Gate Auditor agent |",
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
            "emmet-qt-book-loop-control",
            "tracked／untracked",
            "control_bootstrap=true",
        ):
            with self.subTest(required=required):
                self.assertIn(required, runbook)


if __name__ == "__main__":
    unittest.main()
