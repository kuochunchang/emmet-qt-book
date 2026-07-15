from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import textwrap
import time
import unittest
from unittest.mock import patch

from scripts.codex_loop_runtime import (
    build_event,
    classify_snapshot,
    describe_operator_status,
    detect_stalled_iteration,
    normalize_snapshot,
    notify_agent,
    poll_github,
    pull_request_disappeared,
    read_agent_states,
    select_poll_decisions,
    socket_path,
    workflow_state_fingerprint,
)


ROOT = Path(__file__).resolve().parents[1]


def loop_object(
    kind: str, number: int, *labels: str
) -> dict[str, object]:
    item: dict[str, object] = {
        "kind": kind,
        "number": number,
        "updated_at": "2026-07-15T00:00:00Z",
        "labels": list(labels),
    }
    if kind == "pull_request":
        item.update(
            {
                "head_sha": "a" * 40,
                "base": "main",
                "draft": False,
                "mergeable": "MERGEABLE",
            }
        )
    return item


def snapshot(
    *,
    paused: bool = False,
    issues: list[dict[str, object]] | None = None,
    pull_requests: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "paused": paused,
        "issues": issues or [],
        "pull_requests": pull_requests or [],
    }


class EventRoutingTests(unittest.TestCase):
    def assert_route(
        self, state: dict[str, object], role: str, reason: str
    ) -> None:
        decisions = classify_snapshot(state)
        self.assertEqual(1, len(decisions))
        self.assertEqual(role, decisions[0]["role"])
        self.assertEqual(reason, decisions[0]["reason"])

    def test_empty_loop_wakes_dispatcher(self) -> None:
        self.assert_route(snapshot(), "dispatcher", "reconcile-or-dispatch")

    def test_queued_or_coding_issue_wakes_coder(self) -> None:
        for label in ("loop:queued", "loop:coding"):
            with self.subTest(label=label):
                self.assert_route(
                    snapshot(issues=[loop_object("issue", 3, label)]),
                    "coder",
                    "coding-work-available",
                )

    def test_pull_request_state_routes_to_current_owner(self) -> None:
        issue = loop_object("issue", 3, "loop:coding")
        cases = (
            ("loop:needs-review", "reviewer", "review-requested"),
            ("loop:changes-requested", "coder", "changes-requested"),
            ("loop:approved", "dispatcher", "approved-pull-request"),
        )
        for label, role, reason in cases:
            with self.subTest(label=label):
                self.assert_route(
                    snapshot(
                        issues=[issue],
                        pull_requests=[loop_object("pull_request", 52, label)],
                    ),
                    role,
                    reason,
                )

    def test_blocked_or_malformed_state_wakes_dispatcher(self) -> None:
        cases = (
            loop_object("issue", 3, "loop:coding", "loop:blocked"),
            loop_object("issue", 3, "loop:queued", "loop:coding"),
            loop_object("issue", 3, "loop:blocked"),
        )
        for item in cases:
            with self.subTest(labels=item["labels"]):
                self.assert_route(
                    snapshot(issues=[item]),
                    "dispatcher",
                    "reconciliation-required",
                )

    def test_pause_sends_control_event_to_every_agent(self) -> None:
        decisions = classify_snapshot(snapshot(paused=True))
        self.assertEqual(
            ["dispatcher", "coder", "reviewer"],
            [item["role"] for item in decisions],
        )
        self.assertTrue(all(item["action"] == "paused" for item in decisions))

    def test_graphql_payload_is_normalized_without_meta_as_work(self) -> None:
        payload = {
            "data": {
                "repository": {
                    "meta": {
                        "labels": {"nodes": [{"name": "loop:paused"}]}
                    }
                },
                "loopObjects": {
                    "nodes": [
                        {
                            "__typename": "Issue",
                            "number": 3,
                            "updatedAt": "2026-07-15T00:00:00Z",
                            "labels": {
                                "nodes": [{"name": "loop:queued"}]
                            },
                        },
                        {
                            "__typename": "Issue",
                            "number": 9,
                            "updatedAt": "2026-07-15T00:00:00Z",
                            "labels": {"nodes": [{"name": "documentation"}]},
                        },
                    ]
                },
            }
        }
        normalized = normalize_snapshot(payload)
        self.assertTrue(normalized["paused"])
        self.assertEqual([3], [item["number"] for item in normalized["issues"]])

    def test_github_poll_uses_supported_multi_label_or_qualifier(self) -> None:
        payload = {
            "data": {
                "repository": {"meta": {"labels": {"nodes": []}}},
                "loopObjects": {"nodes": []},
            }
        }
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )

        with patch(
            "scripts.codex_loop_runtime.subprocess.run",
            return_value=completed,
        ) as run:
            poll_github("gh", Path("/tmp"), "test/repo")

        command = run.call_args.args[0]
        search_argument = next(
            argument for argument in command if argument.startswith("search=")
        )
        self.assertEqual(
            "search=repo:test/repo is:open "
            'label:"loop:approved","loop:blocked",'
            '"loop:changes-requested","loop:coding",'
            '"loop:needs-review","loop:queued"',
            search_argument,
        )

    def test_busy_child_serializes_regular_role_wakes(self) -> None:
        decisions = select_poll_decisions(
            snapshot(issues=[loop_object("issue", 3, "loop:queued")]),
            busy=["reviewer"],
            now=100,
            last_dispatcher_wake=0,
            dispatcher_heartbeat_seconds=30,
        )
        self.assertEqual([], decisions)

    def test_dispatcher_oversight_replaces_owner_retry_when_due(self) -> None:
        state = snapshot(issues=[loop_object("issue", 3, "loop:coding")])
        before = select_poll_decisions(
            state,
            busy=[],
            now=29,
            last_dispatcher_wake=0,
            dispatcher_heartbeat_seconds=30,
        )
        due = select_poll_decisions(
            state,
            busy=[],
            now=30,
            last_dispatcher_wake=0,
            dispatcher_heartbeat_seconds=30,
        )
        self.assertEqual("coder", before[0]["role"])
        self.assertEqual("dispatcher", due[0]["role"])
        self.assertEqual("oversight-heartbeat", due[0]["reason"])

    def test_pause_control_events_are_not_suppressed_by_busy_child(self) -> None:
        decisions = select_poll_decisions(
            snapshot(paused=True),
            busy=["coder"],
            now=100,
            last_dispatcher_wake=0,
            dispatcher_heartbeat_seconds=30,
        )
        self.assertEqual(3, len(decisions))
        self.assertTrue(all(item["action"] == "paused" for item in decisions))

    def test_open_pull_request_disappearance_requires_dispatcher_cleanup(self) -> None:
        previous = snapshot(
            issues=[loop_object("issue", 3, "loop:coding")],
            pull_requests=[
                loop_object("pull_request", 52, "loop:approved")
            ],
        )
        current = snapshot(
            issues=[loop_object("issue", 3, "loop:coding")]
        )
        self.assertTrue(pull_request_disappeared(previous, current))
        self.assertFalse(pull_request_disappeared(current, current))


class OperatorStatusTests(unittest.TestCase):
    def test_queued_issue_explains_owner_and_next_transition(self) -> None:
        state = snapshot(
            issues=[loop_object("issue", 3, "loop:queued")]
        )
        status = describe_operator_status(
            state,
            decisions=classify_snapshot(state),
            busy=[],
        )

        self.assertEqual("healthy", status["health"])
        self.assertFalse(status["blocking"])
        self.assertEqual("coder", status["owner"])
        self.assertEqual("coding-work-available", status["reason"])
        self.assertIn("Issue #3", status["current"])
        self.assertIn("loop:coding", status["next"])

    def test_protocol_violation_is_reported_as_blocking(self) -> None:
        state = snapshot(
            issues=[
                loop_object(
                    "issue",
                    3,
                    "loop:coding",
                    "loop:blocked",
                )
            ]
        )
        status = describe_operator_status(
            state,
            decisions=classify_snapshot(state),
            busy=[],
        )

        self.assertEqual("blocked", status["health"])
        self.assertTrue(status["blocking"])
        self.assertEqual("dispatcher", status["owner"])
        self.assertIn("reconciliation", status["next"])
        self.assertIsNotNone(status["attention"])

    def test_busy_role_is_running_not_stalled(self) -> None:
        state = snapshot(
            issues=[loop_object("issue", 3, "loop:coding")]
        )
        status = describe_operator_status(
            state,
            decisions=[],
            busy=["coder"],
        )

        self.assertEqual("running", status["health"])
        self.assertFalse(status["blocking"])
        self.assertEqual("coder", status["owner"])
        self.assertIn("Codex iteration", status["current"])
        self.assertIn("重新讀取 GitHub", status["next"])

    def test_completed_iteration_without_state_change_is_stalled(self) -> None:
        state = snapshot(
            issues=[loop_object("issue", 3, "loop:queued")]
        )
        decision = classify_snapshot(state)[0]
        deliveries = {
            "coder": {
                "event_id": "event-1",
                "reason": decision["reason"],
                "state_fingerprint": workflow_state_fingerprint(state),
            }
        }
        agent_states = {
            "coder": {
                "state": "waiting",
                "last_event_id": "event-1",
                "last_exit_code": 0,
                "last_finished_at": "2026-07-15T05:05:02Z",
            }
        }

        stalled = detect_stalled_iteration(
            state,
            deliveries=deliveries,
            agent_states=agent_states,
        )
        self.assertIsNotNone(stalled)
        status = describe_operator_status(
            state,
            decisions=[decision],
            busy=[],
            stalled=stalled,
        )

        self.assertEqual("stalled", status["health"])
        self.assertTrue(status["blocking"])
        self.assertEqual("dispatcher", status["owner"])
        self.assertEqual(
            "no-durable-progress-after-iteration",
            status["reason"],
        )
        self.assertIn("coder", status["attention"])

    def test_timestamp_only_update_does_not_hide_stalled_iteration(self) -> None:
        before = snapshot(
            issues=[loop_object("issue", 3, "loop:queued")]
        )
        after_item = loop_object("issue", 3, "loop:queued")
        after_item["updated_at"] = "2026-07-15T05:05:02Z"
        after = snapshot(issues=[after_item])
        decision = classify_snapshot(before)[0]

        stalled = detect_stalled_iteration(
            after,
            deliveries={
                "coder": {
                    "event_id": "event-1",
                    "reason": decision["reason"],
                    "state_fingerprint": workflow_state_fingerprint(before),
                }
            },
            agent_states={
                "coder": {
                    "state": "waiting",
                    "last_event_id": "event-1",
                    "last_exit_code": 0,
                }
            },
        )

        self.assertIsNotNone(stalled)

    def test_workflow_transition_clears_stalled_detection(self) -> None:
        before = snapshot(
            issues=[loop_object("issue", 3, "loop:queued")]
        )
        after = snapshot(
            issues=[loop_object("issue", 3, "loop:coding")]
        )
        decision = classify_snapshot(before)[0]

        stalled = detect_stalled_iteration(
            after,
            deliveries={
                "coder": {
                    "event_id": "event-1",
                    "reason": decision["reason"],
                    "state_fingerprint": workflow_state_fingerprint(before),
                }
            },
            agent_states={
                "coder": {
                    "state": "waiting",
                    "last_event_id": "event-1",
                    "last_exit_code": 0,
                }
            },
        )

        self.assertIsNone(stalled)

    def test_empty_dispatcher_no_op_is_not_reported_as_stalled(self) -> None:
        state = snapshot()
        decision = classify_snapshot(state)[0]

        stalled = detect_stalled_iteration(
            state,
            deliveries={
                "dispatcher": {
                    "event_id": "event-1",
                    "reason": decision["reason"],
                    "state_fingerprint": workflow_state_fingerprint(state),
                }
            },
            agent_states={
                "dispatcher": {
                    "state": "waiting",
                    "last_event_id": "event-1",
                    "last_exit_code": 0,
                }
            },
        )

        self.assertIsNone(stalled)

    def test_mergeability_only_update_does_not_hide_stall(self) -> None:
        issue = loop_object("issue", 3, "loop:coding")
        before_pr = loop_object(
            "pull_request", 52, "loop:needs-review"
        )
        before_pr["mergeable"] = "UNKNOWN"
        after_pr = dict(before_pr)
        after_pr["mergeable"] = "MERGEABLE"
        before = snapshot(issues=[issue], pull_requests=[before_pr])
        after = snapshot(issues=[issue], pull_requests=[after_pr])
        decision = classify_snapshot(before)[0]

        stalled = detect_stalled_iteration(
            after,
            deliveries={
                "reviewer": {
                    "event_id": "event-1",
                    "reason": decision["reason"],
                    "state_fingerprint": workflow_state_fingerprint(before),
                }
            },
            agent_states={
                "reviewer": {
                    "state": "waiting",
                    "last_event_id": "event-1",
                    "last_exit_code": 0,
                }
            },
        )

        self.assertIsNotNone(stalled)


class AgentRuntimeTests(unittest.TestCase):
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
            [
                "git",
                "-C",
                str(self.worktree),
                "remote",
                "add",
                "origin",
                str(self.remote),
            ],
            check=True,
        )
        skill = (
            self.worktree
            / ".agents"
            / "skills"
            / "emmet-loop-dispatcher"
            / "SKILL.md"
        )
        skill.parent.mkdir(parents=True)
        skill.write_text(
            "---\nname: emmet-loop-dispatcher\n---\n", encoding="utf-8"
        )
        scripts = self.worktree / "scripts"
        scripts.mkdir()
        shutil.copy2(ROOT / "scripts" / "codex_loop.py", scripts)
        shutil.copy2(ROOT / "scripts" / "codex_loop_runtime.py", scripts)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.worktree),
                "config",
                "user.email",
                "test@example.com",
            ],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.worktree),
                "config",
                "user.name",
                "Test",
            ],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.worktree), "add", "."], check=True
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.worktree),
                "commit",
                "--quiet",
                "-m",
                "fixture",
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
                "-u",
                "origin",
                "main",
            ],
            check=True,
        )
        self.runtime_dir = self.base / "runtime"
        self.capture = self.base / "capture.json"
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

                Path(os.environ["FAKE_CODEX_CAPTURE"]).write_text(
                    json.dumps(sys.argv[1:]), encoding="utf-8"
                )
                print(json.dumps({
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": "fake-visible-event",
                    },
                }), flush=True)
                time.sleep(float(os.environ.get("FAKE_CODEX_SLEEP", "0")))
                """
            ),
            encoding="utf-8",
        )
        self.fake_codex.chmod(0o755)
        self.fake_gh = self.base / "fake gh"
        self.fake_gh.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import os
                import sys

                if sys.argv[1:3] == ["repo", "view"]:
                    print("test/repo")
                else:
                    print(os.environ["FAKE_GH_PAYLOAD"])
                """
            ),
            encoding="utf-8",
        )
        self.fake_gh.chmod(0o755)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def start_agent(
        self, max_events: int = 1, sleep_seconds: str = "0"
    ) -> subprocess.Popen[str]:
        environment = os.environ.copy()
        environment["FAKE_CODEX_CAPTURE"] = str(self.capture)
        environment["FAKE_CODEX_SLEEP"] = sleep_seconds
        process = subprocess.Popen(
            [
                "python3",
                str(self.worktree / "scripts" / "codex_loop_runtime.py"),
                "agent",
                "dispatcher",
                "--workdir",
                str(self.worktree),
                "--codex-bin",
                str(self.fake_codex),
                "--runtime-dir",
                str(self.runtime_dir),
                "--max-events",
                str(max_events),
            ],
            cwd=self.worktree,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
        endpoint = socket_path(self.runtime_dir, "dispatcher")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not endpoint.exists():
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                self.fail(f"agent exited early: {stdout}\n{stderr}")
            time.sleep(0.01)
        self.assertTrue(endpoint.exists(), "agent socket was not created")
        return process

    def test_wake_runs_one_codex_and_streams_every_json_event(self) -> None:
        process = self.start_agent()
        event = build_event(
            {
                "role": "dispatcher",
                "action": "wake",
                "reason": "test",
                "objects": [],
            },
            "test/repo",
        )
        acknowledgement = notify_agent(self.runtime_dir, event)
        self.assertTrue(acknowledgement["accepted"])
        stdout, stderr = process.communicate(timeout=10)

        self.assertEqual(0, process.returncode, stderr)
        self.assertIn("fake-visible-event", stdout)
        self.assertIn('"result": "iteration-finished"', stdout)
        metadata = read_agent_states(self.runtime_dir)["dispatcher"]
        self.assertEqual(event["event_id"], metadata["last_event_id"])
        self.assertEqual(0, metadata["last_exit_code"])
        self.assertEqual("waiting", metadata["state"])
        arguments = json.loads(self.capture.read_text(encoding="utf-8"))
        self.assertEqual(1, arguments.count("exec"))
        self.assertIn("--json", arguments)
        self.assertIn("$emmet-loop-dispatcher", arguments[-1])

    def test_pause_event_is_visible_without_starting_codex(self) -> None:
        process = self.start_agent()
        event = build_event(
            {
                "role": "dispatcher",
                "action": "paused",
                "reason": "global-pause",
            },
            "test/repo",
        )
        notify_agent(self.runtime_dir, event)
        stdout, stderr = process.communicate(timeout=10)

        self.assertEqual(0, process.returncode, stderr)
        self.assertIn('"result": "paused"', stdout)
        self.assertFalse(self.capture.exists())

    def test_event_manager_polls_github_and_notifies_responsible_agent(self) -> None:
        agent = self.start_agent()
        payload = {
            "data": {
                "repository": {"meta": {"labels": {"nodes": []}}},
                "loopObjects": {"nodes": []},
            }
        }
        environment = os.environ.copy()
        environment["FAKE_GH_PAYLOAD"] = json.dumps(payload)
        manager = subprocess.run(
            [
                "python3",
                str(self.worktree / "scripts" / "codex_loop_runtime.py"),
                "events",
                "--workdir",
                str(self.worktree),
                "--gh-bin",
                str(self.fake_gh),
                "--repo",
                "test/repo",
                "--runtime-dir",
                str(self.runtime_dir),
                "--once",
            ],
            cwd=self.worktree,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
        agent_stdout, agent_stderr = agent.communicate(timeout=10)

        self.assertEqual(0, manager.returncode, manager.stderr)
        self.assertIn('"result": "notified"', manager.stdout)
        self.assertIn('"result": "operator-status"', manager.stdout)
        self.assertIn('"health": "healthy"', manager.stdout)
        self.assertIn('"next":', manager.stdout)
        self.assertEqual(0, agent.returncode, agent_stderr)
        self.assertIn("fake-visible-event", agent_stdout)

    def test_event_manager_reports_no_progress_after_iteration(self) -> None:
        agent = self.start_agent()
        payload = {
            "data": {
                "repository": {"meta": {"labels": {"nodes": []}}},
                "loopObjects": {
                    "nodes": [
                        {
                            "__typename": "Issue",
                            "number": 3,
                            "updatedAt": "2026-07-15T00:00:00Z",
                            "labels": {
                                "nodes": [{"name": "loop:coding"}]
                            },
                        },
                        {
                            "__typename": "PullRequest",
                            "number": 52,
                            "updatedAt": "2026-07-15T00:00:00Z",
                            "headRefOid": "a" * 40,
                            "baseRefName": "main",
                            "isDraft": False,
                            "mergeable": "MERGEABLE",
                            "labels": {
                                "nodes": [{"name": "loop:approved"}]
                            },
                        },
                    ]
                },
            }
        }
        environment = os.environ.copy()
        environment["FAKE_GH_PAYLOAD"] = json.dumps(payload)
        manager = subprocess.Popen(
            [
                "python3",
                str(self.worktree / "scripts" / "codex_loop_runtime.py"),
                "events",
                "--workdir",
                str(self.worktree),
                "--gh-bin",
                str(self.fake_gh),
                "--repo",
                "test/repo",
                "--runtime-dir",
                str(self.runtime_dir),
                "--interval-seconds",
                "0.05",
                "--retry-seconds",
                "30",
                "--dispatcher-heartbeat-seconds",
                "30",
            ],
            cwd=self.worktree,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
        records: list[dict[str, object]] = []
        try:
            self.assertIsNotNone(manager.stdout)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                line = manager.stdout.readline()
                if not line:
                    if manager.poll() is not None:
                        break
                    continue
                record = json.loads(line)
                records.append(record)
                if (
                    record.get("result") == "operator-status"
                    and record.get("health") == "stalled"
                ):
                    break
        finally:
            manager.terminate()
            manager_stdout, manager_stderr = manager.communicate(timeout=10)
            if manager_stdout:
                records.extend(
                    json.loads(line)
                    for line in manager_stdout.splitlines()
                    if line
                )
        agent_stdout, agent_stderr = agent.communicate(timeout=10)

        stalled = [
            record
            for record in records
            if record.get("result") == "operator-status"
            and record.get("health") == "stalled"
        ]
        self.assertTrue(stalled, manager_stderr)
        self.assertEqual(
            "no-durable-progress-after-iteration",
            stalled[-1]["reason"],
        )
        self.assertTrue(stalled[-1]["blocking"])
        self.assertEqual("dispatcher", stalled[-1]["owner"])
        self.assertEqual(0, agent.returncode, agent_stderr)
        self.assertIn('"result": "iteration-finished"', agent_stdout)

    def test_busy_agent_rejects_new_delivery_without_socket_backlog(self) -> None:
        process = self.start_agent(max_events=0, sleep_seconds="2")
        event = build_event(
            {
                "role": "dispatcher",
                "action": "wake",
                "reason": "first",
                "objects": [],
            },
            "test/repo",
        )
        notify_agent(self.runtime_dir, event)
        lock_path = self.runtime_dir / "dispatcher.lock"
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            metadata = json.loads(lock_path.read_text(encoding="utf-8"))
            if metadata.get("child_pid") is not None:
                break
            time.sleep(0.01)
        self.assertIsNotNone(metadata.get("child_pid"))
        with self.assertRaises(BlockingIOError):
            notify_agent(
                self.runtime_dir,
                build_event(
                    {
                        "role": "dispatcher",
                        "action": "wake",
                        "reason": "second",
                        "objects": [],
                    },
                    "test/repo",
                ),
            )
        process.terminate()
        process.communicate(timeout=10)

    def test_only_one_event_manager_may_poll_a_repository(self) -> None:
        payload = {
            "data": {
                "repository": {"meta": {"labels": {"nodes": []}}},
                "loopObjects": {"nodes": []},
            }
        }
        environment = os.environ.copy()
        environment["FAKE_GH_PAYLOAD"] = json.dumps(payload)
        command = [
            "python3",
            str(self.worktree / "scripts" / "codex_loop_runtime.py"),
            "events",
            "--workdir",
            str(self.worktree),
            "--gh-bin",
            str(self.fake_gh),
            "--repo",
            "test/repo",
            "--runtime-dir",
            str(self.runtime_dir),
            "--interval-seconds",
            "30",
        ]
        first = subprocess.Popen(
            command,
            cwd=self.worktree,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
        lock_path = self.runtime_dir / "events.lock"
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not lock_path.exists():
            if first.poll() is not None:
                stdout, stderr = first.communicate()
                self.fail(f"event manager exited early: {stdout}\n{stderr}")
            time.sleep(0.01)
        second = subprocess.run(
            [*command, "--once"],
            cwd=self.worktree,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
        first.terminate()
        first.communicate(timeout=10)

        self.assertEqual(75, second.returncode, second.stderr)
        self.assertIn('"result": "already-running"', second.stdout)

    def test_wrong_role_event_is_rejected_without_starting_codex(self) -> None:
        process = self.start_agent()
        endpoint = socket_path(self.runtime_dir, "dispatcher")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2)
            client.connect(str(endpoint))
            client.sendall(
                b'{"role":"reviewer","action":"wake","event_id":"bad"}\n'
            )
            client.shutdown(socket.SHUT_WR)
            response = json.loads(client.makefile().readline())
        self.assertFalse(response["accepted"])
        process.terminate()
        stdout, stderr = process.communicate(timeout=10)
        self.assertNotEqual("", stdout)
        self.assertFalse(self.capture.exists(), stderr)


if __name__ == "__main__":
    unittest.main()
