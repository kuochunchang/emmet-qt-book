from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".agents" / "skills" / "emmet-gate-auditor" / "SKILL.md"
OPENAI_YAML = SKILL.parent / "agents" / "openai.yaml"


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


if __name__ == "__main__":
    unittest.main()
