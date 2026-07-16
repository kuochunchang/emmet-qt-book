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
    agent_event_pane_status,
    build_alert_escalation,
    build_event,
    build_operator_alert,
    build_preflight_packet,
    classify_snapshot,
    command_with_event_context,
    control_rotation_status,
    describe_operator_status,
    detect_stalled_iteration,
    duplicate_delivery_is_suppressed,
    event_fingerprint,
    normalize_snapshot,
    notify_agent,
    operator_pane_status,
    poll_github,
    pull_request_disappearance_decision,
    pull_request_disappeared,
    read_agent_states,
    rotation_state_path,
    run_events,
    select_poll_decisions,
    socket_path,
    transition_operator_alert,
    update_pane_title,
    write_rotation_state,
    workflow_state_fingerprint,
    parser as runtime_parser,
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
    main_sha: str | None = None,
    gate_exit: dict[str, object] | None = None,
    issues: list[dict[str, object]] | None = None,
    pull_requests: list[dict[str, object]] | None = None,
    snapshot_incomplete: list[str] | None = None,
) -> dict[str, object]:
    return {
        "paused": paused,
        "main_sha": main_sha,
        "gate_exit": gate_exit,
        "meta_comments_truncated": False,
        "snapshot_incomplete": snapshot_incomplete or [],
        "issues": issues or [],
        "pull_requests": pull_requests or [],
    }


class PaneTitleTests(unittest.TestCase):
    def test_agent_title_names_current_action_and_work_object(self) -> None:
        issue_event = {
            "reason": "coding-work-available",
            "objects": [loop_object("issue", 3, "loop:coding")],
        }
        review_event = {
            "reason": "review-requested",
            "objects": [
                loop_object("issue", 3, "loop:coding"),
                loop_object("pull_request", 59, "loop:needs-review"),
            ],
        }

        self.assertEqual("撰寫中：Issue #3", agent_event_pane_status(issue_event))
        self.assertEqual("審查中：PR #59", agent_event_pane_status(review_event))
        self.assertEqual(
            "補查狀態中",
            agent_event_pane_status({"reason": "snapshot-incomplete"}),
        )

    def test_operator_title_prioritizes_running_and_blocking_state(self) -> None:
        issue = loop_object("issue", 3, "loop:coding")
        self.assertEqual(
            "正常：coder 執行中／Issue #3",
            operator_pane_status(
                {
                    "health": "running",
                    "owner": "coder",
                    "affected_role": "coder",
                    "busy_roles": ["coder"],
                    "objects": [issue],
                }
            ),
        )
        self.assertEqual(
            "阻斷：reviewer 無法接收事件",
            operator_pane_status(
                {
                    "health": "blocked",
                    "owner": "operator",
                    "affected_role": "reviewer",
                    "reason": "delivery-failed",
                    "objects": [issue],
                }
            ),
        )
        self.assertEqual(
            "阻斷：WIP 狀態衝突",
            operator_pane_status(
                {
                    "health": "blocked",
                    "owner": "dispatcher",
                    "affected_role": None,
                    "reason": "wip-invariant-violation",
                    "objects": [issue],
                }
            ),
        )
        self.assertEqual(
            "阻斷：GitHub 狀態快照不完整",
            operator_pane_status(
                {
                    "health": "blocked",
                    "owner": "dispatcher",
                    "reason": "snapshot-incomplete",
                    "objects": [],
                }
            ),
        )

    def test_active_alert_stays_visible_while_recovery_runs(self) -> None:
        self.assertEqual(
            "告警處理中：coder",
            operator_pane_status(
                {
                    "health": "running",
                    "owner": "dispatcher",
                    "affected_role": "dispatcher",
                    "busy_roles": ["dispatcher"],
                    "objects": [],
                },
                {"affected_role": "coder"},
            ),
        )

    def test_awaiting_user_title_names_gate_transition(self) -> None:
        self.assertEqual(
            "等待使用者：gate transition",
            operator_pane_status(
                {
                    "health": "awaiting-user",
                    "owner": "user",
                    "objects": [],
                }
            ),
        )

    def test_tmux_title_update_is_opt_in_and_targets_current_pane(self) -> None:
        completed = subprocess.CompletedProcess([], 0, "", "")
        with (
            patch.dict(os.environ, {"TMUX_PANE": "%7"}),
            patch(
                "scripts.codex_loop_runtime.subprocess.run",
                return_value=completed,
            ) as run,
        ):
            self.assertTrue(
                update_pane_title(
                    "coder",
                    "撰寫中：Issue #3",
                    enabled=True,
                    tmux_bin="/usr/bin/tmux",
                )
            )

        self.assertEqual(
            [
                "/usr/bin/tmux",
                "select-pane",
                "-t",
                "%7",
                "-T",
                "coder (撰寫中：Issue #3)",
            ],
            run.call_args.args[0],
        )

        with patch(
            "scripts.codex_loop_runtime.subprocess.run"
        ) as disabled_run:
            self.assertFalse(
                update_pane_title(
                    "coder",
                    "等待事件",
                    enabled=False,
                    tmux_bin="tmux",
                )
            )
        disabled_run.assert_not_called()


class ControlRotationTests(unittest.TestCase):
    def test_status_drains_busy_role_without_marking_workflow_blocked(self) -> None:
        state = snapshot(
            issues=[loop_object("issue", 3, "loop:coding")],
            pull_requests=[
                loop_object("pull_request", 62, "loop:needs-review")
            ],
        )

        draining = control_rotation_status(
            state,
            busy=["coder"],
            changes=["scripts/codex_loop_runtime.py"],
        )
        rotating = control_rotation_status(
            state,
            busy=[],
            changes=["scripts/codex_loop_runtime.py"],
        )

        self.assertEqual("draining", draining["health"])
        self.assertFalse(draining["blocking"])
        self.assertEqual([], rotating["busy_roles"])
        self.assertEqual("rotating", rotating["health"])
        self.assertIn(
            "scripts/codex_loop_runtime.py",
            str(rotating["attention"]),
        )
        self.assertEqual(
            "換代：等待目前 iteration 結束",
            operator_pane_status(draining),
        )
        self.assertEqual(
            "換代：同步 trusted runners",
            operator_pane_status(rotating),
        )

    def test_rotation_state_is_atomic_private_and_in_runtime_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime_dir = Path(temporary)
            path = rotation_state_path(runtime_dir)
            write_rotation_state(
                path,
                state="handoff",
                target_main="a" * 40,
            )

            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual("handoff", payload["state"])
            self.assertEqual("a" * 40, payload["target_main"])
            self.assertEqual(0o600, path.stat().st_mode & 0o777)
            self.assertEqual([], list(runtime_dir.glob("*.tmp")))


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

    def test_graphql_partial_response_errors_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "partial response errors"):
            normalize_snapshot(
                {
                    "errors": [{"message": "field failed"}],
                    "data": {
                        "repository": {},
                        "loopObjects": {"nodes": []},
                    },
                }
            )

    def test_incomplete_snapshot_fails_closed_to_dispatcher(self) -> None:
        self.assert_route(
            snapshot(snapshot_incomplete=["loop-objects"]),
            "dispatcher",
            "snapshot-incomplete",
        )

    def test_incomplete_snapshot_ack_is_held_until_snapshot_changes(self) -> None:
        decision = {
            "role": "dispatcher",
            "reason": "snapshot-incomplete",
        }
        previous = {
            "fingerprint": "same",
            "delivered_at": 10.0,
            "event_id": "event-1",
        }
        successful = {
            "last_event_id": "event-1",
            "last_exit_code": 0,
        }

        self.assertTrue(
            duplicate_delivery_is_suppressed(
                decision,
                "same",
                previous,
                successful,
                now=10_000.0,
                retry_seconds=30.0,
            )
        )
        self.assertFalse(
            duplicate_delivery_is_suppressed(
                decision,
                "same",
                previous,
                {**successful, "last_exit_code": 2},
                now=10_000.0,
                retry_seconds=30.0,
            )
        )
        self.assertFalse(
            duplicate_delivery_is_suppressed(
                {**decision, "reason": "reconcile-or-dispatch"},
                "same",
                previous,
                successful,
                now=10_000.0,
                retry_seconds=30.0,
            )
        )
        self.assertFalse(
            duplicate_delivery_is_suppressed(
                decision,
                "changed",
                previous,
                successful,
                now=11.0,
                retry_seconds=30.0,
            )
        )

    def test_current_main_gate_exit_quiesces_empty_loop(self) -> None:
        main_sha = "a" * 40
        state = snapshot(
            main_sha=main_sha,
            gate_exit={"gate": "W1-G2", "main_sha": main_sha},
        )

        self.assertEqual([], classify_snapshot(state))

    def test_stale_gate_exit_does_not_quiesce_empty_loop(self) -> None:
        state = snapshot(
            main_sha="b" * 40,
            gate_exit={"gate": "W1-G2", "main_sha": "a" * 40},
        )

        self.assert_route(state, "dispatcher", "reconcile-or-dispatch")

    def test_current_gate_exit_does_not_mask_work_in_progress(self) -> None:
        main_sha = "a" * 40
        state = snapshot(
            main_sha=main_sha,
            gate_exit={"gate": "W1-G2", "main_sha": main_sha},
            issues=[loop_object("issue", 3, "loop:queued")],
        )

        self.assert_route(state, "coder", "coding-work-available")

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
        main_sha = "a" * 40
        payload = {
            "data": {
                "repository": {
                    "defaultBranchRef": {
                        "name": "main",
                        "target": {"oid": main_sha},
                    },
                    "meta": {
                        "labels": {
                            "nodes": [{"name": "loop:paused"}],
                            "pageInfo": {"hasNextPage": False},
                        },
                        "comments": {
                            "nodes": [
                                {
                                    "body": (
                                        "<!-- emmet-loop:dispatcher:gate-exit:"
                                        f"W1-G2:main={main_sha} -->"
                                    ),
                                    "createdAt": "2026-07-15T00:00:00Z",
                                    "url": "https://example.test/comment",
                                    "viewerDidAuthor": True,
                                }
                            ],
                            "pageInfo": {"hasPreviousPage": True},
                        },
                    }
                },
                "loopObjects": {
                    "nodes": [
                        {
                            "__typename": "Issue",
                            "number": 3,
                            "updatedAt": "2026-07-15T00:00:00Z",
                            "labels": {
                                "nodes": [{"name": "loop:queued"}],
                                "pageInfo": {"hasNextPage": False},
                            },
                        },
                        {
                            "__typename": "Issue",
                            "number": 9,
                            "updatedAt": "2026-07-15T00:00:00Z",
                            "labels": {
                                "nodes": [{"name": "documentation"}],
                                "pageInfo": {"hasNextPage": False},
                            },
                        },
                    ],
                    "pageInfo": {"hasNextPage": False},
                },
            }
        }
        normalized = normalize_snapshot(payload)
        self.assertTrue(normalized["paused"])
        self.assertEqual(main_sha, normalized["main_sha"])
        self.assertEqual("W1-G2", normalized["gate_exit"]["gate"])
        self.assertTrue(normalized["meta_comments_truncated"])
        self.assertEqual([3], [item["number"] for item in normalized["issues"]])

    def test_normalization_marks_paginated_state_incomplete(self) -> None:
        payload = {
            "data": {
                "repository": {
                    "defaultBranchRef": {
                        "name": "main",
                        "target": {"oid": "a" * 40},
                    },
                    "meta": {
                        "labels": {
                            "nodes": [],
                            "pageInfo": {"hasNextPage": True},
                        },
                        "comments": {
                            "nodes": [],
                            "pageInfo": {"hasPreviousPage": False},
                        },
                    },
                },
                "loopObjects": {
                    "nodes": [
                        {
                            "__typename": "Issue",
                            "number": 3,
                            "updatedAt": "2026-07-15T00:00:00Z",
                            "labels": {
                                "nodes": [{"name": "loop:queued"}],
                                "pageInfo": {"hasNextPage": True},
                            },
                        }
                    ],
                    "pageInfo": {"hasNextPage": True},
                },
            }
        }

        normalized = normalize_snapshot(payload)

        self.assertEqual(
            ["issue#3-labels", "loop-objects", "meta-labels"],
            normalized["snapshot_incomplete"],
        )
        self.assert_route(
            normalized, "dispatcher", "snapshot-incomplete"
        )

    def test_missing_connection_page_info_fails_closed(self) -> None:
        payload = {
            "data": {
                "repository": {
                    "defaultBranchRef": {
                        "name": "main",
                        "target": {"oid": "a" * 40},
                    },
                    "meta": {
                        "labels": {"nodes": []},
                        "comments": {"nodes": []},
                    },
                },
                "loopObjects": {"nodes": []},
            }
        }

        normalized = normalize_snapshot(payload)

        self.assertEqual(
            ["loop-objects", "meta-comments", "meta-labels"],
            normalized["snapshot_incomplete"],
        )
        self.assert_route(
            normalized, "dispatcher", "snapshot-incomplete"
        )

    def test_paginated_labels_fail_closed_before_loop_label_filter(self) -> None:
        payload = {
            "data": {
                "repository": {
                    "defaultBranchRef": {
                        "name": "main",
                        "target": {"oid": "a" * 40},
                    },
                    "meta": {
                        "labels": {
                            "nodes": [],
                            "pageInfo": {"hasNextPage": False},
                        },
                        "comments": {
                            "nodes": [],
                            "pageInfo": {"hasPreviousPage": False},
                        },
                    },
                },
                "loopObjects": {
                    "nodes": [
                        {
                            "__typename": "Issue",
                            "number": 3,
                            "updatedAt": "2026-07-15T00:00:00Z",
                            "labels": {
                                "nodes": [{"name": "documentation"}],
                                "pageInfo": {"hasNextPage": True},
                            },
                        }
                    ],
                    "pageInfo": {"hasNextPage": False},
                },
            }
        }

        normalized = normalize_snapshot(payload)

        self.assertEqual([], normalized["issues"])
        self.assertEqual(
            ["issue#3-labels"], normalized["snapshot_incomplete"]
        )
        self.assert_route(
            normalized, "dispatcher", "snapshot-incomplete"
        )

    def test_truncated_meta_history_does_not_block_normal_wip_routing(self) -> None:
        payload = {
            "data": {
                "repository": {
                    "defaultBranchRef": {
                        "name": "main",
                        "target": {"oid": "a" * 40},
                    },
                    "meta": {
                        "labels": {
                            "nodes": [],
                            "pageInfo": {"hasNextPage": False},
                        },
                        "comments": {
                            "nodes": [],
                            "pageInfo": {"hasPreviousPage": True},
                        },
                    },
                },
                "loopObjects": {
                    "nodes": [
                        {
                            "__typename": "Issue",
                            "number": 3,
                            "updatedAt": "2026-07-15T00:00:00Z",
                            "labels": {
                                "nodes": [{"name": "loop:coding"}],
                                "pageInfo": {"hasNextPage": False},
                            },
                        }
                    ],
                    "pageInfo": {"hasNextPage": False},
                },
            }
        }

        normalized = normalize_snapshot(payload)

        self.assertTrue(normalized["meta_comments_truncated"])
        self.assertEqual([], normalized["snapshot_incomplete"])
        self.assert_route(normalized, "coder", "coding-work-available")

    def test_normalization_ignores_gate_exit_for_stale_main(self) -> None:
        current_main = "b" * 40
        payload = {
            "data": {
                "repository": {
                    "defaultBranchRef": {
                        "name": "main",
                        "target": {"oid": current_main},
                    },
                    "meta": {
                        "labels": {
                            "nodes": [],
                            "pageInfo": {"hasNextPage": False},
                        },
                        "comments": {
                            "nodes": [
                                {
                                    "body": (
                                        "<!-- emmet-loop:dispatcher:gate-exit:"
                                        f"W1-G2:main={'a' * 40} -->"
                                    ),
                                    "createdAt": "2026-07-15T00:00:00Z",
                                    "url": "https://example.test/stale",
                                    "viewerDidAuthor": True,
                                },
                                {
                                    "body": (
                                        "<!-- emmet-loop:dispatcher:gate-exit:"
                                        f"W1-G2:main={current_main} -->"
                                    ),
                                    "createdAt": "2026-07-15T00:01:00Z",
                                    "url": "https://example.test/untrusted",
                                    "viewerDidAuthor": False,
                                }
                            ],
                            "pageInfo": {"hasPreviousPage": False},
                        },
                    },
                },
                "loopObjects": {
                    "nodes": [],
                    "pageInfo": {"hasNextPage": False},
                },
            }
        }

        normalized = normalize_snapshot(payload)

        self.assertEqual(current_main, normalized["main_sha"])
        self.assertIsNone(normalized["gate_exit"])
        self.assert_route(
            normalized, "dispatcher", "reconcile-or-dispatch"
        )

    def test_github_poll_uses_supported_multi_label_or_qualifier(self) -> None:
        payload = {
            "data": {
                "repository": {
                    "defaultBranchRef": {
                        "name": "main",
                        "target": {"oid": "a" * 40},
                    },
                    "meta": {
                        "labels": {
                            "nodes": [],
                            "pageInfo": {"hasNextPage": False},
                        },
                        "comments": {
                            "nodes": [],
                            "pageInfo": {"hasPreviousPage": False},
                        },
                    },
                },
                "loopObjects": {
                    "nodes": [],
                    "pageInfo": {"hasNextPage": False},
                },
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
        query_argument = next(
            argument for argument in command if argument.startswith("query=")
        )
        self.assertIn("defaultBranchRef", query_argument)
        self.assertIn("comments(last: 100)", query_argument)
        self.assertIn("pageInfo { hasNextPage }", query_argument)

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
        decision = pull_request_disappearance_decision(previous, current)
        self.assertIsNotNone(decision)
        self.assertEqual(
            52,
            decision["transition"]["disappeared_pull_requests"][0]["number"],
        )


class OperatorStatusTests(unittest.TestCase):
    def test_current_gate_exit_waits_for_user_without_alert(self) -> None:
        main_sha = "a" * 40
        state = snapshot(
            main_sha=main_sha,
            gate_exit={"gate": "W1-G2", "main_sha": main_sha},
        )

        status = describe_operator_status(
            state,
            decisions=classify_snapshot(state),
            busy=[],
        )

        self.assertEqual("awaiting-user", status["health"])
        self.assertFalse(status["blocking"])
        self.assertEqual("user", status["owner"])
        self.assertEqual(
            "gate-transition-awaiting-user", status["reason"]
        )
        self.assertIn("W1-G2", status["current"])
        self.assertIn(main_sha, status["current"])
        self.assertIsNone(build_operator_alert(status))

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

    def test_incomplete_snapshot_requires_targeted_read_before_mutation(self) -> None:
        state = snapshot(snapshot_incomplete=["loop-objects"])
        status = describe_operator_status(
            state,
            decisions=classify_snapshot(state),
            busy=[],
        )

        self.assertEqual("blocked", status["health"])
        self.assertTrue(status["blocking"])
        self.assertEqual("snapshot-incomplete", status["reason"])
        self.assertIn("bounded live query", status["next"])
        self.assertIsNotNone(build_operator_alert(status))

    def test_incomplete_snapshot_iteration_is_not_a_durable_progress_stall(
        self,
    ) -> None:
        state = snapshot(snapshot_incomplete=["loop-objects"])
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

    def test_empty_dispatcher_no_op_without_checkpoint_is_stalled(self) -> None:
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

        self.assertIsNotNone(stalled)

    def test_gate_exit_checkpoint_changes_workflow_fingerprint(self) -> None:
        main_sha = "a" * 40
        before = snapshot(main_sha=main_sha)
        after = snapshot(
            main_sha=main_sha,
            gate_exit={"gate": "W1-G2", "main_sha": main_sha},
        )

        self.assertNotEqual(
            workflow_state_fingerprint(before),
            workflow_state_fingerprint(after),
        )

    def test_mergeability_update_routes_a_fresh_event_without_stall(self) -> None:
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

        self.assertNotEqual(
            workflow_state_fingerprint(before),
            workflow_state_fingerprint(after),
        )
        self.assertIsNone(stalled)

    def test_completed_event_history_keeps_canonical_stall_visible(self) -> None:
        state = snapshot(
            issues=[loop_object("issue", 3, "loop:queued")]
        )
        decision = classify_snapshot(state)[0]
        stalled = detect_stalled_iteration(
            state,
            deliveries={
                "coder": {
                    "event_id": "owner-event",
                    "reason": decision["reason"],
                    "state_fingerprint": workflow_state_fingerprint(state),
                }
            },
            agent_states={
                "coder": {
                    "state": "waiting",
                    "last_event_id": "alert-event",
                    "last_exit_code": 0,
                    "completed_events": [
                        {
                            "event_id": "owner-event",
                            "reason": decision["reason"],
                            "exit_code": 0,
                            "finished_at": "2026-07-15T05:05:02Z",
                        },
                        {
                            "event_id": "alert-event",
                            "reason": "operator-stall-reconciliation",
                            "exit_code": 0,
                            "finished_at": "2026-07-15T05:05:03Z",
                        },
                    ],
                }
            },
        )

        self.assertIsNotNone(stalled)
        self.assertEqual("owner-event", stalled["event_id"])


class OperatorAlertTests(unittest.TestCase):
    def stalled_status(self) -> tuple[dict[str, object], dict[str, object]]:
        state = snapshot(
            issues=[loop_object("issue", 3, "loop:queued")]
        )
        status = describe_operator_status(
            state,
            decisions=classify_snapshot(state),
            busy=[],
            stalled={
                "role": "coder",
                "event_id": "event-1",
                "exit_code": 0,
            },
        )
        return state, status

    def test_blocker_alert_is_stable_deduplicated_and_resolved(self) -> None:
        state, stalled = self.stalled_status()
        alert = build_operator_alert(stalled)

        self.assertIsNotNone(alert)
        self.assertEqual("warning", alert["severity"])
        self.assertEqual("coder", alert["affected_role"])
        self.assertFalse(alert["requires_user"])

        transitions, active = transition_operator_alert(None, stalled)
        self.assertEqual(
            ["operator-alert"], [item["result"] for item in transitions]
        )
        repeated, active = transition_operator_alert(active, stalled)
        self.assertEqual([], repeated)

        running = describe_operator_status(
            state,
            decisions=[],
            busy=["dispatcher"],
        )
        held, active = transition_operator_alert(active, running)
        self.assertEqual([], held)
        self.assertIsNotNone(active)
        same_state_healthy = describe_operator_status(
            state,
            decisions=classify_snapshot(state),
            busy=[],
        )
        held, active = transition_operator_alert(
            active,
            same_state_healthy,
        )
        self.assertEqual([], held)
        self.assertIsNotNone(active)

        advanced = snapshot(
            issues=[loop_object("issue", 3, "loop:coding")]
        )
        healthy = describe_operator_status(
            advanced,
            decisions=classify_snapshot(advanced),
            busy=[],
        )
        resolved, active = transition_operator_alert(active, healthy)
        self.assertEqual(
            ["operator-resolved"], [item["result"] for item in resolved]
        )
        self.assertIsNone(active)

    def test_snapshot_alert_resolves_when_poll_becomes_complete(self) -> None:
        incomplete = snapshot(snapshot_incomplete=["loop-objects"])
        blocked = describe_operator_status(
            incomplete,
            decisions=classify_snapshot(incomplete),
            busy=[],
        )
        transitions, active = transition_operator_alert(None, blocked)
        self.assertEqual(["operator-alert"], [
            item["result"] for item in transitions
        ])
        self.assertEqual(
            workflow_state_fingerprint(incomplete),
            active["workflow_fingerprint"],
        )

        complete = snapshot()
        healthy = describe_operator_status(
            complete,
            decisions=classify_snapshot(complete),
            busy=[],
        )
        transitions, active = transition_operator_alert(active, healthy)

        self.assertEqual(["operator-resolved"], [
            item["result"] for item in transitions
        ])
        self.assertIsNone(active)

    def test_delivery_failure_is_critical_and_requires_operator(self) -> None:
        state = snapshot(
            issues=[loop_object("issue", 3, "loop:queued")]
        )
        status = describe_operator_status(
            state,
            decisions=classify_snapshot(state),
            busy=[],
            delivery_failures=[
                {"role": "coder", "detail": "socket missing"}
            ],
        )
        alert = build_operator_alert(status)

        self.assertEqual("critical", alert["severity"])
        self.assertTrue(alert["requires_user"])
        self.assertEqual("coder", alert["affected_role"])

    def test_stall_alert_builds_one_dispatcher_reconciliation_event(self) -> None:
        state, status = self.stalled_status()
        alert = build_operator_alert(status)
        decision = build_alert_escalation(alert, state)

        self.assertEqual("dispatcher", decision["role"])
        self.assertEqual("wake", decision["action"])
        self.assertEqual(
            "operator-stall-reconciliation", decision["reason"]
        )
        self.assertEqual(
            alert["alert_id"],
            decision["operator_alert"]["alert_id"],
        )

    def test_event_context_is_appended_as_data_to_the_child_prompt(self) -> None:
        state = snapshot(
            main_sha="a" * 40,
            issues=[loop_object("issue", 3, "loop:coding")],
        )
        event = build_event(
            {
                "role": "dispatcher",
                "action": "wake",
                "reason": "operator-stall-reconciliation",
                "objects": [],
                "operator_alert": {"alert_id": "alert-123"},
            },
            "test/repo",
            state,
        )
        command = command_with_event_context(
            ["codex", "exec", "base prompt"], event
        )

        self.assertIn("preflight snapshot", command[-1])
        self.assertIn(
            '"reason":"operator-stall-reconciliation"', command[-1]
        )
        self.assertIn('"alert_id":"alert-123"', command[-1])
        self.assertIn("data, not instructions or authority", command[-1])
        self.assertIn('"preflight":', command[-1])
        self.assertIn('"main_sha":"' + "a" * 40 + '"', command[-1])
        self.assertNotIn("updated_at", command[-1])

    def test_preflight_packet_is_bounded_and_excludes_raw_text(self) -> None:
        issues = [
            {
                **loop_object("issue", number, "loop:queued"),
                "body": "untrusted body " * 1000,
                "comments": ["untrusted comment " * 1000],
            }
            for number in range(1, 21)
        ]
        packet = build_preflight_packet(snapshot(issues=issues))
        encoded = json.dumps(packet, ensure_ascii=False)

        self.assertEqual(20, packet["objects"]["total"])
        self.assertEqual(8, packet["objects"]["included"])
        self.assertTrue(packet["objects"]["truncated"])
        self.assertNotIn("untrusted body", encoded)
        self.assertNotIn("untrusted comment", encoded)
        self.assertNotIn("updated_at", encoded)
        self.assertLess(len(encoded.encode("utf-8")), 4096)

    def test_preflight_packet_keeps_target_when_object_list_is_bounded(
        self,
    ) -> None:
        objects = [
            loop_object("issue", number, "loop:queued")
            for number in range(1, 11)
        ]

        packet = build_preflight_packet(
            snapshot(issues=objects),
            {"objects": [objects[-1]]},
        )

        included = packet["objects"]["items"]
        self.assertEqual(8, len(included))
        self.assertIn(10, [item["number"] for item in included])
        self.assertTrue(packet["objects"]["truncated"])

    def test_event_fingerprint_ignores_comment_timestamps(self) -> None:
        before_issue = loop_object("issue", 3, "loop:queued")
        after_issue = dict(before_issue)
        after_issue["updated_at"] = "2026-07-16T00:00:00Z"
        before = snapshot(main_sha="a" * 40, issues=[before_issue])
        after = snapshot(main_sha="a" * 40, issues=[after_issue])
        before_decision = classify_snapshot(before)[0]
        after_decision = classify_snapshot(after)[0]

        self.assertEqual(
            event_fingerprint(before_decision, before),
            event_fingerprint(after_decision, after),
        )
        changed = snapshot(
            main_sha="a" * 40,
            issues=[loop_object("issue", 3, "loop:coding")],
        )
        self.assertNotEqual(
            event_fingerprint(before_decision, before),
            event_fingerprint(classify_snapshot(changed)[0], changed),
        )

    def test_event_fingerprint_tracks_snapshot_completeness(self) -> None:
        complete = snapshot(
            issues=[loop_object("issue", 3, "loop:queued")]
        )
        incomplete = snapshot(
            issues=[loop_object("issue", 3, "loop:queued")],
            snapshot_incomplete=["loop-objects"],
        )
        decision = classify_snapshot(complete)[0]

        self.assertNotEqual(
            event_fingerprint(decision, complete),
            event_fingerprint(decision, incomplete),
        )


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
        self,
        max_events: int = 1,
        sleep_seconds: str = "0",
        *,
        profile: str | None = None,
        codex_home: Path | None = None,
    ) -> subprocess.Popen[str]:
        environment = os.environ.copy()
        environment["FAKE_CODEX_CAPTURE"] = str(self.capture)
        environment["FAKE_CODEX_SLEEP"] = sleep_seconds
        if codex_home is not None:
            environment["CODEX_HOME"] = str(codex_home)
        command = [
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
        ]
        if profile is not None:
            command.extend(["--profile", profile])
        process = subprocess.Popen(
            command,
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

    def test_named_profile_is_revalidated_before_each_wake(self) -> None:
        codex_home = self.base / "codex-home"
        codex_home.mkdir()
        profile = codex_home / "volatile.config.toml"
        profile.write_text(
            'model = "gpt-5.6-sol"\nmodel_reasoning_effort = "high"\n',
            encoding="utf-8",
        )
        process = self.start_agent(
            profile="volatile",
            codex_home=codex_home,
        )
        profile.unlink()

        event = build_event(
            {
                "role": "dispatcher",
                "action": "wake",
                "reason": "test-profile-revalidation",
                "objects": [],
            },
            "test/repo",
        )
        notify_agent(self.runtime_dir, event)
        stdout, stderr = process.communicate(timeout=10)

        self.assertEqual(0, process.returncode, stderr)
        self.assertIn("找不到 Codex profile", stderr)
        self.assertIn('"exit_code": 2', stdout)
        self.assertFalse(self.capture.exists())

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
                "repository": {
                    "defaultBranchRef": {
                        "name": "main",
                        "target": {"oid": "a" * 40},
                    },
                    "meta": {
                        "labels": {
                            "nodes": [],
                            "pageInfo": {"hasNextPage": False},
                        },
                        "comments": {
                            "nodes": [],
                            "pageInfo": {"hasPreviousPage": False},
                        },
                    },
                },
                "loopObjects": {
                    "nodes": [],
                    "pageInfo": {"hasNextPage": False},
                },
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
        arguments = json.loads(self.capture.read_text(encoding="utf-8"))
        self.assertIn("--model", arguments)
        self.assertIn("gpt-5.6-sol", arguments)
        self.assertIn('model_reasoning_effort="high"', arguments)
        self.assertIn('"preflight":', arguments[-1])

    def test_event_manager_does_not_wake_for_current_gate_exit(self) -> None:
        main_sha = subprocess.run(
            ["git", "-C", str(self.worktree), "rev-parse", "HEAD"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        payload = {
            "data": {
                "repository": {
                    "defaultBranchRef": {
                        "name": "main",
                        "target": {"oid": main_sha},
                    },
                    "meta": {
                        "labels": {
                            "nodes": [],
                            "pageInfo": {"hasNextPage": False},
                        },
                        "comments": {
                            "nodes": [
                                {
                                    "body": (
                                        "<!-- emmet-loop:dispatcher:gate-exit:"
                                        f"W1-G2:main={main_sha} -->"
                                    ),
                                    "createdAt": "2026-07-15T00:00:00Z",
                                    "url": "https://example.test/gate-exit",
                                    "viewerDidAuthor": True,
                                }
                            ],
                            "pageInfo": {"hasPreviousPage": False},
                        },
                    },
                },
                "loopObjects": {
                    "nodes": [],
                    "pageInfo": {"hasNextPage": False},
                },
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

        self.assertEqual(0, manager.returncode, manager.stderr)
        self.assertNotIn('"result": "notified"', manager.stdout)
        self.assertIn('"health": "awaiting-user"', manager.stdout)
        self.assertIn(
            '"reason": "gate-transition-awaiting-user"',
            manager.stdout,
        )
        self.assertIn('"decisions": []', manager.stdout)
        self.assertFalse(self.capture.exists())

    def test_event_manager_hands_off_idle_control_drift_without_delivery(
        self,
    ) -> None:
        options = runtime_parser().parse_args(
            [
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
            ]
        )
        current = snapshot()
        changes = ["scripts/codex_loop_runtime.py"]
        with (
            patch(
                "scripts.codex_loop_runtime._validate_manager_workdir",
                return_value=(self.worktree, self.worktree / ".git"),
            ),
            patch(
                "scripts.codex_loop_runtime.resolve_executable",
                return_value=str(self.fake_gh),
            ),
            patch(
                "scripts.codex_loop_runtime.resolve_repo",
                return_value="test/repo",
            ),
            patch(
                "scripts.codex_loop_runtime.poll_github",
                return_value=current,
            ),
            patch(
                "scripts.codex_loop_runtime.adapter.refresh_trusted_main"
            ),
            patch(
                "scripts.codex_loop_runtime.adapter.control_input_changes",
                return_value=changes,
            ),
            patch(
                "scripts.codex_loop_runtime.start_control_rotation",
                return_value=4321,
            ) as rotate,
            patch("scripts.codex_loop_runtime.notify_agent") as notify,
        ):
            self.assertEqual(0, run_events(options))

        rotate.assert_called_once()
        notify.assert_not_called()

    def test_event_manager_drains_busy_control_drift_without_rotation(
        self,
    ) -> None:
        options = runtime_parser().parse_args(
            [
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
            ]
        )
        current = snapshot()
        with (
            patch(
                "scripts.codex_loop_runtime._validate_manager_workdir",
                return_value=(self.worktree, self.worktree / ".git"),
            ),
            patch(
                "scripts.codex_loop_runtime.resolve_executable",
                return_value=str(self.fake_gh),
            ),
            patch(
                "scripts.codex_loop_runtime.resolve_repo",
                return_value="test/repo",
            ),
            patch(
                "scripts.codex_loop_runtime.poll_github",
                return_value=current,
            ),
            patch(
                "scripts.codex_loop_runtime.busy_roles",
                return_value=["coder"],
            ),
            patch(
                "scripts.codex_loop_runtime.adapter.refresh_trusted_main"
            ),
            patch(
                "scripts.codex_loop_runtime.adapter.control_input_changes",
                return_value=["scripts/codex_loop_runtime.py"],
            ),
            patch(
                "scripts.codex_loop_runtime.start_control_rotation"
            ) as rotate,
            patch("scripts.codex_loop_runtime.notify_agent") as notify,
        ):
            self.assertEqual(0, run_events(options))

        rotate.assert_not_called()
        notify.assert_not_called()

    def test_event_manager_reports_and_escalates_no_progress_once(self) -> None:
        agent = self.start_agent(max_events=2)
        payload = {
            "data": {
                "repository": {
                    "defaultBranchRef": {
                        "name": "main",
                        "target": {"oid": "b" * 40},
                    },
                    "meta": {
                        "labels": {
                            "nodes": [],
                            "pageInfo": {"hasNextPage": False},
                        },
                        "comments": {
                            "nodes": [],
                            "pageInfo": {"hasPreviousPage": False},
                        },
                    },
                },
                "loopObjects": {
                    "nodes": [
                        {
                            "__typename": "Issue",
                            "number": 3,
                            "updatedAt": "2026-07-15T00:00:00Z",
                            "labels": {
                                "nodes": [{"name": "loop:coding"}],
                                "pageInfo": {"hasNextPage": False},
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
                                "nodes": [{"name": "loop:approved"}],
                                "pageInfo": {"hasNextPage": False},
                            },
                        },
                    ],
                    "pageInfo": {"hasNextPage": False},
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
                "0.1",
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
                if record.get("result") == "operator-alert":
                    time.sleep(0.25)
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
        alerts = [
            record
            for record in records
            if record.get("result") == "operator-alert"
        ]
        self.assertEqual(1, len(alerts), records)
        self.assertEqual(
            "no-durable-progress-after-iteration",
            alerts[0]["blocker"],
        )
        notified_reasons = [
            record.get("reason")
            for record in records
            if record.get("result") == "notified"
        ]
        self.assertIn(
            "operator-stall-reconciliation",
            notified_reasons,
        )
        self.assertEqual(
            1,
            notified_reasons.count("operator-stall-reconciliation"),
        )
        self.assertEqual(
            1,
            notified_reasons.count("approved-pull-request"),
        )
        self.assertFalse(
            any(
                record.get("result") == "delivery-failed"
                for record in records
            ),
            records,
        )

        self.assertIn("\aLOOP ALERT", manager_stderr)
        self.assertEqual(0, agent.returncode, agent_stderr)
        self.assertEqual(
            2,
            agent_stdout.count('"result": "iteration-finished"'),
        )
        arguments = json.loads(self.capture.read_text(encoding="utf-8"))
        self.assertIn(
            "operator-stall-reconciliation",
            arguments[-1],
        )
        self.assertIn("operator_alert", arguments[-1])

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
