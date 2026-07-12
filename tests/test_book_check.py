from __future__ import annotations

import os
from pathlib import Path
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


class Fixture:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
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
""",
        )
        self.write("manuscript/chapters/01.md", chapter())

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
