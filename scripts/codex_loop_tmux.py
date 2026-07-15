#!/usr/bin/env python3
"""Safely update trusted runners and launch the Codex loop in tmux."""

from __future__ import annotations

import argparse
import errno
import fcntl
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import time
from typing import Mapping, Sequence

try:
    from . import codex_loop as adapter
    from . import codex_loop_runtime as runtime
except ImportError:  # Direct execution through scripts/codex-loop.
    import codex_loop as adapter
    import codex_loop_runtime as runtime


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SESSION = "emmet-qt-book-loop"
SESSION_MARKER = "@emmet_loop_common_dir"
COMPONENTS = ("events", *adapter.ROLES)
SESSION_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


class LauncherError(ValueError):
    """A fail-closed launcher error with an operator-facing message."""


def emit(**fields: object) -> None:
    print(json.dumps(fields, ensure_ascii=False, sort_keys=True), flush=True)


def positive_number(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("數值必須大於 0")
    return number


def validate_session_name(value: str) -> str:
    if not SESSION_PATTERN.fullmatch(value):
        raise argparse.ArgumentTypeError(
            "session 名稱只能包含英數字、點、底線與連字號"
        )
    return value


def runner_workdirs(repository_root: Path = REPOSITORY_ROOT) -> dict[str, Path]:
    root = repository_root.expanduser().resolve()
    return {
        role: root.parent / f"{root.name}-{role}"
        for role in adapter.ROLES
    }


def build_component_commands(
    adapter_path: Path,
    runners: Mapping[str, Path],
    *,
    interval_seconds: float,
    retry_seconds: float,
    dispatcher_heartbeat_seconds: float,
    profile: str | None = None,
) -> dict[str, list[str]]:
    commands = {
        "dispatcher": [
            str(adapter_path),
            "agent",
            "dispatcher",
            "--workdir",
            str(runners["dispatcher"]),
        ],
        "coder": [
            str(adapter_path),
            "agent",
            "coder",
            "--workdir",
            str(runners["coder"]),
        ],
        "reviewer": [
            str(adapter_path),
            "agent",
            "reviewer",
            "--workdir",
            str(runners["reviewer"]),
        ],
        "events": [
            str(adapter_path),
            "events",
            "--workdir",
            str(runners["dispatcher"]),
            "--interval-seconds",
            str(interval_seconds),
            "--retry-seconds",
            str(retry_seconds),
            "--dispatcher-heartbeat-seconds",
            str(dispatcher_heartbeat_seconds),
        ],
    }
    if profile:
        for role in adapter.ROLES:
            commands[role].extend(["--profile", profile])
    return commands


def resolve_executable(executable: str, name: str) -> str:
    if os.sep in executable:
        path = Path(executable).expanduser().resolve()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
        raise LauncherError(f"{name} executable 不可執行：{path}")
    resolved = shutil.which(executable)
    if resolved is None:
        raise LauncherError(f"PATH 中找不到 {name} executable：{executable}")
    return resolved


def run_command(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    quiet: bool = False,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(command),
        cwd=cwd,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if not quiet:
        if completed.stdout:
            print(completed.stdout, end="", flush=True)
        if completed.stderr:
            print(completed.stderr, end="", file=sys.stderr, flush=True)
    if check and completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        rendered = shlex.join(command)
        raise LauncherError(
            f"命令失敗（exit={completed.returncode}）：{rendered}"
            + (f"；{detail}" if detail else "")
        )
    return completed


def lock_is_held(path: Path) -> bool:
    if not path.exists():
        return False
    descriptor = os.open(path, os.O_RDWR)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN):
                return True
            raise
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return False
    finally:
        os.close(descriptor)


def read_lock_metadata(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError) as error:
        raise LauncherError(
            f"無法讀取 active lock metadata：{path}（{error}）"
        ) from error
    if not isinstance(value, dict):
        raise LauncherError(f"active lock metadata 不是 JSON object：{path}")
    return value


def active_components(runtime_dir: Path) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for component in COMPONENTS:
        path = runtime_dir / f"{component}.lock"
        if lock_is_held(path):
            result[component] = read_lock_metadata(path)
    return result


def component_pid(component: str, metadata: Mapping[str, object]) -> int:
    value = metadata.get("pid")
    if component != "events":
        value = metadata.get("parent_pid", value)
    if not isinstance(value, int) or value <= 0:
        raise LauncherError(
            f"{component} lock 缺少可驗證的 parent PID：{metadata}"
        )
    return value


def process_matches_component(pid: int, component: str) -> bool:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        completed = run_command(
            ["ps", "-p", str(pid), "-o", "args="],
            check=False,
            quiet=True,
        )
        if completed.returncode != 0:
            return False
        arguments = completed.stdout.split()
    else:
        arguments = [
            item.decode("utf-8", errors="replace")
            for item in raw.split(b"\0")
            if item
        ]

    if not any(Path(item).name == "codex_loop_runtime.py" for item in arguments):
        return False
    expected = ["events"] if component == "events" else ["agent", component]
    for index in range(len(arguments) - len(expected) + 1):
        if arguments[index : index + len(expected)] == expected:
            return True
    return False


def process_holds_lock(pid: int, lock_path: Path) -> bool:
    target = lock_path.resolve()
    try:
        descriptors = Path(f"/proc/{pid}/fd").iterdir()
        for descriptor in descriptors:
            try:
                if descriptor.resolve(strict=True) == target:
                    return True
            except OSError:
                continue
    except OSError:
        return False
    return False


def wait_for_lock_release(path: Path, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not lock_is_held(path):
            return
        time.sleep(0.1)
    raise LauncherError(f"component 未在 timeout 內釋放 lock：{path}")


def stop_existing_components(
    runtime_dir: Path,
    *,
    allow_stop: bool,
    timeout_seconds: float,
) -> None:
    active = active_components(runtime_dir)
    if active and not allow_stop:
        names = ", ".join(active)
        raise LauncherError(
            f"已有 loop components 執行中：{names}；"
            "請使用 `tmux restart` 安全重啟或 `tmux stop` 停止"
        )
    for component in COMPONENTS:
        metadata = active.get(component)
        if metadata is None:
            continue
        pid = component_pid(component, metadata)
        lock_path = runtime_dir / f"{component}.lock"
        if not process_matches_component(
            pid, component
        ) or not process_holds_lock(pid, lock_path):
            raise LauncherError(
                "拒絕終止無法驗證 command／lock identity 的 PID："
                f"component={component} pid={pid}"
            )
        os.kill(pid, signal.SIGTERM)
        emit(component="tmux", result="stopping", target=component, pid=pid)
        wait_for_lock_release(lock_path, timeout_seconds)
        emit(component="tmux", result="stopped", target=component, pid=pid)


def prepare_runners(
    repository_root: Path,
    runners: Mapping[str, Path],
) -> tuple[Path, str]:
    root, common_dir = adapter._git_root_and_common_dir(repository_root)
    adapter.refresh_trusted_main(root)
    adapter._validate_control_inputs(root)
    expected = adapter._git_output(root, "rev-parse", adapter.TRUSTED_REF)
    root_origin = adapter._git_output(root, "remote", "get-url", "origin")

    for role in adapter.ROLES:
        workdir = runners[role]
        if not workdir.exists():
            run_command(
                [
                    "git",
                    "-C",
                    str(root),
                    "worktree",
                    "add",
                    "--detach",
                    str(workdir),
                    adapter.TRUSTED_REF,
                ]
            )
        runner_root, runner_common = adapter._git_root_and_common_dir(workdir)
        if runner_root != workdir.resolve() or runner_common != common_dir:
            raise LauncherError(f"{role} runner 不屬於本 repository：{workdir}")
        if adapter._git_output(workdir, "remote", "get-url", "origin") != root_origin:
            raise LauncherError(f"{role} runner origin 不一致：{workdir}")
        dirty = adapter._git_output(
            workdir, "status", "--porcelain", "--untracked-files=all"
        )
        if dirty:
            raise LauncherError(
                f"{role} runner 不乾淨，拒絕更新：{workdir}\n{dirty}"
            )
        run_command(
            [
                "git",
                "-C",
                str(workdir),
                "switch",
                "--detach",
                adapter.TRUSTED_REF,
            ]
        )
        if adapter._git_output(workdir, "rev-parse", "HEAD") != expected:
            raise LauncherError(f"{role} runner 未對齊 origin/main：{workdir}")
        adapter.validate_workdir(role, workdir, root)

    return common_dir, expected


def run_preflight(
    adapter_path: Path,
    runners: Mapping[str, Path],
    profile: str | None = None,
) -> None:
    for role in adapter.ROLES:
        command = [
            str(adapter_path),
            "agent",
            role,
            "--workdir",
            str(runners[role]),
        ]
        if profile:
            command.extend(["--profile", profile])
        command.append("--dry-run")
        run_command(command)
    run_command(
        [
            str(adapter_path),
            "events",
            "--workdir",
            str(runners["dispatcher"]),
            "--once",
            "--dry-run",
        ]
    )


def tmux_command(
    tmux_bin: str,
    *arguments: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return run_command(
        [tmux_bin, *arguments],
        check=check,
        quiet=True,
    )


def session_exists(tmux_bin: str, session: str) -> bool:
    return (
        tmux_command(
            tmux_bin,
            "has-session",
            "-t",
            session,
            check=False,
        ).returncode
        == 0
    )


def session_marker(tmux_bin: str, session: str) -> str:
    return tmux_command(
        tmux_bin,
        "show-options",
        "-qv",
        "-t",
        session,
        SESSION_MARKER,
        check=False,
    ).stdout.strip()


def verify_owned_session(
    tmux_bin: str,
    session: str,
    common_dir: Path,
) -> None:
    if session_marker(tmux_bin, session) != str(common_dir):
        raise LauncherError(
            f"同名 tmux session 不是本 launcher 建立，拒絕取代：{session}"
        )


def create_tmux_session(
    tmux_bin: str,
    session: str,
    common_dir: Path,
    runners: Mapping[str, Path],
) -> dict[str, str]:
    dispatcher = tmux_command(
        tmux_bin,
        "new-session",
        "-d",
        "-P",
        "-F",
        "#{pane_id}",
        "-s",
        session,
        "-n",
        "loop",
        "-c",
        str(runners["dispatcher"]),
    ).stdout.strip()
    try:
        tmux_command(
            tmux_bin,
            "set-option",
            "-t",
            session,
            SESSION_MARKER,
            str(common_dir),
        )
        if session_marker(tmux_bin, session) != str(common_dir):
            raise LauncherError("建立 tmux session ownership marker 失敗")
    except (LauncherError, OSError):
        tmux_command(
            tmux_bin, "kill-session", "-t", session, check=False
        )
        raise
    coder = tmux_command(
        tmux_bin,
        "split-window",
        "-d",
        "-h",
        "-P",
        "-F",
        "#{pane_id}",
        "-t",
        dispatcher,
        "-c",
        str(runners["coder"]),
    ).stdout.strip()
    reviewer = tmux_command(
        tmux_bin,
        "split-window",
        "-d",
        "-v",
        "-P",
        "-F",
        "#{pane_id}",
        "-t",
        dispatcher,
        "-c",
        str(runners["reviewer"]),
    ).stdout.strip()
    events = tmux_command(
        tmux_bin,
        "split-window",
        "-d",
        "-v",
        "-P",
        "-F",
        "#{pane_id}",
        "-t",
        coder,
        "-c",
        str(runners["dispatcher"]),
    ).stdout.strip()
    panes = {
        "dispatcher": dispatcher,
        "coder": coder,
        "reviewer": reviewer,
        "events": events,
    }
    tmux_command(
        tmux_bin,
        "set-window-option",
        "-t",
        f"{session}:loop",
        "remain-on-exit",
        "on",
    )
    tmux_command(
        tmux_bin,
        "set-window-option",
        "-t",
        f"{session}:loop",
        "pane-border-status",
        "top",
    )
    tmux_command(
        tmux_bin,
        "set-window-option",
        "-t",
        f"{session}:loop",
        "pane-border-format",
        " #{pane_title} ",
    )
    for role, pane in panes.items():
        tmux_command(tmux_bin, "select-pane", "-t", pane, "-T", role)
    return panes


def send_pane_command(
    tmux_bin: str,
    pane: str,
    command: Sequence[str],
) -> None:
    rendered = shlex.join(["exec", *command])
    tmux_command(tmux_bin, "send-keys", "-t", pane, "-l", rendered)
    tmux_command(tmux_bin, "send-keys", "-t", pane, "Enter")


def pane_is_dead(tmux_bin: str, pane: str) -> bool:
    return (
        tmux_command(
            tmux_bin,
            "display-message",
            "-p",
            "-t",
            pane,
            "#{pane_dead}",
        ).stdout.strip()
        == "1"
    )


def capture_pane(tmux_bin: str, pane: str) -> str:
    return tmux_command(
        tmux_bin,
        "capture-pane",
        "-p",
        "-S",
        "-80",
        "-t",
        pane,
        check=False,
    ).stdout.strip()


def remove_stale_sockets(runtime_dir: Path) -> None:
    if active_components(runtime_dir):
        raise LauncherError("仍有 active components，拒絕移除 socket")
    for role in adapter.ROLES:
        runtime.socket_path(runtime_dir, role).unlink(missing_ok=True)


def wait_for_agent_sockets(
    tmux_bin: str,
    panes: Mapping[str, str],
    runtime_dir: Path,
    timeout_seconds: float,
) -> None:
    pending = set(adapter.ROLES)
    deadline = time.monotonic() + timeout_seconds
    while pending and time.monotonic() < deadline:
        for role in tuple(pending):
            endpoint = runtime.socket_path(runtime_dir, role)
            try:
                ready = stat.S_ISSOCK(endpoint.stat().st_mode)
            except FileNotFoundError:
                ready = False
            if ready:
                pending.remove(role)
                continue
            if pane_is_dead(tmux_bin, panes[role]):
                detail = capture_pane(tmux_bin, panes[role])
                raise LauncherError(
                    f"{role} agent 啟動失敗"
                    + (f"：\n{detail}" if detail else "")
                )
        if pending:
            time.sleep(0.1)
    if pending:
        raise LauncherError(
            "agents 未在 timeout 內建立 sockets：" + ", ".join(sorted(pending))
        )


def wait_for_events_lock(
    tmux_bin: str,
    pane: str,
    runtime_dir: Path,
    timeout_seconds: float,
) -> None:
    path = runtime_dir / "events.lock"
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if lock_is_held(path):
            return
        if pane_is_dead(tmux_bin, pane):
            detail = capture_pane(tmux_bin, pane)
            raise LauncherError(
                "event manager 啟動失敗"
                + (f"：\n{detail}" if detail else "")
            )
        time.sleep(0.1)
    raise LauncherError("event manager 未在 timeout 內取得 lock")


def runner_git_state(workdir: Path) -> dict[str, object]:
    result: dict[str, object] = {
        "path": str(workdir),
        "exists": workdir.exists(),
    }
    if not workdir.exists():
        return result
    head = run_command(
        ["git", "-C", str(workdir), "rev-parse", "HEAD"],
        check=False,
        quiet=True,
    )
    status = run_command(
        [
            "git",
            "-C",
            str(workdir),
            "status",
            "--porcelain",
            "--untracked-files=all",
        ],
        check=False,
        quiet=True,
    )
    trusted = run_command(
        [
            "git",
            "-C",
            str(workdir),
            "rev-parse",
            adapter.TRUSTED_REF,
        ],
        check=False,
        quiet=True,
    )
    if head.returncode != 0 or status.returncode != 0:
        result["git"] = False
        return result
    head_sha = head.stdout.strip()
    trusted_sha = trusted.stdout.strip() if trusted.returncode == 0 else None
    result.update(
        {
            "git": True,
            "head": head_sha,
            "clean": not bool(status.stdout.strip()),
            "trusted_main": trusted_sha,
            "matches_trusted_main": (
                trusted_sha is not None and head_sha == trusted_sha
            ),
        }
    )
    return result


def status_report(
    tmux_bin: str,
    session: str,
    common_dir: Path,
    runners: Mapping[str, Path],
    runtime_dir: Path,
    profile: str | None,
) -> None:
    exists = session_exists(tmux_bin, session)
    marker = session_marker(tmux_bin, session) if exists else None
    emit(
        component="tmux",
        result="status",
        session=session,
        session_exists=exists,
        session_owned=marker == str(common_dir) if exists else None,
        session_marker=marker,
        runtime_dir=str(runtime_dir),
        active_components=active_components(runtime_dir),
        runners={
            role: runner_git_state(workdir)
            for role, workdir in runners.items()
        },
        codex_configuration="inherited",
        codex_profile=profile,
    )


def dry_run_plan(
    action: str,
    session: str,
    tmux_bin: str,
    common_dir: Path,
    runners: Mapping[str, Path],
    runtime_dir: Path,
    commands: Mapping[str, Sequence[str]],
    profile: str | None,
) -> None:
    exists = session_exists(tmux_bin, session)
    marker = session_marker(tmux_bin, session) if exists else None
    emit(
        component="tmux",
        result="dry-run",
        action=action,
        session=session,
        session_exists=exists,
        session_owned=marker == str(common_dir) if exists else None,
        runtime_dir=str(runtime_dir),
        panes={
            "top-left": "dispatcher",
            "top-right": "coder",
            "bottom-left": "reviewer",
            "bottom-right": "events",
        },
        runners={role: str(path) for role, path in runners.items()},
        commands={
            role: shlex.join(command) for role, command in commands.items()
        },
        active_components=list(active_components(runtime_dir)),
        codex_configuration="inherited",
        codex_profile=profile,
    )


def cleanup_failed_start(
    tmux_bin: str,
    session: str,
    common_dir: Path,
    runtime_dir: Path,
    timeout_seconds: float,
) -> list[str]:
    errors: list[str] = []
    try:
        stop_existing_components(
            runtime_dir,
            allow_stop=True,
            timeout_seconds=timeout_seconds,
        )
    except (LauncherError, OSError) as error:
        errors.append(str(error))

    if session_exists(tmux_bin, session):
        if session_marker(tmux_bin, session) != str(common_dir):
            errors.append("啟動失敗後發現 session ownership marker 不一致")
        else:
            completed = tmux_command(
                tmux_bin,
                "kill-session",
                "-t",
                session,
                check=False,
            )
            if completed.returncode != 0:
                errors.append("啟動失敗後無法清除 tmux session")

    try:
        stop_existing_components(
            runtime_dir,
            allow_stop=True,
            timeout_seconds=timeout_seconds,
        )
    except (LauncherError, OSError) as error:
        errors.append(str(error))
    try:
        remove_stale_sockets(runtime_dir)
    except (LauncherError, OSError) as error:
        errors.append(str(error))
    return list(dict.fromkeys(errors))


def launch(options: argparse.Namespace) -> int:
    repository_root = REPOSITORY_ROOT.resolve()
    runners = runner_workdirs(repository_root)
    _, common_dir = adapter._git_root_and_common_dir(repository_root)
    runtime_dir = adapter.default_lock_dir(common_dir)
    tmux_bin = resolve_executable(options.tmux_bin, "tmux")
    adapter_path = runners["dispatcher"] / "scripts" / "codex-loop"
    commands = build_component_commands(
        adapter_path,
        runners,
        interval_seconds=options.interval_seconds,
        retry_seconds=options.retry_seconds,
        dispatcher_heartbeat_seconds=options.dispatcher_heartbeat_seconds,
        profile=options.profile,
    )

    if options.action == "status":
        status_report(
            tmux_bin,
            options.session,
            common_dir,
            runners,
            runtime_dir,
            options.profile,
        )
        return 0

    if options.dry_run:
        dry_run_plan(
            options.action,
            options.session,
            tmux_bin,
            common_dir,
            runners,
            runtime_dir,
            commands,
            options.profile,
        )
        return 0

    existing_session = session_exists(tmux_bin, options.session)
    if existing_session:
        verify_owned_session(tmux_bin, options.session, common_dir)

    if options.action == "stop":
        stop_existing_components(
            runtime_dir,
            allow_stop=True,
            timeout_seconds=options.stop_timeout_seconds,
        )
        if existing_session:
            tmux_command(
                tmux_bin, "kill-session", "-t", options.session
            )
        remove_stale_sockets(runtime_dir)
        emit(
            component="tmux",
            result="stopped",
            session=options.session,
        )
        return 0

    if existing_session and options.action == "start":
        raise LauncherError(
            f"tmux session 已存在：{options.session}；請使用 tmux restart"
        )

    stop_existing_components(
        runtime_dir,
        allow_stop=options.action == "restart",
        timeout_seconds=options.stop_timeout_seconds,
    )
    if existing_session:
        tmux_command(tmux_bin, "kill-session", "-t", options.session)

    prepared_common_dir, main_sha = prepare_runners(
        repository_root, runners
    )
    if prepared_common_dir != common_dir:
        raise LauncherError("runner Git common-dir 在更新期間改變")
    adapter_path = runners["dispatcher"] / "scripts" / "codex-loop"
    if not adapter_path.is_file() or not os.access(adapter_path, os.X_OK):
        raise LauncherError(f"trusted adapter 不可執行：{adapter_path}")
    commands = build_component_commands(
        adapter_path,
        runners,
        interval_seconds=options.interval_seconds,
        retry_seconds=options.retry_seconds,
        dispatcher_heartbeat_seconds=options.dispatcher_heartbeat_seconds,
        profile=options.profile,
    )
    run_preflight(adapter_path, runners, options.profile)

    remove_stale_sockets(runtime_dir)
    try:
        panes = create_tmux_session(
            tmux_bin,
            options.session,
            common_dir,
            runners,
        )
        for role in adapter.ROLES:
            send_pane_command(tmux_bin, panes[role], commands[role])
        wait_for_agent_sockets(
            tmux_bin,
            panes,
            runtime_dir,
            options.startup_timeout_seconds,
        )
        send_pane_command(
            tmux_bin, panes["events"], commands["events"]
        )
        wait_for_events_lock(
            tmux_bin,
            panes["events"],
            runtime_dir,
            options.startup_timeout_seconds,
        )
    except (LauncherError, OSError, KeyboardInterrupt) as error:
        cleanup_errors = cleanup_failed_start(
            tmux_bin,
            options.session,
            common_dir,
            runtime_dir,
            options.stop_timeout_seconds,
        )
        if isinstance(error, KeyboardInterrupt):
            if cleanup_errors:
                print(
                    "codex-loop tmux cleanup: "
                    + "；".join(cleanup_errors),
                    file=sys.stderr,
                )
            raise
        detail = (
            "；cleanup: " + "；".join(cleanup_errors)
            if cleanup_errors
            else ""
        )
        raise LauncherError(f"啟動失敗：{error}{detail}") from error

    emit(
        component="tmux",
        result="started",
        session=options.session,
        main_sha=main_sha,
        panes=panes,
        codex_configuration="inherited",
        codex_profile=options.profile,
    )

    if options.no_attach:
        print(
            f"tmux attach-session -t {shlex.quote(options.session)}",
            flush=True,
        )
        return 0
    if os.environ.get("TMUX"):
        os.execv(
            tmux_bin,
            [tmux_bin, "switch-client", "-t", options.session],
        )
    os.execv(
        tmux_bin,
        [tmux_bin, "attach-session", "-t", options.session],
    )
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "安全管理 agent loop，更新 trusted runners，"
            "並以 2×2 tmux 啟動。"
        )
    )
    result.add_argument(
        "action",
        nargs="?",
        choices=("start", "restart", "stop", "status"),
        default="start",
        help=(
            "start 防止重複啟動；restart 有序停止後更新；"
            "stop 清理 owned session；status 僅讀取狀態。"
        ),
    )
    result.add_argument(
        "--session",
        type=validate_session_name,
        default=DEFAULT_SESSION,
    )
    result.add_argument(
        "--profile",
        help=(
            "三個 Codex 角色共同使用的設定 profile；"
            "預設繼承 Codex 設定。"
        ),
    )
    result.add_argument(
        "--no-attach",
        action="store_true",
        help="啟動後留在目前 shell，不 attach tmux。",
    )
    result.add_argument(
        "--dry-run",
        action="store_true",
        help="只列出 lifecycle、runner、pane、command 與 active state。",
    )
    result.add_argument("--tmux-bin", default="tmux")
    result.add_argument(
        "--stop-timeout-seconds",
        type=positive_number,
        default=30.0,
    )
    result.add_argument(
        "--startup-timeout-seconds",
        type=positive_number,
        default=30.0,
    )
    result.add_argument(
        "--interval-seconds",
        type=positive_number,
        default=60.0,
    )
    result.add_argument(
        "--retry-seconds",
        type=positive_number,
        default=1800.0,
    )
    result.add_argument(
        "--dispatcher-heartbeat-seconds",
        type=positive_number,
        default=1800.0,
    )
    return result


def main(arguments: Sequence[str] | None = None) -> int:
    options = parser().parse_args(arguments)
    try:
        return launch(options)
    except (LauncherError, ValueError, OSError) as error:
        print(f"codex-loop tmux: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("codex-loop tmux: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
