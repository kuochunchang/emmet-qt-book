from __future__ import annotations

import io
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import unittest
from unittest import mock

from scripts import codex_loop as adapter
from scripts import codex_loop_output as output
from scripts import codex_loop_runtime as runtime


class _TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


class _BrokenTTY(io.StringIO):
    def isatty(self) -> bool:
        raise OSError("not available")


class _FailingText(io.StringIO):
    def write(self, _value: str) -> int:
        raise OSError("display failed")


class _BlockingText(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def write(self, value: str) -> int:
        self.started.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test display remained blocked")
        return super().write(value)

    def flush(self) -> None:
        self.started.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test display flush remained blocked")
        super().flush()


class _FailingRaw:
    def write(self, _value: bytes) -> int:
        raise OSError("component raw failed")

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None


class _BlockingFailingRaw(_FailingRaw):
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def write(self, _value: bytes) -> int:
        self.started.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("raw write remained blocked")
        raise OSError("component raw failed after block")


class OutputFormatTests(unittest.TestCase):
    def test_explicit_formats_and_auto_resolution(self) -> None:
        self.assertEqual(("auto", "pretty", "jsonl"), output.OUTPUT_FORMATS)
        self.assertEqual(
            "pretty", output.resolve_output_format("pretty", io.StringIO())
        )
        self.assertEqual("jsonl", output.resolve_output_format("jsonl", _TTY()))
        self.assertEqual("pretty", output.resolve_output_format("auto", _TTY()))
        self.assertEqual("jsonl", output.resolve_output_format("auto", io.StringIO()))
        self.assertEqual("jsonl", output.resolve_output_format("auto", _BrokenTTY()))

    def test_invalid_format_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown output format"):
            output.resolve_output_format("text", io.StringIO())


class JsonlOutputTests(unittest.TestCase):
    def test_jsonl_preserves_component_and_child_streams_without_raw_files(
        self,
    ) -> None:
        stdout = io.BytesIO()
        stderr = io.BytesIO()
        renderer = output.LoopOutput(
            "jsonl", "dispatcher", stdout=stdout, stderr=stderr
        )
        renderer.emit_component(
            {"role": "dispatcher", "component": "agent", "result": "waiting"}
        )
        child_stdout = b'{"type":"thread.started","thread_id":"abc"}\n'
        child_stderr = b"progress\xff\n"
        renderer.feed_stdout(child_stdout)
        renderer.feed_stderr(child_stderr)
        renderer.close()

        component = (
            json.dumps(
                {
                    "role": "dispatcher",
                    "component": "agent",
                    "result": "waiting",
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
            + b"\n"
        )
        self.assertEqual(component + child_stdout, stdout.getvalue())
        self.assertEqual(child_stderr, stderr.getvalue())
        self.assertFalse(renderer.pretty)
        self.assertIsNone(renderer.raw_stdout_path)
        self.assertIsNone(renderer.raw_stderr_path)
        self.assertIsNone(renderer.raw_component_path)

    def test_close_does_not_close_caller_streams(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        renderer = output.LoopOutput(
            "jsonl", "events", stdout=stdout, stderr=stderr
        )
        renderer.close()
        stdout.write("still open")
        stderr.write("still open")
        self.assertIn("still open", stdout.getvalue())
        self.assertIn("still open", stderr.getvalue())


class PrettyOutputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.logs = self.base / "private-logs"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def renderer(
        self, component: str = "dispatcher"
    ) -> tuple[output.LoopOutput, io.StringIO, io.StringIO]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        renderer = output.LoopOutput(
            "pretty",
            component,
            raw_log_dir=self.logs,
            stdout=stdout,
            stderr=stderr,
        )
        return renderer, stdout, stderr

    def test_pretty_captures_exact_raw_bytes_before_rendering(self) -> None:
        renderer, stdout, stderr = self.renderer()
        component_event = {
            "component": "agent",
            "role": "dispatcher",
            "result": "event-received",
            "event": {"event_id": "event-1", "reason": "review-requested"},
        }
        renderer.emit_component(component_event)
        child_event = {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": "第一行\n第二行 測試",
            },
        }
        child_bytes = (json.dumps(child_event, ensure_ascii=False) + "\n").encode(
            "utf-8"
        )
        split = child_bytes.index("測".encode("utf-8")) + 1
        renderer.feed_stdout(child_bytes[:split])
        renderer.feed_stdout(child_bytes[split:])
        raw_stderr = b"warning \x1b[31mred\x1b[0m\r\n"
        renderer.feed_stderr(raw_stderr)
        renderer.close()

        component_bytes = (
            json.dumps(component_event, ensure_ascii=False, sort_keys=True) + "\n"
        ).encode("utf-8")
        assert renderer.raw_stdout_path is not None
        assert renderer.raw_stderr_path is not None
        assert renderer.raw_component_path is not None
        self.assertEqual(child_bytes, renderer.raw_stdout_path.read_bytes())
        self.assertEqual(
            component_bytes, renderer.raw_component_path.read_bytes()
        )
        self.assertEqual(raw_stderr, renderer.raw_stderr_path.read_bytes())
        self.assertIn("[agent/dispatcher] event-received", stdout.getvalue())
        self.assertIn("reason=review-requested", stdout.getvalue())
        self.assertIn("第一行\n  第二行 測試", stdout.getvalue())
        self.assertNotIn("item.completed", stdout.getvalue())
        self.assertIn("codex stderr:", stderr.getvalue())
        self.assertIn("warning red", stderr.getvalue())
        self.assertNotIn("\x1b", stderr.getvalue())
        self.assertNotIn("\r", stderr.getvalue())

    def test_gate_auditor_operator_card_is_readable_and_lossless(self) -> None:
        renderer, stdout, _ = self.renderer("gate-auditor")
        card = (
            "Gate Auditor\n"
            "判定：等待你決定\n"
            "Gate：目前 <active>；稽核 <active>；後繼 <successor>（未生效）\n"
            "問題：無\n"
            "下一步（使用者）：決定是否啟動 transition。\n"
            "本輪：已發佈 Meta #1 audit；只新增 report，active gate 未變\n"
            "有效：檢查時 main@123456789abc；at=2026-07-17T18:45:30+08:00\n"
            "診斷：published / exit-ready / "
            "meta-comment-only / cache=git-fetch\n"
            "證據：https://example.test/issues/1#issuecomment-42"
        )
        self.assertEqual(9, len(card.splitlines()))

        def display_cells(value: str) -> int:
            return sum(
                0
                if unicodedata.combining(character)
                else 2
                if unicodedata.east_asian_width(character) in {"F", "W"}
                else 1
                for character in value
            )

        for line in card.splitlines()[:-1]:
            with self.subTest(line=line):
                self.assertLessEqual(display_cells(line), 80)
        stale_validity = (
            "有效：過期；bound@123456789abc≠current@abcdef123456；勿沿用"
        )
        self.assertLessEqual(display_cells(stale_validity), 80)
        child_event = {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": card},
        }
        child_bytes = (json.dumps(child_event, ensure_ascii=False) + "\n").encode(
            "utf-8"
        )

        renderer.feed_stdout(child_bytes)
        renderer.close()

        assert renderer.raw_stdout_path is not None
        self.assertEqual(child_bytes, renderer.raw_stdout_path.read_bytes())
        display = stdout.getvalue()
        self.assertIn("agent:\n  Gate Auditor", display)
        self.assertIn("  判定：等待你決定", display)
        self.assertIn("  問題：無", display)
        self.assertIn("  下一步（使用者）：決定是否啟動 transition。", display)
        self.assertIn("  診斷：published / exit-ready", display)
        self.assertIn("  證據：https://example.test", display)
        self.assertNotIn("item.completed", display)

    def test_gate_auditor_stdin_publish_command_does_not_echo_body(self) -> None:
        renderer, stdout, _ = self.renderer("gate-auditor")
        report_body = "PRIVATE FULL GATE AUDIT TABLE"
        command_event = {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "gh issue comment 1 --body-file -",
                "exit_code": 0,
                "aggregated_output": (
                    "https://example.test/issues/1#issuecomment-42\n"
                ),
            },
        }
        command_bytes = (
            json.dumps(command_event, ensure_ascii=False) + "\n"
        ).encode("utf-8")

        renderer.feed_stdout(command_bytes)
        renderer.close()

        display = stdout.getvalue()
        self.assertIn("gh issue comment 1 --body-file -", display)
        self.assertIn("issuecomment-42", display)
        self.assertNotIn(report_body, display)
        self.assertNotIn("--body ", display)

    def test_private_generation_paths_and_permissions(self) -> None:
        first, _, _ = self.renderer("gate-auditor")
        first_stdout = first.raw_stdout_path
        first_stderr = first.raw_stderr_path
        first_component = first.raw_component_path
        first.close()
        second, _, _ = self.renderer("gate-auditor")
        second_stdout = second.raw_stdout_path
        second.close()

        assert first_stdout is not None
        assert first_stderr is not None
        assert first_component is not None
        assert second_stdout is not None
        self.assertNotEqual(first_stdout, second_stdout)
        self.assertIn("gate-auditor-", first_stdout.name)
        self.assertEqual(0o700, stat.S_IMODE(self.logs.stat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE(first_stdout.stat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE(first_stderr.stat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE(first_component.stat().st_mode))

    def test_raw_stdout_is_written_before_a_display_failure(self) -> None:
        renderer = output.LoopOutput(
            "pretty",
            "dispatcher",
            raw_log_dir=self.logs,
            stdout=_FailingText(),
            stderr=io.StringIO(),
        )
        raw = b'{"type":"turn.started"}\n'
        renderer.feed_stdout(raw)
        renderer.close()
        assert renderer.raw_stdout_path is not None
        self.assertEqual(raw, renderer.raw_stdout_path.read_bytes())
        self.assertTrue(renderer.display_errors)
        self.assertIn("display failed", str(renderer.display_errors[0]))

    def test_display_thread_start_failure_cleans_files_and_falls_back_jsonl(
        self,
    ) -> None:
        with (
            mock.patch.object(
                output.threading.Thread,
                "start",
                side_effect=RuntimeError("thread unavailable"),
            ),
            mock.patch.object(
                adapter, "repository_private_roots", return_value=()
            ),
        ):
            renderer = adapter.create_loop_output(
                "pretty",
                component="dispatcher",
                raw_log_dir=self.logs,
                workdir=self.base,
                common_dir=self.base / ".git",
            )

        self.assertFalse(renderer.pretty)
        self.assertEqual([], list(self.logs.glob("*")))
        renderer.close()

    def test_component_raw_failure_degrades_runtime_and_retries_jsonl(self) -> None:
        visible_stdout = io.StringIO()
        visible_stderr = io.StringIO()
        renderer = output.LoopOutput(
            "pretty",
            "events",
            raw_log_dir=self.logs,
            stdout=visible_stdout,
            stderr=visible_stderr,
        )
        original_component = renderer._raw_component
        renderer._raw_component = _FailingRaw()  # type: ignore[assignment]
        previous = runtime._activate_operator_output(renderer)
        try:
            with (
                mock.patch.object(runtime.sys, "stdout", visible_stdout),
                mock.patch.object(runtime.sys, "stderr", visible_stderr),
            ):
                runtime.emit(
                    component="events",
                    result="poll-complete",
                    health="idle",
                )
        finally:
            runtime._restore_operator_output(renderer, previous)
            if original_component is not None:
                original_component.close()

        self.assertFalse(renderer.pretty)
        records = [
            json.loads(line)
            for line in visible_stdout.getvalue().splitlines()
            if line.startswith("{")
        ]
        self.assertEqual("poll-complete", records[-1]["result"])
        self.assertIn("reverting to JSONL", visible_stderr.getvalue())

    def test_split_record_prefix_is_replayed_when_raw_write_fails(self) -> None:
        visible_stdout = io.BytesIO()
        renderer = output.LoopOutput(
            "pretty",
            "dispatcher",
            raw_log_dir=self.logs,
            stdout=visible_stdout,
            stderr=io.BytesIO(),
        )
        original_raw_stdout = renderer._raw_stdout
        prefix = b'{"type":"turn.'
        suffix = b'started"}\n'
        renderer.feed_stdout(prefix)
        renderer._raw_stdout = _FailingRaw()  # type: ignore[assignment]
        try:
            with self.assertRaisesRegex(OSError, "component raw failed"):
                renderer.feed_stdout(suffix)
            renderer.degrade_to_jsonl()
            renderer.feed_stdout(suffix)

            self.assertEqual(prefix + suffix, visible_stdout.getvalue())
            self.assertEqual(bytearray(), renderer._stdout_buffer)
        finally:
            renderer.close()
            if original_raw_stdout is not None:
                original_raw_stdout.close()

    def test_degrade_race_replays_prefix_before_waiting_feed_chunk(self) -> None:
        visible_stdout = io.BytesIO()
        renderer = output.LoopOutput(
            "pretty",
            "dispatcher",
            raw_log_dir=self.logs,
            stdout=visible_stdout,
            stderr=io.BytesIO(),
        )
        prefix = b'{"type":"turn.'
        suffix = b'started"}\n'
        renderer.feed_stdout(prefix)
        entered = threading.Event()

        def feed_suffix() -> None:
            entered.set()
            renderer.feed_stdout(suffix)

        with renderer._lock:
            worker = threading.Thread(target=feed_suffix)
            worker.start()
            self.assertTrue(entered.wait(timeout=1))
            renderer.degrade_to_jsonl()
        worker.join(timeout=1)
        renderer.close()

        self.assertFalse(worker.is_alive())
        self.assertEqual(prefix + suffix, visible_stdout.getvalue())
        self.assertEqual(bytearray(), renderer._fallback_stdout)

    def test_forced_degrade_later_migrates_prefix_after_raw_unblocks(self) -> None:
        visible_stdout = io.BytesIO()
        renderer = output.LoopOutput(
            "pretty",
            "dispatcher",
            raw_log_dir=self.logs,
            stdout=visible_stdout,
            stderr=io.BytesIO(),
        )
        original_raw_stdout = renderer._raw_stdout
        prefix = b'{"type":"turn.'
        suffix = b'started"}\n'
        renderer.feed_stdout(prefix)
        blocked = _BlockingFailingRaw()
        renderer._raw_stdout = blocked  # type: ignore[assignment]

        def fail_then_retry() -> None:
            try:
                renderer.feed_stdout(suffix)
            except OSError:
                renderer.degrade_to_jsonl()
                renderer.feed_stdout(suffix)

        worker = threading.Thread(target=fail_then_retry)
        worker.start()
        self.assertTrue(blocked.started.wait(timeout=1))
        renderer.degrade_to_jsonl(wait_for_raw_lock=False)
        blocked.release.set()
        worker.join(timeout=2)
        renderer.close()
        if original_raw_stdout is not None:
            original_raw_stdout.close()

        self.assertFalse(worker.is_alive())
        self.assertTrue(renderer._fallback_buffers_migrated)
        self.assertEqual(prefix + suffix, visible_stdout.getvalue())

    def test_blocked_display_does_not_block_raw_capture_or_close_forever(
        self,
    ) -> None:
        blocked = _BlockingText()
        diagnostics = io.StringIO()
        with mock.patch.object(output, "MAX_DISPLAY_QUEUE", 1):
            renderer = output.LoopOutput(
                "pretty",
                "dispatcher",
                raw_log_dir=self.logs,
                stdout=blocked,
                stderr=diagnostics,
            )
        raw = b'{"type":"turn.started"}\n'

        started_at = time.monotonic()
        renderer.feed_stdout(raw)
        self.assertLess(time.monotonic() - started_at, 0.5)
        self.assertTrue(blocked.started.wait(timeout=1))
        renderer.feed_stdout(raw)
        renderer.feed_stdout(raw)

        started_at = time.monotonic()
        renderer.close()
        self.assertLess(time.monotonic() - started_at, 1.5)
        assert renderer.raw_stdout_path is not None
        self.assertEqual(raw * 3, renderer.raw_stdout_path.read_bytes())
        self.assertGreater(renderer.display_dropped, 0)

        blocked.release.set()
        assert renderer._display_thread is not None
        renderer._display_thread.join(timeout=1)
        self.assertFalse(renderer._display_thread.is_alive())
        self.assertIn("terminal backpressure omitted", diagnostics.getvalue())

    def test_full_real_stdout_pipe_does_not_abort_python_shutdown(self) -> None:
        program = "\n".join(
            (
                "import json, tempfile",
                "from pathlib import Path",
                "from scripts.codex_loop_output import LoopOutput",
                "temporary = tempfile.TemporaryDirectory()",
                "renderer = LoopOutput('pretty', 'pipe-test', "
                "raw_log_dir=Path(temporary.name) / 'logs')",
                "event = {'type': 'item.completed', 'item': "
                "{'type': 'agent_message', 'text': 'x' * 16000}}",
                "record = (json.dumps(event) + '\\n').encode()",
                "for _ in range(300): renderer.feed_stdout(record)",
                "renderer.close()",
                "temporary.cleanup()",
            )
        )
        process = subprocess.Popen(
            [sys.executable, "-c", program],
            cwd=Path(__file__).resolve().parents[1],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            return_code = process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            self.fail("pretty renderer did not exit with an unread full stdout pipe")
        stdout, stderr = process.communicate(timeout=5)

        self.assertEqual(0, return_code, stderr.decode(errors="replace"))
        self.assertNotIn(b"Fatal Python error", stderr)
        self.assertGreater(len(stdout), 0)

    def test_normal_close_drains_pending_records_to_a_healthy_pipe(self) -> None:
        program = "\n".join(
            (
                "import json, tempfile",
                "from pathlib import Path",
                "from scripts.codex_loop_output import LoopOutput",
                "temporary = tempfile.TemporaryDirectory()",
                "renderer = LoopOutput('pretty', 'pipe-test', "
                "raw_log_dir=Path(temporary.name) / 'logs')",
                "for index in range(100):",
                "    event = {'type': 'item.completed', 'item': "
                "{'type': 'agent_message', 'text': f'MSG-{index:03d}'}}",
                "    renderer.feed_stdout((json.dumps(event) + '\\n').encode())",
                "renderer.close()",
                "temporary.cleanup()",
            )
        )
        result = subprocess.run(
            [sys.executable, "-c", program],
            cwd=Path(__file__).resolve().parents[1],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )

        self.assertEqual(0, result.returncode, result.stderr.decode())
        display = result.stdout.decode()
        self.assertEqual(100, display.count("agent: MSG-"))
        self.assertIn("agent: MSG-099", display)

    def test_raw_failure_plus_unread_pipe_does_not_abort_shutdown(self) -> None:
        program = "\n".join(
            (
                "import os, sys, tempfile",
                "from pathlib import Path",
                "from scripts.codex_loop import EX_TIMEOUT, run_child",
                "from scripts.codex_loop_output import LoopOutput",
                "class FailingRaw:",
                "    def write(self, value): raise OSError('raw disk full')",
                "    def flush(self): pass",
                "    def close(self): pass",
                "temporary = tempfile.TemporaryDirectory()",
                "root = Path(temporary.name)",
                "lock_fd = os.open(root / 'role.lock', os.O_RDWR | os.O_CREAT, 0o600)",
                "renderer = LoopOutput('pretty', 'pipe-test', "
                "raw_log_dir=root / 'logs')",
                "original = renderer._raw_stdout",
                "renderer._raw_stdout = FailingRaw()",
                "code = run_child([sys.executable, '-c', "
                "\"import sys,time; sys.stdout.write('x'*200000); "
                "sys.stdout.flush(); time.sleep(30)\"], "
                "root, lock_fd, .5, output=renderer)",
                "renderer.close()",
                "original.close()",
                "os.close(lock_fd)",
                "temporary.cleanup()",
                "sys.exit(0 if code == EX_TIMEOUT else 3)",
            )
        )
        process = subprocess.Popen(
            [sys.executable, "-c", program],
            cwd=Path(__file__).resolve().parents[1],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            return_code = process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            self.fail("raw fallback remained blocked on unread stdout pipe")
        _stdout, stderr = process.communicate(timeout=5)

        self.assertEqual(0, return_code, stderr.decode(errors="replace"))
        self.assertNotIn(b"Fatal Python error", stderr)

    def test_degraded_component_output_is_bounded_on_full_pipe(self) -> None:
        program = "\n".join(
            (
                "import os, tempfile",
                "from pathlib import Path",
                "from scripts import codex_loop_runtime as runtime",
                "from scripts.codex_loop_output import LoopOutput",
                "temporary = tempfile.TemporaryDirectory()",
                "renderer = LoopOutput('pretty', 'events', "
                "raw_log_dir=Path(temporary.name) / 'logs')",
                "renderer.degrade_to_jsonl()",
                "os.set_blocking(1, False)",
                "while True:",
                "    try: os.write(1, b'x' * 4096)",
                "    except BlockingIOError: break",
                "os.set_blocking(1, True)",
                "previous = runtime._activate_operator_output(renderer)",
                "runtime.emit(component='events', result='poll-complete')",
                "runtime._restore_operator_output(renderer, previous)",
                "temporary.cleanup()",
            )
        )
        process = subprocess.Popen(
            [sys.executable, "-c", program],
            cwd=Path(__file__).resolve().parents[1],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            return_code = process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            self.fail("degraded component output blocked on a full stdout pipe")
        _stdout, stderr = process.communicate(timeout=5)

        self.assertEqual(0, return_code, stderr.decode(errors="replace"))
        self.assertNotIn(b"Fatal Python error", stderr)

    def test_raw_log_directory_inside_repository_is_rejected(self) -> None:
        repository = Path(output.__file__).resolve().parents[1]
        with self.assertRaisesRegex(ValueError, "outside repository"):
            output.LoopOutput(
                "pretty",
                "dispatcher",
                raw_log_dir=repository,
                stdout=io.StringIO(),
                stderr=io.StringIO(),
            )

    def test_additional_linked_worktree_and_common_dir_are_rejected(self) -> None:
        linked = self.base / "linked-worktree"
        common = self.base / "shared-git-dir"
        for forbidden in (linked, common):
            with self.subTest(forbidden=forbidden):
                with self.assertRaisesRegex(ValueError, "outside repository"):
                    output.LoopOutput(
                        "pretty",
                        "dispatcher",
                        raw_log_dir=forbidden / "logs",
                        forbidden_roots=(linked, common),
                        stdout=io.StringIO(),
                        stderr=io.StringIO(),
                    )

    def test_lifecycle_and_common_items_are_human_readable(self) -> None:
        renderer, stdout, _ = self.renderer()
        events = [
            {"type": "thread.started", "thread_id": "thread-123"},
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": "git status --short",
                    "aggregated_output": " M file.md",
                    "exit_code": 0,
                    "status": "completed",
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "type": "file_change",
                    "changes": [
                        {"path": "scripts/a.py", "kind": "update"},
                        {"path": "tests/test_a.py", "kind": "add"},
                    ],
                },
            },
            {
                "type": "item.started",
                "item": {
                    "type": "mcp_tool_call",
                    "server": "github",
                    "tool": "get_issue",
                    "arguments": {"number": 40},
                    "status": "in_progress",
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "type": "collab_tool_call",
                    "tool": "send_message",
                    "receiver_thread_ids": ["agent-7"],
                    "prompt": "請檢查輸出",
                    "status": "completed",
                },
            },
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        ]
        renderer.feed_stdout(
            b"".join(
                (json.dumps(event) + "\n").encode("utf-8") for event in events
            )
        )
        renderer.close()

        display = stdout.getvalue()
        self.assertIn("codex thread started: thread-123", display)
        self.assertIn("codex turn started", display)
        self.assertIn("command completed status=completed exit=0", display)
        self.assertIn("$ git status --short", display)
        self.assertIn("scripts/a.py (update)", display)
        self.assertIn("tool started status=in_progress: github/get_issue", display)
        self.assertIn('arguments: {"number": 40}', display)
        self.assertIn(
            "collaboration completed status=completed: send_message", display
        )
        self.assertIn('targets: ["agent-7"]', display)
        self.assertIn("prompt: 請檢查輸出", display)
        self.assertIn("input_tokens=10", display)
        self.assertIn("output_tokens=5", display)

    def test_output_ready_component_shows_all_raw_paths(self) -> None:
        renderer, stdout, _ = self.renderer()
        renderer.emit_component(
            {
                "component": "agent",
                "role": "dispatcher",
                "result": "output-ready",
                "raw_stdout": "/tmp/private/dispatcher.stdout.jsonl",
                "raw_stderr": "/tmp/private/dispatcher.stderr.log",
                "raw_component": "/tmp/private/dispatcher.component.jsonl",
            }
        )
        renderer.close()

        display = stdout.getvalue()
        self.assertIn(
            "raw_stdout: /tmp/private/dispatcher.stdout.jsonl", display
        )
        self.assertIn("raw_stderr: /tmp/private/dispatcher.stderr.log", display)
        self.assertIn(
            "raw_component: /tmp/private/dispatcher.component.jsonl", display
        )

    def test_terminal_controls_are_removed_but_unicode_multiline_is_preserved(
        self,
    ) -> None:
        renderer, stdout, _ = self.renderer()
        text = (
            "\x1b]2;hostile title\x07第一行\r\n"
            "第二行 \x1b[31m紅色\x1b[0m\x00\x85\u202e"
        )
        event = {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": text},
        }
        renderer.feed_stdout((json.dumps(event) + "\n").encode("utf-8"))
        renderer.close()

        display = stdout.getvalue()
        self.assertIn("第一行\n  第二行 紅色", display)
        self.assertNotIn("hostile title", display)
        for character in ("\x1b", "\x07", "\r", "\x00", "\x85", "\u202e"):
            self.assertNotIn(character, display)

    def test_command_updates_do_not_repeat_accumulated_output(self) -> None:
        renderer, stdout, _ = self.renderer()
        renderer.feed_stdout(
            (
                json.dumps(
                    {
                        "type": "item.updated",
                        "item": {
                            "type": "command_execution",
                            "command": "long-running-command",
                            "aggregated_output": "repeated-output-secret",
                            "status": "in_progress",
                        },
                    }
                )
                + "\n"
            ).encode("utf-8")
        )
        renderer.close()

        display = stdout.getvalue()
        self.assertIn("command updated status=in_progress", display)
        self.assertNotIn("repeated-output-secret", display)

    def test_unknown_malformed_and_partial_records_fail_open(self) -> None:
        renderer, stdout, stderr = self.renderer()
        unknown = b'{"type":"future.event","value":1}\n'
        malformed = b'{not-json}\n'
        valid_without_newline = b'{"type":"turn.started"}'
        renderer.feed_stdout(unknown + malformed + valid_without_newline)
        renderer.finish()
        renderer.close()

        self.assertIn("codex event: future.event", stdout.getvalue())
        self.assertIn("codex turn started", stdout.getvalue())
        diagnostics = stderr.getvalue()
        self.assertIn("codex stdout malformed", diagnostics)
        self.assertIn("missing its final newline", diagnostics)
        assert renderer.raw_stdout_path is not None
        self.assertEqual(
            unknown + malformed + valid_without_newline,
            renderer.raw_stdout_path.read_bytes(),
        )

    def test_partial_child_record_and_component_event_use_separate_traces(
        self,
    ) -> None:
        renderer, _, _ = self.renderer()
        child_partial = b'{"type":"turn.started"'
        component_event = {
            "component": "agent",
            "role": "dispatcher",
            "result": "iteration-finished",
            "exit_code": 0,
        }

        renderer.feed_stdout(child_partial)
        renderer.emit_component(component_event)
        renderer.close()

        assert renderer.raw_stdout_path is not None
        assert renderer.raw_component_path is not None
        self.assertEqual(child_partial, renderer.raw_stdout_path.read_bytes())
        component_lines = renderer.raw_component_path.read_text(
            encoding="utf-8"
        ).splitlines()
        self.assertEqual(1, len(component_lines))
        self.assertEqual(component_event, json.loads(component_lines[0]))

    def test_invalid_utf8_is_diagnostic_and_does_not_hide_next_event(self) -> None:
        renderer, stdout, stderr = self.renderer()
        renderer.feed_stdout(
            b'{"type":"item.completed","item":{"type":"agent_message","text":"\xff"}}\n'
            b'{"type":"turn.started"}\n'
        )
        renderer.close()

        self.assertIn("UnicodeDecodeError", stderr.getvalue())
        self.assertIn("codex turn started", stdout.getvalue())

    def test_line_buffer_is_bounded_while_raw_remains_complete(self) -> None:
        renderer, _, stderr = self.renderer()
        payload = b"x" * 96 + b"\n" + b'{"type":"turn.started"}\n'
        with mock.patch.object(output, "MAX_LINE_BYTES", 32):
            renderer.feed_stdout(payload[:20])
            renderer.feed_stdout(payload[20:])
        renderer.close()

        self.assertIn("line exceeded 32 bytes (96 bytes observed)", stderr.getvalue())
        assert renderer.raw_stdout_path is not None
        self.assertEqual(payload, renderer.raw_stdout_path.read_bytes())

    def test_finish_is_idempotent_and_sink_can_be_reused_for_next_child(self) -> None:
        renderer, stdout, _ = self.renderer()
        renderer.finish()
        renderer.finish()
        renderer.feed_stdout(b'{"type":"turn.started"}\n')
        renderer.emit_component({"component": "agent", "result": "waiting"})
        renderer.close()
        self.assertIn("codex turn started", stdout.getvalue())
        self.assertIn("[agent] waiting", stdout.getvalue())
        renderer.close()

    def test_close_marks_sink_closed_even_when_raw_flush_fails(self) -> None:
        renderer, _, _ = self.renderer()
        original_stdout = renderer._raw_stdout
        assert original_stdout is not None
        failing = mock.Mock()
        failing.flush.side_effect = OSError("raw flush failed")
        renderer._raw_stdout = failing

        with self.assertRaisesRegex(OSError, "raw flush failed"):
            renderer.close()

        failing.close.assert_called_once()
        self.assertTrue(renderer._closed)
        original_stdout.close()


if __name__ == "__main__":
    unittest.main()
