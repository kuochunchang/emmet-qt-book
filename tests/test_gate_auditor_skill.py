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

    def test_published_report_starts_with_an_operator_summary(self) -> None:
        for contract in (self.skill, self.claude):
            for required in (
                "## 操作者摘要",
                "不要求他從完整表格自行",
                "`判定`：`exit-ready` 寫「等待你決定」",
                "`影響`：明寫 active gate 仍未改變",
                "successor 尚未生效",
                "main 一變更即失效",
                "治理／snapshot／freshness、curriculum 原順序",
                "checkpoint、transition／publication 的固定順序",
                "操作者摘要之後才放固定欄位與所有表格",
                "不是另一個 verdict 或授權來源",
            ):
                with self.subTest(required=required):
                    self.assertIn(required, contract)
            self.assertRegex(
                contract,
                r"完整 blockers 仍\s+全部保留在後文",
            )

    def test_terminal_handoff_is_human_first_and_snapshot_bounded(self) -> None:
        for contract in (self.skill, self.claude):
            section = contract.split("## 結尾的操作者交接", 1)[1]
            template_match = re.search(
                r"```text\n(Gate Auditor\n.*?)\n```",
                section,
                flags=re.DOTALL,
            )
            self.assertIsNotNone(template_match)
            lines = template_match.group(1).splitlines()
            self.assertEqual(9, len(lines))
            prefixes = (
                "Gate Auditor",
                "判定：",
                "Gate：",
                "問題：",
                "下一步（",
                "本輪：",
                "有效：",
                "診斷：",
                "證據：",
            )
            for line, prefix in zip(lines, prefixes, strict=True):
                with self.subTest(prefix=prefix):
                    self.assertTrue(line.startswith(prefix), line)
            for required in (
                "## 結尾的操作者交接",
                "--body-file -",
                "不得使用 inline `--body`",
                "至多九個 logical lines",
                "問題：<無|N 項；第一項 [ID] <至多 52 terminal display cells>>",
                "至多 60 terminal display cells 的唯一最小安全動作",
                "每個 logical line 最多 80 terminal display cells",
                "寬字元按兩格計算",
                "卡片只用 12 字元 SHA",
                "不能拿 `audit_time` 猜填",
                "不宣稱 publication 後仍 current",
                "診斷：<result>",
                "cache=<none|git-fetch>",
                "immutable permalink",
                "<AUDIT_COMMENT_ID>",
                "<CHECKPOINT_ID>",
            ):
                with self.subTest(required=required):
                    self.assertIn(required, contract)
            self.assertNotIn("Gate：<audited gate> →", section)
            self.assertNotIn("；audit@<12>", section)
            self.assertNotIn("技術摘要：role=gate-auditor", section)

    def test_terminal_handoff_maps_verdicts_and_noop_without_new_state(self) -> None:
        for contract in (self.skill, self.claude):
            section = contract.split("## 結尾的操作者交接", 1)[1]
            for required in (
                "`exit-ready` →「等待你決定」",
                "`not-ready` →「尚未就緒」",
                "`unknown` →「無法判定（安全停止）」",
                "owner=`使用者`",
                "owner=`Dispatcher`",
                "`matching-audit-no-op`",
                "`stale-snapshot-no-publish`",
                "`precondition-failed-no-publish`",
                "`evidence-incomplete-no-publish`",
                "`invalid-gate-audit-state`",
                "`publication-failed-no-publish`",
                "無 durable 判定／`none`",
                "`publication-state-unknown`",
                "`manual-diagnostic-no-publish`",
                "`invalid-wake-no-publish`",
                "computed=<verdict>",
                "不得放入 `verdict` 冒充 durable audit",
            ):
                with self.subTest(required=required):
                    self.assertIn(required, contract)
            self.assertRegex(
                section,
                r"iteration outcome，不得\s+加入 marker 的三種 verdict taxonomy",
            )
            result_rows = {
                line.split("|")[2].strip().strip("`")
                for line in section.splitlines()
                if line.startswith("|") and line.count("|") == 7
            }
            result_rows.discard("result")
            result_rows.discard("---")
            self.assertEqual(
                {
                    "published",
                    "matching-audit-no-op",
                    "stale-snapshot-no-publish",
                    "precondition-failed-no-publish",
                    "evidence-incomplete-no-publish",
                    "invalid-gate-audit-state",
                    "publication-failed-no-publish",
                    "publication-state-unknown",
                    "manual-diagnostic-no-publish",
                    "invalid-wake-no-publish",
                },
                result_rows,
            )
            matrix = {}
            for line in section.splitlines():
                if not line.startswith("|") or line.count("|") != 7:
                    continue
                cells = [cell.strip() for cell in line.strip("|").split("|")]
                result = cells[1].strip("`")
                if result not in {"result", "---"}:
                    matrix[result] = tuple(cells[2:])
            self.assertEqual(
                (
                    "依本輪 verdict／該 verdict",
                    "已發佈／`meta-comment-only`",
                    "檢查時有效；新 permalink",
                    "依 verdict",
                ),
                matrix["published"],
            )
            self.assertEqual(
                (
                    "依既有 verdict／既有 verdict",
                    "沿用、未重貼／`none`",
                    "檢查時有效；既有 permalink",
                    "依既有 verdict",
                ),
                matrix["matching-audit-no-op"],
            )
            self.assertEqual(
                (
                    "未稽核／`none`",
                    "未發佈／`none`",
                    "stale；舊 link 僅標「已過期」",
                    "`Dispatcher`",
                ),
                matrix["stale-snapshot-no-publish"],
            )
            self.assertEqual(
                (
                    "無法判定／`none`",
                    "發佈結果未知／`unknown`",
                    "unknown；不得宣稱有 report",
                    "`使用者`",
                ),
                matrix["publication-state-unknown"],
            )

    def test_incomplete_transport_and_durable_unknown_are_distinct(self) -> None:
        for contract in (self.skill, self.claude):
            for required in (
                "來源 transport／pagination、live query 或 snapshot completeness",
                "`evidence-incomplete-no-publish` 並令 `verdict=none`",
                "若 snapshot 已完整",
                "必須發佈 `verdict=unknown`",
            ):
                with self.subTest(required=required):
                    self.assertIn(required, contract)
            self.assertRegex(
                contract,
                r"不得用\s+no-publish 逃避 durable unknown blocker",
            )

    def test_stale_and_ambiguous_publication_fail_closed(self) -> None:
        for contract in (self.skill, self.claude):
            for required in (
                "重驗 zero WIP",
                "三方 gate 與退出證據",
                "全部仍成立時才能建立 fresh checkpoint",
                "再由 Gate Auditor 獨立",
                "恢復查詢後先搜尋 exact marker",
                "不得盲目重貼",
                "狀態仍不明必須寫",
                "`mutations=unknown`",
                "沒有嘗試 publication、matching no-op",
                "重查已確認 marker 不存在，才可寫 `mutations=none`",
                "local_cache_refresh",
                "手動／非法 wake 的 cache 固定 `none`",
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
