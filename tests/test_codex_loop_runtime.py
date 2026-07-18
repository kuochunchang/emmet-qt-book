from __future__ import annotations

import contextlib
import io
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
from unittest.mock import Mock, patch

from scripts.codex_loop_runtime import (
    IterationCapture,
    agent_event_pane_status,
    apply_trusted_gate,
    apply_usage_budget,
    build_alert_escalation,
    build_event,
    build_operator_alert,
    build_preflight_packet,
    classify_snapshot,
    command_with_event_context,
    control_rotation_status,
    daily_token_usage,
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
    read_rotation_state,
    rotation_state_path,
    run_events,
    run_inspect_event,
    select_poll_decisions,
    socket_path,
    transition_operator_alert,
    update_pane_title,
    wait_for_control_rotation_handoff,
    write_rotation_state,
    workflow_state_fingerprint,
    parser as runtime_parser,
)


ROOT = Path(__file__).resolve().parents[1]


def meta_body(gate: str = "W1-G2") -> str:
    return f"## 目前寫作狀態\n\n- Active gate：`{gate}`（已生效）"


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


def gate_checkpoint(
    main_sha: str,
    *,
    gate: str = "W1-G2",
    checkpoint_id: int = 101,
) -> dict[str, object]:
    return {
        "gate": gate,
        "main_sha": main_sha,
        "checkpoint_id": checkpoint_id,
    }


def audit_verdict(
    main_sha: str,
    verdict: str,
    *,
    gate: str = "W1-G2",
    checkpoint_id: int = 101,
    comment_id: int = 102,
) -> dict[str, object]:
    return {
        "gate": gate,
        "main_sha": main_sha,
        "checkpoint_id": checkpoint_id,
        "verdict": verdict,
        "comment_id": comment_id,
    }


def gate_audit_report_body(
    main_sha: str,
    verdict: str,
    *,
    gate: str = "W1-G2",
    checkpoint_id: int = 101,
    extra_body: str = "",
) -> str:
    exit_criteria = {
        "not-ready": "fail",
        "unknown": "unknown",
        "exit-ready": "pass",
    }[verdict]
    human_decision = "no" if verdict == "not-ready" else "yes"
    extra = f"\n{extra_body.rstrip()}\n" if extra_body else "\n"
    return (
        "<!-- emmet-loop:gate-auditor:audit:v1:"
        f"gate={gate}:main={main_sha}:checkpoint={checkpoint_id}:"
        f"verdict={verdict} -->\n\n"
        "skill: $emmet-loop-gate-auditor\n"
        f"verdict: {verdict}\n"
        f"exit_criteria: {exit_criteria}\n"
        "governance_consistency: consistent\n"
        "active_gate_transitioned: no\n"
        f"audited_gate: {gate}\n"
        f"observed_active_gate: {gate}\n"
        "successor_gate: unknown\n"
        f"main_sha: {main_sha}\n"
        "audit_time: 2026-07-17T00:00:00+08:00\n"
        f"human_decision_required: {human_decision}\n"
        "local_cache_refresh: git-fetch\n"
        "audit_mutations: none\n"
        "publication_mutation: meta-comment-only\n"
        "mutations: meta-comment-only\n"
        f"{extra}\n— Gate Auditor"
    )


def snapshot(
    *,
    paused: bool = False,
    main_sha: str | None = None,
    active_gate: str = "W1-G2",
    gate_exit: dict[str, object] | None = None,
    gate_audit: dict[str, object] | None = None,
    issues: list[dict[str, object]] | None = None,
    pull_requests: list[dict[str, object]] | None = None,
    snapshot_incomplete: list[str] | None = None,
) -> dict[str, object]:
    return {
        "paused": paused,
        "main_sha": main_sha,
        "active_gate": active_gate,
        "meta_active_gate": active_gate,
        "main_active_gate": active_gate,
        "governance_consistent": True,
        "gate_exit": gate_exit,
        "gate_audit": gate_audit,
        "meta_comments_truncated": False,
        "snapshot_incomplete": snapshot_incomplete or [],
        "issues": issues or [],
        "pull_requests": pull_requests or [],
    }


def graphql_payload(
    main_sha: str,
    comments: list[dict[str, object]],
    *,
    active_gate: str = "W1-G2",
) -> dict[str, object]:
    return {
        "data": {
            "repository": {
                "defaultBranchRef": {
                    "name": "main",
                    "target": {"oid": main_sha},
                },
                "meta": {
                    "body": meta_body(active_gate),
                    "labels": {
                        "nodes": [],
                        "pageInfo": {"hasNextPage": False},
                    },
                    "comments": {
                        "nodes": comments,
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


class IterationCaptureTests(unittest.TestCase):
    def test_valid_outcome_and_usage_are_captured(self) -> None:
        capture = IterationCapture()
        capture.observe(
            {
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": (
                        "summary\n"
                        'LOOP_OUTCOME {"role":"dispatcher",'
                        '"outcome":"terminal-noop",'
                        '"result":"nothing-to-dispatch","mutations":[]}'
                    ),
                },
            }
        )
        capture.observe(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 123,
                    "cached_input_tokens": 100,
                    "output_tokens": 45,
                },
            }
        )

        completion = capture.completion("dispatcher", 0)

        self.assertEqual("terminal-noop", completion["outcome"])
        self.assertEqual("nothing-to-dispatch", completion["result"])
        self.assertEqual(123, completion["usage"]["input_tokens"])

    def test_missing_or_wrong_role_outcome_is_invalid(self) -> None:
        capture = IterationCapture()
        capture.observe(
            {
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": (
                        'LOOP_OUTCOME {"role":"coder",'
                        '"outcome":"terminal-noop","result":"done",'
                        '"mutations":[]}'
                    ),
                },
            }
        )

        completion = capture.completion("dispatcher", 0)

        self.assertEqual("invalid", completion["outcome"])
        self.assertEqual("invalid-outcome-role", completion["result"])


class TrustedGateAndBudgetTests(unittest.TestCase):
    def test_inspect_event_returns_only_bounded_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime_dir = Path(temporary)
            (runtime_dir / "dispatcher.lock").write_text(
                json.dumps(
                    {
                        "raw_log": "/private/huge.jsonl",
                        "completed_events": [
                            {
                                "event_id": "event-1",
                                "reason": "reconcile-or-dispatch",
                                "exit_code": 0,
                                "outcome": "terminal-noop",
                                "result": "nothing-to-dispatch",
                                "mutations": [],
                                "usage": {
                                    "input_tokens": 100,
                                    "output_tokens": 10,
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            options = runtime_parser().parse_args(
                [
                    "inspect-event",
                    "--runtime-dir",
                    str(runtime_dir),
                    "--event-id",
                    "event-1",
                ]
            )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = run_inspect_event(options)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(0, result)
        self.assertTrue(payload["found"])
        self.assertEqual("nothing-to-dispatch", payload["completion"]["result"])
        self.assertNotIn("raw_log", stdout.getvalue())

    def test_trusted_gate_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "AGENTS.md").write_text(
                "> Active gate：`W1-final`\n", encoding="utf-8"
            )
            (root / "docs").mkdir()
            (root / "docs/curriculum.md").write_text(
                "> 目前 active gate：`W1-final`\n", encoding="utf-8"
            )

            state = apply_trusted_gate(snapshot(active_gate="W1-G4"), root)

        self.assertFalse(state["governance_consistent"])
        self.assertEqual("W1-final", state["main_active_gate"])
        self.assertEqual("W1-G4", state["meta_active_gate"])
        self.assertEqual([], classify_snapshot(state))
        status = describe_operator_status(state, decisions=[], busy=[])
        self.assertEqual("governance-inconsistent", status["reason"])
        self.assertEqual("user", status["owner"])

    def test_daily_budget_sums_usage_and_stops_routing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime_dir = Path(temporary)
            (runtime_dir / "usage.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "recorded_at": "2026-07-18T00:00:00Z",
                                "usage": {
                                    "input_tokens": 700,
                                    "output_tokens": 100,
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "recorded_at": "2026-07-17T23:59:59Z",
                                "usage": {
                                    "input_tokens": 9999,
                                    "output_tokens": 9999,
                                },
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertEqual(
                800,
                daily_token_usage(runtime_dir, utc_date="2026-07-18"),
            )
            with patch(
                "scripts.codex_loop_runtime.time.strftime",
                return_value="2026-07-18",
            ):
                state = apply_usage_budget(
                    snapshot(),
                    runtime_dir=runtime_dir,
                    daily_token_budget=800,
                )

        self.assertTrue(state["usage_budget_exhausted"])
        self.assertEqual([], classify_snapshot(state))
        status = describe_operator_status(state, decisions=[], busy=[])
        self.assertEqual("usage-budget-exhausted", status["reason"])
        self.assertEqual("user", status["owner"])


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
        self.assertEqual(
            "Gate 稽核中",
            agent_event_pane_status({"reason": "gate-audit-requested"}),
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
            "換代：同步 control／runners",
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

    def test_parent_holds_events_lock_until_rotator_acknowledges(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = rotation_state_path(Path(temporary))
            process = Mock(pid=4321)
            process.poll.return_value = None
            write_rotation_state(
                path,
                state="waiting-for-manager",
                rotator_pid=4321,
            )

            wait_for_control_rotation_handoff(process, path)

            process.poll.assert_not_called()
            process.terminate.assert_not_called()

    def test_handoff_timeout_stops_rotator_and_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = rotation_state_path(Path(temporary))
            process = Mock(pid=4321)
            process.poll.return_value = None
            process.wait.return_value = 0
            write_rotation_state(path, state="requested")

            with self.assertRaisesRegex(ValueError, "handoff timeout"):
                wait_for_control_rotation_handoff(
                    process,
                    path,
                    timeout_seconds=0.001,
                )

            process.terminate.assert_called_once_with()
            self.assertEqual("failed", read_rotation_state(path)["state"])


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

    def test_hyphenated_gate_auditor_profile_uses_safe_argparse_dest(
        self,
    ) -> None:
        options = runtime_parser().parse_args(
            [
                "events",
                "--rotation-gate-auditor-profile",
                "loop-gate-auditor",
            ]
        )

        self.assertEqual(
            "loop-gate-auditor",
            options.rotation_gate_auditor_profile,
        )

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

    def test_current_main_gate_exit_wakes_gate_auditor(self) -> None:
        main_sha = "a" * 40
        state = snapshot(
            main_sha=main_sha,
            gate_exit=gate_checkpoint(main_sha),
        )

        self.assert_route(state, "gate-auditor", "gate-audit-requested")

    def test_matching_exit_ready_audit_quiesces_empty_loop(self) -> None:
        main_sha = "a" * 40
        state = snapshot(
            main_sha=main_sha,
            gate_exit=gate_checkpoint(main_sha),
            gate_audit=audit_verdict(main_sha, "exit-ready"),
        )

        self.assertEqual([], classify_snapshot(state))

    def test_matching_not_ready_audit_returns_to_dispatcher(self) -> None:
        main_sha = "a" * 40
        self.assert_route(
            snapshot(
                main_sha=main_sha,
                gate_exit=gate_checkpoint(main_sha),
                gate_audit=audit_verdict(main_sha, "not-ready"),
            ),
            "dispatcher",
            "gate-audit-not-ready",
        )

    def test_matching_unknown_audit_stops_role_wakes(self) -> None:
        main_sha = "a" * 40
        state = snapshot(
            main_sha=main_sha,
            gate_exit=gate_checkpoint(main_sha),
            gate_audit=audit_verdict(main_sha, "unknown"),
        )

        self.assertEqual([], classify_snapshot(state))

    def test_mismatched_audit_does_not_satisfy_checkpoint(self) -> None:
        main_sha = "a" * 40
        state = snapshot(
            main_sha=main_sha,
            gate_exit=gate_checkpoint(main_sha, checkpoint_id=202),
            gate_audit=audit_verdict(
                main_sha, "exit-ready", checkpoint_id=101
            ),
        )

        self.assert_route(state, "gate-auditor", "gate-audit-requested")

    def test_stale_gate_exit_does_not_quiesce_empty_loop(self) -> None:
        state = snapshot(
            main_sha="b" * 40,
            gate_exit=gate_checkpoint("a" * 40),
        )

        self.assert_route(state, "dispatcher", "reconcile-or-dispatch")

    def test_current_gate_exit_does_not_mask_work_in_progress(self) -> None:
        main_sha = "a" * 40
        state = snapshot(
            main_sha=main_sha,
            gate_exit=gate_checkpoint(main_sha),
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
            ["dispatcher", "coder", "reviewer", "gate-auditor"],
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
                        "body": meta_body(),
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
                                        "\n\n— Dispatcher"
                                    ),
                                    "createdAt": "2026-07-15T00:00:00Z",
                                    "fullDatabaseId": "4998095350",
                                    "isMinimized": False,
                                    "lastEditedAt": None,
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
        self.assertEqual(
            4998095350, normalized["gate_exit"]["checkpoint_id"]
        )
        self.assertIsNone(normalized["gate_audit"])
        self.assertTrue(normalized["meta_comments_truncated"])
        self.assertEqual([3], [item["number"] for item in normalized["issues"]])

    def test_normalization_binds_later_trusted_audit_to_exact_checkpoint(
        self,
    ) -> None:
        main_sha = "a" * 40
        checkpoint_marker = (
            "<!-- emmet-loop:dispatcher:gate-exit:"
            f"W1-G2:main={main_sha} -->\n\n— Dispatcher"
        )
        audit_marker = gate_audit_report_body(
            main_sha, "exit-ready", checkpoint_id=4998095350
        )
        normalized = normalize_snapshot(
            graphql_payload(
                main_sha,
                [
                    {
                        "body": checkpoint_marker,
                        "createdAt": "2026-07-15T00:00:00Z",
                        "fullDatabaseId": "4998095350",
                        "isMinimized": False,
                        "lastEditedAt": None,
                        "url": "https://example.test/checkpoint",
                        "viewerDidAuthor": True,
                    },
                    {
                        "body": audit_marker,
                        "createdAt": "2026-07-15T00:01:00Z",
                        "fullDatabaseId": "4998095351",
                        "isMinimized": False,
                        "lastEditedAt": None,
                        "url": "https://example.test/audit",
                        "viewerDidAuthor": True,
                    },
                ],
            )
        )

        self.assertEqual(
            4998095350, normalized["gate_exit"]["checkpoint_id"]
        )
        self.assertEqual("exit-ready", normalized["gate_audit"]["verdict"])
        self.assertEqual(4998095351, normalized["gate_audit"]["comment_id"])
        self.assertEqual([], classify_snapshot(normalized))

    def test_audit_checkpoint_quote_does_not_become_a_new_checkpoint(
        self,
    ) -> None:
        main_sha = "a" * 40
        raw_checkpoint = (
            "<!-- emmet-loop:dispatcher:gate-exit:"
            f"W1-G2:main={main_sha} -->"
        )
        checkpoint = f"{raw_checkpoint}\n\n— Dispatcher"
        audit = gate_audit_report_body(
            main_sha,
            "exit-ready",
            checkpoint_id=4998095350,
            extra_body=f"Checkpoint evidence: {raw_checkpoint}",
        )
        normalized = normalize_snapshot(
            graphql_payload(
                main_sha,
                [
                    {
                        "body": checkpoint,
                        "fullDatabaseId": "4998095350",
                        "isMinimized": False,
                        "lastEditedAt": None,
                        "viewerDidAuthor": True,
                    },
                    {
                        "body": audit,
                        "fullDatabaseId": "4998095351",
                        "isMinimized": False,
                        "lastEditedAt": None,
                        "viewerDidAuthor": True,
                    },
                ],
            )
        )

        self.assertEqual(
            4998095350, normalized["gate_exit"]["checkpoint_id"]
        )
        self.assertEqual("exit-ready", normalized["gate_audit"]["verdict"])
        self.assertEqual([], normalized["snapshot_incomplete"])

    def test_audit_marker_must_be_first_line_signed_and_pristine(self) -> None:
        main_sha = "a" * 40
        checkpoint = (
            "<!-- emmet-loop:dispatcher:gate-exit:"
            f"W1-G2:main={main_sha} -->\n\n— Dispatcher"
        )
        marker = (
            "<!-- emmet-loop:gate-auditor:audit:v1:"
            f"gate=W1-G2:main={main_sha}:checkpoint=101:"
            "verdict=exit-ready -->"
        )
        base_checkpoint = {
            "body": checkpoint,
            "fullDatabaseId": "101",
            "isMinimized": False,
            "lastEditedAt": None,
            "viewerDidAuthor": True,
        }
        invalid_audits = (
            {
                "body": marker,
                "fullDatabaseId": "102",
                "isMinimized": False,
                "lastEditedAt": None,
                "viewerDidAuthor": True,
                "expected": None,
            },
            {
                "body": f"Quoted evidence:\n{marker}\n\n— Gate Auditor",
                "fullDatabaseId": "103",
                "isMinimized": False,
                "lastEditedAt": None,
                "viewerDidAuthor": True,
                "expected": "gate-audit-integrity",
            },
            {
                "body": f"{marker}\n\n— Gate Auditor",
                "fullDatabaseId": "104",
                "isMinimized": False,
                "lastEditedAt": None,
                "viewerDidAuthor": True,
                "expected": "gate-audit-integrity",
            },
            {
                "body": gate_audit_report_body(main_sha, "exit-ready"),
                "fullDatabaseId": "105",
                "isMinimized": False,
                "lastEditedAt": "2026-07-17T00:00:00Z",
                "viewerDidAuthor": True,
                "expected": "gate-audit-integrity",
            },
            {
                "body": gate_audit_report_body(main_sha, "exit-ready"),
                "fullDatabaseId": "106",
                "isMinimized": True,
                "lastEditedAt": None,
                "viewerDidAuthor": True,
                "expected": "gate-audit-integrity",
            },
            {
                "body": gate_audit_report_body(main_sha, "exit-ready").replace(
                    "mutations: meta-comment-only",
                    "mutations: none",
                ),
                "fullDatabaseId": "107",
                "isMinimized": False,
                "lastEditedAt": None,
                "viewerDidAuthor": True,
                "expected": "gate-audit-integrity",
            },
            {
                "body": gate_audit_report_body(main_sha, "exit-ready").replace(
                    "main_sha: " + main_sha,
                    "main_sha: " + ("b" * 40),
                ),
                "fullDatabaseId": "108",
                "isMinimized": False,
                "lastEditedAt": None,
                "viewerDidAuthor": True,
                "expected": "gate-audit-integrity",
            },
            {
                "body": gate_audit_report_body(main_sha, "exit-ready").replace(
                    "verdict: exit-ready",
                    "verdict: unknown",
                ),
                "fullDatabaseId": "109",
                "isMinimized": False,
                "lastEditedAt": None,
                "viewerDidAuthor": True,
                "expected": "gate-audit-integrity",
            },
            {
                "body": gate_audit_report_body(main_sha, "exit-ready").replace(
                    "human_decision_required: yes",
                    "human_decision_required: no",
                ),
                "fullDatabaseId": "110",
                "isMinimized": False,
                "lastEditedAt": None,
                "viewerDidAuthor": True,
                "expected": "gate-audit-integrity",
            },
            {
                "body": gate_audit_report_body(main_sha, "exit-ready").replace(
                    "exit_criteria: pass",
                    "exit_criteria: fail",
                ),
                "fullDatabaseId": "111",
                "isMinimized": False,
                "lastEditedAt": None,
                "viewerDidAuthor": True,
                "expected": "gate-audit-integrity",
            },
            {
                "body": gate_audit_report_body(main_sha, "exit-ready").replace(
                    "governance_consistency: consistent",
                    "governance_consistency: inconsistent",
                ),
                "fullDatabaseId": "112",
                "isMinimized": False,
                "lastEditedAt": None,
                "viewerDidAuthor": True,
                "expected": "gate-audit-integrity",
            },
            {
                "body": gate_audit_report_body(main_sha, "exit-ready").replace(
                    "active_gate_transitioned: no",
                    "active_gate_transitioned: yes",
                ),
                "fullDatabaseId": "113",
                "isMinimized": False,
                "lastEditedAt": None,
                "viewerDidAuthor": True,
                "expected": "gate-audit-integrity",
            },
            {
                "body": gate_audit_report_body(main_sha, "exit-ready").replace(
                    "audit_time: 2026-07-17T00:00:00+08:00",
                    "audit_time: potato",
                ),
                "fullDatabaseId": "114",
                "isMinimized": False,
                "lastEditedAt": None,
                "viewerDidAuthor": True,
                "expected": "gate-audit-integrity",
            },
            {
                "body": gate_audit_report_body(
                    main_sha, "exit-ready"
                ).replace(
                    "\n\n— Gate Auditor",
                    "\nmutations: meta-comment-only\n\n— Gate Auditor",
                ),
                "fullDatabaseId": "115",
                "isMinimized": False,
                "lastEditedAt": None,
                "viewerDidAuthor": True,
                "expected": "gate-audit-integrity",
            },
        )
        for raw_audit in invalid_audits:
            expected = raw_audit.pop("expected")
            with self.subTest(expected=expected):
                normalized = normalize_snapshot(
                    graphql_payload(
                        main_sha, [base_checkpoint, raw_audit]
                    )
                )
                self.assertIsNone(normalized["gate_audit"])
                if expected is None:
                    self.assertEqual([], normalized["snapshot_incomplete"])
                    self.assert_route(
                        normalized, "gate-auditor", "gate-audit-requested"
                    )
                else:
                    self.assertIn(expected, normalized["snapshot_incomplete"])
                    self.assert_route(
                        normalized, "dispatcher", "snapshot-incomplete"
                    )

    def test_checkpoint_gate_must_match_meta_active_gate(self) -> None:
        main_sha = "a" * 40
        normalized = normalize_snapshot(
            graphql_payload(
                main_sha,
                [
                    {
                        "body": (
                            "<!-- emmet-loop:dispatcher:gate-exit:"
                            f"W1-G2:main={main_sha} -->\n\n— Dispatcher"
                        ),
                        "fullDatabaseId": "101",
                        "isMinimized": False,
                        "lastEditedAt": None,
                        "viewerDidAuthor": True,
                    }
                ],
                active_gate="W1-G4",
            )
        )

        self.assertIsNone(normalized["gate_exit"])
        self.assertIn(
            "gate-exit-gate-mismatch", normalized["snapshot_incomplete"]
        )
        self.assert_route(normalized, "dispatcher", "snapshot-incomplete")

    def test_meta_active_gate_declaration_must_be_unique_and_effective(
        self,
    ) -> None:
        payloads = (
            "- Active gate：`W1-G2`",
            f"{meta_body()}\n{meta_body('W1-G3')}",
        )
        for body in payloads:
            with self.subTest(body=body):
                payload = graphql_payload("a" * 40, [])
                payload["data"]["repository"]["meta"]["body"] = body
                normalized = normalize_snapshot(payload)
                self.assertIsNone(normalized["active_gate"])
                self.assertIn(
                    "meta-active-gate", normalized["snapshot_incomplete"]
                )
                self.assert_route(
                    normalized, "dispatcher", "snapshot-incomplete"
                )

    def test_edited_or_minimized_checkpoint_fails_closed(self) -> None:
        main_sha = "a" * 40
        marker = (
            "<!-- emmet-loop:dispatcher:gate-exit:"
            f"W1-G2:main={main_sha} -->\n\n— Dispatcher"
        )
        cases = (
            (False, "2026-07-17T00:00:00Z"),
            (True, None),
        )
        for minimized, edited_at in cases:
            with self.subTest(minimized=minimized, edited_at=edited_at):
                normalized = normalize_snapshot(
                    graphql_payload(
                        main_sha,
                        [
                            {
                                "body": marker,
                                "fullDatabaseId": "101",
                                "isMinimized": minimized,
                                "lastEditedAt": edited_at,
                                "viewerDidAuthor": True,
                            }
                        ],
                    )
                )
                self.assertIsNone(normalized["gate_exit"])
                self.assertIn(
                    "gate-exit-integrity",
                    normalized["snapshot_incomplete"],
                )
                self.assert_route(
                    normalized, "dispatcher", "snapshot-incomplete"
                )

    def test_quoted_role_signature_does_not_authorize_checkpoint(self) -> None:
        main_sha = "a" * 40
        normalized = normalize_snapshot(
            graphql_payload(
                main_sha,
                [
                    {
                        "body": (
                            "<!-- emmet-loop:dispatcher:gate-exit:"
                            f"W1-G2:main={main_sha} -->\n\n> — Dispatcher"
                        ),
                        "fullDatabaseId": "101",
                        "isMinimized": False,
                        "lastEditedAt": None,
                        "viewerDidAuthor": True,
                    }
                ],
            )
        )

        self.assertIsNone(normalized["gate_exit"])
        self.assertEqual([], normalized["snapshot_incomplete"])
        self.assert_route(
            normalized, "dispatcher", "reconcile-or-dispatch"
        )

    def test_conflicting_audits_for_one_checkpoint_fail_closed(self) -> None:
        main_sha = "a" * 40
        checkpoint_marker = (
            "<!-- emmet-loop:dispatcher:gate-exit:"
            f"W1-G2:main={main_sha} -->\n\n— Dispatcher"
        )

        def audit_marker(verdict: str) -> str:
            return gate_audit_report_body(main_sha, verdict)

        normalized = normalize_snapshot(
            graphql_payload(
                main_sha,
                [
                    {
                        "body": checkpoint_marker,
                        "fullDatabaseId": "101",
                        "isMinimized": False,
                        "lastEditedAt": None,
                        "viewerDidAuthor": True,
                    },
                    {
                        "body": audit_marker("exit-ready"),
                        "fullDatabaseId": "102",
                        "isMinimized": False,
                        "lastEditedAt": None,
                        "viewerDidAuthor": True,
                    },
                    {
                        "body": audit_marker("unknown"),
                        "fullDatabaseId": "103",
                        "isMinimized": False,
                        "lastEditedAt": None,
                        "viewerDidAuthor": True,
                    },
                ],
            )
        )

        self.assertIsNone(normalized["gate_audit"])
        self.assertIn(
            "gate-audit-conflict", normalized["snapshot_incomplete"]
        )
        self.assert_route(normalized, "dispatcher", "snapshot-incomplete")

    def test_normalization_rejects_earlier_untrusted_or_mismatched_audits(
        self,
    ) -> None:
        main_sha = "a" * 40
        matching_audit = gate_audit_report_body(main_sha, "exit-ready")
        checkpoint_marker = (
            "<!-- emmet-loop:dispatcher:gate-exit:"
            f"W1-G2:main={main_sha} -->\n\n— Dispatcher"
        )
        mismatched_audit = matching_audit.replace(
            "checkpoint=101", "checkpoint=999"
        )
        normalized = normalize_snapshot(
            graphql_payload(
                main_sha,
                [
                    {
                        "body": matching_audit,
                        "fullDatabaseId": "100",
                        "isMinimized": False,
                        "lastEditedAt": None,
                        "viewerDidAuthor": True,
                    },
                    {
                        "body": checkpoint_marker,
                        "fullDatabaseId": "101",
                        "isMinimized": False,
                        "lastEditedAt": None,
                        "viewerDidAuthor": True,
                    },
                    {
                        "body": matching_audit,
                        "fullDatabaseId": "102",
                        "isMinimized": False,
                        "lastEditedAt": None,
                        "viewerDidAuthor": False,
                    },
                    {
                        "body": mismatched_audit,
                        "fullDatabaseId": "103",
                        "isMinimized": False,
                        "lastEditedAt": None,
                        "viewerDidAuthor": True,
                    },
                ],
            )
        )

        self.assertIsNone(normalized["gate_audit"])
        self.assert_route(
            normalized, "gate-auditor", "gate-audit-requested"
        )

    def test_normalization_marks_paginated_state_incomplete(self) -> None:
        payload = {
            "data": {
                "repository": {
                    "defaultBranchRef": {
                        "name": "main",
                        "target": {"oid": "a" * 40},
                    },
                    "meta": {
                        "body": meta_body(),
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
                        "body": meta_body(),
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
                        "body": meta_body(),
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
                        "body": meta_body(),
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
                        "body": meta_body(),
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
                                        "\n\n— Dispatcher"
                                    ),
                                    "createdAt": "2026-07-15T00:00:00Z",
                                    "fullDatabaseId": "201",
                                    "isMinimized": False,
                                    "lastEditedAt": None,
                                    "url": "https://example.test/stale",
                                    "viewerDidAuthor": True,
                                },
                                {
                                    "body": (
                                        "<!-- emmet-loop:dispatcher:gate-exit:"
                                        f"W1-G2:main={current_main} -->"
                                        "\n\n— Dispatcher"
                                    ),
                                    "createdAt": "2026-07-15T00:01:00Z",
                                    "fullDatabaseId": "202",
                                    "isMinimized": False,
                                    "lastEditedAt": None,
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
                        "body": meta_body(),
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
            poll_github("gh", ROOT, "test/repo")

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
        self.assertIn("fullDatabaseId", query_argument)
        self.assertIn("isMinimized", query_argument)
        self.assertIn("lastEditedAt", query_argument)
        self.assertNotIn(" databaseId", query_argument)
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

    def test_dispatcher_heartbeat_does_not_replace_gate_auditor_wake(self) -> None:
        main_sha = "a" * 40
        decisions = select_poll_decisions(
            snapshot(
                main_sha=main_sha,
                gate_exit=gate_checkpoint(main_sha),
            ),
            busy=[],
            now=300,
            last_dispatcher_wake=0,
            dispatcher_heartbeat_seconds=30,
        )

        self.assertEqual("gate-auditor", decisions[0]["role"])
        self.assertEqual("gate-audit-requested", decisions[0]["reason"])

    def test_pause_control_events_are_not_suppressed_by_busy_child(self) -> None:
        decisions = select_poll_decisions(
            snapshot(paused=True),
            busy=["coder"],
            now=100,
            last_dispatcher_wake=0,
            dispatcher_heartbeat_seconds=30,
        )
        self.assertEqual(4, len(decisions))
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
            gate_exit=gate_checkpoint(main_sha),
            gate_audit=audit_verdict(main_sha, "exit-ready"),
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

    def test_current_gate_exit_without_audit_names_gate_auditor_owner(
        self,
    ) -> None:
        main_sha = "a" * 40
        state = snapshot(
            main_sha=main_sha,
            gate_exit=gate_checkpoint(main_sha),
        )
        status = describe_operator_status(
            state,
            decisions=classify_snapshot(state),
            busy=[],
        )

        self.assertEqual("healthy", status["health"])
        self.assertEqual("gate-auditor", status["owner"])
        self.assertEqual("gate-audit-requested", status["reason"])
        self.assertIn("checkpoint #101", status["current"])

    def test_unknown_gate_audit_blocks_for_user(self) -> None:
        main_sha = "a" * 40
        state = snapshot(
            main_sha=main_sha,
            gate_exit=gate_checkpoint(main_sha),
            gate_audit=audit_verdict(main_sha, "unknown"),
        )
        status = describe_operator_status(
            state,
            decisions=classify_snapshot(state),
            busy=[],
        )

        self.assertEqual("blocked", status["health"])
        self.assertTrue(status["blocking"])
        self.assertEqual("user", status["owner"])
        self.assertEqual("gate-audit-unknown", status["reason"])
        self.assertTrue(build_operator_alert(status)["requires_user"])

    def test_not_ready_gate_audit_returns_operator_status_to_dispatcher(
        self,
    ) -> None:
        main_sha = "a" * 40
        state = snapshot(
            main_sha=main_sha,
            gate_exit=gate_checkpoint(main_sha),
            gate_audit=audit_verdict(main_sha, "not-ready"),
        )
        status = describe_operator_status(
            state,
            decisions=classify_snapshot(state),
            busy=[],
        )

        self.assertEqual("healthy", status["health"])
        self.assertEqual("dispatcher", status["owner"])
        self.assertEqual("gate-audit-not-ready", status["reason"])
        self.assertIn("不得執行 gate transition", status["next"])

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

    def test_gate_marker_integrity_blocker_names_user_and_exact_gap(self) -> None:
        state = snapshot(snapshot_incomplete=["gate-audit-integrity"])
        status = describe_operator_status(
            state,
            decisions=classify_snapshot(state),
            busy=[],
        )

        self.assertEqual("blocked", status["health"])
        self.assertTrue(status["blocking"])
        self.assertEqual("user", status["owner"])
        self.assertIn("gate-audit-integrity", status["current"])
        alert = build_operator_alert(status)
        self.assertIsNotNone(alert)
        self.assertTrue(alert["requires_user"])

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
                "completed_events": [
                    {
                        "event_id": "event-1",
                        "exit_code": 0,
                        "outcome": "mutated",
                        "result": "claimed-work",
                    }
                ],
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
                    "completed_events": [
                        {
                            "event_id": "event-1",
                            "exit_code": 0,
                            "outcome": "mutated",
                            "result": "claimed-work",
                        }
                    ],
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

    def test_empty_dispatcher_terminal_no_op_is_not_stalled(self) -> None:
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
                    "completed_events": [
                        {
                            "event_id": "event-1",
                            "exit_code": 0,
                            "outcome": "terminal-noop",
                            "result": "nothing-to-dispatch",
                        }
                    ],
                }
            },
        )

        self.assertIsNone(stalled)

    def test_gate_exit_checkpoint_changes_workflow_fingerprint(self) -> None:
        main_sha = "a" * 40
        before = snapshot(main_sha=main_sha)
        after = snapshot(
            main_sha=main_sha,
            gate_exit=gate_checkpoint(main_sha),
        )

        self.assertNotEqual(
            workflow_state_fingerprint(before),
            workflow_state_fingerprint(after),
        )

    def test_gate_audit_verdict_changes_workflow_fingerprint(self) -> None:
        main_sha = "a" * 40
        before = snapshot(
            main_sha=main_sha,
            gate_exit=gate_checkpoint(main_sha),
        )
        after = snapshot(
            main_sha=main_sha,
            gate_exit=gate_checkpoint(main_sha),
            gate_audit=audit_verdict(main_sha, "exit-ready"),
        )

        self.assertNotEqual(
            workflow_state_fingerprint(before),
            workflow_state_fingerprint(after),
        )

    def test_gate_auditor_completion_without_verdict_is_stalled(self) -> None:
        main_sha = "a" * 40
        state = snapshot(
            main_sha=main_sha,
            gate_exit=gate_checkpoint(main_sha),
        )
        decision = classify_snapshot(state)[0]
        stalled = detect_stalled_iteration(
            state,
            deliveries={
                "gate-auditor": {
                    "event_id": "audit-event",
                    "reason": decision["reason"],
                    "state_fingerprint": workflow_state_fingerprint(state),
                }
            },
            agent_states={
                "gate-auditor": {
                    "state": "waiting",
                    "last_event_id": "audit-event",
                    "last_exit_code": 0,
                    "completed_events": [
                        {
                            "event_id": "audit-event",
                            "exit_code": 0,
                            "outcome": "blocked",
                            "result": "publication-failed-no-publish",
                        }
                    ],
                }
            },
        )

        self.assertIsNotNone(stalled)
        self.assertEqual("gate-auditor", stalled["role"])
        status = describe_operator_status(
            state,
            decisions=[decision],
            busy=[],
            stalled=stalled,
        )
        self.assertEqual("iteration-blocked", status["reason"])
        self.assertEqual("operator", status["owner"])

    def test_matching_gate_audit_clears_gate_auditor_stall(self) -> None:
        main_sha = "a" * 40
        before = snapshot(
            main_sha=main_sha,
            gate_exit=gate_checkpoint(main_sha),
        )
        after = snapshot(
            main_sha=main_sha,
            gate_exit=gate_checkpoint(main_sha),
            gate_audit=audit_verdict(main_sha, "exit-ready"),
        )
        decision = classify_snapshot(before)[0]
        stalled = detect_stalled_iteration(
            after,
            deliveries={
                "gate-auditor": {
                    "event_id": "audit-event",
                    "reason": decision["reason"],
                    "state_fingerprint": workflow_state_fingerprint(before),
                }
            },
            agent_states={
                "gate-auditor": {
                    "state": "waiting",
                    "last_event_id": "audit-event",
                    "last_exit_code": 0,
                }
            },
        )

        self.assertIsNone(stalled)

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
                            "outcome": "mutated",
                            "result": "claimed-work",
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
                "outcome": "mutated",
                "iteration_result": "claimed-work",
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

    def test_preflight_packet_includes_bound_gate_audit(self) -> None:
        main_sha = "a" * 40
        packet = build_preflight_packet(
            snapshot(
                main_sha=main_sha,
                gate_exit=gate_checkpoint(main_sha),
                gate_audit=audit_verdict(main_sha, "exit-ready"),
            )
        )

        self.assertEqual(101, packet["gate_exit"]["checkpoint_id"])
        self.assertEqual("exit-ready", packet["gate_audit"]["verdict"])
        self.assertEqual(102, packet["gate_audit"]["comment_id"])

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
        (self.worktree / "AGENTS.md").write_text(
            "> Active gate：`W1-G2`\n", encoding="utf-8"
        )
        docs = self.worktree / "docs"
        docs.mkdir()
        (docs / "curriculum.md").write_text(
            "> 目前 active gate：`W1-G2`\n", encoding="utf-8"
        )
        scripts = self.worktree / "scripts"
        scripts.mkdir()
        shutil.copy2(ROOT / "scripts" / "codex_loop.py", scripts)
        shutil.copy2(ROOT / "scripts" / "codex_loop_output.py", scripts)
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
                        "text": (
                            "fake-visible-event\\n"
                            'LOOP_OUTCOME {"role":"dispatcher",'
                            '"outcome":"terminal-noop",'
                            '"result":"fixture-no-op","mutations":[]}'
                        ),
                    },
                }), flush=True)
                print(json.dumps({
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 50,
                        "output_tokens": 20,
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
        output_format: str | None = None,
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
        if output_format is not None:
            command.extend(["--output-format", output_format])
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

    def test_pretty_wake_renders_component_and_codex_events(self) -> None:
        process = self.start_agent(output_format="pretty")
        event = build_event(
            {
                "role": "dispatcher",
                "action": "wake",
                "reason": "test-pretty",
                "objects": [],
            },
            "test/repo",
        )
        notify_agent(self.runtime_dir, event)
        stdout, stderr = process.communicate(timeout=10)

        self.assertEqual(0, process.returncode, stderr)
        self.assertIn("fake-visible-event", stdout)
        self.assertIn("dispatcher", stdout)
        self.assertNotIn('"type": "item.completed"', stdout)
        self.assertNotIn('"result": "iteration-finished"', stdout)
        logs = self.runtime_dir / "logs"
        raw_logs = list(logs.glob("dispatcher-*.stdout.jsonl"))
        stderr_logs = list(logs.glob("dispatcher-*.stderr.log"))
        component_logs = list(logs.glob("dispatcher-*.component.jsonl"))
        self.assertEqual(1, len(raw_logs))
        self.assertEqual(1, len(stderr_logs))
        self.assertEqual(1, len(component_logs))
        raw = raw_logs[0].read_text(encoding="utf-8")
        self.assertIn('"type": "item.completed"', raw)
        component_raw = component_logs[0].read_text(encoding="utf-8")
        self.assertIn('"result": "iteration-finished"', component_raw)
        self.assertEqual(0o700, logs.stat().st_mode & 0o777)
        self.assertEqual(0o600, raw_logs[0].stat().st_mode & 0o777)

    def test_runtime_output_format_defaults_to_auto(self) -> None:
        agent = runtime_parser().parse_args(["agent", "dispatcher"])
        events = runtime_parser().parse_args(["events"])

        self.assertEqual("auto", agent.output_format)
        self.assertEqual("auto", events.output_format)
        self.assertEqual("pretty", events.rotation_output_format)

    def test_pretty_agent_dry_run_does_not_create_raw_trace(self) -> None:
        environment = os.environ.copy()
        environment["FAKE_CODEX_CAPTURE"] = str(self.capture)
        result = subprocess.run(
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
                "--output-format",
                "pretty",
                "--dry-run",
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("$emmet-loop-dispatcher", result.stdout)
        self.assertIn('"result": "preflight-ok"', result.stdout)
        self.assertFalse((self.runtime_dir / "logs").exists())
        self.assertFalse(self.capture.exists())

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
                        "body": meta_body(),
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
        self.assertIn("gpt-5.6-luna", arguments)
        self.assertIn('model_reasoning_effort="medium"', arguments)
        self.assertIn('"preflight":', arguments[-1])

    def test_event_manager_waits_for_user_after_exit_ready_gate_audit(self) -> None:
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
                        "body": meta_body(),
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
                                        "\n\n— Dispatcher"
                                    ),
                                    "createdAt": "2026-07-15T00:00:00Z",
                                    "fullDatabaseId": "101",
                                    "isMinimized": False,
                                    "lastEditedAt": None,
                                    "url": "https://example.test/gate-exit",
                                    "viewerDidAuthor": True,
                                },
                                {
                                    "body": gate_audit_report_body(
                                        main_sha, "exit-ready"
                                    ),
                                    "createdAt": "2026-07-15T00:01:00Z",
                                    "fullDatabaseId": "102",
                                    "isMinimized": False,
                                    "lastEditedAt": None,
                                    "url": "https://example.test/gate-audit",
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
            ) as rotate,
            patch(
                "scripts.codex_loop_runtime.wait_for_control_rotation_handoff",
            ) as wait_handoff,
            patch("scripts.codex_loop_runtime.notify_agent") as notify,
        ):
            rotate.return_value.pid = 4321
            self.assertEqual(0, run_events(options))

        rotate.assert_called_once()
        wait_handoff.assert_called_once_with(
            rotate.return_value,
            rotation_state_path(self.runtime_dir),
        )
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
                        "body": meta_body(),
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
