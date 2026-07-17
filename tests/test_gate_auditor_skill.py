from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".agents" / "skills" / "emmet-gate-auditor" / "SKILL.md"
OPENAI_YAML = SKILL.parent / "agents" / "openai.yaml"
LOOP_SKILL = (
    ROOT / ".agents" / "skills" / "emmet-loop-gate-auditor" / "SKILL.md"
)
LOOP_OPENAI_YAML = LOOP_SKILL.parent / "agents" / "openai.yaml"
CLAUDE_LOOP_SKILL = ROOT / ".claude" / "skills" / "gate-auditor" / "SKILL.md"


class GateAuditorSkillContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.skill = SKILL.read_text(encoding="utf-8")
        cls.metadata = OPENAI_YAML.read_text(encoding="utf-8")

    def test_frontmatter_is_minimal_and_complete(self) -> None:
        match = re.match(r"\A---\n(.*?)\n---\n", self.skill, re.DOTALL)
        self.assertIsNotNone(match)
        frontmatter = match.group(1)
        self.assertRegex(frontmatter, r"(?m)^name: emmet-gate-auditor$")
        self.assertRegex(frontmatter, r"(?m)^description: .+explicitly invokes")
        self.assertEqual(
            {"name", "description"},
            {line.split(":", 1)[0] for line in frontmatter.splitlines()},
        )
        self.assertNotIn("TODO", self.skill)

    def test_skill_is_explicit_only(self) -> None:
        self.assertIn('default_prompt: "Use $emmet-gate-auditor', self.metadata)
        self.assertIn("allow_implicit_invocation: false", self.metadata)

    def test_authoritative_snapshot_and_evidence_rules_are_required(self) -> None:
        for required in (
            "MAIN_SHA",
            "AGENTS.md",
            "docs/curriculum.md",
            "Meta Issue #1",
            "40 字元 SHA",
            "evidence_sha",
            "freshness",
            "stale",
            "unbound",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.skill)

    def test_separate_state_and_verdict_taxonomy_are_stable(self) -> None:
        for state in (
            "exit_criteria: <pass|fail|unknown>",
            "governance_consistency: <consistent|transition-window|inconsistent|unknown>",
            "active_gate_transitioned: <yes|no|unknown>",
            "not-ready",
            "unknown",
            "exit-ready",
            "transition-in-progress",
            "transition-complete",
        ):
            with self.subTest(state=state):
                self.assertIn(state, self.skill)

    def test_checkpoint_contract_guards_stale_and_cross_gate_state(self) -> None:
        for required in (
            "Dispatcher gate-exit marker",
            "完整 MAIN_SHA",
            "open 不等於未完成",
            "未完成或 blocked",
            "誤派的下一 gate 工作",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.skill)

    def test_forward_test_ambiguities_have_deterministic_rules(self) -> None:
        for required in (
            "只彙總 curriculum",
            "stale marker 不改寫 exit_criteria",
            "Meta Issue #1 的 active gate 以 body",
            "目前 integration candidate",
            "human_decision_required 的固定映射",
            "immutable comment／commit permalink",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.skill)

    def test_read_only_boundary_and_output_are_explicit(self) -> None:
        for forbidden_action in (
            "commit",
            "push",
            "Issue／PR 建立或留言",
            "body／label 變更",
            "merge",
            "scheduler／timer 操作",
        ):
            with self.subTest(forbidden_action=forbidden_action):
                self.assertIn(forbidden_action, self.skill)
        self.assertIn("mutations: none", self.skill)
        self.assertIn("human_decision_required", self.skill)
        self.assertIn("local_cache_refresh: <none|git-fetch>", self.skill)


class LoopGateAuditorSkillContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.skill = LOOP_SKILL.read_text(encoding="utf-8")
        cls.metadata = LOOP_OPENAI_YAML.read_text(encoding="utf-8")
        cls.claude = CLAUDE_LOOP_SKILL.read_text(encoding="utf-8")

    def test_loop_skill_is_separate_minimal_and_explicit_only(self) -> None:
        match = re.match(r"\A---\n(.*?)\n---\n", self.skill, re.DOTALL)
        self.assertIsNotNone(match)
        frontmatter = match.group(1)
        self.assertRegex(frontmatter, r"(?m)^name: emmet-loop-gate-auditor$")
        self.assertEqual(
            {"name", "description"},
            {line.split(":", 1)[0] for line in frontmatter.splitlines()},
        )
        self.assertIn(
            'default_prompt: "Use $emmet-loop-gate-auditor', self.metadata
        )
        self.assertIn("allow_implicit_invocation: false", self.metadata)
        self.assertNotIn("TODO", self.skill)

    def test_loop_role_reuses_manual_evidence_algorithm(self) -> None:
        for required in (
            ".agents/skills/emmet-gate-auditor/SKILL.md",
            "不另造較寬鬆的演算法",
            "三方治理真相",
            "逐條 curriculum",
            "main-bound",
            "freshness",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.skill)

    def test_publish_requires_exact_wake_and_fresh_checkpoint(self) -> None:
        for required in (
            "role=gate-auditor",
            "reason=gate-audit-requested",
            "loop:paused",
            "zero WIP",
            "snapshot 完整",
            "完整 40 字元",
            "CHECKPOINT_ID",
            "沒有既有 matching audit",
            "發佈前再以一次 bounded live query 重驗",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.skill)

    def test_marker_and_verdicts_are_exact(self) -> None:
        marker = (
            "<!-- emmet-loop:gate-auditor:audit:v1:gate=<GATE>:main=<SHA>:"
            "checkpoint=<ID>:verdict=<not-ready|unknown|exit-ready> -->"
        )
        self.assertIn(marker, self.skill)
        self.assertIn("任一 verdict 已存在", self.skill)
        self.assertIn("不重複留言", self.skill)

    def test_published_report_replaces_manual_mutation_fields(self) -> None:
        for contract in (self.skill, self.claude):
            for required in (
                "skill: $emmet-loop-gate-auditor",
                "audit_mutations: none",
                "publication_mutation: meta-comment-only",
                "mutations: meta-comment-only",
                "不得輸出 `skill: $emmet-gate-auditor`",
                "`mutations: none` 到成功發佈的 durable report",
                "exit-ready`=`pass/consistent/no",
                "not-ready`=`fail/consistent/no",
                "unknown`=`unknown/consistent/no",
                "含 timezone 的 ISO 8601",
            ):
                with self.subTest(required=required):
                    self.assertIn(required, contract)

    def test_only_event_wake_can_narrow_manual_read_only_boundary(self) -> None:
        for required in (
            "result=manual-diagnostic-no-publish",
            "result=invalid-wake-no-publish",
            "publication_mutation: meta-comment-only",
            "audit_mutations: none",
            "comment edit/delete",
            "Issue body/state",
            "PR/review/merge",
            "tracked 或 untracked",
            "gate declaration",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.skill)
        self.assertIn("仍須人類核准", self.skill)
        self.assertIn("不得輸出 approve", self.skill)

    def test_invalid_or_manual_wakes_exit_before_evidence_reads(self) -> None:
        for contract in (self.skill, self.claude):
            for required in (
                "先檢查 wake，再做任何 fetch",
                "完全沒有 event payload",
                "有 event-like payload",
                "立即結束",
                "不執行 `git fetch`",
                "gate=unknown",
                "main_sha=unknown",
                "checkpoint_id=none",
                "verdict=none",
                "mutations=none",
            ):
                with self.subTest(required=required):
                    self.assertIn(required, contract)

    def test_claude_role_has_the_same_publish_boundary(self) -> None:
        for required in (
            "reason=gate-audit-requested",
            ".agents/skills/emmet-gate-auditor/SKILL.md",
            "result=manual-diagnostic-no-publish",
            "publication_mutation: meta-comment-only",
            "checkpoint=<ID>:verdict=<not-ready|unknown|exit-ready>",
            "仍須人類核准",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.claude)

    def test_codex_and_claude_role_procedures_remain_in_parity(self) -> None:
        def normalized_body(value: str) -> str:
            without_frontmatter = re.sub(
                r"\A---\n.*?\n---\n",
                "",
                value,
                count=1,
                flags=re.DOTALL,
            ).lstrip("\n")
            return re.sub(
                r"\A# [^\n]+\n",
                "",
                without_frontmatter,
                count=1,
            )

        self.assertEqual(
            normalized_body(self.skill), normalized_body(self.claude)
        )


if __name__ == "__main__":
    unittest.main()
