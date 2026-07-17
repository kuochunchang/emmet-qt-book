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
CONTROL_WORKTREE_SUFFIX = "-loop-control"
SESSION_MARKER = "@emmet_loop_common_dir"
SESSION_PROFILES = "@emmet_loop_codex_profiles"
SESSION_CODEX_CONFIGURATION = "@emmet_loop_codex_configuration"
COMPONENTS = ("events", *adapter.ROLES)
LEGACY_ROLES = frozenset(("dispatcher", "coder", "reviewer"))
SESSION_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
PROFILE_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class LauncherError(ValueError):
    """A fail-closed launcher error with an operator-facing message."""


class ControlReloadRequired(LauncherError):
    """The trusted main moved after this launcher process was loaded."""


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


def validate_profile_name(value: str) -> str:
    if not PROFILE_PATTERN.fullmatch(value):
        raise argparse.ArgumentTypeError(
            "profile 名稱只能包含英數字、底線與連字號"
        )
    return value


def role_profile_attribute(role: str) -> str:
    """Return the argparse attribute for one possibly hyphenated role."""

    return f"{role.replace('-', '_')}_profile"


def is_legacy_role_set(roles: set[str]) -> bool:
    """Recognize the only pre-gate-auditor launcher generation."""

    return (
        roles == LEGACY_ROLES
        and set(adapter.ROLES) == LEGACY_ROLES | {"gate-auditor"}
    )


def resolve_role_profiles(
    profile: str | None,
    role_profiles: Mapping[str, str | None] | None = None,
) -> dict[str, str | None]:
    overrides = dict(role_profiles or {})
    unknown = set(overrides) - set(adapter.ROLES)
    if unknown:
        raise LauncherError(
            "未知的 Codex role profile：" + ", ".join(sorted(unknown))
        )
    return {
        role: profile if overrides.get(role) is None else overrides[role]
        for role in adapter.ROLES
    }


def discover_automatic_profile(role: str) -> str | None:
    """Prefer a role profile, then the shared loop profile, when present."""

    for profile in (f"loop-{role}", "loop"):
        if (codex_home() / f"{profile}.config.toml").is_file():
            return profile
    return None


def apply_automatic_role_profiles(options: argparse.Namespace) -> None:
    """Fill only profile slots that were not selected explicitly."""

    if options.profile is not None:
        return
    for role in adapter.ROLES:
        attribute = role_profile_attribute(role)
        if getattr(options, attribute) is None:
            setattr(options, attribute, discover_automatic_profile(role))


def common_role_profile(
    role_profiles: Mapping[str, str | None],
) -> str | None:
    values = set(role_profiles.values())
    if len(values) == 1:
        return next(iter(values))
    return None


def codex_role_configuration(
    role_profiles: Mapping[str, str | None],
    *,
    legacy_inherited: bool = False,
) -> dict[str, dict[str, object]]:
    """Describe the exact per-role config source used by one generation."""

    profiles = resolve_role_profiles(None, role_profiles)
    result: dict[str, dict[str, object]] = {}
    for role, profile in profiles.items():
        if profile is not None:
            result[role] = {"source": "profile", "profile": profile}
        elif legacy_inherited:
            result[role] = {"source": "inherited"}
        else:
            defaults = adapter.role_execution_config(role)
            result[role] = {
                "source": "repo-default",
                "model": defaults["model"],
                "reasoning_effort": defaults["model_reasoning_effort"],
                "verbosity": defaults["model_verbosity"],
            }
    return result


def codex_configuration_kind(
    role_configuration: Mapping[str, Mapping[str, object]],
) -> str:
    sources = {
        str(config.get("source")) for config in role_configuration.values()
    }
    if sources == {"repo-default"}:
        return "repo-defaults"
    if sources == {"profile"}:
        return "profiles"
    if sources == {"inherited"}:
        return "inherited"
    return "mixed"


def codex_home() -> Path:
    return adapter.codex_home()


def validate_profile_files(
    role_profiles: Mapping[str, str | None],
) -> None:
    names = sorted(
        {profile for profile in role_profiles.values() if profile is not None}
    )
    for profile in names:
        try:
            adapter.validate_profile_file(profile)
        except ValueError as error:
            raise LauncherError(str(error)) from error


def runner_workdirs(repository_root: Path = REPOSITORY_ROOT) -> dict[str, Path]:
    root = repository_root.expanduser().resolve()
    return {
        role: root.parent / f"{root.name}-{role}"
        for role in adapter.ROLES
    }


def control_worktree_path(repository_root: Path) -> Path:
    """Return the dedicated worktree that is allowed to load the launcher."""

    root = repository_root.expanduser().resolve()
    return root.parent / f"{root.name}{CONTROL_WORKTREE_SUFFIX}"


def resolve_repository_layout(
    explicit_root: Path | None = None,
    *,
    loaded_worktree: Path = REPOSITORY_ROOT,
) -> tuple[Path, Path]:
    """Resolve the canonical checkout independently of the invoking worktree."""

    _, loaded_common_dir = adapter._git_root_and_common_dir(
        loaded_worktree.expanduser().resolve()
    )
    repository_root = (
        explicit_root.expanduser().resolve()
        if explicit_root is not None
        else loaded_common_dir.parent.resolve()
    )
    root, common_dir = adapter._git_root_and_common_dir(repository_root)
    if root != repository_root:
        raise LauncherError(
            f"repository root 必須指向 canonical worktree root：{root}"
        )
    if common_dir != loaded_common_dir:
        raise LauncherError(
            "repository root 不屬於目前 launcher repository"
        )
    return root, common_dir


def prepare_control_worktree(
    repository_root: Path,
    control_worktree: Path | None = None,
) -> tuple[Path, Path, str]:
    """Create or fast-forward the clean detached launcher control worktree."""

    root, common_dir = adapter._git_root_and_common_dir(repository_root)
    control = (
        control_worktree.expanduser().resolve()
        if control_worktree is not None
        else control_worktree_path(root)
    )
    adapter.refresh_trusted_main(root)
    expected = adapter._git_output(root, "rev-parse", adapter.TRUSTED_REF)
    root_origin = adapter._git_output(root, "remote", "get-url", "origin")

    if not control.exists():
        run_command(
            [
                "git",
                "-C",
                str(root),
                "worktree",
                "add",
                "--detach",
                str(control),
                adapter.TRUSTED_REF,
            ]
        )

    control_root, control_common_dir = adapter._git_root_and_common_dir(control)
    if control_root != control or control_common_dir != common_dir:
        raise LauncherError(
            f"control worktree 不屬於本 repository：{control}"
        )
    if adapter._git_output(control, "remote", "get-url", "origin") != root_origin:
        raise LauncherError(f"control worktree origin 不一致：{control}")
    dirty = adapter._git_output(
        control, "status", "--porcelain", "--untracked-files=all"
    )
    if dirty:
        raise LauncherError(
            f"control worktree 不乾淨，拒絕更新：{control}\n{dirty}"
        )

    run_command(
        [
            "git",
            "-C",
            str(control),
            "switch",
            "--detach",
            adapter.TRUSTED_REF,
        ]
    )
    head = adapter._git_output(control, "rev-parse", "HEAD")
    branch = adapter._git_output(control, "rev-parse", "--abbrev-ref", "HEAD")
    dirty_after = adapter._git_output(
        control, "status", "--porcelain", "--untracked-files=all"
    )
    if head != expected or branch != "HEAD" or dirty_after:
        raise LauncherError(
            f"control worktree 未乾淨 detached 對齊 origin/main：{control}"
        )
    return control, common_dir, expected


def validate_loaded_control_worktree(
    repository_root: Path,
    control_worktree: Path,
) -> None:
    """Fail before lifecycle changes unless this process came from trusted main."""

    loaded_root, loaded_common_dir = adapter._git_root_and_common_dir(
        REPOSITORY_ROOT.resolve()
    )
    _, repository_common_dir = adapter._git_root_and_common_dir(
        repository_root
    )
    if (
        loaded_root != control_worktree
        or loaded_common_dir != repository_common_dir
    ):
        raise LauncherError(
            "start/restart 必須由 dedicated control worktree 載入"
        )
    adapter.refresh_trusted_main(repository_root)
    expected = adapter._git_output(
        repository_root, "rev-parse", adapter.TRUSTED_REF
    )
    dirty = adapter._git_output(
        loaded_root, "status", "--porcelain", "--untracked-files=all"
    )
    if dirty:
        raise LauncherError(
            f"control worktree 不乾淨，拒絕啟動：{loaded_root}\n{dirty}"
        )
    branch = adapter._git_output(
        loaded_root, "rev-parse", "--abbrev-ref", "HEAD"
    )
    if branch != "HEAD":
        raise LauncherError("launcher control worktree 必須是 detached HEAD")
    if adapter._git_output(loaded_root, "rev-parse", "HEAD") != expected:
        raise ControlReloadRequired(
            "origin/main 在 launcher 載入後前進；重新載入 control worktree"
        )
    adapter._validate_control_inputs(loaded_root)


def build_control_reexec_command(
    control_worktree: Path,
    repository_root: Path,
    arguments: Sequence[str],
) -> list[str]:
    adapter_path = control_worktree / "scripts" / "codex-loop"
    return [
        str(adapter_path),
        "tmux",
        *arguments,
        "--repository-root",
        str(repository_root),
        "--trusted-control",
    ]


def bootstrap_control_launcher(arguments: Sequence[str]) -> int:
    """Sync the dedicated control worktree and replace this bootstrap process."""

    repository_root, common_dir = resolve_repository_layout()
    control, prepared_common_dir, _ = prepare_control_worktree(repository_root)
    if prepared_common_dir != common_dir:
        raise LauncherError("control worktree Git common-dir 在 bootstrap 期間改變")
    adapter_path = control / "scripts" / "codex-loop"
    if not adapter_path.is_file() or not os.access(adapter_path, os.X_OK):
        raise LauncherError(f"control worktree launcher 不可執行：{adapter_path}")
    command = build_control_reexec_command(control, repository_root, arguments)
    os.execv(str(adapter_path), command)
    return 0


def build_component_commands(
    adapter_path: Path,
    runners: Mapping[str, Path],
    *,
    interval_seconds: float,
    retry_seconds: float,
    dispatcher_heartbeat_seconds: float,
    profile: str | None = None,
    role_profiles: Mapping[str, str | None] | None = None,
    tmux_bin: str = "tmux",
    session: str = DEFAULT_SESSION,
    repository_root: Path = REPOSITORY_ROOT,
    output_format: str = "pretty",
) -> dict[str, list[str]]:
    commands = {
        role: [
            str(adapter_path),
            "agent",
            role,
            "--workdir",
            str(runners[role]),
            "--tmux-title",
            "--tmux-bin",
            tmux_bin,
            "--output-format",
            output_format,
        ]
        for role in adapter.ROLES
    }
    commands["events"] = [
        str(adapter_path),
        "events",
        "--workdir",
        str(runners["dispatcher"]),
        "--tmux-title",
        "--tmux-bin",
        tmux_bin,
        "--tmux-session",
        session,
        "--repository-root",
        str(repository_root),
        "--output-format",
        output_format,
        "--rotation-output-format",
        output_format,
        "--interval-seconds",
        str(interval_seconds),
        "--retry-seconds",
        str(retry_seconds),
        "--dispatcher-heartbeat-seconds",
        str(dispatcher_heartbeat_seconds),
    ]
    resolved_profiles = resolve_role_profiles(profile, role_profiles)
    for role, selected_profile in resolved_profiles.items():
        if selected_profile:
            commands[role].extend(["--profile", selected_profile])
    shared_profile = common_role_profile(resolved_profiles)
    if shared_profile:
        commands["events"].extend(["--rotation-profile", shared_profile])
    else:
        for role, selected_profile in resolved_profiles.items():
            if selected_profile:
                commands["events"].extend(
                    [f"--rotation-{role}-profile", selected_profile]
                )
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
    *,
    validate_loaded_control: bool = True,
) -> tuple[Path, str]:
    root, common_dir = adapter._git_root_and_common_dir(repository_root)
    adapter.refresh_trusted_main(root)
    if validate_loaded_control:
        adapter._validate_control_inputs(REPOSITORY_ROOT)
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
    role_profiles: Mapping[str, str | None] | None = None,
) -> None:
    profiles = resolve_role_profiles(profile, role_profiles)
    for role in adapter.ROLES:
        command = [
            str(adapter_path),
            "agent",
            role,
            "--workdir",
            str(runners[role]),
        ]
        if profiles[role]:
            command.extend(["--profile", profiles[role]])
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


def session_role_profiles(
    tmux_bin: str,
    session: str,
) -> dict[str, str | None] | None:
    raw = tmux_command(
        tmux_bin,
        "show-options",
        "-qv",
        "-t",
        session,
        SESSION_PROFILES,
        check=False,
    ).stdout.strip()
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise LauncherError("tmux session 的 Codex profile metadata 無效") from error
    if not isinstance(value, dict):
        raise LauncherError("tmux session 的 Codex profile metadata 不完整")
    stored_roles = set(value)
    if stored_roles != set(adapter.ROLES) and not is_legacy_role_set(
        stored_roles
    ):
        raise LauncherError("tmux session 的 Codex profile metadata 不完整")
    result: dict[str, str | None] = {}
    for role in adapter.ROLES:
        selected = value.get(role)
        if selected is not None and not isinstance(selected, str):
            raise LauncherError("tmux session 的 Codex profile metadata 類型錯誤")
        result[role] = selected
    return result


def session_codex_configuration(
    tmux_bin: str,
    session: str,
) -> dict[str, dict[str, object]] | None:
    raw = tmux_command(
        tmux_bin,
        "show-options",
        "-qv",
        "-t",
        session,
        SESSION_CODEX_CONFIGURATION,
        check=False,
    ).stdout.strip()
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise LauncherError(
            "tmux session 的 Codex execution metadata 無效"
        ) from error
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise LauncherError("tmux session 的 Codex execution metadata 不完整")
    roles = value.get("roles")
    if not isinstance(roles, dict):
        raise LauncherError("tmux session 的 Codex execution metadata 不完整")
    stored_roles = set(roles)
    if stored_roles != set(adapter.ROLES) and not is_legacy_role_set(
        stored_roles
    ):
        raise LauncherError("tmux session 的 Codex execution metadata 不完整")
    result: dict[str, dict[str, object]] = {}
    for role in adapter.ROLES:
        config = roles.get(role, {"source": "inherited"})
        if not isinstance(config, dict) or config.get("source") not in {
            "repo-default",
            "profile",
            "inherited",
        }:
            raise LauncherError("tmux session 的 Codex execution metadata 類型錯誤")
        result[role] = dict(config)
    return result


def set_session_role_profiles(
    tmux_bin: str,
    session: str,
    role_profiles: Mapping[str, str | None],
) -> None:
    profiles = resolve_role_profiles(None, role_profiles)
    payload = json.dumps(profiles, ensure_ascii=False, sort_keys=True)
    tmux_command(
        tmux_bin,
        "set-option",
        "-t",
        session,
        SESSION_PROFILES,
        payload,
    )
    if session_role_profiles(tmux_bin, session) != profiles:
        raise LauncherError("建立 tmux session Codex profile metadata 失敗")
    configuration = codex_role_configuration(profiles)
    tmux_command(
        tmux_bin,
        "set-option",
        "-t",
        session,
        SESSION_CODEX_CONFIGURATION,
        json.dumps(
            {"schema": 1, "roles": configuration},
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
    if session_codex_configuration(tmux_bin, session) != configuration:
        raise LauncherError("建立 tmux session Codex execution metadata 失敗")


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
    gate_auditor = tmux_command(
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
        str(runners["gate-auditor"]),
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
        reviewer,
        "-c",
        str(runners["dispatcher"]),
    ).stdout.strip()
    panes = {
        "dispatcher": dispatcher,
        "coder": coder,
        "reviewer": reviewer,
        "gate-auditor": gate_auditor,
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
        " #{pane_title}#{?pane_dead, [已退出],} ",
    )
    initial_titles = {
        "dispatcher": "dispatcher (啟動中)",
        "coder": "coder (啟動中)",
        "reviewer": "reviewer (啟動中)",
        "gate-auditor": "gate-auditor (啟動中)",
        "events": "events (等待 agents)",
    }
    for role, pane in panes.items():
        tmux_command(
            tmux_bin,
            "select-pane",
            "-t",
            pane,
            "-T",
            initial_titles[role],
        )
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
    try:
        control_changes = adapter.control_input_changes(workdir)
    except ValueError:
        control_changes = None
    result.update(
        {
            "git": True,
            "head": head_sha,
            "clean": not bool(status.stdout.strip()),
            "trusted_main": trusted_sha,
            "matches_trusted_main": (
                trusted_sha is not None and head_sha == trusted_sha
            ),
            "control_inputs_match": (
                not control_changes if control_changes is not None else None
            ),
            "changed_control_paths": control_changes,
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
    role_profiles: Mapping[str, str | None] | None = None,
    *,
    control_worktree: Path | None = None,
) -> None:
    exists = session_exists(tmux_bin, session)
    marker = session_marker(tmux_bin, session) if exists else None
    profiles = resolve_role_profiles(profile, role_profiles)
    configuration = codex_role_configuration(profiles)
    profile_source = "arguments" if any(profiles.values()) else "repo-defaults"
    if exists and marker == str(common_dir):
        stored_profiles = session_role_profiles(tmux_bin, session)
        if stored_profiles is not None:
            profiles = stored_profiles
            profile_source = "session"
        stored_configuration = session_codex_configuration(tmux_bin, session)
        if stored_configuration is not None:
            configuration = stored_configuration
            if any(
                config.get("source") == "inherited"
                for config in configuration.values()
            ):
                profile_source = "legacy-session"
        else:
            configuration = codex_role_configuration(
                profiles, legacy_inherited=True
            )
            profile_source = "legacy-session"
    try:
        rotation = json.loads(
            runtime.rotation_state_path(runtime_dir).read_text(encoding="utf-8")
        )
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        rotation = None
    emit(
        component="tmux",
        result="status",
        session=session,
        session_exists=exists,
        session_owned=marker == str(common_dir) if exists else None,
        session_marker=marker,
        runtime_dir=str(runtime_dir),
        active_components=active_components(runtime_dir),
        control_worktree=(
            runner_git_state(control_worktree)
            if control_worktree is not None
            else None
        ),
        runners={
            role: runner_git_state(workdir)
            for role, workdir in runners.items()
        },
        codex_configuration=codex_configuration_kind(configuration),
        codex_profile=common_role_profile(profiles),
        codex_profiles=profiles,
        codex_role_configuration=configuration,
        codex_profile_source=profile_source,
        rotation=rotation,
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
    role_profiles: Mapping[str, str | None] | None = None,
    *,
    control_worktree: Path | None = None,
) -> None:
    exists = session_exists(tmux_bin, session)
    marker = session_marker(tmux_bin, session) if exists else None
    profiles = resolve_role_profiles(profile, role_profiles)
    configuration = codex_role_configuration(profiles)
    emit(
        component="tmux",
        result="dry-run",
        action=action,
        session=session,
        session_exists=exists,
        session_owned=marker == str(common_dir) if exists else None,
        runtime_dir=str(runtime_dir),
        control_worktree=(
            str(control_worktree) if control_worktree is not None else None
        ),
        control_bootstrap=action in ("start", "restart"),
        panes={
            "left-top": "dispatcher",
            "left-middle": "reviewer",
            "left-bottom": "events",
            "right-top": "coder",
            "right-bottom": "gate-auditor",
        },
        runners={role: str(path) for role, path in runners.items()},
        commands={
            role: shlex.join(command) for role, command in commands.items()
        },
        active_components=list(active_components(runtime_dir)),
        codex_configuration=codex_configuration_kind(configuration),
        codex_profile=common_role_profile(profiles),
        codex_profiles=profiles,
        codex_role_configuration=configuration,
        codex_profile_source=(
            "arguments" if any(profiles.values()) else "repo-defaults"
        ),
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


def update_rotation_state(
    path: Path,
    *,
    state: str,
    **fields: object,
) -> None:
    existing: dict[str, object] = {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    else:
        if isinstance(value, dict):
            existing = value
    existing.pop("updated_at", None)
    payload = {**existing, **fields, "state": state}
    runtime.write_rotation_state(
        path,
        **payload,
    )


def verify_rotation_parent(
    runtime_dir: Path,
    parent_pid: int,
) -> None:
    metadata = active_components(runtime_dir).get("events")
    if metadata is None:
        raise LauncherError("rotation parent 的 events lock 不存在")
    if (
        component_pid("events", metadata) != parent_pid
        or not process_matches_component(parent_pid, "events")
        or not process_holds_lock(parent_pid, runtime_dir / "events.lock")
    ):
        raise LauncherError("rotation parent command／lock identity 驗證失敗")


def build_rotated_start_command(
    adapter_path: Path,
    options: argparse.Namespace,
    repository_root: Path,
) -> list[str]:
    command = [
        str(adapter_path),
        "tmux",
        "start",
        "--repository-root",
        str(repository_root),
        "--trusted-control",
        "--session",
        options.session,
        "--tmux-bin",
        options.tmux_bin,
        "--no-attach",
        "--output-format",
        options.output_format,
        "--stop-timeout-seconds",
        str(options.stop_timeout_seconds),
        "--startup-timeout-seconds",
        str(options.startup_timeout_seconds),
        "--interval-seconds",
        str(options.interval_seconds),
        "--retry-seconds",
        str(options.retry_seconds),
        "--dispatcher-heartbeat-seconds",
        str(options.dispatcher_heartbeat_seconds),
    ]
    if options.profile:
        command.extend(["--profile", options.profile])
    for role in adapter.ROLES:
        selected_profile = getattr(options, role_profile_attribute(role))
        if selected_profile:
            command.extend([f"--{role}-profile", selected_profile])
    return command


def rotate_session(
    options: argparse.Namespace,
    repository_root: Path,
    runners: Mapping[str, Path],
    common_dir: Path,
    runtime_dir: Path,
    tmux_bin: str,
) -> int:
    """Replace an idle owned session from the newly fetched trusted controls."""

    if options.wait_pid is None or options.rotation_state is None:
        raise LauncherError("rotate 缺少 parent PID 或 rotation state path")
    state_path = options.rotation_state.expanduser().resolve()
    if not session_exists(tmux_bin, options.session):
        raise LauncherError("待換代的 tmux session 不存在")
    verify_owned_session(tmux_bin, options.session, common_dir)
    verify_rotation_parent(runtime_dir, options.wait_pid)
    update_rotation_state(
        state_path,
        state="waiting-for-manager",
        rotator_pid=os.getpid(),
    )
    wait_for_lock_release(
        runtime_dir / "events.lock",
        options.stop_timeout_seconds,
    )
    update_rotation_state(state_path, state="stopping-components")
    stop_existing_components(
        runtime_dir,
        allow_stop=True,
        timeout_seconds=options.stop_timeout_seconds,
    )
    if session_exists(tmux_bin, options.session):
        verify_owned_session(tmux_bin, options.session, common_dir)
        tmux_command(tmux_bin, "kill-session", "-t", options.session)
    update_rotation_state(state_path, state="syncing-runners")
    prepared_common_dir, main_sha = prepare_runners(
        repository_root,
        runners,
        validate_loaded_control=False,
    )
    if prepared_common_dir != common_dir:
        raise LauncherError("runner Git common-dir 在換代期間改變")
    control_worktree, control_common_dir, control_main_sha = (
        prepare_control_worktree(repository_root)
    )
    if control_common_dir != common_dir or control_main_sha != main_sha:
        raise LauncherError("control worktree 在換代期間未對齊 runners")
    adapter_path = control_worktree / "scripts" / "codex-loop"
    if not adapter_path.is_file() or not os.access(adapter_path, os.X_OK):
        raise LauncherError(f"換代後 control launcher 不可執行：{adapter_path}")
    update_rotation_state(
        state_path,
        state="starting-session",
        target_main=main_sha,
    )
    run_command(
        build_rotated_start_command(
            adapter_path,
            options,
            repository_root,
        )
    )
    update_rotation_state(
        state_path,
        state="completed",
        target_main=main_sha,
    )
    emit(
        component="tmux",
        result="rotation-completed",
        session=options.session,
        main_sha=main_sha,
        rotation_state=str(state_path),
    )
    return 0


def launch(options: argparse.Namespace) -> int:
    repository_root, common_dir = resolve_repository_layout(
        options.repository_root
    )
    control_worktree = control_worktree_path(repository_root)
    runners = runner_workdirs(repository_root)
    runtime_dir = adapter.default_lock_dir(common_dir)
    tmux_bin = resolve_executable(options.tmux_bin, "tmux")
    profiles = resolve_role_profiles(
        options.profile,
        {
            role: getattr(options, role_profile_attribute(role))
            for role in adapter.ROLES
        },
    )
    if options.action == "rotate":
        validate_profile_files(profiles)
        return rotate_session(
            options,
            repository_root,
            runners,
            common_dir,
            runtime_dir,
            tmux_bin,
        )
    if (
        options.action in ("start", "restart")
        and options.trusted_control
        and not options.dry_run
    ):
        validate_loaded_control_worktree(
            repository_root, control_worktree
        )
    adapter_path = runners["dispatcher"] / "scripts" / "codex-loop"
    commands = build_component_commands(
        adapter_path,
        runners,
        interval_seconds=options.interval_seconds,
        retry_seconds=options.retry_seconds,
        dispatcher_heartbeat_seconds=options.dispatcher_heartbeat_seconds,
        role_profiles=profiles,
        tmux_bin=tmux_bin,
        session=options.session,
        repository_root=repository_root,
        output_format=options.output_format,
    )

    if options.action == "status":
        status_report(
            tmux_bin,
            options.session,
            common_dir,
            runners,
            runtime_dir,
            options.profile,
            profiles,
            control_worktree=control_worktree,
        )
        return 0

    if options.action in ("start", "restart"):
        validate_profile_files(profiles)

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
            profiles,
            control_worktree=control_worktree,
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
        role_profiles=profiles,
        tmux_bin=tmux_bin,
        session=options.session,
        repository_root=repository_root,
        output_format=options.output_format,
    )
    run_preflight(adapter_path, runners, role_profiles=profiles)

    remove_stale_sockets(runtime_dir)
    try:
        panes = create_tmux_session(
            tmux_bin,
            options.session,
            common_dir,
            runners,
        )
        set_session_role_profiles(tmux_bin, options.session, profiles)
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
        codex_configuration=codex_configuration_kind(
            codex_role_configuration(profiles)
        ),
        codex_profile=common_role_profile(profiles),
        codex_profiles=profiles,
        codex_role_configuration=codex_role_configuration(profiles),
        codex_profile_source="session",
        control_worktree=str(control_worktree),
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
            "並以五 pane tmux 啟動。"
        )
    )
    result.add_argument(
        "action",
        nargs="?",
        choices=("start", "restart", "stop", "status", "rotate"),
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
        "--repository-root", type=Path, help=argparse.SUPPRESS
    )
    result.add_argument(
        "--trusted-control", action="store_true", help=argparse.SUPPRESS
    )
    result.add_argument(
        "--profile",
        type=validate_profile_name,
        help=(
            "四個 Codex 角色共同使用的預設 profile；"
            "role-specific profile 可覆寫，未指定則使用 repo 角色預設。"
        ),
    )
    for role in adapter.ROLES:
        result.add_argument(
            f"--{role}-profile",
            dest=role_profile_attribute(role),
            type=validate_profile_name,
            help=(
                f"{role} 專用的 Codex profile；覆寫共用 --profile。"
            ),
        )
    result.add_argument(
        "--no-attach",
        action="store_true",
        help="啟動後留在目前 shell，不 attach tmux。",
    )
    result.add_argument(
        "--output-format",
        choices=("auto", "pretty", "jsonl"),
        default="pretty",
        help=(
            "五個 tmux pane 的顯示格式；pretty 保留 repo 外 raw log，"
            "jsonl 顯示原始事件（預設 pretty）。"
        ),
    )
    result.add_argument(
        "--dry-run",
        action="store_true",
        help="只列出 lifecycle、runner、pane、command 與 active state。",
    )
    result.add_argument("--tmux-bin", default="tmux")
    result.add_argument(
        "--wait-pid", type=int, help=argparse.SUPPRESS
    )
    result.add_argument(
        "--rotation-state", type=Path, help=argparse.SUPPRESS
    )
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
    raw_arguments = list(arguments) if arguments is not None else sys.argv[1:]
    options = parser().parse_args(raw_arguments)
    apply_automatic_role_profiles(options)
    try:
        if (
            options.action in ("start", "restart")
            and not options.dry_run
            and not options.trusted_control
        ):
            return bootstrap_control_launcher(raw_arguments)
        return launch(options)
    except ControlReloadRequired as error:
        if (
            options.action in ("start", "restart")
            and options.trusted_control
            and not options.dry_run
        ):
            try:
                return bootstrap_control_launcher(raw_arguments)
            except (LauncherError, ValueError, OSError) as reload_error:
                error = reload_error
        print(f"codex-loop tmux: {error}", file=sys.stderr)
        return 2
    except (LauncherError, ValueError, OSError) as error:
        if options.action == "rotate" and options.rotation_state is not None:
            try:
                update_rotation_state(
                    options.rotation_state.expanduser().resolve(),
                    state="failed",
                    detail=str(error),
                )
            except OSError:
                pass
        print(f"codex-loop tmux: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("codex-loop tmux: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
