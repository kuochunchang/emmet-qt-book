#!/usr/bin/env python3
"""Long-lived event manager and role agents for the Codex loop."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
from typing import Mapping, Sequence

try:
    from . import codex_loop as adapter
except ImportError:  # Direct execution through scripts/codex-loop.
    import codex_loop as adapter


PRIMARY_LABELS = (
    "loop:queued",
    "loop:coding",
    "loop:needs-review",
    "loop:changes-requested",
    "loop:approved",
)
OBJECT_LABELS = frozenset((*PRIMARY_LABELS, "loop:blocked"))
MAX_EVENT_BYTES = 64 * 1024
EVENT_QUERY = """
query EmmetLoopState($owner: String!, $name: String!, $search: String!) {
  repository(owner: $owner, name: $name) {
    meta: issue(number: 1) {
      labels(first: 20) { nodes { name } }
    }
  }
  loopObjects: search(query: $search, type: ISSUE, first: 100) {
    nodes {
      __typename
      ... on Issue {
        number
        updatedAt
        labels(first: 20) { nodes { name } }
      }
      ... on PullRequest {
        number
        updatedAt
        headRefOid
        baseRefName
        isDraft
        mergeable
        labels(first: 20) { nodes { name } }
      }
    }
  }
}
"""


def emit(**fields: object) -> None:
    """Write one operator-visible JSONL component record."""

    print(json.dumps(fields, ensure_ascii=False, sort_keys=True), flush=True)


def positive_number(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("數值必須是大於 0 的有限值")
    return number


def _prepare_role(
    role: str,
    requested_workdir: Path,
    codex_executable: str,
    profile: str | None,
) -> tuple[Path, Path, list[str]]:
    adapter.refresh_trusted_main(adapter.REPOSITORY_ROOT)
    control_root, _ = adapter._git_root_and_common_dir(adapter.REPOSITORY_ROOT)
    adapter._validate_control_inputs(control_root)
    workdir, common_dir = adapter.validate_workdir(
        role, requested_workdir, adapter.REPOSITORY_ROOT
    )
    codex_bin = adapter.resolve_codex(codex_executable)
    command = adapter.build_command(role, workdir, codex_bin, profile)
    return workdir, common_dir, command


def socket_path(runtime_dir: Path, role: str) -> Path:
    return runtime_dir / f"{role}.sock"


def _read_message(connection: socket.socket) -> dict[str, object]:
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = connection.recv(min(4096, MAX_EVENT_BYTES + 1 - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
        if size > MAX_EVENT_BYTES:
            raise ValueError("事件超過大小上限")
        if b"\n" in chunk:
            break
    raw = b"".join(chunks).split(b"\n", 1)[0]
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("事件必須是 JSON object")
    return value


def _send_message(connection: socket.socket, value: Mapping[str, object]) -> None:
    connection.sendall(
        (json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8")
    )


def _write_agent_state(
    lock_descriptor: int, role: str, state: str
) -> None:
    metadata = (
        json.dumps(
            {
                "pid": os.getpid(),
                "role": role,
                "component": "agent",
                "state": state,
            }
        )
        + "\n"
    ).encode("utf-8")
    os.ftruncate(lock_descriptor, 0)
    os.pwrite(lock_descriptor, metadata, 0)


def run_agent(options: argparse.Namespace) -> int:
    """Wait forever by default and run one Codex iteration per wake event."""

    requested_workdir = options.workdir or adapter.default_workdir(options.role)
    try:
        workdir, common_dir, command = _prepare_role(
            options.role, requested_workdir, options.codex_bin, options.profile
        )
    except ValueError as error:
        print(f"codex-loop: {error}", file=sys.stderr)
        return 2

    runtime_dir = (
        options.runtime_dir.expanduser().resolve()
        if options.runtime_dir
        else adapter.default_lock_dir(common_dir)
    )
    endpoint = socket_path(runtime_dir, options.role)
    if options.print_command or options.dry_run:
        print(shlex.join(command), flush=True)
        emit(
            component="agent",
            role=options.role,
            result="preflight-ok",
            socket=str(endpoint),
        )
    if options.dry_run:
        return 0

    stop_requested = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True

    previous_handlers = {
        signum: signal.signal(signum, stop)
        for signum in (signal.SIGINT, signal.SIGTERM)
    }
    processed = 0
    try:
        with adapter.role_lock(runtime_dir, options.role) as lock_descriptor:
            _write_agent_state(lock_descriptor, options.role, "waiting")
            runtime_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(runtime_dir, 0o700)
            endpoint.unlink(missing_ok=True)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(str(endpoint))
                os.chmod(endpoint, 0o600)
                server.listen(8)
                server.settimeout(1.0)
                emit(
                    component="agent",
                    role=options.role,
                    result="waiting",
                    socket=str(endpoint),
                )
                while not stop_requested:
                    try:
                        connection, _ = server.accept()
                    except socket.timeout:
                        continue
                    with connection:
                        connection.settimeout(5.0)
                        try:
                            event = _read_message(connection)
                            if event.get("role") != options.role:
                                raise ValueError("事件 role 與 agent 不符")
                            if event.get("action") not in ("wake", "paused"):
                                raise ValueError("未知的事件 action")
                            _send_message(
                                connection,
                                {
                                    "accepted": True,
                                    "role": options.role,
                                    "event_id": event.get("event_id"),
                                },
                            )
                        except (
                            ValueError,
                            json.JSONDecodeError,
                            UnicodeDecodeError,
                        ) as error:
                            _send_message(
                                connection, {"accepted": False, "error": str(error)}
                            )
                            emit(
                                component="agent",
                                role=options.role,
                                result="invalid-event",
                                detail=str(error),
                            )
                            continue

                    processed += 1
                    emit(
                        component="agent",
                        role=options.role,
                        result="event-received",
                        event=event,
                    )
                    if event["action"] == "paused":
                        emit(
                            component="agent",
                            role=options.role,
                            result="paused",
                            event_id=event.get("event_id"),
                        )
                    else:
                        try:
                            workdir, _, command = _prepare_role(
                                options.role,
                                requested_workdir,
                                options.codex_bin,
                                options.profile,
                            )
                            exit_code = adapter.run_child(
                                command,
                                workdir,
                                lock_descriptor,
                                options.timeout_seconds,
                                on_signal=lambda: stop(signal.SIGTERM, None),
                            )
                        except ValueError as error:
                            print(f"codex-loop: {error}", file=sys.stderr)
                            exit_code = 2
                        _write_agent_state(
                            lock_descriptor, options.role, "waiting"
                        )
                        emit(
                            component="agent",
                            role=options.role,
                            result="iteration-finished",
                            event_id=event.get("event_id"),
                            exit_code=exit_code,
                        )
                    if options.max_events and processed >= options.max_events:
                        break
    except BlockingIOError as error:
        emit(
            component="agent",
            role=options.role,
            result="already-running",
            detail=str(error),
        )
        return adapter.EX_TEMPFAIL
    except OSError as error:
        print(f"codex-loop: agent socket 錯誤：{error}", file=sys.stderr)
        return 2
    finally:
        endpoint.unlink(missing_ok=True)
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)

    emit(component="agent", role=options.role, result="stopped")
    return 0


def _label_names(node: Mapping[str, object] | None) -> list[str]:
    if not node:
        return []
    labels = node.get("labels")
    nodes = labels.get("nodes") if isinstance(labels, Mapping) else None
    if not isinstance(nodes, list):
        return []
    return sorted(
        item["name"]
        for item in nodes
        if isinstance(item, Mapping) and isinstance(item.get("name"), str)
    )


def normalize_snapshot(payload: Mapping[str, object]) -> dict[str, object]:
    """Reduce GraphQL output to the durable state needed for routing."""

    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise ValueError("GitHub polling 回傳缺少 data")
    repository = data.get("repository")
    search = data.get("loopObjects")
    if not isinstance(repository, Mapping) or not isinstance(search, Mapping):
        raise ValueError("GitHub polling 回傳格式錯誤")
    meta = repository.get("meta")
    paused = "loop:paused" in _label_names(
        meta if isinstance(meta, Mapping) else None
    )
    nodes = search.get("nodes")
    if not isinstance(nodes, list):
        raise ValueError("GitHub polling 回傳缺少 loop objects")

    issues: list[dict[str, object]] = []
    pull_requests: list[dict[str, object]] = []
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        labels = [name for name in _label_names(node) if name in OBJECT_LABELS]
        if not labels:
            continue
        kind = node.get("__typename")
        item: dict[str, object] = {
            "kind": "pull_request" if kind == "PullRequest" else "issue",
            "number": node.get("number"),
            "updated_at": node.get("updatedAt"),
            "labels": labels,
        }
        if kind == "PullRequest":
            item.update(
                {
                    "head_sha": node.get("headRefOid"),
                    "base": node.get("baseRefName"),
                    "draft": node.get("isDraft"),
                    "mergeable": node.get("mergeable"),
                }
            )
            pull_requests.append(item)
        else:
            issues.append(item)
    key = lambda item: int(item["number"])
    return {
        "paused": paused,
        "issues": sorted(issues, key=key),
        "pull_requests": sorted(pull_requests, key=key),
    }


def classify_snapshot(snapshot: Mapping[str, object]) -> list[dict[str, object]]:
    """Choose only the role currently responsible under the canonical protocol."""

    if snapshot.get("paused") is True:
        return [
            {"role": role, "action": "paused", "reason": "global-pause"}
            for role in adapter.ROLES
        ]

    issues = list(snapshot.get("issues", []))
    pull_requests = list(snapshot.get("pull_requests", []))
    objects = [*issues, *pull_requests]

    def primary(item: Mapping[str, object]) -> list[str]:
        return [
            label
            for label in item.get("labels", [])
            if label in PRIMARY_LABELS
        ]

    malformed = [item for item in objects if len(primary(item)) != 1]
    blocked = [
        item for item in objects if "loop:blocked" in item.get("labels", [])
    ]
    if malformed or blocked:
        return [
            {
                "role": "dispatcher",
                "action": "wake",
                "reason": "reconciliation-required",
                "objects": malformed or blocked,
            }
        ]

    if len(pull_requests) > 1 or len(issues) > 1:
        return [
            {
                "role": "dispatcher",
                "action": "wake",
                "reason": "wip-invariant-violation",
                "objects": objects,
            }
        ]

    if pull_requests:
        if len(issues) != 1 or primary(issues[0]) != ["loop:coding"]:
            return [
                {
                    "role": "dispatcher",
                    "action": "wake",
                    "reason": "pull-request-issue-reconciliation",
                    "objects": objects,
                }
            ]
        item = pull_requests[0]
        mapping = {
            "loop:approved": ("dispatcher", "approved-pull-request"),
            "loop:changes-requested": ("coder", "changes-requested"),
            "loop:needs-review": ("reviewer", "review-requested"),
        }
        state = primary(item)[0]
        if state not in mapping:
            return [
                {
                    "role": "dispatcher",
                    "action": "wake",
                    "reason": "invalid-pull-request-state",
                    "objects": objects,
                }
            ]
        role, reason = mapping[state]
        return [
            {
                "role": role,
                "action": "wake",
                "reason": reason,
                "objects": objects,
            }
        ]

    if issues:
        state = primary(issues[0])[0]
        if state in ("loop:queued", "loop:coding"):
            return [
                {
                    "role": "coder",
                    "action": "wake",
                    "reason": "coding-work-available",
                    "objects": issues,
                }
            ]
        return [
            {
                "role": "dispatcher",
                "action": "wake",
                "reason": "invalid-issue-state",
                "objects": issues,
            }
        ]

    return [
        {
            "role": "dispatcher",
            "action": "wake",
            "reason": "reconcile-or-dispatch",
            "objects": [],
        }
    ]


def resolve_executable(executable: str, name: str) -> str:
    if os.sep in executable:
        path = Path(executable).expanduser().resolve()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
        raise ValueError(f"{name} executable 不可執行：{path}")
    resolved = shutil.which(executable)
    if resolved is None:
        raise ValueError(f"PATH 中找不到 {name} executable：{executable}")
    return resolved


def resolve_repo(gh_bin: str, workdir: Path, explicit: str | None) -> str:
    completed = subprocess.run(
        [
            gh_bin,
            "repo",
            "view",
            "--json",
            "nameWithOwner",
            "--jq",
            ".nameWithOwner",
        ],
        cwd=workdir,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ValueError(f"無法判定 GitHub repository：{detail}")
    slug = completed.stdout.strip()
    parts = slug.split("/", 1)
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"GitHub repository 必須是 OWNER/NAME：{slug}")
    if explicit and explicit != slug:
        raise ValueError(
            f"--repo 與 trusted worktree repository 不一致：{explicit} != {slug}"
        )
    return slug


def poll_github(gh_bin: str, workdir: Path, repo: str) -> dict[str, object]:
    owner, name = repo.split("/", 1)
    labels = " OR ".join(
        f'label:\"{label}\"' for label in sorted(OBJECT_LABELS)
    )
    search = f"repo:{repo} is:open ({labels})"
    completed = subprocess.run(
        [
            gh_bin,
            "api",
            "graphql",
            "-f",
            f"query={EVENT_QUERY}",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "-F",
            f"search={search}",
        ],
        cwd=workdir,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ValueError(f"GitHub polling 失敗：{detail}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ValueError(f"GitHub polling 回傳無效 JSON：{error}") from error
    if not isinstance(payload, Mapping):
        raise ValueError("GitHub polling 回傳格式錯誤")
    return normalize_snapshot(payload)


def event_fingerprint(event: Mapping[str, object]) -> str:
    canonical = json.dumps(
        event, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_event(decision: Mapping[str, object], repo: str) -> dict[str, object]:
    fingerprint = event_fingerprint(decision)
    return {
        **decision,
        "event_id": f"{fingerprint[:16]}-{time.time_ns()}",
        "fingerprint": fingerprint,
        "repository": repo,
        "polled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def notify_agent(
    runtime_dir: Path,
    event: Mapping[str, object],
    timeout_seconds: float = 5.0,
) -> dict[str, object]:
    role = event.get("role")
    if role not in adapter.ROLES:
        raise ValueError(f"未知 role：{role}")
    lock_path = runtime_dir / f"{role}.lock"
    try:
        metadata = json.loads(lock_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        metadata = {}
    if isinstance(metadata, Mapping) and metadata.get("child_pid") is not None:
        raise BlockingIOError(f"{role} Codex iteration 仍在執行")
    payload = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
    if len(payload) > MAX_EVENT_BYTES:
        raise ValueError("事件超過大小上限")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout_seconds)
        client.connect(str(socket_path(runtime_dir, str(role))))
        client.sendall(payload)
        client.shutdown(socket.SHUT_WR)
        response = _read_message(client)
    if response.get("accepted") is not True:
        raise ValueError(f"agent 拒絕事件：{response}")
    return response


def busy_roles(runtime_dir: Path) -> list[str]:
    """Return roles whose lock metadata names a running Codex child."""

    result: list[str] = []
    for role in adapter.ROLES:
        try:
            metadata = json.loads(
                (runtime_dir / f"{role}.lock").read_text(encoding="utf-8")
            )
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            continue
        child_pid = metadata.get("child_pid") if isinstance(metadata, Mapping) else None
        if not isinstance(child_pid, int):
            continue
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            continue
        except PermissionError:
            pass
        result.append(role)
    return result


def select_poll_decisions(
    snapshot: Mapping[str, object],
    *,
    busy: Sequence[str],
    now: float,
    last_dispatcher_wake: float,
    dispatcher_heartbeat_seconds: float,
) -> list[dict[str, object]]:
    """Serialize role wakes and preserve dispatcher staleness oversight."""

    decisions = classify_snapshot(snapshot)
    if snapshot.get("paused") is True:
        return decisions
    if busy:
        return []
    if (
        decisions
        and decisions[0]["role"] != "dispatcher"
        and now - last_dispatcher_wake >= dispatcher_heartbeat_seconds
    ):
        return [
            {
                "role": "dispatcher",
                "action": "wake",
                "reason": "oversight-heartbeat",
                "objects": [
                    *snapshot.get("issues", []),
                    *snapshot.get("pull_requests", []),
                ],
            }
        ]
    return decisions


def pull_request_disappeared(
    previous: Mapping[str, object] | None,
    current: Mapping[str, object],
) -> bool:
    """Detect the transition that requires dispatcher post-merge cleanup."""

    if previous is None:
        return False
    return bool(previous.get("pull_requests")) and not bool(
        current.get("pull_requests")
    )


def _validate_manager_workdir(workdir: Path) -> tuple[Path, Path]:
    adapter.refresh_trusted_main(adapter.REPOSITORY_ROOT)
    control_root, control_common = adapter._git_root_and_common_dir(
        adapter.REPOSITORY_ROOT
    )
    adapter._validate_control_inputs(control_root)
    root, common_dir = adapter._git_root_and_common_dir(workdir)
    if root != workdir:
        raise ValueError(f"--workdir 必須指向 worktree root：{root}")
    if common_dir != control_common:
        raise ValueError(f"worktree 不屬於 adapter repository：{workdir}")
    if adapter._git_output(root, "remote", "get-url", "origin") != adapter._git_output(
        control_root, "remote", "get-url", "origin"
    ):
        raise ValueError("worktree origin 與 adapter repository 不一致")
    adapter._validate_control_inputs(root)
    return root, common_dir


def run_events(options: argparse.Namespace) -> int:
    """Poll GitHub and route state-derived events until stopped."""

    requested_workdir = (options.workdir or adapter.REPOSITORY_ROOT).resolve()
    try:
        workdir, common_dir = _validate_manager_workdir(requested_workdir)
        gh_bin = resolve_executable(options.gh_bin, "GitHub CLI")
        repo = resolve_repo(gh_bin, workdir, options.repo)
    except ValueError as error:
        print(f"codex-loop: {error}", file=sys.stderr)
        return 2

    runtime_dir = (
        options.runtime_dir.expanduser().resolve()
        if options.runtime_dir
        else adapter.default_lock_dir(common_dir)
    )
    manager_lock = adapter.role_lock(runtime_dir, "events")
    try:
        manager_lock.__enter__()
    except BlockingIOError as error:
        emit(
            component="events",
            result="already-running",
            detail=str(error),
        )
        return adapter.EX_TEMPFAIL
    delivered: dict[str, tuple[str, float]] = {}
    last_dispatcher_wake = time.monotonic()
    previous_snapshot: dict[str, object] | None = None
    dispatcher_cleanup_pending = False
    stop_requested = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True

    previous_handlers = {
        signum: signal.signal(signum, stop)
        for signum in (signal.SIGINT, signal.SIGTERM)
    }
    emit(
        component="events",
        result="polling",
        repository=repo,
        interval_seconds=options.interval_seconds,
        retry_seconds=options.retry_seconds,
        runtime_dir=str(runtime_dir),
    )
    try:
        while not stop_requested:
            try:
                snapshot = poll_github(gh_bin, workdir, repo)
                now = time.monotonic()
                busy = busy_roles(runtime_dir)
                if pull_request_disappeared(previous_snapshot, snapshot):
                    dispatcher_cleanup_pending = True
                previous_snapshot = snapshot
                if (
                    dispatcher_cleanup_pending
                    and not busy
                    and snapshot.get("paused") is not True
                ):
                    decisions = [
                        {
                            "role": "dispatcher",
                            "action": "wake",
                            "reason": "pull-request-disappeared",
                            "objects": [
                                *snapshot.get("issues", []),
                                *snapshot.get("pull_requests", []),
                            ],
                        }
                    ]
                else:
                    decisions = select_poll_decisions(
                        snapshot,
                        busy=busy,
                        now=now,
                        last_dispatcher_wake=last_dispatcher_wake,
                        dispatcher_heartbeat_seconds=options.dispatcher_heartbeat_seconds,
                    )
                for decision in decisions:
                    role = str(decision["role"])
                    fingerprint = event_fingerprint(decision)
                    previous = delivered.get(role)
                    if (
                        previous
                        and previous[0] == fingerprint
                        and now - previous[1] < options.retry_seconds
                    ):
                        continue
                    event = build_event(decision, repo)
                    if options.dry_run:
                        emit(component="events", result="would-notify", event=event)
                        delivered[role] = (fingerprint, now)
                        continue
                    try:
                        acknowledgement = notify_agent(runtime_dir, event)
                    except (OSError, ValueError, json.JSONDecodeError) as error:
                        emit(
                            component="events",
                            role=role,
                            result="delivery-failed",
                            event_id=event["event_id"],
                            detail=str(error),
                        )
                    else:
                        delivered[role] = (fingerprint, now)
                        if role == "dispatcher":
                            last_dispatcher_wake = now
                            if decision["reason"] == "pull-request-disappeared":
                                dispatcher_cleanup_pending = False
                        emit(
                            component="events",
                            role=role,
                            result="notified",
                            event_id=event["event_id"],
                            acknowledgement=acknowledgement,
                        )
                emit(
                    component="events",
                    result="poll-complete",
                    paused=snapshot["paused"],
                    busy_roles=busy,
                    decisions=[
                        {
                            key: value
                            for key, value in item.items()
                            if key != "objects"
                        }
                        for item in decisions
                    ],
                )
            except ValueError as error:
                print(f"codex-loop: {error}", file=sys.stderr)
                if options.once or options.dry_run:
                    return 2

            if options.once or options.dry_run:
                break
            deadline = time.monotonic() + options.interval_seconds
            while not stop_requested and time.monotonic() < deadline:
                time.sleep(min(1.0, deadline - time.monotonic()))
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        manager_lock.__exit__(None, None, None)
    emit(component="events", result="stopped")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="以 GitHub polling 事件驅動常駐 Codex loop agents。"
    )
    components = result.add_subparsers(dest="component", required=True)

    agent = components.add_parser("agent", help="等待事件並啟動 Codex role。")
    agent.add_argument("role", choices=adapter.ROLES)
    agent.add_argument("--workdir", type=Path)
    agent.add_argument("--codex-bin", default="codex")
    agent.add_argument("--profile")
    agent.add_argument("--runtime-dir", "--lock-dir", dest="runtime_dir", type=Path)
    agent.add_argument("--dry-run", action="store_true")
    agent.add_argument("--print-command", action="store_true")
    agent.add_argument(
        "--timeout-seconds", type=positive_number, default=7200.0
    )
    agent.add_argument(
        "--max-events",
        type=int,
        default=0,
        help="處理指定事件數後退出；0 表示無限等待（主要供測試）。",
    )

    events = components.add_parser("events", help="poll GitHub 並通知負責角色。")
    events.add_argument("--workdir", type=Path)
    events.add_argument("--gh-bin", default="gh")
    events.add_argument("--repo")
    events.add_argument("--runtime-dir", type=Path)
    events.add_argument(
        "--interval-seconds", type=positive_number, default=60.0
    )
    events.add_argument(
        "--retry-seconds", type=positive_number, default=1800.0
    )
    events.add_argument(
        "--dispatcher-heartbeat-seconds",
        type=positive_number,
        default=1800.0,
        help="在途狀態持續時喚醒 dispatcher 做 oversight（預設 1800）。",
    )
    events.add_argument("--once", action="store_true")
    events.add_argument("--dry-run", action="store_true")
    return result


def main(arguments: Sequence[str] | None = None) -> int:
    options = parser().parse_args(arguments)
    if options.component == "agent":
        if options.max_events < 0:
            parser().error("--max-events 不得小於 0")
        return run_agent(options)
    return run_events(options)


if __name__ == "__main__":
    raise SystemExit(main())
