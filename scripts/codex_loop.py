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
import shlex
import shutil
import signal
import subprocess
import sys
from typing import Iterator, Sequence


ROLES = ("dispatcher", "coder", "reviewer")
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
    "docs/authoring-guide.md",
    "docs/curriculum.md",
    "docs/superpowers/specs/2026-07-13-agent-loop-skills-design.md",
    "scripts/codex-loop",
    "scripts/codex_loop.py",
)


def default_workdir(role: str, repository_root: Path = REPOSITORY_ROOT) -> Path:
    """Return the role's documented checkout without creating it."""

    if role == "dispatcher":
        return repository_root
    return repository_root.parent / f"{repository_root.name}-{role}"


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


def _validate_control_inputs(workdir: Path) -> None:
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
    unsafe = sorted({line for line in (changed + "\n" + untracked).splitlines() if line})
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


def build_command(
    role: str,
    workdir: Path,
    codex_bin: str,
    profile: str | None = None,
) -> list[str]:
    """Build the non-interactive, lower-risk Codex invocation."""

    prompt = (
        f"Use $emmet-loop-{role} to execute exactly one idempotent {role} "
        "iteration for this repository, then stop. Do not sleep, poll, schedule "
        "another run, or start a second iteration. If no safe action is available, "
        "report no-op and exit."
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
    command.append(prompt)
    return command


def default_lock_dir(common_dir: Path) -> Path:
    runtime_root = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
    digest = hashlib.sha256(str(common_dir).encode("utf-8")).hexdigest()[:16]
    return runtime_root / f"emmet-qt-book-codex-loop-{digest}"


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
) -> int:
    """Run Codex in its own process group while the child also holds the lock."""

    child = subprocess.Popen(
        command,
        cwd=workdir,
        pass_fds=(lock_descriptor,),
        start_new_session=True,
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
            os.killpg(child.pid, signum)
        except ProcessLookupError:
            pass

    previous_handlers = {
        signum: signal.signal(signum, forward_signal)
        for signum in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        try:
            return_code = child.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            print(
                json.dumps(
                    {
                        "result": "timeout",
                        "timeout_seconds": timeout_seconds,
                    }
                ),
                file=sys.stderr,
            )
            forward_signal(signal.SIGTERM, None)
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                forward_signal(signal.SIGKILL, None)
                child.wait()
            return EX_TIMEOUT
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
    try:
        with role_lock(lock_dir, options.role) as lock_descriptor:
            return run_child(
                command,
                workdir,
                lock_descriptor,
                options.timeout_seconds,
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
    except OSError as error:
        print(f"codex-loop: 無法啟動或管理 Codex：{error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
