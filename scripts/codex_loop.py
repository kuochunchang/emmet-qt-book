#!/usr/bin/env python3
"""Wake exactly one Codex loop role for one bounded iteration."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import errno
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import tomllib
from typing import Callable, Iterator, Sequence

try:
    from .codex_loop_output import LoopOutput, resolve_output_format
except ImportError:  # Direct execution through scripts/codex-loop.
    from codex_loop_output import LoopOutput, resolve_output_format


ROLES = ("dispatcher", "coder", "reviewer", "gate-auditor")
PROFILE_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
ROLE_CODEX_DEFAULTS: dict[str, dict[str, str]] = {
    "dispatcher": {
        "model": "gpt-5.6-sol",
        "model_reasoning_effort": "high",
        "model_verbosity": "low",
    },
    "coder": {
        "model": "gpt-5.6-sol",
        "model_reasoning_effort": "high",
        "model_verbosity": "low",
    },
    "reviewer": {
        "model": "gpt-5.6-sol",
        "model_reasoning_effort": "xhigh",
        "model_verbosity": "low",
    },
    "gate-auditor": {
        "model": "gpt-5.6-sol",
        "model_reasoning_effort": "xhigh",
        "model_verbosity": "low",
    },
}
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
EX_TEMPFAIL = 75
EX_TIMEOUT = 124
TRUSTED_REF = "refs/remotes/origin/main"
CONTROL_PATHS = (
    ":(glob)**/AGENTS.md",
    ":(glob)**/AGENTS.override.md",
    ".agents",
    ".claude",
    ".codex",
    "CLAUDE.md",
    "docs/agent-loop.md",
    "docs/agent-loop-operations.md",
    "docs/authoring-guide.md",
    "docs/curriculum.md",
    "docs/superpowers/specs/2026-07-13-agent-loop-skills-design.md",
    "scripts/codex-loop",
    "scripts/codex_loop.py",
    "scripts/codex_loop_output.py",
    "scripts/codex_loop_runtime.py",
    "scripts/codex_loop_tmux.py",
)


def default_workdir(role: str, repository_root: Path = REPOSITORY_ROOT) -> Path:
    """Return the role's documented checkout without creating it."""

    if role == "dispatcher":
        return repository_root
    return repository_root.parent / f"{repository_root.name}-{role}"


def role_option_key(role: str) -> str:
    """Return the argparse attribute fragment for one public role name."""

    if role not in ROLES:
        raise ValueError(f"未知 role：{role}")
    return role.replace("-", "_")


def _git_output(workdir: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(workdir), *arguments],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ValueError(f"不是可用的 Git worktree：{workdir}（{detail}）")
    return completed.stdout.strip()


def _git_root_and_common_dir(workdir: Path) -> tuple[Path, Path]:
    root = Path(_git_output(workdir, "rev-parse", "--show-toplevel")).resolve()
    common_dir_value = _git_output(root, "rev-parse", "--git-common-dir")
    common_dir = Path(common_dir_value)
    if not common_dir.is_absolute():
        common_dir = root / common_dir
    return root, common_dir.resolve()


def refresh_trusted_main(repository_root: Path) -> None:
    """Refresh the only revision allowed to supply role control inputs."""

    completed = subprocess.run(
        ["git", "-C", str(repository_root), "fetch", "origin", "main", "--prune"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ValueError(f"無法更新受信任 origin/main：{detail}")


def control_input_changes(workdir: Path) -> list[str]:
    """Return control paths whose content differs from trusted main."""

    changed = _git_output(
        workdir,
        "diff",
        "--name-only",
        "--no-ext-diff",
        "--no-textconv",
        TRUSTED_REF,
        "--",
        *CONTROL_PATHS,
    )
    untracked = _git_output(
        workdir,
        "ls-files",
        "--others",
        "--",
        *CONTROL_PATHS,
    )
    return sorted(
        {line for line in (changed + "\n" + untracked).splitlines() if line}
    )


def _validate_control_inputs(workdir: Path) -> None:
    unsafe = control_input_changes(workdir)
    if unsafe:
        names = ", ".join(unsafe)
        raise ValueError(f"role control inputs 與 origin/main 不一致：{names}")


def validate_workdir(
    role: str,
    workdir: Path,
    repository_root: Path = REPOSITORY_ROOT,
) -> tuple[Path, Path]:
    """Validate a trusted runner checkout from this adapter's repository."""

    workdir = workdir.expanduser().resolve()
    repository_root = repository_root.expanduser().resolve()
    if not workdir.is_dir():
        raise ValueError(f"worktree 不存在：{workdir}")

    control_root, control_common_dir = _git_root_and_common_dir(repository_root)
    root, common_dir = _git_root_and_common_dir(workdir)
    if root != workdir:
        raise ValueError(f"--workdir 必須指向 worktree root：{root}")
    if common_dir != control_common_dir:
        raise ValueError(f"worktree 不屬於 adapter repository：{workdir}")

    control_origin = _git_output(control_root, "remote", "get-url", "origin")
    worktree_origin = _git_output(root, "remote", "get-url", "origin")
    if worktree_origin != control_origin:
        raise ValueError("worktree origin 與 adapter repository 不一致")

    skill = root / ".agents" / "skills" / f"emmet-loop-{role}" / "SKILL.md"
    if not skill.is_file():
        raise ValueError(f"找不到角色 skill：{skill}")

    _validate_control_inputs(root)
    return root, common_dir


def resolve_codex(executable: str) -> str:
    """Resolve an executable without invoking a shell."""

    if os.sep in executable:
        path = Path(executable).expanduser().resolve()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
        raise ValueError(f"Codex executable 不可執行：{path}")

    resolved = shutil.which(executable)
    if resolved is None:
        raise ValueError(f"PATH 中找不到 Codex executable：{executable}")
    return resolved


def codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".codex").resolve()


def validate_profile_file(profile: str) -> Path:
    """Require one named profile to remain present and parseable."""

    if PROFILE_PATTERN.fullmatch(profile) is None:
        raise ValueError(f"Codex profile 名稱無效：{profile}")
    path = codex_home() / f"{profile}.config.toml"
    if not path.is_file():
        raise ValueError(f"找不到 Codex profile：{profile}（{path}）")
    try:
        with path.open("rb") as stream:
            tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ValueError(
            f"無法解析 Codex profile：{profile}（{path}）：{error}"
        ) from error
    return path


def role_execution_config(role: str) -> dict[str, str]:
    """Return the repo-controlled default for a role without a named profile."""

    try:
        return dict(ROLE_CODEX_DEFAULTS[role])
    except KeyError as error:
        raise ValueError(f"未知 role：{role}") from error


def build_command(
    role: str,
    workdir: Path,
    codex_bin: str,
    profile: str | None = None,
) -> list[str]:
    """Build the non-interactive, lower-risk Codex invocation."""

    prompt = (
        f"Use $emmet-loop-{role} to execute exactly one idempotent {role} "
        "iteration for this repository, then stop. This wake came from the loop "
        "event manager. Do not sleep, poll, schedule another run, or start a second "
        "iteration. If no safe action is available, report no-op and exit."
    )
    command = [
        codex_bin,
        "exec",
        "--ephemeral",
        "--json",
        "--color",
        "never",
        "--sandbox",
        "workspace-write",
        "-c",
        'approval_policy="on-request"',
        "-c",
        'approvals_reviewer="auto_review"',
        "--cd",
        str(workdir),
    ]
    if profile:
        command.extend(["--profile", profile])
    else:
        execution = role_execution_config(role)
        command.extend(
            [
                "--model",
                execution["model"],
                "-c",
                (
                    'model_reasoning_effort="'
                    + execution["model_reasoning_effort"]
                    + '"'
                ),
                "-c",
                'model_verbosity="' + execution["model_verbosity"] + '"',
            ]
        )
    command.append(prompt)
    return command


def default_lock_dir(common_dir: Path) -> Path:
    runtime_root = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
    digest = hashlib.sha256(str(common_dir).encode("utf-8")).hexdigest()[:16]
    return runtime_root / f"emmet-qt-book-codex-loop-{digest}"


def repository_private_roots(workdir: Path, common_dir: Path) -> tuple[Path, ...]:
    """Return every linked worktree plus the shared Git directory."""

    roots = {common_dir.expanduser().resolve(), workdir.expanduser().resolve()}
    listing = _git_output(workdir, "worktree", "list", "--porcelain")
    for line in listing.splitlines():
        if line.startswith("worktree "):
            roots.add(Path(line.removeprefix("worktree ")).resolve())
    return tuple(sorted(roots, key=str))


def create_loop_output(
    output_format: str,
    *,
    component: str,
    raw_log_dir: Path,
    workdir: Path,
    common_dir: Path,
) -> LoopOutput:
    """Create the optional display layer, falling back to JSONL on failure."""

    resolved_format = resolve_output_format(output_format, sys.stdout)
    if resolved_format == "jsonl":
        return LoopOutput("jsonl", component=component)
    try:
        return LoopOutput(
            resolved_format,
            component=component,
            raw_log_dir=raw_log_dir,
            forbidden_roots=repository_private_roots(workdir, common_dir),
        )
    except (OSError, RuntimeError, ValueError) as error:
        try:
            print(
                "codex-loop: pretty output unavailable; reverting to JSONL: "
                f"{error}",
                file=sys.stderr,
                flush=True,
            )
        except (OSError, ValueError):
            pass
        return LoopOutput("jsonl", component=component)


@contextmanager
def role_lock(lock_dir: Path, role: str) -> Iterator[int]:
    """Hold a non-blocking per-role lock until the child exits."""

    lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(lock_dir, 0o700)
    path = lock_dir / f"{role}.lock"
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.chmod(path, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN):
                holder = (
                    os.pread(descriptor, 4096, 0)
                    .decode("utf-8", errors="replace")
                    .strip()
                )
                detail = f"；holder={holder}" if holder else ""
                raise BlockingIOError(f"{role} 已有一輪正在執行{detail}") from error
            raise
        os.ftruncate(descriptor, 0)
        metadata = json.dumps({"pid": os.getpid(), "role": role}) + "\n"
        os.write(descriptor, metadata.encode("utf-8"))
        yield descriptor
    finally:
        os.close(descriptor)


def run_child(
    command: Sequence[str],
    workdir: Path,
    lock_descriptor: int,
    timeout_seconds: float,
    on_signal: Callable[[], None] | None = None,
    output: LoopOutput | None = None,
) -> int:
    """Run Codex in its own process group while the child also holds the lock."""

    capture = output is not None and output.pretty
    child = subprocess.Popen(
        command,
        cwd=workdir,
        pass_fds=(lock_descriptor,),
        start_new_session=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    os.ftruncate(lock_descriptor, 0)
    os.pwrite(
        lock_descriptor,
        (
            json.dumps(
                {
                    "parent_pid": os.getpid(),
                    "child_pid": child.pid,
                    "timeout_seconds": timeout_seconds,
                }
            )
            + "\n"
        ).encode("utf-8"),
        0,
    )

    def forward_signal(signum: int, _frame: object) -> None:
        try:
            if on_signal is not None:
                on_signal()
        finally:
            try:
                os.killpg(child.pid, signum)
            except ProcessLookupError:
                pass

    previous_handlers = {
        signum: signal.signal(signum, forward_signal)
        for signum in (signal.SIGINT, signal.SIGTERM)
    }
    reader_threads: list[threading.Thread] = []
    reader_errors: list[BaseException] = []
    reader_stop = threading.Event()

    def pump(stream: object, feed: Callable[[bytes], None]) -> None:
        try:
            descriptor = stream.fileno()  # type: ignore[attr-defined]
            os.set_blocking(descriptor, False)
            while not reader_stop.is_set():
                try:
                    chunk = os.read(descriptor, 65536)
                except BlockingIOError:
                    reader_stop.wait(0.02)
                    continue
                if not chunk:
                    break
                try:
                    feed(chunk)
                except BaseException as error:  # Renderer must not stop draining.
                    if len(reader_errors) < 3:
                        reader_errors.append(error)
                    if output is not None:
                        output.degrade_to_jsonl()
                        try:
                            feed(chunk)
                        except BaseException as fallback_error:
                            if len(reader_errors) < 3:
                                reader_errors.append(fallback_error)
        except BaseException as error:
            if len(reader_errors) < 3:
                reader_errors.append(error)
        finally:
            try:
                stream.close()  # type: ignore[attr-defined]
            except (OSError, ValueError):
                pass

    def terminate_child() -> None:
        if child.poll() is not None:
            return
        forward_signal(signal.SIGTERM, None)
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            forward_signal(signal.SIGKILL, None)
            child.wait()

    try:
        try:
            if capture:
                assert child.stdout is not None
                assert child.stderr is not None
                for stream, feed, name in (
                    (child.stdout, output.feed_stdout, "stdout"),
                    (child.stderr, output.feed_stderr, "stderr"),
                ):
                    thread = threading.Thread(
                        target=pump,
                        args=(stream, feed),
                        name=f"codex-loop-{name}",
                        daemon=True,
                    )
                    thread.start()
                    reader_threads.append(thread)
            try:
                return_code = child.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                timeout_record = {
                    "component": "codex",
                    "result": "timeout",
                    "timeout_seconds": timeout_seconds,
                }
                terminate_child()
                if output is not None and output.pretty:
                    try:
                        output.emit_component(timeout_record)
                    except Exception as error:
                        if len(reader_errors) < 3:
                            reader_errors.append(error)
                else:
                    try:
                        timeout_message = json.dumps(
                            {
                                "result": "timeout",
                                "timeout_seconds": timeout_seconds,
                            }
                        )
                        if output is not None and output.degraded:
                            output.emit_diagnostic(timeout_message)
                        else:
                            print(timeout_message, file=sys.stderr)
                    except (OSError, TimeoutError, ValueError):
                        pass
                return EX_TIMEOUT
        except BaseException:
            terminate_child()
            raise
    finally:
        try:
            for thread in reader_threads:
                thread.join(timeout=1)
            if any(thread.is_alive() for thread in reader_threads):
                reader_stop.set()
                for thread in reader_threads:
                    thread.join(timeout=1)
            readers_stopped = not any(
                thread.is_alive() for thread in reader_threads
            )
            if not readers_stopped and len(reader_errors) < 3:
                reader_errors.append(
                    RuntimeError("pretty renderer drain thread did not stop")
                )
            if not readers_stopped and output is not None:
                output.degrade_to_jsonl(wait_for_raw_lock=False)
            if readers_stopped:
                for stream in (child.stdout, child.stderr):
                    if stream is not None:
                        stream.close()
            if capture and output is not None and readers_stopped:
                try:
                    output.finish()
                except Exception as error:
                    if len(reader_errors) < 3:
                        reader_errors.append(error)
                    output.degrade_to_jsonl()
            if reader_errors:
                diagnostic = (
                    "codex-loop: pretty renderer stream error: "
                    + "; ".join(str(error) for error in reader_errors[:3])
                )
                try:
                    if output is not None:
                        output.emit_diagnostic(diagnostic)
                    else:
                        print(diagnostic, file=sys.stderr, flush=True)
                except (OSError, TimeoutError, ValueError):
                    pass
        finally:
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)

    if return_code < 0:
        return 128 + abs(return_code)
    return return_code


def positive_timeout(value: str) -> float:
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0:
        raise argparse.ArgumentTypeError("timeout 必須是大於 0 的有限數值")
    return timeout


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="喚醒一個 Codex loop 角色，執行恰好一輪後結束。"
    )
    result.add_argument("role", choices=ROLES)
    result.add_argument(
        "--workdir",
        type=Path,
        help="角色的 Git worktree root；預設使用協定中的角色 worktree。",
    )
    result.add_argument("--codex-bin", default="codex")
    result.add_argument("--profile", help="傳給 codex exec 的設定 profile。")
    result.add_argument(
        "--lock-dir",
        type=Path,
        help="覆寫非重疊鎖目錄（主要供受控部署與測試使用）。",
    )
    result.add_argument(
        "--dry-run",
        action="store_true",
        help="驗證設定並列印命令，但不取得鎖或啟動 Codex。",
    )
    result.add_argument(
        "--print-command",
        action="store_true",
        help="以 shell-safe 格式列印命令；除非同時指定 --dry-run，仍會執行。",
    )
    result.add_argument(
        "--timeout-seconds",
        type=positive_timeout,
        default=7200.0,
        help="一輪最長秒數；逾時先 TERM、10 秒後仍未退出則 KILL（預設 7200）。",
    )
    result.add_argument(
        "--output-format",
        choices=("auto", "pretty", "jsonl"),
        default="auto",
        help=(
            "operator output；auto 在 TTY 使用 pretty，pipe／redirect 使用 JSONL。"
        ),
    )
    return result


def main(arguments: Sequence[str] | None = None) -> int:
    options = parser().parse_args(arguments)
    requested_workdir = options.workdir or default_workdir(options.role)
    try:
        refresh_trusted_main(REPOSITORY_ROOT)
        control_root, _ = _git_root_and_common_dir(REPOSITORY_ROOT)
        _validate_control_inputs(control_root)
        workdir, common_dir = validate_workdir(
            options.role, requested_workdir, REPOSITORY_ROOT
        )
        codex_bin = resolve_codex(options.codex_bin)
        if options.profile:
            validate_profile_file(options.profile)
    except ValueError as error:
        print(f"codex-loop: {error}", file=sys.stderr)
        return 2

    command = build_command(options.role, workdir, codex_bin, options.profile)
    if options.print_command or options.dry_run:
        print(shlex.join(command), flush=True)
    if options.dry_run:
        return 0

    lock_dir = (
        options.lock_dir.expanduser().resolve()
        if options.lock_dir
        else default_lock_dir(common_dir)
    )
    output = create_loop_output(
        options.output_format,
        component=options.role,
        raw_log_dir=lock_dir / "logs",
        workdir=workdir,
        common_dir=common_dir,
    )
    if output.pretty:
        try:
            output.emit_component(
                {
                    "component": "agent",
                    "role": options.role,
                    "result": "output-ready",
                    "raw_stdout": str(output.raw_stdout_path),
                    "raw_stderr": str(output.raw_stderr_path),
                    "raw_component": str(output.raw_component_path),
                }
            )
        except Exception as error:
            output.degrade_to_jsonl()
            try:
                output.emit_diagnostic(
                    f"codex-loop: raw component log unavailable: {error}"
                )
            except Exception:
                pass
    try:
        with role_lock(lock_dir, options.role) as lock_descriptor:
            return run_child(
                command,
                workdir,
                lock_descriptor,
                options.timeout_seconds,
                output=output,
            )
    except BlockingIOError as error:
        print(
            json.dumps(
                {"role": options.role, "result": "already-running", "detail": str(error)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return EX_TEMPFAIL
    except (OSError, RuntimeError) as error:
        print(f"codex-loop: 無法啟動或管理 Codex：{error}", file=sys.stderr)
        return 2
    finally:
        try:
            output.close()
        except Exception as error:
            try:
                output.emit_diagnostic(
                    f"codex-loop: 無法完整關閉 operator output：{error}"
                )
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
