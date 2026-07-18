#!/usr/bin/env python3
"""Lossless raw capture and a bounded human-readable Codex loop display.

This module keeps the producer's JSONL bytes as a private diagnostic record
when pretty output is selected and only then renders a terminal-safe summary.
Formatting failures therefore do not hide or rewrite the original producer
output; the files remain observational and are never workflow state.
"""

from __future__ import annotations

from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import queue
import re
import secrets
import select
import stat
import sys
import tempfile
import threading
import time
from typing import BinaryIO, Callable, Mapping, Sequence, TextIO


OUTPUT_FORMATS = ("auto", "pretty", "jsonl")

MAX_LINE_BYTES = 1024 * 1024
MAX_DISPLAY_CHARS = 16 * 1024
MAX_PREVIEW_CHARS = 512
MAX_DISPLAY_QUEUE = 1024
DISPLAY_CLOSE_TIMEOUT_SECONDS = 1.0
DISPLAY_WRITE_TIMEOUT_SECONDS = 0.25
DISPLAY_WRITE_CHUNK_BYTES = 1024
DEGRADED_WRITE_TIMEOUT_SECONDS = 0.05
CLOSE_LOCK_TIMEOUT_SECONDS = 1.0

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_CSI_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_OSC_PATTERN = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?")
_ESCAPE_PATTERN = re.compile(r"\x1b(?:[@-_]|[ -/].)?")
_UNSAFE_CONTROL_PATTERN = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]"
)
_BIDI_CONTROL_PATTERN = re.compile(r"[\u202a-\u202e\u2066-\u2069]")
_COMPONENT_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")
_DISPLAY_STOP = object()


def resolve_output_format(value: str, stream: object) -> str:
    """Resolve ``auto`` conservatively from the destination stream's TTY state."""

    if value not in OUTPUT_FORMATS:
        choices = ", ".join(OUTPUT_FORMATS)
        raise ValueError(f"unknown output format {value!r}; choose one of: {choices}")
    if value != "auto":
        return value
    try:
        interactive = bool(stream.isatty())  # type: ignore[attr-defined]
    except (AttributeError, OSError, ValueError):
        interactive = False
    return "pretty" if interactive else "jsonl"


def _sanitize_terminal_text(value: object, *, limit: int = MAX_DISPLAY_CHARS) -> str:
    """Remove terminal controls while retaining newlines, tabs, and Unicode."""

    text = str(value).replace("\r", "")
    text = _OSC_PATTERN.sub("", text)
    text = _CSI_PATTERN.sub("", text)
    text = _ESCAPE_PATTERN.sub("", text)
    text = _UNSAFE_CONTROL_PATTERN.sub("", text)
    text = _BIDI_CONTROL_PATTERN.sub("", text)
    if len(text) > limit:
        omitted = len(text) - limit
        text = (
            text[:limit].rstrip()
            + f"\n… {omitted} characters omitted; see raw log"
        )
    return text


def _bounded_preview(value: object) -> str:
    text = _sanitize_terminal_text(value, limit=MAX_PREVIEW_CHARS)
    return text if text else "<empty>"


def _block(label: str, value: object) -> str:
    text = _sanitize_terminal_text(value)
    if not text:
        return label
    if "\n" not in text:
        return f"{label} {text}"
    return f"{label}\n" + "\n".join(f"  {line}" for line in text.splitlines())


def _mapping_text(value: object, *keys: str) -> str | None:
    if not isinstance(value, Mapping):
        return None
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def _structured_text(value: object) -> str | None:
    if isinstance(value, str):
        return value or None
    if isinstance(value, (Mapping, list, tuple)):
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            return str(value)
    if value is not None:
        return str(value)
    return None


def _format_usage(value: object) -> str:
    if not isinstance(value, Mapping):
        return ""
    fields: list[str] = []
    for key in (
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "total_tokens",
    ):
        item = value.get(key)
        if isinstance(item, int) and not isinstance(item, bool):
            fields.append(f"{key}={item}")
    return " ".join(fields)


def _format_file_changes(item: Mapping[str, object]) -> str:
    changes = item.get("changes")
    if not isinstance(changes, list):
        path = item.get("path")
        return _sanitize_terminal_text(path) if isinstance(path, str) else ""
    rendered: list[str] = []
    for change in changes[:20]:
        if not isinstance(change, Mapping):
            continue
        path = change.get("path")
        if not isinstance(path, str):
            continue
        kind = change.get("kind") or change.get("type")
        rendered.append(
            f"{_sanitize_terminal_text(path)}"
            + (f" ({_sanitize_terminal_text(kind)})" if kind else "")
        )
    if len(changes) > 20:
        rendered.append(f"… {len(changes) - 20} more paths; see raw log")
    return "\n".join(rendered)


def _format_item(event_type: str, item: Mapping[str, object]) -> str:
    item_type = str(item.get("type") or "unknown")
    phase = event_type.removeprefix("item.")
    status = item.get("status")
    status_text = (
        f" status={_sanitize_terminal_text(status)}" if status is not None else ""
    )

    if item_type in {"agent_message", "message"}:
        text = _mapping_text(item, "text", "message", "content") or ""
        return _block("agent:", text)

    if item_type == "reasoning":
        text = _mapping_text(item, "text", "summary", "content") or ""
        return _block("reasoning:", text)

    if item_type == "command_execution":
        if phase == "updated":
            return f"command updated{status_text} (live output retained in raw JSONL)"
        command = _mapping_text(item, "command") or "<command unavailable>"
        exit_code = item.get("exit_code")
        result = f"command {phase}{status_text}"
        if isinstance(exit_code, int) and not isinstance(exit_code, bool):
            result += f" exit={exit_code}"
        result += "\n" + _block("$", command)
        output = _mapping_text(item, "aggregated_output", "output")
        if output:
            result += "\n" + _block("output:", output)
        return result

    if item_type == "file_change":
        changes = _format_file_changes(item)
        return _block(f"file change {phase}{status_text}:", changes)

    if item_type in {"mcp_tool_call", "tool_call"}:
        server = item.get("server") or item.get("server_name")
        tool = item.get("tool") or item.get("tool_name") or item.get("name")
        identity = "/".join(
            _sanitize_terminal_text(part)
            for part in (server, tool)
            if part is not None
        ) or "unknown tool"
        result = f"tool {phase}{status_text}: {identity}"
        arguments = _structured_text(item.get("arguments"))
        if arguments:
            result += "\n" + _block("arguments:", arguments)
        tool_output = None
        for key in ("result", "output", "error"):
            tool_output = _structured_text(item.get(key))
            if tool_output:
                break
        if tool_output:
            result += "\n" + _block("result:", tool_output)
        return result

    if item_type == "collab_tool_call":
        tool = item.get("tool") or item.get("name") or "unknown collaboration tool"
        result = (
            f"collaboration {phase}{status_text}: "
            f"{_sanitize_terminal_text(tool)}"
        )
        targets = None
        for key in (
            "receiver_thread_ids",
            "agent_ids",
            "agent_id",
            "target",
        ):
            targets = _structured_text(item.get(key))
            if targets:
                break
        if targets:
            result += "\n" + _block("targets:", targets)
        prompt = _mapping_text(item, "prompt", "message")
        if prompt:
            result += "\n" + _block("prompt:", prompt)
        collab_output = None
        for key in ("result", "output", "error"):
            collab_output = _structured_text(item.get(key))
            if collab_output:
                break
        if collab_output:
            result += "\n" + _block("result:", collab_output)
        return result

    if item_type == "web_search":
        query = _mapping_text(item, "query") or "<query unavailable>"
        return _block(f"web search {phase}:", query)

    if item_type == "todo_list":
        entries = item.get("items")
        if isinstance(entries, list):
            text = "\n".join(
                _sanitize_terminal_text(entry.get("text") or entry.get("step") or entry)
                if isinstance(entry, Mapping)
                else _sanitize_terminal_text(entry)
                for entry in entries[:30]
            )
            return _block(f"todo {phase}:", text)

    if item_type == "error":
        detail = _mapping_text(item, "message", "text", "error") or "unknown error"
        return _block(f"item error {phase}:", detail)

    return (
        f"codex item {phase}: {_sanitize_terminal_text(item_type)}"
        " (pretty renderer has no formatter; see raw JSONL)"
    )


def _format_codex_event(event: Mapping[str, object]) -> str:
    event_type = event.get("type")
    if not isinstance(event_type, str) or not event_type:
        return "codex stdout malformed: missing string type; see raw JSONL"

    if event_type == "thread.started":
        thread_id = event.get("thread_id") or event.get("threadId")
        suffix = f": {_sanitize_terminal_text(thread_id)}" if thread_id else ""
        return f"codex thread started{suffix}"
    if event_type == "turn.started":
        return "codex turn started"
    if event_type == "turn.completed":
        usage = _format_usage(event.get("usage"))
        return "codex turn completed" + (f" ({usage})" if usage else "")
    if event_type == "turn.failed":
        detail = _mapping_text(event.get("error"), "message")
        detail = detail or _mapping_text(event, "message", "error") or "unknown error"
        return _block("codex turn failed:", detail)
    if event_type == "error":
        detail = _mapping_text(event, "message", "error") or _mapping_text(
            event.get("error"), "message"
        ) or "unknown error"
        return _block("codex error:", detail)
    if event_type.startswith("item."):
        item = event.get("item")
        if not isinstance(item, Mapping):
            return f"codex {event_type} malformed: missing item; see raw JSONL"
        return _format_item(event_type, item)
    return (
        f"codex event: {_sanitize_terminal_text(event_type)}"
        " (pretty renderer has no formatter; see raw JSONL)"
    )


def _format_component_event(event: Mapping[str, object]) -> str:
    component = _sanitize_terminal_text(event.get("component") or "loop")
    role = event.get("role")
    identity = component + (
        f"/{_sanitize_terminal_text(role)}" if role is not None else ""
    )
    result = _sanitize_terminal_text(event.get("result") or "event")
    heading = f"[{identity}] {result}"

    event_value = event.get("event")
    if isinstance(event_value, Mapping):
        reason = event_value.get("reason")
        event_id = event_value.get("event_id")
    else:
        reason = event.get("reason")
        event_id = event.get("event_id")
    details: list[str] = []
    if reason:
        details.append(f"reason={_sanitize_terminal_text(reason)}")
    if event_id:
        details.append(f"event={_sanitize_terminal_text(event_id)}")
    for key in ("health", "exit_code", "target", "session", "repository"):
        value = event.get(key)
        if value is not None:
            details.append(f"{key}={_sanitize_terminal_text(value)}")
    if details:
        heading += " " + " ".join(details)
    for key in ("current", "next", "detail", "attention"):
        value = event.get(key)
        if value:
            heading += "\n" + _block(f"{key}:", value)
    if result == "output-ready":
        for key in ("raw_stdout", "raw_stderr", "raw_component"):
            value = event.get(key)
            if value:
                heading += "\n" + _block(f"{key}:", value)
    return heading


def _write_text(stream: object, value: str) -> None:
    try:
        stream.write(value)  # type: ignore[attr-defined]
    except TypeError:
        stream.write(value.encode("utf-8"))  # type: ignore[attr-defined]
    stream.flush()  # type: ignore[attr-defined]


def _write_bytes(stream: object, value: bytes) -> None:
    if isinstance(stream, (io.BufferedIOBase, io.RawIOBase, io.BytesIO)):
        stream.write(value)
        stream.flush()
        return
    buffer = getattr(stream, "buffer", None)
    if buffer is not None:
        stream.flush()  # type: ignore[attr-defined]
        buffer.write(value)
        buffer.flush()
        return
    try:
        stream.write(value)  # type: ignore[attr-defined]
    except TypeError:
        decoded = value.decode("utf-8", errors="replace")
        stream.write(decoded)  # type: ignore[attr-defined]
    stream.flush()  # type: ignore[attr-defined]


def _write_raw_bytes(stream: BinaryIO, value: bytes) -> None:
    """Write every raw byte or raise before the renderer projects the record."""

    offset = 0
    while offset < len(value):
        written = stream.write(value[offset:])
        if not isinstance(written, int) or written <= 0:
            raise OSError("raw trace write made no progress")
        offset += written
    stream.flush()


def _default_raw_log_dir() -> Path:
    runtime_root = Path(os.environ.get("XDG_RUNTIME_DIR", tempfile.gettempdir()))
    candidate = runtime_root / f"emmet-qt-book-loop-output-{os.getuid()}"
    if candidate.resolve(strict=False).is_relative_to(_REPOSITORY_ROOT):
        candidate = Path(tempfile.gettempdir()) / (
            f"emmet-qt-book-loop-output-{os.getuid()}"
        )
    return candidate


def _prepare_raw_log_dir(
    value: Path | str | None,
    forbidden_roots: Sequence[Path | str] = (),
) -> Path:
    requested = (
        Path(value).expanduser() if value is not None else _default_raw_log_dir()
    )
    if requested.exists() and requested.is_symlink():
        raise ValueError(f"raw log directory must not be a symlink: {requested}")
    resolved = requested.resolve(strict=False)
    protected = [_REPOSITORY_ROOT]
    protected.extend(
        Path(root).expanduser().resolve(strict=False) for root in forbidden_roots
    )
    for root in protected:
        if resolved == root or resolved.is_relative_to(root):
            raise ValueError(
                f"raw log directory must be outside repository paths: {resolved}"
            )
    resolved.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = resolved.stat()
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError(f"raw log path is not a directory: {resolved}")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise PermissionError(
            f"raw log directory is not owned by this user: {resolved}"
        )
    os.chmod(resolved, 0o700)
    return resolved


def _open_private_file(path: Path) -> BinaryIO:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        return os.fdopen(descriptor, "wb", buffering=0)
    except BaseException:
        os.close(descriptor)
        raise


class LoopOutput:
    """Render loop output while retaining exact raw streams in pretty mode."""

    def __init__(
        self,
        output_format: str,
        component: str,
        raw_log_dir: Path | str | None = None,
        stdout: TextIO | BinaryIO | None = None,
        stderr: TextIO | BinaryIO | None = None,
        forbidden_roots: Sequence[Path | str] = (),
        event_observer: Callable[[Mapping[str, object]], None] | None = None,
    ) -> None:
        self.stdout = stdout if stdout is not None else sys.stdout
        self.stderr = stderr if stderr is not None else sys.stderr
        self.output_format = resolve_output_format(output_format, self.stdout)
        self.component = str(component)
        self._lock = threading.RLock()
        self._stdout_buffer = bytearray()
        self._stderr_buffer = bytearray()
        self._observer_buffer = bytearray()
        self._observer_overflow = 0
        self._event_observer = event_observer
        self._observer_errors: list[Exception] = []
        self._stdout_overflow = 0
        self._stderr_overflow = 0
        self._closed = False
        self._degraded = False
        self._fallback_buffers_migrated = False
        self._degrade_lock = threading.Lock()
        self._fallback_stdout = bytearray()
        self._fallback_stderr = bytearray()
        self._display_queue: queue.Queue[object] | None = None
        self._display_thread: threading.Thread | None = None
        self._display_stop = threading.Event()
        self._display_errors: list[Exception] = []
        self._display_failed_streams: set[int] = set()
        self._display_dropped = 0
        self._display_drops_reported = 0
        self._raw_stdout: BinaryIO | None = None
        self._raw_stderr: BinaryIO | None = None
        self._raw_component: BinaryIO | None = None
        self._raw_stdout_path: Path | None = None
        self._raw_stderr_path: Path | None = None
        self._raw_component_path: Path | None = None

        if self.pretty:
            directory = _prepare_raw_log_dir(raw_log_dir, forbidden_roots)
            slug = _COMPONENT_PATTERN.sub("-", self.component).strip("-.") or "loop"
            slug = slug[:64]
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
            generation = f"{slug}-{timestamp}-{os.getpid()}-{secrets.token_hex(4)}"
            stdout_path = directory / f"{generation}.stdout.jsonl"
            stderr_path = directory / f"{generation}.stderr.log"
            component_path = directory / f"{generation}.component.jsonl"
            raw_stdout: BinaryIO | None = None
            raw_stderr: BinaryIO | None = None
            raw_component: BinaryIO | None = None
            try:
                raw_stdout = _open_private_file(stdout_path)
                raw_stderr = _open_private_file(stderr_path)
                raw_component = _open_private_file(component_path)
                self._display_queue = queue.Queue(maxsize=MAX_DISPLAY_QUEUE)
                self._display_thread = threading.Thread(
                    target=self._display_worker,
                    name=f"codex-loop-display-{slug}",
                    daemon=True,
                )
                self._display_thread.start()
            except BaseException:
                for stream in (raw_stdout, raw_stderr, raw_component):
                    if stream is not None:
                        try:
                            stream.close()
                        except OSError:
                            pass
                for path in (stdout_path, stderr_path, component_path):
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
                raise
            self._raw_stdout_path = stdout_path
            self._raw_stderr_path = stderr_path
            self._raw_component_path = component_path
            self._raw_stdout = raw_stdout
            self._raw_stderr = raw_stderr
            self._raw_component = raw_component

    @property
    def pretty(self) -> bool:
        return self.output_format == "pretty"

    @property
    def degraded(self) -> bool:
        return self._degraded

    @property
    def raw_stdout_path(self) -> Path | None:
        return self._raw_stdout_path

    @property
    def raw_stderr_path(self) -> Path | None:
        return self._raw_stderr_path

    @property
    def raw_component_path(self) -> Path | None:
        return self._raw_component_path

    @property
    def display_errors(self) -> tuple[Exception, ...]:
        return tuple(self._display_errors)

    @property
    def observer_errors(self) -> tuple[Exception, ...]:
        return tuple(self._observer_errors)

    @property
    def display_dropped(self) -> int:
        return self._display_dropped

    def __enter__(self) -> "LoopOutput":
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()

    def _require_open(self) -> None:
        if self._closed:
            raise ValueError("loop output is closed")

    def _display(self, value: str, *, error: bool = False) -> None:
        stream = self.stderr if error else self.stdout
        text = _sanitize_terminal_text(value)
        if not text.endswith("\n"):
            text += "\n"
        display_queue = self._display_queue
        if display_queue is None:
            return
        try:
            display_queue.put_nowait((stream, text))
        except queue.Full:
            self._display_dropped += 1

    def _display_worker(self) -> None:
        display_queue = self._display_queue
        assert display_queue is not None
        while True:
            if self._display_stop.is_set() and display_queue.empty():
                self._report_display_drops()
                return
            try:
                item = display_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                if item is _DISPLAY_STOP:
                    self._report_display_drops()
                    return
                stream, text = item  # type: ignore[misc]
                self._write_display(stream, text)
            finally:
                display_queue.task_done()
            self._report_display_drops()

    def _write_display(self, stream: object, text: str) -> None:
        stream_key = id(stream)
        if stream_key in self._display_failed_streams:
            return
        try:
            descriptor = stream.fileno()  # type: ignore[attr-defined]
        except (AttributeError, io.UnsupportedOperation, OSError, ValueError):
            try:
                _write_text(stream, text)
            except Exception as error:
                self._record_display_error(stream_key, error)
            return
        try:
            encoding = getattr(stream, "encoding", None) or "utf-8"
            payload = text.encode(encoding, errors="replace")
            deadline = time.monotonic() + DISPLAY_WRITE_TIMEOUT_SECONDS
            offset = 0
            while offset < len(payload):
                if self._display_stop.is_set():
                    self._display_dropped += 1
                    return
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._display_dropped += 1
                    return
                _, writable, _ = select.select(
                    [], [descriptor], [], min(0.05, remaining)
                )
                if not writable:
                    continue
                end = min(offset + DISPLAY_WRITE_CHUNK_BYTES, len(payload))
                written = os.write(descriptor, payload[offset:end])
                if written <= 0:
                    raise OSError("operator display write made no progress")
                offset += written
        except Exception as error:
            self._record_display_error(stream_key, error)

    def _record_display_error(self, stream_key: int, error: Exception) -> None:
        self._display_failed_streams.add(stream_key)
        if len(self._display_errors) < 3:
            self._display_errors.append(error)

    def _report_display_drops(self) -> None:
        dropped = self._display_dropped
        if dropped <= self._display_drops_reported:
            return
        difference = dropped - self._display_drops_reported
        self._display_drops_reported = dropped
        self._write_display(
            self.stderr,
            (
                "codex-loop: terminal backpressure omitted "
                f"{difference} pretty record(s); see raw logs\n"
            ),
        )

    def _stop_display_worker(self) -> None:
        display_queue = self._display_queue
        thread = self._display_thread
        if display_queue is None or thread is None:
            return
        deadline = time.monotonic() + DISPLAY_CLOSE_TIMEOUT_SECONDS
        try:
            display_queue.put(
                _DISPLAY_STOP, timeout=DISPLAY_CLOSE_TIMEOUT_SECONDS
            )
        except queue.Full:
            pass
        thread.join(timeout=max(0.0, deadline - time.monotonic()))
        if thread.is_alive():
            self._request_display_stop()
            thread.join(timeout=DISPLAY_WRITE_TIMEOUT_SECONDS + 0.1)

    def _request_display_stop(self) -> None:
        display_queue = self._display_queue
        if display_queue is None:
            return
        self._display_stop.set()
        try:
            display_queue.put_nowait(_DISPLAY_STOP)
        except queue.Full:
            pass

    def degrade_to_jsonl(self, *, wait_for_raw_lock: bool = True) -> None:
        """Fail open to direct JSONL after a renderer stream becomes unusable."""

        with self._degrade_lock:
            if self._degraded and (
                self._fallback_buffers_migrated or not wait_for_raw_lock
            ):
                return
            acquired = self._lock.acquire(
                timeout=CLOSE_LOCK_TIMEOUT_SECONDS if wait_for_raw_lock else 0
            )
            if acquired:
                try:
                    self._fallback_stdout.extend(self._stdout_buffer)
                    self._fallback_stderr.extend(self._stderr_buffer)
                    self._stdout_buffer.clear()
                    self._stderr_buffer.clear()
                    self._stdout_overflow = 0
                    self._stderr_overflow = 0
                    self._fallback_buffers_migrated = True
                finally:
                    self._lock.release()
            self._degraded = True
            self.output_format = "jsonl"
            self._request_display_stop()

    def _write_degraded_bytes(
        self,
        stream_name: str,
        value: bytes,
        *,
        include_parser_prefix: bool = True,
    ) -> None:
        with self._degrade_lock:
            prefix = (
                self._fallback_stdout
                if stream_name == "stdout"
                else self._fallback_stderr
            )
            if include_parser_prefix:
                payload = bytes(prefix) + value
                prefix.clear()
            else:
                payload = value
        stream = self.stdout if stream_name == "stdout" else self.stderr
        try:
            descriptor = stream.fileno()  # type: ignore[attr-defined]
        except (AttributeError, io.UnsupportedOperation, OSError, ValueError):
            _write_bytes(stream, payload)
            return
        deadline = time.monotonic() + DEGRADED_WRITE_TIMEOUT_SECONDS
        offset = 0
        while offset < len(payload):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"degraded {stream_name} backpressure exceeded timeout"
                )
            _, writable, _ = select.select(
                [], [descriptor], [], min(0.01, remaining)
            )
            if not writable:
                continue
            end = min(offset + DISPLAY_WRITE_CHUNK_BYTES, len(payload))
            written = os.write(descriptor, payload[offset:end])
            if written <= 0:
                raise OSError("degraded operator write made no progress")
            offset += written

    def emit_diagnostic(self, value: object) -> None:
        """Best-effort bounded stderr diagnostic for renderer failures."""

        text = _sanitize_terminal_text(value)
        if not text.endswith("\n"):
            text += "\n"
        payload = text.encode("utf-8", errors="replace")
        if self._degraded:
            self._write_degraded_bytes(
                "stderr", payload, include_parser_prefix=False
            )
        elif self.pretty:
            self._display(text, error=True)
        else:
            _write_bytes(self.stderr, payload)

    def emit_component(self, event: Mapping[str, object]) -> None:
        """Emit one component record, retaining its original JSONL in pretty mode."""

        line = (
            json.dumps(dict(event), ensure_ascii=False, sort_keys=True) + "\n"
        ).encode("utf-8")
        if not self.pretty:
            self._require_open()
            if self._degraded:
                self._write_degraded_bytes(
                    "stdout", line, include_parser_prefix=False
                )
            else:
                _write_bytes(self.stdout, line)
            return
        degraded_while_waiting = False
        with self._lock:
            self._require_open()
            if not self.pretty:
                degraded_while_waiting = True
            else:
                assert self._raw_component is not None
                _write_raw_bytes(self._raw_component, line)
                self._display(_format_component_event(event))
                return
        if degraded_while_waiting:
            self._write_degraded_bytes(
                "stdout", line, include_parser_prefix=False
            )

    def feed_stdout(self, value: bytes) -> None:
        """Consume Codex stdout bytes without losing chunk boundaries or raw data."""

        if not isinstance(value, bytes):
            raise TypeError("stdout data must be bytes")
        if not value:
            return
        self._observe_stdout(value)
        if not self.pretty:
            self._require_open()
            if self._degraded:
                self._write_degraded_bytes("stdout", value)
            else:
                _write_bytes(self.stdout, value)
            return
        degraded_while_waiting = False
        with self._lock:
            self._require_open()
            if not self.pretty:
                degraded_while_waiting = True
            else:
                assert self._raw_stdout is not None
                _write_raw_bytes(self._raw_stdout, value)
                if self.pretty:
                    self._feed_lines("stdout", value)
                return
        if degraded_while_waiting:
            self._write_degraded_bytes("stdout", value)

    def _observe_stdout(self, value: bytes) -> None:
        """Parse a bounded copy of child JSONL for local completion telemetry."""

        if self._event_observer is None:
            return
        start = 0
        while start < len(value):
            end = value.find(b"\n", start)
            fragment = value[start:] if end < 0 else value[start:end]
            if self._observer_overflow:
                self._observer_overflow += len(fragment)
            elif len(self._observer_buffer) + len(fragment) <= MAX_LINE_BYTES:
                self._observer_buffer.extend(fragment)
            else:
                available = max(0, MAX_LINE_BYTES - len(self._observer_buffer))
                self._observer_buffer.extend(fragment[:available])
                self._observer_overflow = (
                    len(self._observer_buffer) + len(fragment) - available
                )
            if end < 0:
                break
            self._complete_observer_line()
            start = end + 1

    def _complete_observer_line(self) -> None:
        data = bytes(self._observer_buffer)
        overflow = self._observer_overflow
        self._observer_buffer.clear()
        self._observer_overflow = 0
        if overflow or not data or self._event_observer is None:
            return
        try:
            value = json.loads(data.removesuffix(b"\r").decode("utf-8"))
            if isinstance(value, Mapping):
                self._event_observer(value)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        except Exception as error:
            if len(self._observer_errors) < 3:
                self._observer_errors.append(error)

    def feed_stderr(self, value: bytes) -> None:
        """Consume Codex stderr bytes, preserving them separately in pretty mode."""

        if not isinstance(value, bytes):
            raise TypeError("stderr data must be bytes")
        if not value:
            return
        if not self.pretty:
            self._require_open()
            if self._degraded:
                self._write_degraded_bytes("stderr", value)
            else:
                _write_bytes(self.stderr, value)
            return
        degraded_while_waiting = False
        with self._lock:
            self._require_open()
            if not self.pretty:
                degraded_while_waiting = True
            else:
                assert self._raw_stderr is not None
                _write_raw_bytes(self._raw_stderr, value)
                if self.pretty:
                    self._feed_lines("stderr", value)
                return
        if degraded_while_waiting:
            self._write_degraded_bytes("stderr", value)

    def _feed_lines(self, stream_name: str, value: bytes) -> None:
        buffer = (
            self._stdout_buffer if stream_name == "stdout" else self._stderr_buffer
        )
        overflow_name = (
            "_stdout_overflow" if stream_name == "stdout" else "_stderr_overflow"
        )
        start = 0
        while start < len(value):
            end = value.find(b"\n", start)
            fragment = value[start:] if end < 0 else value[start:end]
            overflow = getattr(self, overflow_name)
            if overflow:
                setattr(self, overflow_name, overflow + len(fragment))
            elif len(buffer) + len(fragment) <= MAX_LINE_BYTES:
                buffer.extend(fragment)
            else:
                available = max(0, MAX_LINE_BYTES - len(buffer))
                buffer.extend(fragment[:available])
                setattr(self, overflow_name, len(buffer) + len(fragment) - available)
            if end < 0:
                break
            self._complete_line(stream_name, final=False)
            start = end + 1

    def _complete_line(self, stream_name: str, *, final: bool) -> None:
        if stream_name == "stdout":
            buffer = self._stdout_buffer
            overflow = self._stdout_overflow
            self._stdout_overflow = 0
        else:
            buffer = self._stderr_buffer
            overflow = self._stderr_overflow
            self._stderr_overflow = 0
        data = bytes(buffer)
        buffer.clear()
        if overflow:
            length = overflow
            self._display(
                f"codex {stream_name} line exceeded {MAX_LINE_BYTES} bytes "
                f"({length} bytes observed); see raw log",
                error=True,
            )
            return
        if stream_name == "stderr":
            if not data and not final:
                return
            text = data.decode("utf-8", errors="backslashreplace")
            suffix = " (missing final newline)" if final else ""
            self._display(_block(f"codex stderr{suffix}:", text), error=True)
            return
        if not data and not final:
            return
        try:
            text = data.removesuffix(b"\r").decode("utf-8", errors="strict")
            event = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            preview = data.decode("utf-8", errors="backslashreplace")
            suffix = " partial" if final else ""
            self._display(
                f"codex stdout{suffix} malformed ({type(error).__name__}): "
                f"{_bounded_preview(preview)}; see raw JSONL",
                error=True,
            )
            return
        if not isinstance(event, Mapping):
            self._display(
                "codex stdout malformed (JSON value is not an object): "
                f"{_bounded_preview(text)}; see raw JSONL",
                error=True,
            )
            return
        self._display(_format_codex_event(event))
        if final:
            self._display(
                "codex stdout record was missing its final newline; see raw JSONL",
                error=True,
            )

    def finish(self) -> None:
        """Finish the current child stream while keeping this process sink reusable."""

        acquired = self._lock.acquire(timeout=CLOSE_LOCK_TIMEOUT_SECONDS)
        if not acquired:
            raise TimeoutError("renderer raw stream did not release its lock")
        try:
            if self._closed:
                return
            if self._observer_buffer or self._observer_overflow:
                self._complete_observer_line()
            if self.pretty:
                if self._stdout_buffer or self._stdout_overflow:
                    self._complete_line("stdout", final=True)
                if self._stderr_buffer or self._stderr_overflow:
                    self._complete_line("stderr", final=True)
            if self._raw_stdout is not None:
                self._raw_stdout.flush()
            if self._raw_stderr is not None:
                self._raw_stderr.flush()
            if self._raw_component is not None:
                self._raw_component.flush()
        finally:
            self._lock.release()

    def close(self) -> None:
        """Finish rendering and close only files owned by this output layer."""

        acquired = self._lock.acquire(timeout=CLOSE_LOCK_TIMEOUT_SECONDS)
        if not acquired:
            self._closed = True
            self._stop_display_worker()
            raise TimeoutError("renderer raw stream did not release its lock")
        try:
            if self._closed:
                return
            errors: list[Exception] = []
            try:
                self.finish()
            except Exception as error:
                errors.append(error)
            for stream in (
                self._raw_stdout,
                self._raw_stderr,
                self._raw_component,
            ):
                if stream is None:
                    continue
                try:
                    stream.close()
                except Exception as error:
                    errors.append(error)
            self._stop_display_worker()
            self._closed = True
            if errors:
                raise errors[0]
        finally:
            self._lock.release()
