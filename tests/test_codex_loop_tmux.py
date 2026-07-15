from __future__ import annotations

import contextlib
import fcntl
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts import codex_loop_tmux as launcher


class CodexLoopTmuxTests(unittest.TestCase):
    @staticmethod
    def completed(
        stdout: str = "", returncode: int = 0
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[],
            returncode=returncode,
            stdout=stdout,
            stderr="",
        )

    def test_parser_exposes_explicit_lifecycle(self) -> None:
        parser = launcher.parser()
        self.assertEqual("start", parser.parse_args([]).action)
        for action in ("start", "restart", "stop", "status", "rotate"):
            with self.subTest(action=action):
                self.assertEqual(action, parser.parse_args([action]).action)
        rotate = parser.parse_args(
            ["rotate", "--repository-root", "/repo", "--wait-pid", "42"]
        )
        self.assertEqual(Path("/repo"), rotate.repository_root)
        self.assertEqual(42, rotate.wait_pid)
        with (
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit),
        ):
            parser.parse_args(["--interval-seconds", "nan"])
        profiles = parser.parse_args(
            [
                "--profile",
                "loop",
                "--dispatcher-profile",
                "loop-dispatcher",
                "--coder-profile",
                "loop_coder",
                "--reviewer-profile",
                "loop-reviewer",
            ]
        )
        self.assertEqual("loop", profiles.profile)
        self.assertEqual("loop-dispatcher", profiles.dispatcher_profile)
        self.assertEqual("loop_coder", profiles.coder_profile)
        self.assertEqual("loop-reviewer", profiles.reviewer_profile)
        with (
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit),
        ):
            parser.parse_args(["--coder-profile", "not/a/profile"])

    def test_runner_workdirs_are_dedicated_siblings(self) -> None:
        root = Path("/workspace/emmet-qt-book")
        self.assertEqual(
            {
                "dispatcher": Path("/workspace/emmet-qt-book-dispatcher"),
                "coder": Path("/workspace/emmet-qt-book-coder"),
                "reviewer": Path("/workspace/emmet-qt-book-reviewer"),
            },
            launcher.runner_workdirs(root),
        )

    def test_commands_start_agents_before_events_and_share_profile(self) -> None:
        commands = launcher.build_component_commands(
            Path("/trusted/scripts/codex-loop"),
            launcher.runner_workdirs(Path("/workspace/emmet-qt-book")),
            interval_seconds=60,
            retry_seconds=1800,
            dispatcher_heartbeat_seconds=1800,
            profile="loop",
        )
        self.assertEqual(
            ["dispatcher", "coder", "reviewer", "events"],
            list(commands),
        )
        for role in launcher.adapter.ROLES:
            with self.subTest(role=role):
                self.assertEqual(["--profile", "loop"], commands[role][-2:])
                self.assertNotIn("--model", commands[role])
        self.assertNotIn("--profile", commands["events"])
        self.assertIn("--rotation-profile", commands["events"])
        self.assertIn("--tmux-session", commands["events"])
        self.assertIn("--repository-root", commands["events"])
        for command in commands.values():
            self.assertIn("--tmux-title", command)
            self.assertIn("--tmux-bin", command)

    def test_role_profiles_override_the_shared_profile(self) -> None:
        commands = launcher.build_component_commands(
            Path("/trusted/scripts/codex-loop"),
            launcher.runner_workdirs(Path("/workspace/emmet-qt-book")),
            interval_seconds=60,
            retry_seconds=1800,
            dispatcher_heartbeat_seconds=1800,
            profile="loop",
            role_profiles={
                "dispatcher": "loop-dispatcher",
                "coder": None,
                "reviewer": "loop-reviewer",
            },
        )

        self.assertEqual(
            ["--profile", "loop-dispatcher"],
            commands["dispatcher"][-2:],
        )
        self.assertEqual(["--profile", "loop"], commands["coder"][-2:])
        self.assertEqual(
            ["--profile", "loop-reviewer"],
            commands["reviewer"][-2:],
        )
        self.assertNotIn("--profile", commands["events"])
        for role, selected in (
            ("dispatcher", "loop-dispatcher"),
            ("coder", "loop"),
            ("reviewer", "loop-reviewer"),
        ):
            self.assertIn(f"--rotation-{role}-profile", commands["events"])
            self.assertIn(selected, commands["events"])

    def test_profile_files_must_exist_and_contain_valid_toml(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            codex_home = Path(temporary)
            (codex_home / "valid.config.toml").write_text(
                'model = "example-model"\n'
                'model_reasoning_effort = "high"\n',
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ, {"CODEX_HOME": str(codex_home)}
            ):
                launcher.validate_profile_files(
                    {
                        "dispatcher": "valid",
                        "coder": "valid",
                        "reviewer": None,
                    }
                )
                with self.assertRaisesRegex(
                    launcher.LauncherError, "找不到 Codex profile"
                ):
                    launcher.validate_profile_files(
                        {
                            "dispatcher": "missing",
                            "coder": None,
                            "reviewer": None,
                        }
                    )

                (codex_home / "broken.config.toml").write_text(
                    "model = [\n",
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(
                    launcher.LauncherError, "無法解析 Codex profile"
                ):
                    launcher.validate_profile_files(
                        {
                            "dispatcher": "broken",
                            "coder": None,
                            "reviewer": None,
                        }
                    )

    def test_preflight_checks_the_selected_profile_for_every_agent(self) -> None:
        runners = launcher.runner_workdirs(Path("/workspace/emmet-qt-book"))
        with mock.patch.object(launcher, "run_command") as run:
            launcher.run_preflight(
                Path("/trusted/scripts/codex-loop"), runners, "loop"
            )

        self.assertEqual(4, run.call_count)
        for call in run.call_args_list[:3]:
            command = call.args[0]
            self.assertIn("--profile", command)
            self.assertIn("loop", command)
            self.assertEqual("--dry-run", command[-1])
        self.assertNotIn("--profile", run.call_args_list[3].args[0])

    def test_preflight_uses_each_roles_resolved_profile(self) -> None:
        runners = launcher.runner_workdirs(Path("/workspace/emmet-qt-book"))
        with mock.patch.object(launcher, "run_command") as run:
            launcher.run_preflight(
                Path("/trusted/scripts/codex-loop"),
                runners,
                role_profiles={
                    "dispatcher": "loop-dispatcher",
                    "coder": "loop-coder",
                    "reviewer": "loop-reviewer",
                },
            )

        for call, selected in zip(
            run.call_args_list[:3],
            ("loop-dispatcher", "loop-coder", "loop-reviewer"),
            strict=True,
        ):
            self.assertEqual(
                ["--profile", selected, "--dry-run"],
                call.args[0][-3:],
            )

    def test_dry_run_does_not_call_mutating_helpers(self) -> None:
        options = launcher.parser().parse_args(["restart", "--dry-run"])
        runners = launcher.runner_workdirs(Path("/workspace/emmet-qt-book"))
        with (
            mock.patch.object(
                launcher.adapter,
                "_git_root_and_common_dir",
                return_value=(Path("/repo"), Path("/repo/.git")),
            ),
            mock.patch.object(
                launcher, "runner_workdirs", return_value=runners
            ),
            mock.patch.object(
                launcher, "resolve_executable", return_value="/usr/bin/tmux"
            ),
            mock.patch.object(launcher, "dry_run_plan") as plan,
            mock.patch.object(launcher, "prepare_runners") as prepare,
            mock.patch.object(
                launcher, "stop_existing_components"
            ) as stop_components,
            mock.patch.object(launcher, "create_tmux_session") as create,
        ):
            self.assertEqual(0, launcher.launch(options))

        plan.assert_called_once()
        prepare.assert_not_called()
        stop_components.assert_not_called()
        create.assert_not_called()

    def test_start_refuses_active_components_without_stopping_them(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime_dir = Path(temporary)
            lock_path = runtime_dir / "dispatcher.lock"
            lock_path.write_text(
                '{"component":"agent","role":"dispatcher","parent_pid":123}',
                encoding="utf-8",
            )
            descriptor = os.open(lock_path, os.O_RDWR)
            self.addCleanup(os.close, descriptor)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(
                launcher.LauncherError, "tmux restart"
            ):
                launcher.stop_existing_components(
                    runtime_dir,
                    allow_stop=False,
                    timeout_seconds=0.1,
                )

    def test_process_lock_identity_uses_the_actual_open_descriptor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lock_path = Path(temporary) / "component.lock"
            lock_path.touch()
            descriptor = os.open(lock_path, os.O_RDWR)
            try:
                self.assertTrue(
                    launcher.process_holds_lock(os.getpid(), lock_path)
                )
            finally:
                os.close(descriptor)

    def test_safe_stop_orders_events_before_agents(self) -> None:
        active = {
            "dispatcher": {"parent_pid": 101},
            "events": {"pid": 100},
            "coder": {"parent_pid": 102},
        }
        with (
            mock.patch.object(
                launcher, "active_components", return_value=active
            ),
            mock.patch.object(
                launcher, "process_matches_component", return_value=True
            ),
            mock.patch.object(
                launcher, "process_holds_lock", return_value=True
            ),
            mock.patch.object(launcher, "wait_for_lock_release"),
            mock.patch.object(launcher, "emit"),
            mock.patch.object(launcher.os, "kill") as kill,
        ):
            launcher.stop_existing_components(
                Path("/runtime"),
                allow_stop=True,
                timeout_seconds=1,
            )

        self.assertEqual(
            [
                mock.call(100, signal.SIGTERM),
                mock.call(101, signal.SIGTERM),
                mock.call(102, signal.SIGTERM),
            ],
            kill.call_args_list,
        )

    def test_rotate_session_verifies_stops_syncs_and_restarts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            repository_root = base / "repo"
            common_dir = repository_root / ".git"
            common_dir.mkdir(parents=True)
            runners = launcher.runner_workdirs(repository_root)
            adapter_path = runners["dispatcher"] / "scripts" / "codex-loop"
            adapter_path.parent.mkdir(parents=True)
            adapter_path.write_text("#!/bin/sh\n", encoding="utf-8")
            adapter_path.chmod(0o755)
            state_path = base / "runtime" / "rotation-state.json"
            options = launcher.parser().parse_args(
                [
                    "rotate",
                    "--repository-root",
                    str(repository_root),
                    "--session",
                    "test-loop",
                    "--wait-pid",
                    "123",
                    "--rotation-state",
                    str(state_path),
                    "--no-attach",
                    "--dispatcher-profile",
                    "loop-dispatcher",
                    "--reviewer-profile",
                    "loop-reviewer",
                ]
            )
            order: list[str] = []

            with (
                mock.patch.object(
                    launcher, "session_exists", return_value=True
                ),
                mock.patch.object(
                    launcher,
                    "verify_owned_session",
                    side_effect=lambda *_args: order.append("owned"),
                ),
                mock.patch.object(
                    launcher,
                    "verify_rotation_parent",
                    side_effect=lambda *_args: order.append("parent"),
                ),
                mock.patch.object(
                    launcher,
                    "wait_for_lock_release",
                    side_effect=lambda *_args: order.append("wait"),
                ),
                mock.patch.object(
                    launcher,
                    "stop_existing_components",
                    side_effect=lambda *_args, **_kwargs: order.append("stop"),
                ),
                mock.patch.object(
                    launcher,
                    "tmux_command",
                    side_effect=lambda *_args, **_kwargs: (
                        order.append("kill") or self.completed()
                    ),
                ),
                mock.patch.object(
                    launcher,
                    "prepare_runners",
                    side_effect=lambda *_args, **_kwargs: (
                        order.append("sync") or (common_dir, "a" * 40)
                    ),
                ) as prepare,
                mock.patch.object(
                    launcher,
                    "run_command",
                    side_effect=lambda command, **_kwargs: (
                        order.append("start") or self.completed()
                    ),
                ) as run,
                mock.patch.object(launcher, "emit"),
            ):
                result = launcher.rotate_session(
                    options,
                    repository_root,
                    runners,
                    common_dir,
                    state_path.parent,
                    "tmux",
                )

            self.assertEqual(0, result)
            self.assertEqual(
                ["owned", "parent", "wait", "stop", "owned", "kill", "sync", "start"],
                order,
            )
            self.assertFalse(prepare.call_args.kwargs["validate_loaded_control"])
            command = run.call_args.args[0]
            self.assertEqual(str(adapter_path), command[0])
            self.assertIn("--repository-root", command)
            self.assertIn("--no-attach", command)
            self.assertIn("--dispatcher-profile", command)
            self.assertIn("loop-dispatcher", command)
            self.assertIn("--reviewer-profile", command)
            self.assertIn("loop-reviewer", command)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual("completed", state["state"])
            self.assertEqual("a" * 40, state["target_main"])

    def test_tmux_layout_maps_roles_to_four_quadrants(self) -> None:
        pane_ids = iter(("%2", "%3", "%4"))
        common_dir = Path("/repo/.git")
        runners = launcher.runner_workdirs(Path("/workspace/emmet-qt-book"))

        def fake_tmux(
            _tmux_bin: str, *arguments: str, check: bool = True
        ) -> subprocess.CompletedProcess[str]:
            del check
            command = arguments[0]
            if command == "new-session":
                return self.completed("%1\n")
            if command == "show-options":
                return self.completed(str(common_dir) + "\n")
            if command == "split-window":
                return self.completed(next(pane_ids) + "\n")
            return self.completed()

        with mock.patch.object(
            launcher, "tmux_command", side_effect=fake_tmux
        ) as tmux:
            panes = launcher.create_tmux_session(
                "tmux", "test-loop", common_dir, runners
            )

        self.assertEqual(
            {
                "dispatcher": "%1",
                "coder": "%2",
                "reviewer": "%3",
                "events": "%4",
            },
            panes,
        )
        calls = [call.args for call in tmux.call_args_list]
        splits = [
            (
                args[args.index("-t") + 1],
                "-h" in args,
                args[-1],
            )
            for args in calls
            if args[1] == "split-window"
        ]
        self.assertEqual(
            [
                ("%1", True, str(runners["coder"])),
                ("%1", False, str(runners["reviewer"])),
                ("%2", False, str(runners["dispatcher"])),
            ],
            splits,
        )
        titles = {
            args[args.index("-t") + 1]: args[-1]
            for args in calls
            if args[1] == "select-pane" and "-T" in args
        }
        self.assertEqual(
            {
                "%1": "dispatcher (啟動中)",
                "%2": "coder (啟動中)",
                "%3": "reviewer (啟動中)",
                "%4": "events (等待 agents)",
            },
            titles,
        )
        border_format = next(
            args[-1]
            for args in calls
            if args[1] == "set-window-option"
            and "pane-border-format" in args
        )
        self.assertIn("pane_dead", border_format)
        self.assertIn("已退出", border_format)

    def test_marker_failure_removes_new_session(self) -> None:
        def fake_tmux(
            _tmux_bin: str, *arguments: str, check: bool = True
        ) -> subprocess.CompletedProcess[str]:
            del check
            if arguments[0] == "new-session":
                return self.completed("%1\n")
            if arguments[0] == "set-option":
                raise launcher.LauncherError("marker failed")
            return self.completed()

        runners = launcher.runner_workdirs(Path("/workspace/emmet-qt-book"))
        with mock.patch.object(
            launcher, "tmux_command", side_effect=fake_tmux
        ) as tmux:
            with self.assertRaisesRegex(
                launcher.LauncherError, "marker failed"
            ):
                launcher.create_tmux_session(
                    "tmux", "test-loop", Path("/repo/.git"), runners
                )

        self.assertIn(
            mock.call(
                "tmux",
                "kill-session",
                "-t",
                "test-loop",
                check=False,
            ),
            tmux.call_args_list,
        )

    def test_session_profile_metadata_round_trips(self) -> None:
        stored: dict[str, str] = {}

        def fake_tmux(
            _tmux_bin: str, *arguments: str, check: bool = True
        ) -> subprocess.CompletedProcess[str]:
            del check
            if arguments[0] == "set-option":
                stored[arguments[-2]] = arguments[-1]
                return self.completed()
            if arguments[0] == "show-options":
                return self.completed(stored.get(arguments[-1], "") + "\n")
            raise AssertionError(arguments)

        profiles = {
            "dispatcher": "loop-dispatcher",
            "coder": "loop-coder",
            "reviewer": "loop-reviewer",
        }
        with mock.patch.object(
            launcher, "tmux_command", side_effect=fake_tmux
        ):
            launcher.set_session_role_profiles(
                "tmux", "test-loop", profiles
            )
            self.assertEqual(
                profiles,
                launcher.session_role_profiles("tmux", "test-loop"),
            )

    def test_failed_start_cleanup_stops_around_owned_session_removal(
        self,
    ) -> None:
        order: list[str] = []

        def stop(*_args: object, **_kwargs: object) -> None:
            order.append("stop-components")

        def tmux(
            _tmux_bin: str, *arguments: str, check: bool = True
        ) -> subprocess.CompletedProcess[str]:
            del check
            order.append(":".join(arguments[:2]))
            return self.completed()

        with (
            mock.patch.object(
                launcher,
                "stop_existing_components",
                side_effect=stop,
            ),
            mock.patch.object(
                launcher, "session_exists", return_value=True
            ),
            mock.patch.object(
                launcher, "session_marker", return_value="/repo/.git"
            ),
            mock.patch.object(
                launcher, "tmux_command", side_effect=tmux
            ),
            mock.patch.object(
                launcher,
                "remove_stale_sockets",
                side_effect=lambda *_args: order.append("remove-sockets"),
            ),
        ):
            errors = launcher.cleanup_failed_start(
                "tmux", "test-loop", Path("/repo/.git"), Path("/runtime"), 1
            )

        self.assertEqual([], errors)
        self.assertEqual(
            ["stop-components", "kill-session:-t", "stop-components", "remove-sockets"],
            order,
        )

    def test_status_action_does_not_call_mutating_helpers(self) -> None:
        options = launcher.parser().parse_args(["status"])
        runners = launcher.runner_workdirs(Path("/workspace/emmet-qt-book"))
        with (
            mock.patch.object(
                launcher.adapter,
                "_git_root_and_common_dir",
                return_value=(Path("/repo"), Path("/repo/.git")),
            ),
            mock.patch.object(
                launcher, "runner_workdirs", return_value=runners
            ),
            mock.patch.object(
                launcher, "resolve_executable", return_value="/usr/bin/tmux"
            ),
            mock.patch.object(launcher, "status_report") as status,
            mock.patch.object(launcher, "prepare_runners") as prepare,
            mock.patch.object(
                launcher, "stop_existing_components"
            ) as stop_components,
            mock.patch.object(launcher, "create_tmux_session") as create,
        ):
            self.assertEqual(0, launcher.launch(options))

        status.assert_called_once()
        prepare.assert_not_called()
        stop_components.assert_not_called()
        create.assert_not_called()

    def test_launch_starts_events_only_after_all_agents_are_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            common_dir = root / ".git"
            runners = launcher.runner_workdirs(root)
            adapter_path = runners["dispatcher"] / "scripts" / "codex-loop"
            adapter_path.parent.mkdir(parents=True)
            adapter_path.write_text("#!/bin/sh\n", encoding="utf-8")
            adapter_path.chmod(0o755)
            order: list[str] = []
            panes = {
                "dispatcher": "%1",
                "coder": "%2",
                "reviewer": "%3",
                "events": "%4",
            }
            options = launcher.parser().parse_args(["start", "--no-attach"])

            def send(
                _tmux_bin: str, pane: str, _command: list[str]
            ) -> None:
                order.append(f"send:{pane}")

            with (
                mock.patch.object(
                    launcher.adapter,
                    "_git_root_and_common_dir",
                    return_value=(root, common_dir),
                ),
                mock.patch.object(
                    launcher, "runner_workdirs", return_value=runners
                ),
                mock.patch.object(
                    launcher,
                    "resolve_executable",
                    return_value="/usr/bin/tmux",
                ),
                mock.patch.object(
                    launcher, "session_exists", return_value=False
                ),
                mock.patch.object(
                    launcher, "stop_existing_components"
                ),
                mock.patch.object(
                    launcher,
                    "prepare_runners",
                    return_value=(common_dir, "abc123"),
                ),
                mock.patch.object(launcher, "run_preflight"),
                mock.patch.object(launcher, "remove_stale_sockets"),
                mock.patch.object(
                    launcher, "create_tmux_session", return_value=panes
                ),
                mock.patch.object(
                    launcher,
                    "set_session_role_profiles",
                    side_effect=lambda *_args: order.append("profiles"),
                ),
                mock.patch.object(
                    launcher, "send_pane_command", side_effect=send
                ),
                mock.patch.object(
                    launcher,
                    "wait_for_agent_sockets",
                    side_effect=lambda *_args: order.append("agents-ready"),
                ),
                mock.patch.object(
                    launcher,
                    "wait_for_events_lock",
                    side_effect=lambda *_args: order.append("events-ready"),
                ),
                mock.patch.object(launcher, "emit"),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(0, launcher.launch(options))

        self.assertEqual(
            [
                "profiles",
                "send:%1",
                "send:%2",
                "send:%3",
                "agents-ready",
                "send:%4",
                "events-ready",
            ],
            order,
        )

    def test_foreign_same_name_session_blocks_stop_before_process_changes(
        self,
    ) -> None:
        options = launcher.parser().parse_args(["stop"])
        with (
            mock.patch.object(
                launcher.adapter,
                "_git_root_and_common_dir",
                return_value=(Path("/repo"), Path("/repo/.git")),
            ),
            mock.patch.object(
                launcher, "resolve_executable", return_value="/usr/bin/tmux"
            ),
            mock.patch.object(
                launcher, "session_exists", return_value=True
            ),
            mock.patch.object(
                launcher,
                "verify_owned_session",
                side_effect=launcher.LauncherError("foreign"),
            ),
            mock.patch.object(
                launcher, "stop_existing_components"
            ) as stop_components,
        ):
            with self.assertRaisesRegex(launcher.LauncherError, "foreign"):
                launcher.launch(options)

        stop_components.assert_not_called()


if __name__ == "__main__":
    unittest.main()
