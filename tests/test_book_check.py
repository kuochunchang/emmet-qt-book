from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from scripts.book_check import (
    output_ownership_finding,
    validate_html_output,
    validate_repository_render,
    validate_source,
)


FULL_SHA = "c999965e5cc923281541409cda9502beb93b8a60"


class CompanionFixture:
    """Synthetic emmet-qt-bt1: a real git repository for tier-2 checks."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.git("init", "--initial-branch=main")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        self.git("config", "commit.gpgsign", "false")
        self.write("tests/unit/test_models_orders.py", "# orders test\n")
        self.write("src/quant/common/models/orders.py", "# orders\n")
        self.git("add", "-A")
        self.git("commit", "-m", "baseline")
        self.commit = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("tag", "v0.3.0")

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )

    def write(self, relative: str, content: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def add_commit(self, relative: str, content: str) -> str:
        self.write(relative, content)
        self.git("add", "-A")
        self.git("commit", "-m", f"add {relative}")
        return self.git("rev-parse", "HEAD").stdout.strip()

    def move_tag(self, tag: str, commit: str) -> None:
        self.git("tag", "-f", tag, commit)


def chapter(title: str = "第一章", state: str = "可操作") -> str:
    return f"""# {title}

> 配套基線：`emmet-qt-bt1 v0.3.0@c999965e5cc9`
> 內容狀態：{state}
> 最後驗證日期：2026-07-12

## 作者驗證紀錄

- 對照 tag／commit：`v0.3.0@{FULL_SHA}`
- 驗證命令：`true`
- 通過結果：命令成功
- 待處理差異：無
"""


def ledger(
    *,
    record_id: str = "ch01-order-smoke",
    document: str = "manuscript/chapters/01.md",
    title: str = "第一章",
    state: str = "可操作",
    result: str = "pass",
) -> str:
    return f'''schema_version = 1

[[records]]
id = "{record_id}"
batch = "W1"
document = "{document}"
chapter = "{title}"
claim = "固定版本的訂單模型 smoke 通過。"
content_state = "{state}"
tag_commit = "v0.3.0@{FULL_SHA}"
full_commit = "{FULL_SHA}"
data_checksums = []
data_checksum_note = "不適用：這個測試不使用市場資料。"
formal_entrypoints = []
schemas = ["quant.common.models.orders.Order"]
interface_note = "使用已發布的訂單模型測試。"
evidence_refs = [
  "repo:emmet-qt-bt1@{FULL_SHA}:tests/unit/test_models_orders.py",
]
verification_commands = ["uv run pytest tests/unit/test_models_orders.py -q"]
oracle = "exit 0 且顯示 32 passed。"
result = "{result}"
observed = "32 passed。"
known_differences = []
verified_on = "2026-07-12"
revalidation_triggers = ["訂單模型、測試或基線改變"]
'''


class Fixture:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "book-repo"
        self.root.mkdir()
        self.companion = CompanionFixture(self.base / "emmet-qt-bt1")
        self.write(
            "book.toml",
            """[book]
title = "測試書"
description = "測試說明"
language = "zh-TW"
src = "manuscript"

[build]
build-dir = "book"
create-missing = false

[output.html]
""",
        )
        self.write(
            "book-check.toml",
            """[metadata]
required = ["chapters/*.md"]

[verification]
ledger = "verification/ledger.toml"
""",
        )
        self.write(
            "manuscript/SUMMARY.md",
            """# 全書目錄

[序章](preface.md)

# 第一篇

- [第一章](chapters/01.md)
- [尚未撰寫的章節]()
""",
        )
        self.write(
            "manuscript/preface.md",
            """# 序章

## 內容狀態

- **可操作**：已驗證。
- **規劃中**：尚未交付。
- **需重驗**：既有證據已失效。
""",
        )
        self.write("manuscript/chapters/01.md", chapter())
        self.write("verification/ledger.toml", ledger())

    def write(self, relative: str, content: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def read(self, relative: str) -> str:
        return (self.root / relative).read_text(encoding="utf-8")

    def close(self) -> None:
        self.temporary.cleanup()

    def init_git(self, ignore: str = "/book/\n") -> None:
        self.write(".gitignore", ignore)
        subprocess.run(
            ["git", "init", "--quiet", str(self.root)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )


def codes(findings) -> set[str]:
    return {finding.code for finding in findings}


class SourceCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Fixture()

    def tearDown(self) -> None:
        self.fixture.close()

    def validate(self, *, git: bool = False):
        return validate_source(self.fixture.root, check_git=git)

    def test_companion_is_resolved_from_the_sibling_directory(self) -> None:
        from scripts.book_check import _resolve_companion

        companion, error = _resolve_companion(self.fixture.root)
        self.assertIsNone(error)
        self.assertEqual(self.fixture.companion.root, companion)

    def test_companion_missing_is_reported(self) -> None:
        from scripts.book_check import _resolve_companion

        shutil.rmtree(self.fixture.companion.root)
        companion, error = _resolve_companion(self.fixture.root)
        self.assertIsNone(companion)
        self.assertEqual("COMPANION_MISSING", error)

    def test_companion_env_override_must_be_a_git_repository(self) -> None:
        from scripts.book_check import _resolve_companion

        plain = self.fixture.base / "not-a-repo"
        plain.mkdir()
        os.environ["EMMET_QT_BT1_DIR"] = str(plain)
        self.addCleanup(os.environ.pop, "EMMET_QT_BT1_DIR", None)
        companion, error = _resolve_companion(self.fixture.root)
        self.assertIsNone(companion)
        self.assertEqual("COMPANION_NOT_GIT", error)

    def test_baseline_verification_against_the_companion(self) -> None:
        from scripts.book_check import Finding, _verify_baseline

        companion = self.fixture.companion
        findings: list[Finding] = []
        self.assertTrue(
            _verify_baseline(
                companion.root, "v0.3.0", companion.commit, "d", "l", findings
            )
        )
        self.assertEqual([], findings)

        findings = []
        self.assertFalse(
            _verify_baseline(
                companion.root, "v9.9.9", companion.commit, "d", "l", findings
            )
        )
        self.assertEqual(["BASELINE_TAG_UNRESOLVED"], [f.code for f in findings])

        findings = []
        self.assertFalse(
            _verify_baseline(companion.root, "v0.3.0", "0" * 40, "d", "l", findings)
        )
        self.assertEqual(["BASELINE_TAG_MISMATCH"], [f.code for f in findings])

    def test_moved_tag_is_caught(self) -> None:
        from scripts.book_check import Finding, _verify_baseline

        companion = self.fixture.companion
        original = companion.commit
        moved = companion.add_commit("src/quant/new.py", "# new\n")
        companion.move_tag("v0.3.0", moved)

        findings: list[Finding] = []
        self.assertFalse(
            _verify_baseline(companion.root, "v0.3.0", original, "d", "l", findings)
        )
        self.assertEqual(["BASELINE_TAG_MISMATCH"], [f.code for f in findings])

    def test_evidence_is_checked_against_the_baseline_commit_not_head(self) -> None:
        from scripts.book_check import _evidence_exists

        companion = self.fixture.companion
        baseline = companion.commit
        head = companion.add_commit("src/quant/later.py", "# later\n")

        self.assertTrue(
            _evidence_exists(
                companion.root, baseline, "tests/unit/test_models_orders.py"
            )
        )
        self.assertFalse(
            _evidence_exists(companion.root, baseline, "src/quant/missing.py")
        )
        # 檔案存在於 HEAD，但不存在於 baseline commit：檢查必須是 commit-scoped，
        # 不是 worktree-scoped。少了這條，整個 tier-2 可以被「檔案現在還在，
        # 所以算過」的實作悄悄掏空。
        self.assertTrue(_evidence_exists(companion.root, head, "src/quant/later.py"))
        self.assertFalse(
            _evidence_exists(companion.root, baseline, "src/quant/later.py")
        )

    def test_minimal_valid_book_passes_and_fenced_template_is_ignored(self) -> None:
        path = "manuscript/chapters/01.md"
        self.fixture.write(
            path,
            self.fixture.read(path)
            + """

```markdown
# 假標題

> 內容狀態：不存在
```
""",
        )
        self.assertEqual([], [str(item) for item in self.validate()])

    def test_missing_and_duplicate_summary_targets_fail_for_the_right_reason(
        self,
    ) -> None:
        summary = self.fixture.read("manuscript/SUMMARY.md")
        self.fixture.write(
            "manuscript/SUMMARY.md",
            summary
            + "\n- [第一章](chapters/01.md)\n"
            + "- [不存在](chapters/missing.md)\n",
        )
        found = codes(self.validate())
        self.assertIn("NAV_DUPLICATE", found)
        self.assertIn("NAV_TARGET_MISSING", found)

    def test_orphan_fails_even_if_another_chapter_links_to_it(self) -> None:
        self.fixture.write("manuscript/chapters/02.md", chapter("第二章"))
        path = "manuscript/chapters/01.md"
        self.fixture.write(path, self.fixture.read(path) + "\n[第二章](02.md)\n")
        self.assertIn("NAV_ORPHAN", codes(self.validate()))

    def test_summary_cannot_escape_manuscript(self) -> None:
        self.fixture.write("README.md", "# outside\n")
        self.fixture.write(
            "manuscript/SUMMARY.md",
            self.fixture.read("manuscript/SUMMARY.md")
            + "\n- [Outside](../README.md)\n",
        )
        self.assertIn("NAV_TARGET_INVALID", codes(self.validate()))

    def test_plain_bullet_is_not_a_valid_summary_chapter(self) -> None:
        self.fixture.write(
            "manuscript/SUMMARY.md",
            self.fixture.read("manuscript/SUMMARY.md") + "\n- 不是 mdBook link\n",
        )
        self.assertIn("NAV_SYNTAX", codes(self.validate()))

    def test_missing_metadata_and_unknown_state_have_stable_codes(self) -> None:
        path = "manuscript/chapters/01.md"
        self.fixture.write(
            path,
            chapter(state="未知狀態").replace("> 最後驗證日期：2026-07-12\n", ""),
        )
        found = codes(self.validate())
        self.assertIn("META_MISSING", found)
        self.assertIn("META_STATE", found)

    def test_author_record_requires_full_commit(self) -> None:
        path = "manuscript/chapters/01.md"
        self.fixture.write(path, chapter().replace(FULL_SHA, "c999965e5cc9"))
        self.assertIn("VERIFY_BASELINE", codes(self.validate()))

    def test_header_and_author_record_baselines_must_match(self) -> None:
        path = "manuscript/chapters/01.md"
        self.fixture.write(
            path,
            chapter().replace(
                f"- 對照 tag／commit：`v0.3.0@{FULL_SHA}`",
                f"- 對照 tag／commit：`v9.9.9@{FULL_SHA}`",
            ),
        )
        self.assertIn("VERIFY_BASELINE_MISMATCH", codes(self.validate()))

    def test_verification_ledger_requires_coverage_and_unique_ids(self) -> None:
        duplicate = ledger() + ledger().replace("schema_version = 1\n\n", "", 1)
        self.fixture.write("verification/ledger.toml", duplicate)
        self.assertIn("LEDGER_ID_DUPLICATE", codes(self.validate()))

        duplicate_claim = ledger() + ledger(record_id="ch01-order-smoke-copy").replace(
            "schema_version = 1\n\n", "", 1
        )
        self.fixture.write("verification/ledger.toml", duplicate_claim)
        self.assertIn("LEDGER_CLAIM_DUPLICATE", codes(self.validate()))

        equivalent_path_claim = ledger() + ledger(
            record_id="ch01-order-smoke-copy",
            document="manuscript/chapters/./01.md",
        ).replace("schema_version = 1\n\n", "", 1)
        self.fixture.write("verification/ledger.toml", equivalent_path_claim)
        found = codes(self.validate())
        self.assertIn("LEDGER_CLAIM_DUPLICATE", found)
        self.assertIn("LEDGER_DOCUMENT", found)

        self.fixture.write(
            "verification/ledger.toml", "schema_version = 1\nrecords = []\n"
        )
        found = codes(self.validate())
        self.assertIn("LEDGER_RECORDS", found)
        self.assertIn("LEDGER_COVERAGE", found)

    def test_verification_ledger_schema_is_closed_and_versioned(self) -> None:
        content = ledger().replace(
            "schema_version = 1", "schema_version = 2\nunknown_root = true"
        )
        self.fixture.write(
            "verification/ledger.toml", content + '\nmisspelled_field = "x"\n'
        )
        found = codes(self.validate())
        self.assertIn("LEDGER_SCHEMA_VERSION", found)
        self.assertIn("LEDGER_SCHEMA", found)
        self.assertIn("LEDGER_FIELD_UNKNOWN", found)

        self.fixture.write(
            "verification/ledger.toml",
            ledger().replace("schema_version = 1", "schema_version = 1.0"),
        )
        self.assertIn("LEDGER_SCHEMA_VERSION", codes(self.validate()))

        (self.fixture.root / "verification/ledger.toml").write_bytes(b"\xff")
        self.assertIn("LEDGER_PARSE", codes(self.validate()))

        self.fixture.write("verification/ledger.toml", ledger(record_id="bad--id"))
        self.assertIn("LEDGER_ID", codes(self.validate()))

    def test_verification_ledger_must_match_document_claims(self) -> None:
        content = (
            ledger()
            .replace('chapter = "第一章"', 'chapter = "另一章"')
            .replace('content_state = "可操作"', 'content_state = "規劃中"')
            .replace('verified_on = "2026-07-12"', 'verified_on = "2026-07-11"')
            .replace("v0.3.0@", "v9.9.9@", 1)
        )
        self.fixture.write("verification/ledger.toml", content)
        found = codes(self.validate())
        self.assertIn("LEDGER_CHAPTER", found)
        self.assertIn("LEDGER_STATE_MISMATCH", found)
        self.assertIn("LEDGER_DATE_MISMATCH", found)
        self.assertIn("LEDGER_BASELINE_MISMATCH", found)

        alternate = FULL_SHA[:12] + "0" * 28
        self.fixture.write(
            "manuscript/chapters/01.md",
            chapter().replace("- 對照 tag／commit：", "-  對照 tag／commit："),
        )
        self.fixture.write(
            "verification/ledger.toml", ledger().replace(FULL_SHA, alternate)
        )
        self.assertIn("LEDGER_AUTHOR_MISMATCH", codes(self.validate()))

        self.fixture.write(
            "manuscript/chapters/01.md",
            chapter().replace(
                f"- 對照 tag／commit：`v0.3.0@{FULL_SHA}`",
                f"- 對照 tag／commit：\n  `v0.3.0@{FULL_SHA}`",
            ),
        )
        self.assertIn("LEDGER_AUTHOR_MISMATCH", codes(self.validate()))

    def test_verification_ledger_checks_checksum_and_na_rules(self) -> None:
        content = ledger().replace(
            "data_checksums = []",
            'data_checksums = ["sample=sha256:abc"]',
        )
        self.fixture.write("verification/ledger.toml", content)
        self.assertIn("LEDGER_CHECKSUM", codes(self.validate()))

        digest_a = "a" * 64
        digest_b = "b" * 64
        content = ledger().replace(
            "data_checksums = []",
            "data_checksums = [\n"
            f'  "sample=sha256:{digest_a}",\n'
            f'  "sample=sha256:{digest_b}",\n'
            "]",
        )
        self.fixture.write("verification/ledger.toml", content)
        found = codes(self.validate())
        self.assertIn("LEDGER_CHECKSUM_ID_DUPLICATE", found)
        self.assertIn("LEDGER_CHECKSUM_CONTRADICTION", found)

        content = ledger().replace(
            'data_checksum_note = "不適用：這個測試不使用市場資料。"',
            'data_checksum_note = "沒有資料"',
        )
        self.fixture.write("verification/ledger.toml", content)
        self.assertIn("LEDGER_CHECKSUM_NA", codes(self.validate()))

        content = ledger().replace(
            'interface_note = "使用已發布的訂單模型測試。"',
            'interface_note = "不適用：已有 schema。"',
        )
        self.fixture.write("verification/ledger.toml", content)
        self.assertIn("LEDGER_INTERFACE_CONTRADICTION", codes(self.validate()))

        content = (
            ledger()
            .replace(
                'data_checksum_note = "不適用：這個測試不使用市場資料。"',
                'data_checksum_note = "不適用："',
            )
            .replace(
                'formal_entrypoints = ["pytest tests/unit/test_models_orders.py"]',
                "formal_entrypoints = []",
            )
            .replace('schemas = ["quant.common.models.orders.Order"]', "schemas = []")
            .replace(
                'interface_note = "使用已發布的訂單模型測試。"',
                'interface_note = "不適用："',
            )
        )
        self.fixture.write("verification/ledger.toml", content)
        found = codes(self.validate())
        self.assertIn("LEDGER_CHECKSUM_NA", found)
        self.assertIn("LEDGER_INTERFACE_NA", found)

    def test_verification_ledger_requires_commands_triggers_and_state_pair(
        self,
    ) -> None:
        content = (
            ledger(result="needs-revalidation")
            .replace(
                'verification_commands = ["uv run pytest tests/unit/test_models_orders.py -q"]',
                "verification_commands = []",
            )
            .replace(
                'revalidation_triggers = ["訂單模型、測試或基線改變"]',
                "revalidation_triggers = []",
            )
        )
        self.fixture.write("verification/ledger.toml", content)
        found = codes(self.validate())
        self.assertIn("LEDGER_COMMANDS", found)
        self.assertIn("LEDGER_REVALIDATION_TRIGGERS", found)
        self.assertIn("LEDGER_REVALIDATION_STATE", found)

        self.fixture.write(
            "verification/ledger.toml",
            ledger().replace(
                "evidence_refs = [\n"
                f'  "repo:emmet-qt-bt1@{FULL_SHA}:tests/unit/test_models_orders.py",\n'
                "]",
                "evidence_refs = []",
            ),
        )
        self.assertIn("LEDGER_EVIDENCE_REFS", codes(self.validate()))

        self.fixture.write("manuscript/chapters/01.md", chapter(state="需重驗"))
        self.fixture.write(
            "verification/ledger.toml",
            ledger(state="需重驗", result="needs-revalidation"),
        )
        self.assertEqual([], [str(item) for item in self.validate()])

    def test_verification_ledger_validates_book_and_url_evidence(self) -> None:
        repository_reference = (
            f"repo:emmet-qt-bt1@{FULL_SHA}:tests/unit/test_models_orders.py"
        )
        self.fixture.write("docs/evidence.md", "# Evidence\n\n## Oracle\n")
        self.fixture.write("docs/evidence.txt", "not a fragment-aware format\n")
        self.fixture.write(
            "verification/ledger.toml",
            ledger().replace(repository_reference, "book:docs/evidence.md#oracle"),
        )
        self.assertEqual([], [str(item) for item in self.validate()])

        self.fixture.write(
            "verification/ledger.toml",
            ledger().replace(repository_reference, "book:docs/evidence.md#missing"),
        )
        self.assertIn("LEDGER_EVIDENCE_FRAGMENT", codes(self.validate()))

        self.fixture.write(
            "verification/ledger.toml",
            ledger().replace(repository_reference, "book:docs/evidence.txt#missing"),
        )
        self.assertIn("LEDGER_EVIDENCE_FRAGMENT", codes(self.validate()))

        for invalid in (
            "url:http://example.com/evidence",
            "url:https://[",
            "url:https://exa mple.com/evidence",
            "url:https://example.com/%zz",
            "url:https://example.com:abc",
            "book://[",
            "book:docs/evidence.md#bad%zz",
        ):
            self.fixture.write(
                "verification/ledger.toml",
                ledger().replace(repository_reference, invalid),
            )
            self.assertIn("LEDGER_EVIDENCE", codes(self.validate()), invalid)

        for invalid_repository_path in (".git/config", "."):
            invalid = f"repo:emmet-qt-bt1@{FULL_SHA}:{invalid_repository_path}"
            self.fixture.write(
                "verification/ledger.toml",
                ledger().replace(repository_reference, invalid),
            )
            self.assertIn("LEDGER_EVIDENCE", codes(self.validate()), invalid)

        self.fixture.init_git(ignore="/book/\n/.cache/\n")
        self.fixture.write(".cache/ignored.md", "# Ignored\n")
        for unavailable in ("book:.cache/ignored.md", "book:.git/config"):
            self.fixture.write(
                "verification/ledger.toml",
                ledger().replace(repository_reference, unavailable),
            )
            self.assertIn(
                "LEDGER_EVIDENCE_MISSING", codes(self.validate(git=True)), unavailable
            )

        loop_a = self.fixture.root / "loop-a"
        loop_b = self.fixture.root / "loop-b"
        loop_a.symlink_to(loop_b)
        loop_b.symlink_to(loop_a)
        self.fixture.write(
            "verification/ledger.toml",
            ledger().replace(repository_reference, "book:loop-a"),
        )
        self.assertIn("LEDGER_EVIDENCE_MISSING", codes(self.validate(git=True)))

    def test_verification_ledger_rejects_escaped_or_ignored_location(self) -> None:
        self.fixture.write(
            "verification/ledger.toml",
            ledger(document="../outside.md"),
        )
        self.assertIn("LEDGER_DOCUMENT", codes(self.validate()))

        ledger_path = self.fixture.root / "verification/ledger.toml"
        ledger_path.unlink()
        outside = self.fixture.write("outside-ledger.toml", ledger())
        ledger_path.symlink_to(outside)
        self.assertIn("LEDGER_SYMLINK", codes(self.validate()))
        ledger_path.unlink()

        self.fixture.write("verification/ledger.toml", ledger())
        self.fixture.init_git(ignore="/book/\n/verification/\n")
        self.assertIn("LEDGER_IGNORED", codes(self.validate(git=True)))

    def test_author_record_hidden_or_not_last_fails(self) -> None:
        path = "manuscript/chapters/01.md"
        hidden = (
            chapter().replace("## 作者驗證紀錄", "<!--\n## 作者驗證紀錄").rstrip()
            + "\n-->\n"
        )
        self.fixture.write(path, hidden)
        self.assertIn("VERIFY_SECTION_MISSING", codes(self.validate()))

        self.fixture.write(
            path, chapter() + "\n## 後續正文\n\n不應出現在驗證紀錄後。\n"
        )
        self.assertIn("VERIFY_SECTION_NOT_LAST", codes(self.validate()))

    def test_active_raw_html_is_rejected_outside_code_fences(self) -> None:
        path = "manuscript/chapters/01.md"
        self.fixture.write(
            path,
            chapter()
            + '\n<script>alert(document.domain)</script>\n<a onclick="alert(1)">x</a>\n',
        )
        found = codes(self.validate())
        self.assertIn("HTML_RAW_TAG", found)
        self.assertIn("HTML_RAW_ATTRIBUTE", found)

    def test_chapters_cannot_leave_metadata_scope_or_use_uppercase_md(self) -> None:
        self.fixture.write("book-check.toml", '[metadata]\nrequired = ["preface.md"]\n')
        self.assertIn("CONFIG_METADATA_SCOPE", codes(self.validate()))

        self.fixture.write("manuscript/chapters/02.MD", chapter("第二章"))
        self.fixture.write(
            "manuscript/SUMMARY.md",
            self.fixture.read("manuscript/SUMMARY.md")
            + "\n- [第二章](chapters/02.MD)\n",
        )
        self.assertIn("SOURCE_EXTENSION_CASE", codes(self.validate()))

    def test_repository_links_accept_cjk_anchor_reference_and_ignored_code(
        self,
    ) -> None:
        self.fixture.write(
            "README.md",
            """# 入口

[內容狀態](manuscript/preface.md#內容狀態)
[同一連結][state]
[state]
[外部網址](https://example.invalid/offline)
`[inline fake](missing-inline.md)`

```markdown
[fenced fake](missing-fenced.md)
```

[state]: manuscript/preface.md#內容狀態
<!-- [comment fake](missing-comment.md) -->
<a href="manuscript/preface.md#內容狀態">raw HTML link</a>
""",
        )
        self.assertEqual([], [str(item) for item in self.validate()])

    def test_repository_links_reject_missing_file_fragment_and_reference(self) -> None:
        self.fixture.write(
            "README.md",
            """# 入口

[missing](docs/missing.md)
[bad anchor](manuscript/preface.md#不存在)
[undefined][nowhere]
<a href="docs/raw-missing.md">raw missing</a>
""",
        )
        found = codes(self.validate())
        self.assertIn("MD_TARGET_MISSING", found)
        self.assertIn("MD_FRAGMENT_MISSING", found)
        self.assertIn("MD_REFERENCE_UNDEFINED", found)

    def test_repository_link_cannot_escape_root(self) -> None:
        self.fixture.write("README.md", "# 入口\n\n[outside](../outside.md)\n")
        self.assertIn("MD_TARGET_ESCAPE", codes(self.validate()))

    @unittest.skipUnless(
        os.environ.get("BOOK_CHECK_TEST_MDBOOK"),
        "canonical wrapper provides the pinned mdBook",
    )
    def test_renderer_catches_escaped_and_nested_commonmark_links(self) -> None:
        self.fixture.write(
            "README.md",
            "# 入口\n\n"
            "[API \\] beta](docs/escaped-missing.md)\n"
            "[API [beta]](docs/nested-missing.md)\n",
        )
        workspace = self.fixture.root / ".cache"
        workspace.mkdir()
        findings = validate_repository_render(
            self.fixture.root,
            Path(os.environ["BOOK_CHECK_TEST_MDBOOK"]),
            workspace,
        )
        self.assertIn("HTML_TARGET_MISSING", codes(findings))

    def test_unanchored_output_ignore_cannot_hide_manuscript(self) -> None:
        self.fixture.init_git(ignore="book/\n")
        self.fixture.write("manuscript/book/extra.md", "# 額外頁\n")
        self.fixture.write(
            "manuscript/SUMMARY.md",
            self.fixture.read("manuscript/SUMMARY.md")
            + "\n- [額外頁](book/extra.md)\n",
        )
        self.assertIn("SOURCE_IGNORED", codes(self.validate(git=True)))

    def test_tracked_output_fails(self) -> None:
        self.fixture.init_git()
        self.fixture.write("book/index.html", "<!doctype html>")
        subprocess.run(
            ["git", "-C", str(self.fixture.root), "add", "-f", "book/index.html"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertIn("OUTPUT_TRACKED", codes(self.validate(git=True)))

    def test_ignored_summary_and_reader_asset_fail(self) -> None:
        self.fixture.write("manuscript/figure.png", "not-a-real-image")
        self.fixture.init_git(
            ignore="/book/\n/manuscript/SUMMARY.md\n/manuscript/figure.png\n"
        )
        self.assertIn("SOURCE_IGNORED", codes(self.validate(git=True)))

    def test_reader_asset_symlink_cannot_copy_external_data(self) -> None:
        secret = self.fixture.root.parent / f"{self.fixture.root.name}-secret.txt"
        secret.write_text("OUTSIDE-SECRET-SENTINEL", encoding="utf-8")
        try:
            (self.fixture.root / "manuscript/leak.txt").symlink_to(secret)
            self.assertIn("SOURCE_SYMLINK", codes(self.validate()))
        finally:
            secret.unlink(missing_ok=True)


class HtmlCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Fixture()
        self.output = self.fixture.root / "book"
        self.fixture.write(
            "book/index.html",
            '<!doctype html><html lang="zh-TW"><body>'
            '<a href="chapters/01.html#小節">第一章</a></body></html>',
        )
        self.fixture.write(
            "book/preface.html",
            '<!doctype html><html lang="zh-TW"><body id="序章">'
            '<h2 id="內容狀態">內容狀態</h2></body></html>',
        )
        self.fixture.write(
            "book/chapters/01.html",
            '<!doctype html><html lang="zh-TW"><body>'
            "<p>emmet-qt-bt1 v0.3.0@c999965e5cc9 可操作 2026-07-12 "
            f"v0.3.0@{FULL_SHA}</p>"
            '<h2 id="小節">小節</h2>'
            '<h2 id="作者驗證紀錄">作者驗證紀錄</h2></body></html>',
        )

    def tearDown(self) -> None:
        self.fixture.close()

    def test_valid_html_with_cjk_fragment_passes(self) -> None:
        self.assertEqual(
            [],
            [
                str(item)
                for item in validate_html_output(self.fixture.root, self.output)
            ],
        )

    def test_missing_local_file_fails_but_external_url_does_not(self) -> None:
        self.fixture.write(
            "book/index.html",
            '<html lang="zh-TW"><a href="missing.html">missing</a>'
            '<a href="https://example.invalid/offline">external</a></html>',
        )
        found = codes(validate_html_output(self.fixture.root, self.output))
        self.assertEqual({"HTML_TARGET_MISSING"}, found)

    def test_missing_fragment_fails(self) -> None:
        self.fixture.write(
            "book/index.html",
            '<html lang="zh-TW"><a href="chapters/01.html#不存在">bad</a></html>',
        )
        self.assertIn(
            "HTML_FRAGMENT_MISSING",
            codes(validate_html_output(self.fixture.root, self.output)),
        )

    def test_unsafe_link_scheme_and_external_resource_fail(self) -> None:
        self.fixture.write(
            "book/index.html",
            '<html lang="zh-TW"><a href="javascript:alert(1)">bad</a>'
            '<script src="https://example.invalid/app.js"></script></html>',
        )
        found = codes(validate_html_output(self.fixture.root, self.output))
        self.assertIn("HTML_SCHEME", found)
        self.assertIn("HTML_RESOURCE_EXTERNAL", found)

    def test_rendered_author_heading_and_evidence_are_required(self) -> None:
        self.fixture.write(
            "book/chapters/01.html",
            '<html lang="zh-TW"><body><p>'
            "emmet-qt-bt1 v0.3.0@c999965e5cc9 可操作 2026-07-12"
            "</p></body></html>",
        )
        found = codes(validate_html_output(self.fixture.root, self.output))
        self.assertIn("OUTPUT_VERIFY_HEADING", found)
        self.assertIn("OUTPUT_EVIDENCE_MISSING", found)

    def test_missing_index_fails(self) -> None:
        (self.output / "index.html").unlink()
        self.assertIn(
            "OUTPUT_INDEX", codes(validate_html_output(self.fixture.root, self.output))
        )

    def test_unowned_output_is_not_deleted(self) -> None:
        keep = self.fixture.write("book/KEEP.txt", "user data")
        finding = output_ownership_finding(self.output)
        self.assertIsNotNone(finding)
        assert finding is not None
        self.assertEqual("OUTPUT_UNOWNED", finding.code)
        self.assertTrue(keep.is_file())


if __name__ == "__main__":
    unittest.main()
