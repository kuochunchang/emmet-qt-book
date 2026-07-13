#!/usr/bin/env python3
"""Deterministic source and rendered-output checks for the Emmet book."""

from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import hashlib
import html
from html.parser import HTMLParser
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unicodedata
from urllib.parse import unquote, urlsplit


MDBOOK_VERSION = "0.5.4"
SUMMARY_LINK_RE = re.compile(r"\[([^\]]+)]\(([^)]*)\)")
SUMMARY_ITEM_RE = re.compile(r"^\s*(?:[-*]\s+)?\[[^]]+]\([^)]*\)\s*$")
METADATA_RE = re.compile(r"^>\s*(配套基線|內容狀態|最後驗證日期)：\s*(.*?)\s*$")
BASELINE_RE = re.compile(r"^`emmet-qt-bt1\s+([^@\s`]+)@([0-9a-f]{12,40})`$")
VERIFICATION_BASELINE_RE = re.compile(r"`?([^@\s`]+)@([0-9a-f]{40})`?")
PLACEHOLDER_RE = re.compile(r"(?:\bT(?:BD|ODO)\b|待定|placeholder)", re.IGNORECASE)
INLINE_LINK_RE = re.compile(
    r"!?\[[^\]\n]*]\(\s*(?:<([^>\n]+)>|([^\s)\n]+))(?:\s+[^)]*)?\)"
)
REFERENCE_DEFINITION_RE = re.compile(
    r"^\s{0,3}\[([^]\n]+)]:\s*(?:<([^>\n]+)>|([^\s]+))"
)
REFERENCE_USE_RE = re.compile(r"!?\[([^]\n]+)]\[([^]\n]*)]")
SHORTCUT_REFERENCE_RE = re.compile(r"!?\[([^]\n]+)]")
FORBIDDEN_RAW_HTML_TAGS = {
    "base",
    "embed",
    "form",
    "iframe",
    "input",
    "link",
    "meta",
    "object",
    "pre",
    "script",
    "style",
    "svg",
    "template",
    "textarea",
    "xmp",
}
OUTPUT_MARKER = ".emmet-book-output"
OUTPUT_MARKER_CONTENT = "emmet-qt-book book-check v1\n"
COMPANION_REPO = "emmet-qt-bt1"
COMPANION_ENV = "EMMET_QT_BT1_DIR"
VERIFICATION_LEDGER_PATH = "verification/ledger.toml"
LEDGER_SCHEMA_VERSION = 2
LEDGER_RESULT_VALUES = {"pass", "needs-revalidation"}
LEDGER_BATCH_RE = re.compile(r"^W[1-9][0-9]*$")
LEDGER_RECORD_FIELDS = {
    "id",
    "batch",
    "document",
    "chapter",
    "claim",
    "content_state",
    "data_checksums",
    "data_checksum_note",
    "formal_entrypoints",
    "schemas",
    "interface_note",
    "evidence_refs",
    "verification_commands",
    "executable",
    "oracle_exit_code",
    "oracle_stdout_contains",
    "result",
    "observed",
    "known_differences",
    "verified_on",
    "revalidation_triggers",
}
# 只允許在特定情境出現；出現在別處或該出現卻缺席，兩個方向都失敗。
LEDGER_CONDITIONAL_FIELDS = {"verified_against", "executable_note"}
LEDGER_LIST_FIELDS = {
    "data_checksums",
    "formal_entrypoints",
    "schemas",
    "evidence_refs",
    "verification_commands",
    "known_differences",
    "revalidation_triggers",
    "oracle_stdout_contains",
}
LEDGER_BOOL_FIELDS = {"executable"}
LEDGER_INT_FIELDS = {"oracle_exit_code"}
LEDGER_STRING_FIELDS = (
    (LEDGER_RECORD_FIELDS | LEDGER_CONDITIONAL_FIELDS)
    - LEDGER_LIST_FIELDS
    - LEDGER_BOOL_FIELDS
    - LEDGER_INT_FIELDS
)
LEDGER_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LEDGER_BASELINE_RE = re.compile(r"^([^@\s]+)@([0-9a-f]{40})$")
LEDGER_DATA_CHECKSUM_RE = re.compile(r"^([^=\s]+)=sha256:([0-9a-f]{64})$")
LEDGER_EVIDENCE_REF_RE = re.compile(r"^repo:emmet-qt-bt1:([^\x00]+)$")


class Finding:
    def __init__(self, code: str, path: str, line: int, message: str) -> None:
        self.code = code
        self.path = path
        self.line = line
        self.message = message

    def sort_key(self) -> tuple[str, int, str, str]:
        return (self.path, self.line, self.code, self.message)

    def __str__(self) -> str:
        location = f"{self.path}:{self.line}" if self.line else self.path
        return f"{self.code} {location} {self.message}"


class SummaryLink:
    def __init__(self, title: str, target: str, line: int) -> None:
        self.title = title
        self.target = target
        self.line = line


def _read_text(
    path: Path, code: str, findings: list[Finding], display: str
) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        findings.append(Finding(code, display, 0, str(error)))
        return None


def _path_uses_symlink(path: Path, boundary: Path) -> bool:
    try:
        relative = path.relative_to(boundary)
    except ValueError:
        return True
    cursor = boundary
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            return True
    return False


def _load_toml(path: Path, code: str, findings: list[Finding], display: str) -> dict:
    try:
        with path.open("rb") as handle:
            value = tomllib.load(handle)
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        findings.append(Finding(code, display, 0, str(error)))
        return {}
    if not isinstance(value, dict):
        findings.append(Finding(code, display, 0, "設定檔根節點必須是 table"))
        return {}
    return value


def visible_markdown_lines(text: str) -> list[tuple[int, str]]:
    """Return lines outside fenced code blocks and HTML comments."""
    result: list[tuple[int, str]] = []
    fence_char: str | None = None
    fence_length = 0
    in_comment = False

    for number, line in enumerate(text.splitlines(), start=1):
        if fence_char is not None:
            stripped = line.lstrip()
            match = re.match(r"(`{3,}|~{3,})", stripped)
            if (
                match
                and match.group(1)[0] == fence_char
                and len(match.group(1)) >= fence_length
                and stripped[len(match.group(1)) :].strip() == ""
            ):
                fence_char = None
                fence_length = 0
            continue

        characters = list(line)
        cursor = 0
        while cursor < len(line):
            if in_comment:
                closing = line.find("-->", cursor)
                if closing == -1:
                    for position in range(cursor, len(characters)):
                        characters[position] = " "
                    cursor = len(line)
                    continue
                for position in range(cursor, closing + 3):
                    characters[position] = " "
                in_comment = False
                cursor = closing + 3
                continue
            opening = line.find("<!--", cursor)
            if opening == -1:
                break
            closing = line.find("-->", opening + 4)
            if closing == -1:
                for position in range(opening, len(characters)):
                    characters[position] = " "
                in_comment = True
                cursor = len(line)
                continue
            for position in range(opening, closing + 3):
                characters[position] = " "
            cursor = closing + 3

        visible_line = "".join(characters)
        stripped = visible_line.lstrip()
        match = re.match(r"(`{3,}|~{3,})", stripped)
        if match:
            fence_char = match.group(1)[0]
            fence_length = len(match.group(1))
            continue
        result.append((number, visible_line))

    return result


def _plain_title(value: str) -> str:
    return re.sub(r"[`*_]", "", value).strip()


def _mask_inline_code(line: str) -> str:
    """Mask closed inline-code spans while retaining character positions."""
    characters = list(line)
    index = 0
    while index < len(line):
        if line[index] != "`":
            index += 1
            continue
        end_of_run = index
        while end_of_run < len(line) and line[end_of_run] == "`":
            end_of_run += 1
        marker = line[index:end_of_run]
        closing = line.find(marker, end_of_run)
        if closing == -1:
            index = end_of_run
            continue
        for position in range(index, closing + len(marker)):
            characters[position] = " "
        index = closing + len(marker)
    return "".join(characters)


def _reference_label(value: str) -> str:
    return " ".join(value.split()).casefold()


def _heading_text(value: str) -> str:
    value = re.sub(r"\s+#+\s*$", "", value).strip()
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"!?\[([^]]+)]\([^)]*\)", r"\1", value)
    value = re.sub(r"`+([^`]*)`+", r"\1", value)
    value = re.sub(r"[*_~]", "", value)
    return html.unescape(value)


def _github_heading_slug(value: str) -> str:
    """Approximate GitHub's heading IDs for repository Markdown documents."""
    output: list[str] = []
    pending_space = False
    for character in _heading_text(value).strip().casefold():
        if character.isspace():
            pending_space = bool(output)
            continue
        category = unicodedata.category(character)
        keep = (
            category[0] in {"L", "M", "N"}
            or category == "Pc"
            or category == "So"
            or character == "-"
        )
        if not keep:
            continue
        if pending_space:
            output.append("-")
            pending_space = False
        output.append(character)
    return "".join(output)


def _markdown_heading_ids(path: Path) -> set[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return set()
    identifiers: set[str] = set()
    counts: dict[str, int] = {}
    visible = visible_markdown_lines(text)
    for index, (_, line) in enumerate(visible):
        match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", line)
        heading = match.group(1) if match else None
        if heading is None and index + 1 < len(visible) and line.strip():
            underline = visible[index + 1][1]
            if re.fullmatch(r"\s{0,3}(?:=+|-+)\s*", underline):
                heading = line.strip()
        if heading is None:
            continue
        base = _github_heading_slug(heading)
        if not base:
            continue
        occurrence = counts.get(base, 0)
        identifier = base if occurrence == 0 else f"{base}-{occurrence}"
        counts[base] = occurrence + 1
        identifiers.add(identifier)
    return identifiers


def _first_h1(
    text: str, display: str, findings: list[Finding]
) -> tuple[int, str] | None:
    headings: list[tuple[int, str]] = []
    for line_number, line in visible_markdown_lines(text):
        match = re.match(r"^#\s+(.+?)\s*$", line)
        if match:
            headings.append((line_number, match.group(1)))

    if not headings:
        findings.append(Finding("DOC_H1_MISSING", display, 1, "缺少文件 H1 標題"))
        return None
    if len(headings) > 1:
        findings.append(
            Finding(
                "DOC_H1_DUPLICATE",
                display,
                headings[1][0],
                "文件只能有一個 H1 標題",
            )
        )
    first_nonempty = next(
        (
            (number, line)
            for number, line in visible_markdown_lines(text)
            if line.strip()
        ),
        None,
    )
    if first_nonempty and first_nonempty[0] != headings[0][0]:
        findings.append(
            Finding(
                "DOC_H1_POSITION",
                display,
                headings[0][0],
                "H1 必須是文件第一個非空白內容",
            )
        )
    return headings[0]


def _content_states(preface: Path, findings: list[Finding], root: Path) -> set[str]:
    display = preface.relative_to(root).as_posix()
    text = _read_text(preface, "STATE_SOURCE_READ", findings, display)
    if text is None:
        return set()

    in_section = False
    states: list[str] = []
    for line_number, line in visible_markdown_lines(text):
        if line == "## 內容狀態":
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if in_section:
            match = re.match(r"^-\s+\*\*(.+?)\*\*：", line)
            if match:
                states.append(match.group(1))

    if not states:
        findings.append(
            Finding(
                "STATE_SOURCE_EMPTY",
                display,
                0,
                "序章的「內容狀態」小節沒有可用狀態",
            )
        )
    if len(states) != len(set(states)):
        findings.append(
            Finding("STATE_SOURCE_DUPLICATE", display, 0, "序章內容狀態不可重複")
        )
    return set(states)


def _validate_book_config(root: Path, findings: list[Finding]) -> tuple[Path, Path]:
    config = _load_toml(root / "book.toml", "CONFIG_BOOK", findings, "book.toml")
    book = config.get("book")
    build = config.get("build")
    output = config.get("output")
    if not isinstance(book, dict):
        findings.append(Finding("CONFIG_BOOK_SECTION", "book.toml", 0, "缺少 [book]"))
        book = {}
    if not isinstance(build, dict):
        findings.append(Finding("CONFIG_BUILD_SECTION", "book.toml", 0, "缺少 [build]"))
        build = {}
    if not isinstance(output, dict) or "html" not in output:
        findings.append(
            Finding("CONFIG_HTML_OUTPUT", "book.toml", 0, "必須明確啟用 [output.html]")
        )
    elif set(output) != {"html"}:
        findings.append(
            Finding(
                "CONFIG_OUTPUT_FORMAT",
                "book.toml",
                0,
                "目前正式輸出只能啟用 HTML；其他格式需另行驗收",
            )
        )

    for key in ("title", "description"):
        value = book.get(key)
        if (
            not isinstance(value, str)
            or not value.strip()
            or PLACEHOLDER_RE.search(value)
        ):
            findings.append(
                Finding("CONFIG_METADATA", "book.toml", 0, f"book.{key} 必須是正式文字")
            )

    if book.get("language") != "zh-TW":
        findings.append(
            Finding("CONFIG_LANGUAGE", "book.toml", 0, "book.language 必須是 zh-TW")
        )
    if book.get("src") != "manuscript":
        findings.append(
            Finding("CONFIG_SOURCE", "book.toml", 0, "book.src 必須是 manuscript")
        )
    if build.get("build-dir") != "book":
        findings.append(
            Finding("CONFIG_OUTPUT_DIR", "book.toml", 0, "build.build-dir 必須是 book")
        )
    if build.get("create-missing") is not False:
        findings.append(
            Finding(
                "CONFIG_CREATE_MISSING",
                "book.toml",
                0,
                "build.create-missing 必須為 false，不得自動建立空章稿",
            )
        )

    return root / "manuscript", root / "book"


def _metadata_patterns(
    root: Path, findings: list[Finding], config: dict
) -> list[str]:
    metadata = config.get("metadata")
    if not isinstance(metadata, dict):
        findings.append(
            Finding("CONFIG_METADATA_SECTION", "book-check.toml", 0, "缺少 [metadata]")
        )
        return []
    patterns = metadata.get("required")
    if not isinstance(patterns, list) or not patterns:
        findings.append(
            Finding(
                "CONFIG_METADATA_REQUIRED",
                "book-check.toml",
                0,
                "metadata.required 必須是非空字串陣列",
            )
        )
        return []
    valid: list[str] = []
    for pattern in patterns:
        if (
            not isinstance(pattern, str)
            or not pattern
            or PurePosixPath(pattern).is_absolute()
            or ".." in PurePosixPath(pattern).parts
        ):
            findings.append(
                Finding(
                    "CONFIG_METADATA_PATTERN",
                    "book-check.toml",
                    0,
                    f"非法 metadata pattern：{pattern!r}",
                )
            )
            continue
        valid.append(pattern)
    for mandatory in ("front-matter/setup.md",):
        if (root / "manuscript" / mandatory).is_file() and not any(
            fnmatch.fnmatchcase(mandatory, pattern) for pattern in valid
        ):
            findings.append(
                Finding(
                    "CONFIG_METADATA_SCOPE",
                    "book-check.toml",
                    0,
                    f"操作型 front matter 不可退出 metadata scope：{mandatory}",
                )
            )
    return valid


def _verification_ledger_path(root: Path, findings: list[Finding]) -> Path | None:
    """The ledger location is fixed, not configurable: there is exactly one."""
    path = root / "verification" / "ledger.toml"
    if _path_uses_symlink(path, root):
        findings.append(
            Finding(
                "LEDGER_SYMLINK",
                VERIFICATION_LEDGER_PATH,
                0,
                "驗證台帳不得使用 symlink",
            )
        )
        return None
    if not path.is_file():
        findings.append(
            Finding("LEDGER_MISSING", VERIFICATION_LEDGER_PATH, 0, "找不到驗證台帳")
        )
        return None
    if not _case_exact(path, root):
        findings.append(
            Finding(
                "LEDGER_PATH_CASE",
                VERIFICATION_LEDGER_PATH,
                0,
                "台帳路徑大小寫與檔案系統不一致",
            )
        )
    return path


def _summary_links(
    summary: Path, source_root: Path, root: Path, findings: list[Finding]
) -> list[SummaryLink]:
    display = summary.relative_to(root).as_posix()
    text = _read_text(summary, "NAV_SUMMARY_READ", findings, display)
    if text is None:
        return []

    result: list[SummaryLink] = []
    for line_number, line in visible_markdown_lines(text):
        stripped = line.strip()
        if (
            stripped
            and not re.fullmatch(r"#\s+.+", stripped)
            and not re.fullmatch(r"-{3,}", stripped)
            and not SUMMARY_ITEM_RE.fullmatch(line)
        ):
            findings.append(
                Finding(
                    "NAV_SYNTAX",
                    display,
                    line_number,
                    "SUMMARY 只接受 H1、separator 與 mdBook chapter links",
                )
            )
        for match in SUMMARY_LINK_RE.finditer(line):
            target = match.group(2).strip()
            if not target:
                continue  # mdBook draft chapter
            result.append(SummaryLink(match.group(1), target, line_number))
    if not result:
        findings.append(Finding("NAV_EMPTY", display, 0, "SUMMARY 沒有已發布章節連結"))
    return result


def _resolve_summary_target(
    link: SummaryLink,
    summary_display: str,
    source_root: Path,
    findings: list[Finding],
) -> Path | None:
    parsed = urlsplit(link.target)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        findings.append(
            Finding(
                "NAV_TARGET_INVALID",
                summary_display,
                link.line,
                f"章節 target 必須是無 query／fragment 的相對 Markdown 路徑：{link.target}",
            )
        )
        return None
    raw_path = unquote(parsed.path)
    pure = PurePosixPath(raw_path)
    if pure.is_absolute() or ".." in pure.parts or pure.suffix.lower() != ".md":
        findings.append(
            Finding(
                "NAV_TARGET_INVALID",
                summary_display,
                link.line,
                f"章節 target 必須留在 manuscript 且使用 .md：{link.target}",
            )
        )
        return None
    target = source_root.joinpath(*pure.parts)
    try:
        target.resolve(strict=False).relative_to(source_root.resolve())
    except ValueError:
        findings.append(
            Finding(
                "NAV_TARGET_ESCAPE",
                summary_display,
                link.line,
                f"章節 target 逃出 manuscript：{link.target}",
            )
        )
        return None
    return target


def _validate_metadata(
    path: Path,
    display: str,
    text: str,
    states: set[str],
    findings: list[Finding],
) -> None:
    visible = visible_markdown_lines(text)
    values: dict[str, list[tuple[int, str]]] = {
        "配套基線": [],
        "內容狀態": [],
        "最後驗證日期": [],
    }
    for line_number, line in visible:
        match = METADATA_RE.match(line)
        if match:
            values[match.group(1)].append((line_number, match.group(2)))

    for field, occurrences in values.items():
        if not occurrences:
            findings.append(Finding("META_MISSING", display, 1, f"缺少「{field}」"))
        elif len(occurrences) > 1:
            findings.append(
                Finding(
                    "META_DUPLICATE",
                    display,
                    occurrences[1][0],
                    f"「{field}」只能出現一次",
                )
            )

    complete = all(len(items) == 1 for items in values.values())
    baseline = values["配套基線"][0] if len(values["配套基線"]) == 1 else None
    state = values["內容狀態"][0] if len(values["內容狀態"]) == 1 else None
    verified = values["最後驗證日期"][0] if len(values["最後驗證日期"]) == 1 else None

    if complete and baseline and state and verified:
        ordered = [baseline[0], state[0], verified[0]]
        if ordered != list(range(ordered[0], ordered[0] + 3)):
            findings.append(
                Finding(
                    "META_BLOCK",
                    display,
                    ordered[0],
                    "三個 metadata 欄位必須連續且依固定順序",
                )
            )

        h1 = _first_h1(text, display, findings)
        if h1:
            first_after_h1 = next(
                (number for number, line in visible if number > h1[0] and line.strip()),
                None,
            )
            if first_after_h1 != baseline[0]:
                findings.append(
                    Finding(
                        "META_POSITION",
                        display,
                        baseline[0],
                        "metadata 必須緊接在 H1 之後",
                    )
                )

    baseline_identity: tuple[str, str] | None = None
    if baseline:
        baseline_match = BASELINE_RE.fullmatch(baseline[1])
        if baseline_match is None:
            findings.append(
                Finding(
                    "META_BASELINE",
                    display,
                    baseline[0],
                    "配套基線必須是 `emmet-qt-bt1 <tag>@<12–40 hex commit>`",
                )
            )
        else:
            baseline_identity = (baseline_match.group(1), baseline_match.group(2))
    if state and state[1] not in states:
        findings.append(
            Finding(
                "META_STATE",
                display,
                state[0],
                f"未知內容狀態：{state[1]}",
            )
        )
    if verified:
        try:
            parsed_date = dt.date.fromisoformat(verified[1])
            if parsed_date.isoformat() != verified[1]:
                raise ValueError
        except ValueError:
            findings.append(
                Finding(
                    "META_DATE",
                    display,
                    verified[0],
                    "最後驗證日期必須是有效的 YYYY-MM-DD",
                )
            )

    _validate_author_record(display, visible, baseline_identity, findings)


def _validate_author_record(
    display: str,
    visible: list[tuple[int, str]],
    header_baseline: tuple[str, str] | None,
    findings: list[Finding],
) -> None:
    starts = [
        index
        for index, (_, line) in enumerate(visible)
        if re.fullmatch(r"##\s+作者驗證紀錄\s*", line)
    ]
    if not starts:
        findings.append(
            Finding("VERIFY_SECTION_MISSING", display, 1, "缺少「作者驗證紀錄」小節")
        )
        return
    if len(starts) > 1:
        findings.append(
            Finding(
                "VERIFY_SECTION_DUPLICATE",
                display,
                visible[starts[1]][0],
                "作者驗證紀錄只能出現一次",
            )
        )

    start = starts[0] + 1
    end = len(visible)
    for index in range(start, len(visible)):
        if re.match(r"^#{1,6}\s+", visible[index][1]):
            end = index
            findings.append(
                Finding(
                    "VERIFY_SECTION_NOT_LAST",
                    display,
                    visible[index][0],
                    "作者驗證紀錄必須位於文件章末",
                )
            )
            break
    section = visible[start:end]

    fields: dict[str, list[tuple[int, str, int]]] = {}
    for index, (line_number, line) in enumerate(section):
        match = re.match(r"^-\s+([^：]+)：\s*(.*)$", line)
        if match:
            fields.setdefault(match.group(1), []).append(
                (line_number, match.group(2), index)
            )

    required = ("對照 tag／commit", "驗證命令", "通過結果", "待處理差異")
    for name in required:
        occurrences = fields.get(name, [])
        if not occurrences:
            findings.append(
                Finding(
                    "VERIFY_FIELD_MISSING",
                    display,
                    visible[starts[0]][0],
                    f"缺少「{name}」",
                )
            )
        elif len(occurrences) > 1:
            findings.append(
                Finding(
                    "VERIFY_FIELD_DUPLICATE",
                    display,
                    occurrences[1][0],
                    f"「{name}」只能出現一次",
                )
            )

    if not all(len(fields.get(name, [])) == 1 for name in required):
        return

    def field_content(name: str) -> str:
        _, initial, section_index = fields[name][0]
        pieces = [initial]
        for _, line in section[section_index + 1 :]:
            if re.match(r"^-\s+[^：]+：", line):
                break
            if line.strip():
                pieces.append(line.strip())
        return "\n".join(piece for piece in pieces if piece).strip()

    baseline_content = field_content("對照 tag／commit")
    verification_match = VERIFICATION_BASELINE_RE.search(baseline_content)
    if verification_match is None:
        findings.append(
            Finding(
                "VERIFY_BASELINE",
                display,
                fields["對照 tag／commit"][0][0],
                "作者驗證紀錄必須保存完整 40 字元 commit SHA",
            )
        )
    elif header_baseline is not None:
        header_tag, header_sha = header_baseline
        record_tag, record_sha = verification_match.groups()
        if record_tag != header_tag or not record_sha.startswith(header_sha):
            findings.append(
                Finding(
                    "VERIFY_BASELINE_MISMATCH",
                    display,
                    fields["對照 tag／commit"][0][0],
                    "作者驗證紀錄的 tag／SHA 必須與章首配套基線一致",
                )
            )
    for name in ("驗證命令", "通過結果", "待處理差異"):
        if not field_content(name):
            findings.append(
                Finding(
                    "VERIFY_FIELD_EMPTY",
                    display,
                    fields[name][0][0],
                    f"「{name}」不得為空",
                )
            )


def _author_record_field_content(
    visible: list[tuple[int, str]], name: str
) -> str | None:
    starts = [
        index
        for index, (_, line) in enumerate(visible)
        if re.fullmatch(r"##\s+作者驗證紀錄\s*", line)
    ]
    if len(starts) != 1:
        return None
    section = visible[starts[0] + 1 :]
    for index, (_, line) in enumerate(section):
        if re.match(r"^#{1,6}\s+", line):
            break
        field_match = re.match(r"^-\s+([^：]+)：\s*(.*)$", line)
        if field_match is None or field_match.group(1) != name:
            continue
        pieces = [field_match.group(2)]
        for _, continuation in section[index + 1 :]:
            if re.match(r"^#{1,6}\s+", continuation) or re.match(
                r"^-\s+[^：]+：", continuation
            ):
                break
            if continuation.strip():
                pieces.append(continuation.strip())
        return "\n".join(piece for piece in pieces if piece).strip()
    return None


def _ledger_document_claims(
    text: str,
) -> tuple[
    str | None, str | None, str | None, tuple[str, str] | None, tuple[str, str] | None
]:
    visible = visible_markdown_lines(text)
    title = next(
        (
            _plain_title(match.group(1))
            for _, line in visible
            if (match := re.match(r"^#\s+(.+?)\s*$", line))
        ),
        None,
    )
    metadata: dict[str, str] = {}
    for _, line in visible:
        match = METADATA_RE.match(line)
        if match and match.group(1) not in metadata:
            metadata[match.group(1)] = match.group(2).replace("`", "")

    header_identity: tuple[str, str] | None = None
    baseline = metadata.get("配套基線")
    if baseline:
        match = re.fullmatch(r"emmet-qt-bt1\s+([^@\s]+)@([0-9a-f]{12,40})", baseline)
        if match:
            header_identity = (match.group(1), match.group(2))

    author_identity: tuple[str, str] | None = None
    author_baseline = _author_record_field_content(visible, "對照 tag／commit")
    if author_baseline is not None:
        baseline_match = VERIFICATION_BASELINE_RE.search(author_baseline)
        if baseline_match:
            author_identity = (baseline_match.group(1), baseline_match.group(2))

    return (
        title,
        metadata.get("內容狀態"),
        metadata.get("最後驗證日期"),
        header_identity,
        author_identity,
    )


def _validate_ledger_evidence_ref(
    root: Path,
    reference: str,
    companion: Path,
    baseline_sha: str | None,
    repository_files: set[Path],
    display: str,
    record_id: str,
    findings: list[Finding],
) -> None:
    repository_match = LEDGER_EVIDENCE_REF_RE.fullmatch(reference)
    if repository_match:
        raw_path = repository_match.group(1)
        pure = PurePosixPath(raw_path)
        if (
            pure.is_absolute()
            or ".." in pure.parts
            or not pure.parts
            or raw_path != raw_path.strip()
            or any(
                ord(character) < 32 or ord(character) == 127 for character in raw_path
            )
            or pure.parts[0]
            in {".git", ".cache", ".venv", "book", "dist", "site", "node_modules"}
        ):
            findings.append(
                Finding(
                    "LEDGER_EVIDENCE",
                    display,
                    0,
                    f"record {record_id!r} 的 repository evidence path 非法：{reference!r}",
                )
            )
            return
        # 對 baseline commit 驗證，不是對工作樹：檔案「現在還在」不代表它在
        # 台帳宣告的那個 commit 存在過。
        if baseline_sha is not None and not _evidence_exists(
            companion, baseline_sha, raw_path
        ):
            findings.append(
                Finding(
                    "LEDGER_EVIDENCE_MISSING",
                    display,
                    0,
                    f"record {record_id!r} 的 evidence 在 {baseline_sha[:12]} 不存在："
                    f"{raw_path}",
                )
            )
        return

    if reference.startswith("url:"):
        raw_url = reference[4:]
        if any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in raw_url
        ) or re.search(r"%(?![0-9A-Fa-f]{2})", raw_url):
            findings.append(
                Finding(
                    "LEDGER_EVIDENCE",
                    display,
                    0,
                    f"record {record_id!r} 的 URL evidence 非法：{reference!r}",
                )
            )
            return
        try:
            parsed = urlsplit(raw_url)
            hostname = parsed.hostname
            parsed.port
        except ValueError:
            findings.append(
                Finding(
                    "LEDGER_EVIDENCE",
                    display,
                    0,
                    f"record {record_id!r} 的 URL evidence 非法：{reference!r}",
                )
            )
            return
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            findings.append(
                Finding(
                    "LEDGER_EVIDENCE",
                    display,
                    0,
                    f"record {record_id!r} 的 URL evidence 非法：{reference!r}",
                )
            )
        return

    if reference.startswith("book:"):
        raw_book_reference = reference[5:]
        if any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in raw_book_reference
        ) or re.search(r"%(?![0-9A-Fa-f]{2})", raw_book_reference):
            findings.append(
                Finding(
                    "LEDGER_EVIDENCE",
                    display,
                    0,
                    f"record {record_id!r} 的 book evidence path 非法：{reference!r}",
                )
            )
            return
        try:
            parsed = urlsplit(raw_book_reference)
        except ValueError:
            findings.append(
                Finding(
                    "LEDGER_EVIDENCE",
                    display,
                    0,
                    f"record {record_id!r} 的 book evidence path 非法：{reference!r}",
                )
            )
            return
        pure = PurePosixPath(unquote(parsed.path))
        if (
            parsed.scheme
            or parsed.netloc
            or parsed.query
            or pure.is_absolute()
            or ".." in pure.parts
            or not pure.parts
        ):
            findings.append(
                Finding(
                    "LEDGER_EVIDENCE",
                    display,
                    0,
                    f"record {record_id!r} 的 book evidence path 非法：{reference!r}",
                )
            )
            return
        target = root.joinpath(*pure.parts)
        if _path_uses_symlink(target, root):
            target_inside = False
        else:
            try:
                target.resolve(strict=False).relative_to(root.resolve())
                target_inside = True
            except (OSError, RuntimeError, ValueError):
                target_inside = False
        if (
            not target_inside
            or not target.is_file()
            or not _case_exact(target, root)
            or target not in repository_files
            or target.relative_to(root).as_posix() == VERIFICATION_LEDGER_PATH
        ):
            findings.append(
                Finding(
                    "LEDGER_EVIDENCE_MISSING",
                    display,
                    0,
                    f"record {record_id!r} 找不到 book evidence：{reference!r}",
                )
            )
            return
        if parsed.fragment:
            if target.suffix.casefold() != ".md" or unquote(
                parsed.fragment
            ) not in _markdown_heading_ids(target):
                findings.append(
                    Finding(
                        "LEDGER_EVIDENCE_FRAGMENT",
                        display,
                        0,
                        f"record {record_id!r} 找不到 book evidence fragment：{reference!r}",
                    )
                )
        return

    findings.append(
        Finding(
            "LEDGER_EVIDENCE",
            display,
            0,
            f"record {record_id!r} 的 evidence 必須使用 repo:、book: 或 url: 格式",
        )
    )


def _ledger_baselines(
    ledger: dict, companion: Path, display: str, findings: list[Finding]
) -> dict[str, tuple[str, str]]:
    """Resolve and verify one baseline per writing batch.

    Only batches whose declared tag really resolves to the declared commit are
    returned. A broken baseline therefore also fails every record that depends
    on it, instead of letting the rest pass on a root cause that was reported
    once and then forgotten.
    """
    raw = ledger.get("baselines")
    if not isinstance(raw, dict) or not raw:
        findings.append(
            Finding("BASELINE_MISSING", display, 0, "[baselines] 必須是非空 table")
        )
        return {}
    resolved: dict[str, tuple[str, str]] = {}
    for batch in sorted(raw):
        label = f"baselines.{batch}"
        value = raw[batch]
        if not LEDGER_BATCH_RE.fullmatch(batch):
            findings.append(
                Finding(
                    "BASELINE_BATCH", display, 0, f"{label}：batch 必須是 W<number>"
                )
            )
            continue
        if not isinstance(value, str) or (
            match := LEDGER_BASELINE_RE.fullmatch(value.strip())
        ) is None:
            findings.append(
                Finding(
                    "BASELINE_FORMAT",
                    display,
                    0,
                    f"{label}：值必須是 tag@40 字元 SHA",
                )
            )
            continue
        tag, sha = match.group(1), match.group(2)
        if _verify_baseline(companion, tag, sha, display, label, findings):
            resolved[batch] = (tag, sha)
    return resolved


def _validate_verification_ledger(
    root: Path,
    ledger_path: Path,
    required_documents: set[Path],
    texts: dict[Path, str],
    states: set[str],
    findings: list[Finding],
    *,
    check_git: bool,
    companion: Path,
) -> None:
    display = ledger_path.relative_to(root).as_posix()
    ledger = _load_toml(ledger_path, "LEDGER_PARSE", findings, display)
    repository_files = set(_repository_files(root))
    baselines = _ledger_baselines(ledger, companion, display, findings)
    unknown_top_level = set(ledger) - {"schema_version", "baselines", "records"}
    if unknown_top_level:
        findings.append(
            Finding(
                "LEDGER_SCHEMA",
                display,
                0,
                f"未知的台帳根欄位：{', '.join(sorted(unknown_top_level))}",
            )
        )
    version = ledger.get("schema_version")
    if type(version) is not int or version != LEDGER_SCHEMA_VERSION:
        findings.append(
            Finding(
                "LEDGER_SCHEMA_VERSION",
                display,
                0,
                f"schema_version 必須是 {LEDGER_SCHEMA_VERSION}",
            )
        )
    records = ledger.get("records")
    if not isinstance(records, list) or not records:
        findings.append(
            Finding("LEDGER_RECORDS", display, 0, "records 必須是非空 table array")
        )
        records = []

    seen_ids: set[str] = set()
    seen_claims: set[tuple[str, str]] = set()
    covered_documents: set[Path] = set()
    needs_revalidation_documents: set[Path] = set()
    for index, raw_record in enumerate(records, start=1):
        label = f"record #{index}"
        if not isinstance(raw_record, dict):
            findings.append(
                Finding("LEDGER_RECORD", display, 0, f"{label} 必須是 table")
            )
            continue
        record = raw_record
        identifier_value = record.get("id")
        if isinstance(identifier_value, str) and identifier_value.strip():
            label = f"record {identifier_value!r}"
        missing_fields = LEDGER_RECORD_FIELDS - set(record)
        unknown_fields = set(record) - (
            LEDGER_RECORD_FIELDS | LEDGER_CONDITIONAL_FIELDS
        )
        if missing_fields:
            findings.append(
                Finding(
                    "LEDGER_FIELD_MISSING",
                    display,
                    0,
                    f"{label} 缺少欄位：{', '.join(sorted(missing_fields))}",
                )
            )
        if unknown_fields:
            findings.append(
                Finding(
                    "LEDGER_FIELD_UNKNOWN",
                    display,
                    0,
                    f"{label} 有未知欄位：{', '.join(sorted(unknown_fields))}",
                )
            )

        strings: dict[str, str] = {}
        for field in sorted(LEDGER_STRING_FIELDS):
            if field not in record:
                continue
            value = record.get(field)
            if not isinstance(value, str) or not value.strip():
                findings.append(
                    Finding(
                        "LEDGER_FIELD_TYPE",
                        display,
                        0,
                        f"{label} 的 {field} 必須是非空字串",
                    )
                )
                continue
            strings[field] = value.strip()
            if PLACEHOLDER_RE.search(value):
                findings.append(
                    Finding(
                        "LEDGER_PLACEHOLDER",
                        display,
                        0,
                        f"{label} 的 {field} 不得使用 placeholder",
                    )
                )

        lists: dict[str, list[str]] = {}
        for field in sorted(LEDGER_LIST_FIELDS):
            if field not in record:
                continue
            value = record.get(field)
            if not isinstance(value, list) or any(
                not isinstance(item, str) or not item.strip() for item in value
            ):
                findings.append(
                    Finding(
                        "LEDGER_FIELD_TYPE",
                        display,
                        0,
                        f"{label} 的 {field} 必須是字串陣列",
                    )
                )
                continue
            cleaned = [item.strip() for item in value]
            lists[field] = cleaned
            if len(cleaned) != len(set(cleaned)):
                findings.append(
                    Finding(
                        "LEDGER_LIST_DUPLICATE",
                        display,
                        0,
                        f"{label} 的 {field} 不得重複",
                    )
                )
            if any(PLACEHOLDER_RE.search(item) for item in cleaned):
                findings.append(
                    Finding(
                        "LEDGER_PLACEHOLDER",
                        display,
                        0,
                        f"{label} 的 {field} 不得使用 placeholder",
                    )
                )

        record_id = strings.get("id")
        if record_id is not None:
            if not LEDGER_ID_RE.fullmatch(record_id):
                findings.append(
                    Finding(
                        "LEDGER_ID",
                        display,
                        0,
                        f"{label} 的 id 必須是小寫 kebab-case",
                    )
                )
            if record_id in seen_ids:
                findings.append(
                    Finding(
                        "LEDGER_ID_DUPLICATE",
                        display,
                        0,
                        f"重複的 record id：{record_id}",
                    )
                )
            seen_ids.add(record_id)
        record_label = record_id or f"#{index}"

        claim_document = strings.get("document")
        claim_text = strings.get("claim")
        if claim_document is not None and claim_text is not None:
            claim_key = (PurePosixPath(claim_document).as_posix(), claim_text)
            if claim_key in seen_claims:
                findings.append(
                    Finding(
                        "LEDGER_CLAIM_DUPLICATE",
                        display,
                        0,
                        f"record {record_label!r} 重複同一 document 的 claim",
                    )
                )
            seen_claims.add(claim_key)

        batch = strings.get("batch")
        if batch is not None and not LEDGER_BATCH_RE.fullmatch(batch):
            findings.append(
                Finding(
                    "LEDGER_BATCH",
                    display,
                    0,
                    f"record {record_label!r} 的 batch 必須使用 W<number>",
                )
            )

        executable = record.get("executable")
        if "executable" in record and not isinstance(executable, bool):
            findings.append(
                Finding(
                    "LEDGER_FIELD_TYPE",
                    display,
                    0,
                    f"record {record_label!r} 的 executable 必須是 bool",
                )
            )
            executable = None
        # bool 是 int 的子類；用 type() 才擋得住 `oracle_exit_code = true`。
        if "oracle_exit_code" in record and type(record["oracle_exit_code"]) is not int:
            findings.append(
                Finding(
                    "LEDGER_ORACLE",
                    display,
                    0,
                    f"record {record_label!r} 的 oracle_exit_code 必須是 int",
                )
            )

        state = strings.get("content_state")
        if state is not None and state not in states:
            findings.append(
                Finding(
                    "LEDGER_STATE",
                    display,
                    0,
                    f"record {record_label!r} 使用未知內容狀態：{state}",
                )
            )

        result = strings.get("result")
        if result is not None and result not in LEDGER_RESULT_VALUES:
            findings.append(
                Finding(
                    "LEDGER_RESULT",
                    display,
                    0,
                    f"record {record_label!r} 的 result 必須是 pass 或 needs-revalidation",
                )
            )
        if result == "needs-revalidation" and state != "需重驗":
            findings.append(
                Finding(
                    "LEDGER_REVALIDATION_STATE",
                    display,
                    0,
                    f"record {record_label!r} 只有「需重驗」文件可標 needs-revalidation",
                )
            )

        override = strings.get("verified_against")
        if result == "needs-revalidation":
            if override is None:
                findings.append(
                    Finding(
                        "LEDGER_OVERRIDE_MISSING",
                        display,
                        0,
                        f"record {record_label!r} 為 needs-revalidation，必須以 "
                        "verified_against 記錄最後一次通過的基線",
                    )
                )
        elif override is not None:
            findings.append(
                Finding(
                    "LEDGER_OVERRIDE_FORBIDDEN",
                    display,
                    0,
                    f"record {record_label!r} 不是 needs-revalidation，不得使用 "
                    "verified_against",
                )
            )

        note = strings.get("executable_note")
        if executable is False:
            if (
                note is None
                or not note.startswith("不適用：")
                or not note.removeprefix("不適用：").strip()
            ):
                findings.append(
                    Finding(
                        "LEDGER_EXECUTABLE_NOTE_MISSING",
                        display,
                        0,
                        f"record {record_label!r} executable=false 時必須以「不適用：」"
                        "說明具體原因",
                    )
                )
        elif note is not None:
            findings.append(
                Finding(
                    "LEDGER_EXECUTABLE_NOTE_FORBIDDEN",
                    display,
                    0,
                    f"record {record_label!r} executable=true 時不得有 executable_note",
                )
            )

        # verified_against 記錄的舊基線不是免驗區，走與 [baselines] 相同的驗證。
        effective: tuple[str, str] | None = None
        if override is not None:
            match = LEDGER_BASELINE_RE.fullmatch(override)
            if match is None:
                findings.append(
                    Finding(
                        "BASELINE_FORMAT",
                        display,
                        0,
                        f"record {record_label!r} 的 verified_against 必須是 "
                        "tag@40 字元 SHA",
                    )
                )
            elif _verify_baseline(
                companion,
                match.group(1),
                match.group(2),
                display,
                f"record {record_label!r} 的 verified_against",
                findings,
            ):
                effective = (match.group(1), match.group(2))
        elif batch is not None:
            effective = baselines.get(batch)
            if effective is None:
                findings.append(
                    Finding(
                        "LEDGER_BATCH_UNDECLARED",
                        display,
                        0,
                        f"record {record_label!r} 的 batch {batch} 在 [baselines] "
                        "沒有可用宣告",
                    )
                )

        verified_on = strings.get("verified_on")
        if verified_on is not None:
            try:
                parsed_date = dt.date.fromisoformat(verified_on)
                if parsed_date.isoformat() != verified_on:
                    raise ValueError
            except ValueError:
                findings.append(
                    Finding(
                        "LEDGER_DATE",
                        display,
                        0,
                        f"record {record_label!r} 的 verified_on 必須是 YYYY-MM-DD",
                    )
                )

        checksums = lists.get("data_checksums")
        checksum_ids: set[str] = set()
        if checksums is not None:
            for checksum in checksums:
                checksum_match = LEDGER_DATA_CHECKSUM_RE.fullmatch(checksum)
                if checksum_match is None:
                    findings.append(
                        Finding(
                            "LEDGER_CHECKSUM",
                            display,
                            0,
                            f"record {record_label!r} 的 checksum 必須是 <logical-id>=sha256:<64 hex>",
                        )
                    )
                    continue
                logical_id = checksum_match.group(1)
                if logical_id in checksum_ids:
                    findings.append(
                        Finding(
                            "LEDGER_CHECKSUM_ID_DUPLICATE",
                            display,
                            0,
                            f"record {record_label!r} 的 data logical-id 不得重複：{logical_id}",
                        )
                    )
                checksum_ids.add(logical_id)
        checksum_note = strings.get("data_checksum_note")
        if checksum_note is not None and checksums is not None:
            checksum_na = checksum_note.startswith("不適用：")
            checksum_na_reason = checksum_note.removeprefix("不適用：").strip()
            if not checksums and (not checksum_na or not checksum_na_reason):
                findings.append(
                    Finding(
                        "LEDGER_CHECKSUM_NA",
                        display,
                        0,
                        f"record {record_label!r} 無資料輸入時須以「不適用：」說明具體原因",
                    )
                )
            if checksums and checksum_na:
                findings.append(
                    Finding(
                        "LEDGER_CHECKSUM_CONTRADICTION",
                        display,
                        0,
                        f"record {record_label!r} 已列 checksum，不得同時標示不適用",
                    )
                )

        entrypoints = lists.get("formal_entrypoints", [])
        schemas = lists.get("schemas", [])
        interface_note = strings.get("interface_note")
        if (
            interface_note is not None
            and "formal_entrypoints" in lists
            and "schemas" in lists
        ):
            interface_na = interface_note.startswith("不適用：")
            interface_na_reason = interface_note.removeprefix("不適用：").strip()
            if (
                not entrypoints
                and not schemas
                and (not interface_na or not interface_na_reason)
            ):
                findings.append(
                    Finding(
                        "LEDGER_INTERFACE_NA",
                        display,
                        0,
                        f"record {record_label!r} 無正式入口／schema 時須以「不適用：」說明具體原因",
                    )
                )
            if (entrypoints or schemas) and interface_na:
                findings.append(
                    Finding(
                        "LEDGER_INTERFACE_CONTRADICTION",
                        display,
                        0,
                        f"record {record_label!r} 已列正式入口／schema，不得同時標示不適用",
                    )
                )
        if "verification_commands" in lists and not lists["verification_commands"]:
            findings.append(
                Finding(
                    "LEDGER_COMMANDS",
                    display,
                    0,
                    f"record {record_label!r} 至少需要一個驗證命令",
                )
            )
        if "evidence_refs" in lists and not lists["evidence_refs"]:
            findings.append(
                Finding(
                    "LEDGER_EVIDENCE_REFS",
                    display,
                    0,
                    f"record {record_label!r} 至少需要一個可追溯 evidence ref",
                )
            )
        if "revalidation_triggers" in lists and not lists["revalidation_triggers"]:
            findings.append(
                Finding(
                    "LEDGER_REVALIDATION_TRIGGERS",
                    display,
                    0,
                    f"record {record_label!r} 至少需要一個重驗觸發原因",
                )
            )
        for reference in lists.get("evidence_refs", []):
            _validate_ledger_evidence_ref(
                root,
                reference,
                companion,
                effective[1] if effective is not None else None,
                repository_files,
                display,
                record_label,
                findings,
            )

        document_value = strings.get("document")
        if document_value is None:
            continue
        pure_document = PurePosixPath(document_value)
        if (
            pure_document.is_absolute()
            or ".." in pure_document.parts
            or not pure_document.parts
            or pure_document.parts[0] != "manuscript"
            or pure_document.suffix != ".md"
            or pure_document.as_posix() != document_value
        ):
            findings.append(
                Finding(
                    "LEDGER_DOCUMENT",
                    display,
                    0,
                    f"record {record_label!r} 的 document 必須是 manuscript 內的小寫 .md 路徑",
                )
            )
            continue
        document = root.joinpath(*pure_document.parts)
        try:
            document.resolve(strict=False).relative_to(root.resolve())
        except (OSError, RuntimeError, ValueError):
            findings.append(
                Finding(
                    "LEDGER_DOCUMENT",
                    display,
                    0,
                    f"record {record_label!r} 的 document 逃出 repository",
                )
            )
            continue
        if (
            _path_uses_symlink(document, root)
            or not document.is_file()
            or not _case_exact(document, root)
        ):
            findings.append(
                Finding(
                    "LEDGER_DOCUMENT_MISSING",
                    display,
                    0,
                    f"record {record_label!r} 找不到 document：{document_value}",
                )
            )
            continue
        if document not in required_documents:
            findings.append(
                Finding(
                    "LEDGER_DOCUMENT_SCOPE",
                    display,
                    0,
                    f"record {record_label!r} 的 document 不在 metadata 驗證範圍",
                )
            )
            continue
        covered_documents.add(document)
        if result == "needs-revalidation":
            needs_revalidation_documents.add(document)

        document_text = texts.get(document)
        if document_text is None:
            document_text = _read_text(
                document,
                "LEDGER_DOCUMENT_READ",
                findings,
                document_value,
            )
            if document_text is None:
                continue
            texts[document] = document_text
        title, doc_state, doc_date, header_identity, author_identity = (
            _ledger_document_claims(document_text)
        )
        chapter = strings.get("chapter")
        if chapter is not None and title is not None and chapter != title:
            findings.append(
                Finding(
                    "LEDGER_CHAPTER",
                    display,
                    0,
                    f"record {record_label!r} 的 chapter 與文件 H1 不一致",
                )
            )
        if state is not None and doc_state is not None and state != doc_state:
            findings.append(
                Finding(
                    "LEDGER_STATE_MISMATCH",
                    display,
                    0,
                    f"record {record_label!r} 的內容狀態與章首不一致",
                )
            )
        if verified_on is not None and doc_date is not None and verified_on != doc_date:
            findings.append(
                Finding(
                    "LEDGER_DATE_MISMATCH",
                    display,
                    0,
                    f"record {record_label!r} 的日期與章首最後驗證日期不一致",
                )
            )
        if effective is not None and header_identity is not None:
            tag, commit = effective
            header_tag, header_commit = header_identity
            if tag != header_tag or not commit.startswith(header_commit):
                findings.append(
                    Finding(
                        "LEDGER_BASELINE_MISMATCH",
                        display,
                        0,
                        f"record {record_label!r} 的有效基線與章首不一致",
                    )
                )
        if effective is not None and author_identity is not None:
            if effective != author_identity:
                findings.append(
                    Finding(
                        "LEDGER_AUTHOR_MISMATCH",
                        display,
                        0,
                        f"record {record_label!r} 的有效基線與章末作者紀錄不一致",
                    )
                )

    for missing in sorted(
        required_documents - covered_documents, key=lambda item: item.as_posix()
    ):
        findings.append(
            Finding(
                "LEDGER_COVERAGE",
                display,
                0,
                f"缺少文件台帳紀錄：{missing.relative_to(root).as_posix()}",
            )
        )
    for document in required_documents:
        text = texts.get(document)
        if text is None:
            continue
        _, document_state, _, _, _ = _ledger_document_claims(text)
        if document_state == "需重驗" and document not in needs_revalidation_documents:
            findings.append(
                Finding(
                    "LEDGER_REVALIDATION_COVERAGE",
                    display,
                    0,
                    f"需重驗文件缺少 needs-revalidation 紀錄：{document.relative_to(root).as_posix()}",
                )
            )

    if check_git:
        ignored = _git_command(
            root, "check-ignore", "--no-index", "--quiet", "--", display
        )
        if ignored.returncode == 0:
            findings.append(
                Finding(
                    "LEDGER_IGNORED",
                    display,
                    0,
                    "驗證台帳不可被 .gitignore 靜默排除",
                )
            )


def _git_command(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _resolve_companion(root: Path) -> tuple[Path | None, str | None, str]:
    """Locate the pinned companion repository.

    Read-only: callers may only run `rev-parse` and `cat-file` against it.
    An explicit `$EMMET_QT_BT1_DIR` never falls back to the sibling default;
    a typo must fail rather than silently downgrade to another repository.
    The last element names the path that was actually checked (and where it
    came from), so failure diagnostics never blame the wrong knob.
    """
    override = os.environ.get(COMPANION_ENV)
    if override:
        candidate = Path(override)
        tried = f"${COMPANION_ENV}={candidate}"
    elif override is not None:
        # 空字串是設定錯誤，不是「未設定」：fallback 會靜默降級到別的
        # repository，Path("") 又等於 cwd，兩者都不允許。
        return None, "COMPANION_MISSING", f"${COMPANION_ENV}（已設定但為空）"
    else:
        candidate = root.parent / COMPANION_REPO
        tried = f"{candidate}（預設 sibling；${COMPANION_ENV} 未設定）"
    if not candidate.is_dir():
        return None, "COMPANION_MISSING", tried
    if _git_command(candidate, "rev-parse", "--git-common-dir").returncode != 0:
        return None, "COMPANION_NOT_GIT", tried
    return candidate.resolve(), None, tried


def _verify_baseline(
    companion: Path,
    tag: str,
    sha: str,
    display: str,
    label: str,
    findings: list[Finding],
) -> bool:
    """Confirm the declared tag really resolves to the declared commit.

    A tag can be moved, so the tag alone is not an identity.
    """
    resolved = _git_command(
        companion, "rev-parse", "--verify", "--quiet", f"{tag}^{{commit}}"
    )
    if resolved.returncode != 0:
        findings.append(
            Finding(
                "BASELINE_TAG_UNRESOLVED",
                display,
                0,
                f"{label}：配套 repo 沒有 tag {tag}；可能需要 git fetch --tags",
            )
        )
        return False
    actual = resolved.stdout.strip()
    if actual != sha:
        findings.append(
            Finding(
                "BASELINE_TAG_MISMATCH",
                display,
                0,
                f"{label}：tag {tag} 解析到 {actual}，台帳宣告 {sha}；tag 可能已被移動",
            )
        )
        return False
    return True


def _evidence_exists(companion: Path, sha: str, path: str) -> bool:
    return _git_command(companion, "cat-file", "-e", f"{sha}:{path}").returncode == 0


def _repository_files(root: Path) -> list[Path]:
    listed = _git_command(
        root,
        "ls-files",
        "-z",
        "--cached",
        "--others",
        "--exclude-standard",
        "--",
    )
    if listed.returncode == 0:
        files = [
            root / relative
            for relative in listed.stdout.split("\0")
            if relative
            and ((root / relative).is_file() or (root / relative).is_symlink())
        ]
        return sorted(set(files), key=lambda path: path.as_posix())

    excluded = {".git", ".cache", ".venv", "book", "dist", "site", "node_modules"}
    return sorted(
        (
            path
            for path in root.rglob("*")
            if (path.is_file() or path.is_symlink())
            and path.relative_to(root).parts
            and path.relative_to(root).parts[0] not in excluded
        ),
        key=lambda path: path.as_posix(),
    )


def _repository_markdown_files(root: Path) -> list[Path]:
    return [
        path
        for path in _repository_files(root)
        if path.is_file() and path.suffix.casefold() == ".md"
    ]


class _RawMarkdownHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: list[tuple[int, str, str]] = []
        self.dangers: list[tuple[int, str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._handle_tag(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._handle_tag(tag, attrs)

    def _handle_tag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        line = self.getpos()[0]
        if tag in FORBIDDEN_RAW_HTML_TAGS:
            self.dangers.append((line, "HTML_RAW_TAG", f"不允許 raw <{tag}>"))
        for name, value in attrs:
            name = name.casefold()
            if name.startswith("on") or name in {"srcdoc", "style"}:
                self.dangers.append(
                    (line, "HTML_RAW_ATTRIBUTE", f"不允許 raw HTML attribute：{name}")
                )
            if value is None:
                continue
            if name in {"href", "src", "poster", "action"}:
                self.references.append((line, name, value))
            elif name == "srcset":
                for candidate in value.split(","):
                    url = (
                        candidate.strip().split(maxsplit=1)[0]
                        if candidate.strip()
                        else ""
                    )
                    if url:
                        self.references.append((line, name, url))


def _raw_markdown_html(text: str) -> _RawMarkdownHtmlParser:
    source_lines = text.splitlines()
    sanitized = ["" for _ in source_lines]
    for line_number, line in visible_markdown_lines(text):
        sanitized[line_number - 1] = _mask_inline_code(line)
    parser = _RawMarkdownHtmlParser()
    parser.feed("\n".join(sanitized))
    parser.close()
    return parser


def _validate_manuscript_raw_html(
    text: str, display: str, findings: list[Finding]
) -> None:
    parser = _raw_markdown_html(text)
    for line_number, code, message in parser.dangers:
        findings.append(Finding(code, display, line_number, message))


def _markdown_link_targets(path: Path) -> tuple[list[tuple[int, str]], list[Finding]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        return [], [Finding("MD_READ", path.as_posix(), 0, str(error))]

    definitions: dict[str, tuple[int, str]] = {}
    references: list[tuple[int, str]] = []
    targets: list[tuple[int, str]] = []
    findings: list[Finding] = []
    masked_lines: list[tuple[int, str]] = []
    for line_number, line in visible_markdown_lines(text):
        masked = _mask_inline_code(line)
        masked_lines.append((line_number, masked))
        definition = REFERENCE_DEFINITION_RE.match(masked)
        if definition:
            label = _reference_label(definition.group(1))
            target = definition.group(2) or definition.group(3)
            if label in definitions:
                findings.append(
                    Finding(
                        "MD_REFERENCE_DUPLICATE",
                        path.as_posix(),
                        line_number,
                        f"reference label 重複：{definition.group(1)}",
                    )
                )
            else:
                definitions[label] = (line_number, target)

        for match in INLINE_LINK_RE.finditer(masked):
            if match.start() > 0 and masked[match.start() - 1] == "\\":
                continue
            targets.append((line_number, match.group(1) or match.group(2)))

        for match in REFERENCE_USE_RE.finditer(masked):
            if match.start() > 0 and masked[match.start() - 1] == "\\":
                continue
            label = _reference_label(match.group(2) or match.group(1))
            references.append((line_number, label))

    raw_html = _raw_markdown_html(text)
    targets.extend(
        (line_number, value) for line_number, _, value in raw_html.references
    )

    for line_number, line in masked_lines:
        characters = list(line)
        spans = []
        spans.extend(match.span() for match in INLINE_LINK_RE.finditer(line))
        spans.extend(match.span() for match in REFERENCE_USE_RE.finditer(line))
        if REFERENCE_DEFINITION_RE.match(line):
            spans.append((0, len(line)))
        for start, end in spans:
            for position in range(start, end):
                characters[position] = " "
        shortcut_line = "".join(characters)
        for match in SHORTCUT_REFERENCE_RE.finditer(shortcut_line):
            if match.start() > 0 and shortcut_line[match.start() - 1] == "\\":
                continue
            label = _reference_label(match.group(1))
            if label in definitions:
                references.append((line_number, label))

    for line_number, label in references:
        definition = definitions.get(label)
        if definition is None:
            findings.append(
                Finding(
                    "MD_REFERENCE_UNDEFINED",
                    path.as_posix(),
                    line_number,
                    f"找不到 reference definition：{label}",
                )
            )
            continue
        targets.append((line_number, definition[1]))
    return targets, findings


def validate_repository_markdown_links(root: Path) -> list[Finding]:
    root = root.resolve()
    findings: list[Finding] = []
    heading_cache: dict[Path, set[str]] = {}
    for source in _repository_markdown_files(root):
        display = source.relative_to(root).as_posix()
        targets, parse_findings = _markdown_link_targets(source)
        for finding in parse_findings:
            finding.path = display
        findings.extend(parse_findings)

        for line_number, value in targets:
            if value.startswith("//"):
                findings.append(
                    Finding(
                        "MD_SCHEME",
                        display,
                        line_number,
                        f"不允許 protocol-relative URL：{value}",
                    )
                )
                continue
            try:
                parsed = urlsplit(value)
            except ValueError as error:
                findings.append(
                    Finding(
                        "MD_TARGET_INVALID", display, line_number, f"{value}：{error}"
                    )
                )
                continue
            if parsed.scheme or parsed.netloc:
                scheme = parsed.scheme.casefold()
                if scheme == "file":
                    findings.append(
                        Finding(
                            "MD_FILE_URL",
                            display,
                            line_number,
                            f"不可使用 file URL：{value}",
                        )
                    )
                elif scheme not in {"http", "https", "mailto"}:
                    findings.append(
                        Finding(
                            "MD_SCHEME",
                            display,
                            line_number,
                            f"不允許 URL scheme：{value}",
                        )
                    )
                continue

            raw_path = unquote(parsed.path)
            if raw_path.startswith("/"):
                findings.append(
                    Finding(
                        "MD_TARGET_ABSOLUTE",
                        display,
                        line_number,
                        f"repository 文件必須使用相對連結：{value}",
                    )
                )
                continue
            target = source if not raw_path else source.parent / raw_path
            target = target.resolve(strict=False)
            try:
                target.relative_to(root)
            except ValueError:
                findings.append(
                    Finding(
                        "MD_TARGET_ESCAPE",
                        display,
                        line_number,
                        f"本機連結逃出 repository：{value}",
                    )
                )
                continue
            if not target.exists():
                findings.append(
                    Finding(
                        "MD_TARGET_MISSING",
                        display,
                        line_number,
                        f"找不到本機 target：{value}",
                    )
                )
                continue
            if not _case_exact(target, root):
                findings.append(
                    Finding(
                        "MD_TARGET_CASE",
                        display,
                        line_number,
                        f"target 大小寫與檔案系統不一致：{value}",
                    )
                )

            fragment = unquote(parsed.fragment)
            if not fragment:
                continue
            if not target.is_file() or target.suffix.casefold() != ".md":
                findings.append(
                    Finding(
                        "MD_FRAGMENT_TARGET",
                        display,
                        line_number,
                        f"fragment target 必須是 Markdown 檔案：{value}",
                    )
                )
                continue
            identifiers = heading_cache.setdefault(
                target, _markdown_heading_ids(target)
            )
            if fragment not in identifiers:
                findings.append(
                    Finding(
                        "MD_FRAGMENT_MISSING",
                        display,
                        line_number,
                        f"找不到 fragment #{fragment}（target：{value}）",
                    )
                )
    return sorted(findings, key=Finding.sort_key)


def _validate_git_paths(
    root: Path, output_root: Path, reader_files: list[Path], findings: list[Finding]
) -> None:
    inside = _git_command(root, "rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        findings.append(
            Finding("GIT_REPOSITORY", ".", 0, "book check 必須在 Git worktree 執行")
        )
        return

    output_rel = output_root.relative_to(root).as_posix()
    tracked = _git_command(root, "ls-files", "--", output_rel)
    if tracked.returncode != 0:
        findings.append(Finding("GIT_LS_FILES", output_rel, 0, tracked.stderr.strip()))
    elif tracked.stdout.strip():
        findings.append(
            Finding("OUTPUT_TRACKED", output_rel, 0, "建置產物不可納入版本控制")
        )

    probe = f"{output_rel}/.book-check-ignore-probe"
    ignored = _git_command(root, "check-ignore", "--no-index", "--quiet", "--", probe)
    if ignored.returncode != 0:
        findings.append(
            Finding(
                "OUTPUT_NOT_IGNORED",
                output_rel,
                0,
                "canonical output 必須由 .gitignore 忽略",
            )
        )

    for source in reader_files:
        relative = source.relative_to(root).as_posix()
        ignored_source = _git_command(
            root, "check-ignore", "--no-index", "--quiet", "--", relative
        )
        if ignored_source.returncode == 0:
            findings.append(
                Finding(
                    "SOURCE_IGNORED",
                    relative,
                    0,
                    "manuscript 來源不可被 .gitignore 靜默排除",
                )
            )


def validate_source(
    root: Path, *, check_git: bool = True, companion: Path | None = None
) -> list[Finding]:
    root = root.resolve()
    findings: list[Finding] = []
    source_root, output_root = _validate_book_config(root, findings)
    check_config = _load_toml(
        root / "book-check.toml",
        "CONFIG_CHECK",
        findings,
        "book-check.toml",
    )
    patterns = _metadata_patterns(root, findings, check_config)
    ledger_path = _verification_ledger_path(root, findings)
    if ledger_path is not None and companion is None:
        companion, error, tried = _resolve_companion(root)
        if companion is None:
            if error == "COMPANION_NOT_GIT":
                code, reason = "COMPANION_NOT_GIT", "配套路徑不是 git repository"
            else:
                code, reason = "COMPANION_MISSING", "找不到配套 repo"
            findings.append(
                Finding(
                    code,
                    VERIFICATION_LEDGER_PATH,
                    0,
                    f"{reason}（{tried}）；台帳身分驗證無法執行",
                )
            )
            ledger_path = None
    findings.extend(validate_repository_markdown_links(root))
    summary = source_root / "SUMMARY.md"
    summary_display = summary.relative_to(root).as_posix()

    managed = (
        sorted(
            (
                path
                for path in source_root.rglob("*")
                if path.is_file()
                and path.suffix.casefold() == ".md"
                and path != summary
            ),
            key=lambda path: path.as_posix(),
        )
        if source_root.is_dir()
        else []
    )
    reader_files = (
        sorted(
            (path for path in source_root.rglob("*") if path.is_file()),
            key=lambda path: path.as_posix(),
        )
        if source_root.is_dir()
        else []
    )
    reader_entries = (
        [source_root, *source_root.rglob("*")] if source_root.exists() else []
    )
    if not source_root.is_dir():
        findings.append(Finding("SOURCE_ROOT", "manuscript", 0, "來源目錄不存在"))

    for path in managed:
        if path.suffix != ".md":
            findings.append(
                Finding(
                    "SOURCE_EXTENSION_CASE",
                    path.relative_to(root).as_posix(),
                    0,
                    "Markdown 出版來源必須使用小寫 .md 副檔名",
                )
            )
    for path in reader_entries:
        if path.is_symlink():
            findings.append(
                Finding(
                    "SOURCE_SYMLINK",
                    path.relative_to(root).as_posix(),
                    0,
                    "manuscript 的檔案與目錄都不得使用 symlink",
                )
            )

    links = _summary_links(summary, source_root, root, findings)
    nav_occurrences: dict[Path, list[SummaryLink]] = {}
    texts: dict[Path, str] = {}
    for link in links:
        target = _resolve_summary_target(link, summary_display, source_root, findings)
        if target is None:
            continue
        nav_occurrences.setdefault(target, []).append(link)
        if not target.is_file():
            findings.append(
                Finding(
                    "NAV_TARGET_MISSING",
                    summary_display,
                    link.line,
                    f"找不到章節：{link.target}",
                )
            )
            continue
        if not _case_exact(target, source_root):
            findings.append(
                Finding(
                    "NAV_TARGET_CASE",
                    summary_display,
                    link.line,
                    f"章節 target 大小寫與檔案系統不一致：{link.target}",
                )
            )
        display = target.relative_to(root).as_posix()
        text = _read_text(target, "DOC_READ", findings, display)
        if text is None:
            continue
        texts[target] = text
        h1 = _first_h1(text, display, findings)
        if h1 and _plain_title(h1[1]) != _plain_title(link.title):
            findings.append(
                Finding(
                    "NAV_TITLE_MISMATCH",
                    summary_display,
                    link.line,
                    f"導航標題與 {display}:{h1[0]} 的 H1 不一致",
                )
            )

    for target, occurrences in nav_occurrences.items():
        if len(occurrences) > 1:
            findings.append(
                Finding(
                    "NAV_DUPLICATE",
                    summary_display,
                    occurrences[1].line,
                    f"章節重複出現在導航：{target.relative_to(source_root).as_posix()}",
                )
            )

    nav_set = {path for path in nav_occurrences if path.is_file()}
    for orphan in sorted(set(managed) - nav_set, key=lambda path: path.as_posix()):
        findings.append(
            Finding(
                "NAV_ORPHAN",
                orphan.relative_to(root).as_posix(),
                0,
                "manuscript 檔案必須直接且唯一列入 SUMMARY.md",
            )
        )

    for path in managed:
        display = path.relative_to(root).as_posix()
        text = texts.get(path)
        if text is None:
            text = _read_text(path, "DOC_READ", findings, display)
            if text is not None:
                texts[path] = text
        if text is not None:
            _validate_manuscript_raw_html(text, display, findings)

    states = _content_states(source_root / "preface.md", findings, root)
    managed_rel = {path: path.relative_to(source_root).as_posix() for path in managed}
    required: set[Path] = set()
    for pattern in patterns:
        matches = {
            path
            for path, relative in managed_rel.items()
            if fnmatch.fnmatchcase(relative, pattern)
        }
        if not matches:
            findings.append(
                Finding(
                    "CONFIG_METADATA_PATTERN_EMPTY",
                    "book-check.toml",
                    0,
                    f"metadata pattern 沒有對應檔案：{pattern}",
                )
            )
        required.update(matches)

    chapters = {
        path
        for path, relative in managed_rel.items()
        if PurePosixPath(relative).parts
        and PurePosixPath(relative).parts[0] == "chapters"
    }
    for uncovered in sorted(chapters - required, key=lambda item: item.as_posix()):
        findings.append(
            Finding(
                "CONFIG_METADATA_SCOPE",
                "book-check.toml",
                0,
                f"正文不可退出 metadata scope：{managed_rel[uncovered]}",
            )
        )
    required.update(chapters)

    for path in sorted(required, key=lambda item: item.as_posix()):
        display = path.relative_to(root).as_posix()
        text = texts.get(path)
        if text is None:
            text = _read_text(path, "DOC_READ", findings, display)
        if text is not None:
            _validate_metadata(path, display, text, states, findings)

    if ledger_path is not None and companion is not None:
        _validate_verification_ledger(
            root,
            ledger_path,
            required,
            texts,
            states,
            findings,
            check_git=check_git,
            companion=companion,
        )

    if check_git:
        _validate_git_paths(root, output_root, reader_files, findings)

    return sorted(findings, key=Finding.sort_key)


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.references: list[tuple[int, str, str]] = []
        self.language: str | None = None
        self.text_parts: list[str] = []
        self.headings: set[tuple[str, str]] = set()
        self._heading_tag: str | None = None
        self._heading_parts: list[str] = []
        self._ignored_text_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        tag = tag.casefold()
        if tag in {"script", "style"}:
            self._ignored_text_depth += 1
        if re.fullmatch(r"h[1-6]", tag):
            self._heading_tag = tag
            self._heading_parts = []
        if tag == "html":
            self.language = values.get("lang")
        element_id = values.get("id")
        if element_id:
            self.ids.add(element_id)
        if tag == "a" and values.get("name"):
            self.ids.add(values["name"] or "")
        for attribute in ("href", "src", "poster", "action"):
            value = values.get(attribute)
            if value:
                self.references.append((self.getpos()[0], attribute, value))
        srcset = values.get("srcset")
        if srcset:
            for candidate in srcset.split(","):
                value = (
                    candidate.strip().split(maxsplit=1)[0] if candidate.strip() else ""
                )
                if value:
                    self.references.append((self.getpos()[0], "srcset", value))

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == self._heading_tag:
            heading_text = " ".join("".join(self._heading_parts).split())
            self.headings.add((tag, heading_text))
            self._heading_tag = None
            self._heading_parts = []
        if tag in {"script", "style"} and self._ignored_text_depth:
            self._ignored_text_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._ignored_text_depth:
            return
        self.text_parts.append(data)
        if self._heading_tag is not None:
            self._heading_parts.append(data)

    @property
    def visible_text(self) -> str:
        return " ".join(" ".join(self.text_parts).split())


def _case_exact(path: Path, boundary: Path) -> bool:
    try:
        relative = path.relative_to(boundary)
    except ValueError:
        return False
    cursor = boundary
    for part in relative.parts:
        try:
            names = {child.name for child in cursor.iterdir()}
        except OSError:
            return False
        if part not in names:
            return False
        cursor /= part
    return True


def _parse_html(
    path: Path, output_root: Path, findings: list[Finding]
) -> _PageParser | None:
    display = path.relative_to(output_root).as_posix()
    text = _read_text(path, "HTML_READ", findings, display)
    if text is None:
        return None
    parser = _PageParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception as error:  # HTMLParser errors are rare but must fail closed.
        findings.append(Finding("HTML_PARSE", display, 0, str(error)))
        return None
    return parser


def _built_summary_targets(root: Path, findings: list[Finding]) -> list[Path]:
    source_root = root / "manuscript"
    summary = source_root / "SUMMARY.md"
    links = _summary_links(summary, source_root, root, findings)
    targets: list[Path] = []
    for link in links:
        target = _resolve_summary_target(
            link, summary.relative_to(root).as_posix(), source_root, findings
        )
        if target is not None and target.is_file():
            targets.append(target.relative_to(source_root).with_suffix(".html"))
    return targets


def _required_metadata_output_paths(root: Path) -> list[Path]:
    source_root = root / "manuscript"
    try:
        with (root / "book-check.toml").open("rb") as handle:
            patterns = tomllib.load(handle).get("metadata", {}).get("required", [])
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        patterns = []
    managed = [
        path
        for path in source_root.rglob("*")
        if path.is_file()
        and path.suffix.casefold() == ".md"
        and path.name != "SUMMARY.md"
    ]
    required = {
        path
        for path in managed
        if path.relative_to(source_root).parts
        and path.relative_to(source_root).parts[0] == "chapters"
    }
    for pattern in patterns if isinstance(patterns, list) else []:
        if isinstance(pattern, str):
            required.update(
                path
                for path in managed
                if fnmatch.fnmatchcase(
                    path.relative_to(source_root).as_posix(), pattern
                )
            )
    return sorted(required, key=lambda path: path.as_posix())


def _source_evidence_values(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return []
    values: list[str] = []
    for _, line in visible_markdown_lines(text):
        metadata = METADATA_RE.match(line)
        if metadata:
            values.append(metadata.group(2).replace("`", ""))
        verification = re.match(r"^-\s+對照 tag／commit：\s*(.+)$", line)
        if verification:
            values.append(verification.group(1).replace("`", ""))
    return values


def _validate_page_links(
    output_root: Path,
    parsed_pages: dict[Path, _PageParser],
    findings: list[Finding],
) -> None:
    for page, parser in list(parsed_pages.items()):
        page_display = page.relative_to(output_root).as_posix()
        for line_number, attribute, value in parser.references:
            if value.startswith("//"):
                findings.append(
                    Finding(
                        "HTML_SCHEME",
                        page_display,
                        line_number,
                        f"不允許 protocol-relative {attribute}：{value}",
                    )
                )
                continue
            try:
                parsed = urlsplit(value)
            except ValueError as error:
                findings.append(
                    Finding(
                        "HTML_TARGET_INVALID",
                        page_display,
                        line_number,
                        f"無法解析 {attribute}={value}：{error}",
                    )
                )
                continue
            if parsed.scheme:
                scheme = parsed.scheme.casefold()
                if attribute == "href" and scheme in {"http", "https", "mailto"}:
                    continue
                if scheme == "file":
                    findings.append(
                        Finding(
                            "HTML_FILE_URL",
                            page_display,
                            line_number,
                            f"不可使用 file URL：{value}",
                        )
                    )
                elif attribute in {"src", "srcset", "poster"}:
                    findings.append(
                        Finding(
                            "HTML_RESOURCE_EXTERNAL",
                            page_display,
                            line_number,
                            f"reader resource 必須是 output 內本機檔案：{attribute}={value}",
                        )
                    )
                else:
                    findings.append(
                        Finding(
                            "HTML_SCHEME",
                            page_display,
                            line_number,
                            f"不允許 {attribute} URL scheme：{value}",
                        )
                    )
                continue

            raw_path = unquote(parsed.path)
            if raw_path.startswith("/"):
                target = output_root / raw_path.lstrip("/")
            elif raw_path:
                target = page.parent / raw_path
            else:
                target = page
            target = target.resolve(strict=False)
            try:
                target.relative_to(output_root)
            except ValueError:
                findings.append(
                    Finding(
                        "HTML_LINK_ESCAPE",
                        page_display,
                        line_number,
                        f"{attribute} 逃出 HTML output：{value}",
                    )
                )
                continue
            if target.is_dir():
                target /= "index.html"
            if not target.is_file():
                findings.append(
                    Finding(
                        "HTML_TARGET_MISSING",
                        page_display,
                        line_number,
                        f"找不到 {attribute} target：{value}",
                    )
                )
                continue
            if not _case_exact(target, output_root):
                findings.append(
                    Finding(
                        "HTML_TARGET_CASE",
                        page_display,
                        line_number,
                        f"target 大小寫與檔案系統不一致：{value}",
                    )
                )
            fragment = unquote(parsed.fragment)
            if fragment and target.suffix.lower() == ".html":
                target_parser = parsed_pages.get(target.resolve())
                if target_parser is None:
                    target_parser = _parse_html(target, output_root, findings)
                    if target_parser is not None:
                        parsed_pages[target.resolve()] = target_parser
                if target_parser is not None and fragment not in target_parser.ids:
                    findings.append(
                        Finding(
                            "HTML_FRAGMENT_MISSING",
                            page_display,
                            line_number,
                            f"找不到 fragment #{fragment}（target：{value}）",
                        )
                    )


def validate_repository_render(
    root: Path, mdbook: Path, workspace: Path
) -> list[Finding]:
    """Render every repository Markdown file with mdBook, then crawl real links."""
    root = root.resolve()
    findings: list[Finding] = []
    synthetic_root = Path(tempfile.mkdtemp(prefix="repo-links-", dir=workspace))
    try:
        source_root = synthetic_root / "src"
        repository_copy = source_root / "repo"
        markdown_paths: list[Path] = []
        for source in _repository_files(root):
            relative = source.relative_to(root)
            display = relative.as_posix()
            if source.is_symlink():
                findings.append(
                    Finding(
                        "REPO_SOURCE_SYMLINK",
                        display,
                        0,
                        "repository render check 不複製 symlink",
                    )
                )
                continue
            if not source.is_file():
                continue
            destination = repository_copy / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            if source.suffix.casefold() == ".md":
                markdown_paths.append(relative)

        if findings:
            return sorted(findings, key=Finding.sort_key)

        source_root.mkdir(parents=True, exist_ok=True)
        (source_root / "index.md").write_text(
            "# Repository Markdown link check\n", encoding="utf-8"
        )
        summary_lines = [
            "# Summary",
            "",
            "[Repository Markdown link check](index.md)",
            "",
            "# Repository Markdown",
            "",
        ]
        for relative in sorted(markdown_paths, key=lambda path: path.as_posix()):
            label = relative.as_posix().replace("[", "\\[").replace("]", "\\]")
            summary_lines.append(f"- [{label}](repo/{relative.as_posix()})")
        (source_root / "SUMMARY.md").write_text(
            "\n".join(summary_lines) + "\n", encoding="utf-8"
        )
        (synthetic_root / "book.toml").write_text(
            """[book]
title = "Repository Markdown link check"
language = "zh-TW"
src = "src"

[build]
create-missing = false

[output.html]
""",
            encoding="utf-8",
        )
        output_root = synthetic_root / "output"
        try:
            build = subprocess.run(
                [
                    str(mdbook),
                    "build",
                    str(synthetic_root),
                    "--dest-dir",
                    str(output_root),
                ],
                cwd=root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except OSError as error:
            return [Finding("REPO_RENDER_FAILED", ".", 0, str(error))]
        if build.returncode != 0:
            detail = (build.stderr or build.stdout).strip().splitlines()
            message = detail[-1] if detail else f"mdbook exit {build.returncode}"
            return [Finding("REPO_RENDER_FAILED", ".", 0, message)]

        pages = sorted(output_root.rglob("*.html"), key=lambda path: path.as_posix())
        parsed_pages: dict[Path, _PageParser] = {}
        for page in pages:
            parser = _parse_html(page, output_root, findings)
            if parser is None:
                continue
            parsed_pages[page.resolve()] = parser
            if parser.language != "zh-TW":
                findings.append(
                    Finding(
                        "HTML_LANGUAGE",
                        page.relative_to(output_root).as_posix(),
                        0,
                        f"html lang 應為 zh-TW，實際為 {parser.language!r}",
                    )
                )
        for relative in markdown_paths:
            rendered_relative = (
                relative.parent / "index.html"
                if relative.name.casefold() == "readme.md"
                else relative.with_suffix(".html")
            )
            expected = output_root / "repo" / rendered_relative
            if not expected.is_file():
                findings.append(
                    Finding(
                        "REPO_RENDER_CHAPTER_MISSING",
                        relative.as_posix(),
                        0,
                        "repository Markdown 沒有對應 rendered HTML",
                    )
                )
        _validate_page_links(output_root, parsed_pages, findings)
        return sorted(findings, key=Finding.sort_key)
    finally:
        shutil.rmtree(synthetic_root, ignore_errors=True)


def validate_html_output(root: Path, output_root: Path | None = None) -> list[Finding]:
    root = root.resolve()
    output_root = (output_root or root / "book").resolve()
    findings: list[Finding] = []
    if not output_root.is_dir():
        return [
            Finding("OUTPUT_MISSING", output_root.as_posix(), 0, "HTML output 不存在")
        ]

    index = output_root / "index.html"
    if not index.is_file() or index.stat().st_size == 0:
        findings.append(Finding("OUTPUT_INDEX", "index.html", 0, "缺少非空 HTML 入口"))

    pages = sorted(output_root.rglob("*.html"), key=lambda path: path.as_posix())
    if not pages:
        findings.append(Finding("OUTPUT_HTML_EMPTY", ".", 0, "沒有產生 HTML 頁面"))
        return findings

    parsed_pages: dict[Path, _PageParser] = {}
    for page in pages:
        parser = _parse_html(page, output_root, findings)
        if parser is None:
            continue
        parsed_pages[page.resolve()] = parser
        if parser.language != "zh-TW":
            findings.append(
                Finding(
                    "HTML_LANGUAGE",
                    page.relative_to(output_root).as_posix(),
                    0,
                    f"html lang 應為 zh-TW，實際為 {parser.language!r}",
                )
            )

    for expected in _built_summary_targets(root, findings):
        target = output_root / expected
        if not target.is_file() or target.stat().st_size == 0:
            findings.append(
                Finding(
                    "OUTPUT_CHAPTER_MISSING",
                    expected.as_posix(),
                    0,
                    "SUMMARY 中的已發布章節沒有對應 HTML",
                )
            )

    source_root = root / "manuscript"
    for source in _required_metadata_output_paths(root):
        expected = source.relative_to(source_root).with_suffix(".html")
        parser = parsed_pages.get((output_root / expected).resolve())
        if parser is None:
            continue
        if ("h2", "作者驗證紀錄") not in parser.headings:
            findings.append(
                Finding(
                    "OUTPUT_VERIFY_HEADING",
                    expected.as_posix(),
                    0,
                    "rendered chapter 缺少作者驗證紀錄 H2",
                )
            )
        for value in _source_evidence_values(source):
            if value not in parser.visible_text:
                findings.append(
                    Finding(
                        "OUTPUT_EVIDENCE_MISSING",
                        expected.as_posix(),
                        0,
                        f"rendered chapter 看不到來源證據：{value}",
                    )
                )

    preface_parser = parsed_pages.get((output_root / "preface.html").resolve())
    if preface_parser is not None and ("h2", "內容狀態") not in preface_parser.headings:
        findings.append(
            Finding(
                "OUTPUT_STATE_HEADING",
                "preface.html",
                0,
                "rendered preface 缺少內容狀態 H2",
            )
        )

    _validate_page_links(output_root, parsed_pages, findings)

    return sorted(findings, key=Finding.sort_key)


def output_manifest(output_root: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    count = 0
    for path in sorted(output_root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file():
            continue
        relative = path.relative_to(output_root).as_posix().encode("utf-8")
        content_digest = hashlib.sha256(path.read_bytes()).digest()
        digest.update(relative)
        digest.update(b"\0")
        digest.update(content_digest)
        digest.update(b"\n")
        count += 1
    return digest.hexdigest(), count


def output_ownership_finding(output_root: Path) -> Finding | None:
    if not output_root.exists() and not output_root.is_symlink():
        return None
    if output_root.is_symlink() or not output_root.is_dir():
        return Finding("OUTPUT_UNSAFE", "book", 0, "book 必須是一般目錄或尚不存在")
    if not any(output_root.iterdir()):
        return None
    marker = output_root / OUTPUT_MARKER
    try:
        marker_content = marker.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        marker_content = None
    if marker_content != OUTPUT_MARKER_CONTENT:
        return Finding(
            "OUTPUT_UNOWNED",
            "book",
            0,
            "既有非空 book/ 不屬於 book check；為避免資料遺失，不會刪除或覆寫",
        )
    return None


def _print_findings(findings: list[Finding]) -> None:
    for finding in findings:
        print(finding, file=sys.stderr)


def _mdbook_version(mdbook: Path) -> str | None:
    try:
        result = subprocess.run(
            [str(mdbook), "--version"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def run(root: Path, mdbook: Path) -> int:
    root = root.resolve()
    print(f"Python {sys.version.split()[0]}")

    companion, error, tried = _resolve_companion(root)
    if companion is None:
        if error == "COMPANION_NOT_GIT":
            print(
                f"COMPANION_NOT_GIT 配套路徑不是 git repository：{tried}",
                file=sys.stderr,
            )
        else:
            print(
                f"COMPANION_MISSING 找不到配套 repo：{tried}\n"
                "台帳身分驗證無法執行；先依 manuscript/front-matter/setup.md "
                "建立隔離 worktree。",
                file=sys.stderr,
            )
        return 2
    print(f"companion: {companion}")

    source_findings = validate_source(root, companion=companion)
    if source_findings:
        _print_findings(source_findings)
        return 1
    print("source check: PASS")

    actual_version = _mdbook_version(mdbook)
    if actual_version not in {f"mdbook v{MDBOOK_VERSION}", f"mdbook {MDBOOK_VERSION}"}:
        print(
            f"TOOL_VERSION mdBook 必須是 {MDBOOK_VERSION}，實際為 {actual_version!r}",
            file=sys.stderr,
        )
        return 1
    print(actual_version)

    staging_parent = root / ".cache" / "book-build"
    staging_parent.mkdir(parents=True, exist_ok=True)
    repository_findings = validate_repository_render(root, mdbook, staging_parent)
    if repository_findings:
        _print_findings(repository_findings)
        return 1
    print("repository render link check: PASS")

    output_root = root / "book"
    ownership_finding = output_ownership_finding(output_root)
    if ownership_finding is not None:
        print(ownership_finding, file=sys.stderr)
        return 1

    staging = Path(tempfile.mkdtemp(prefix="staging-", dir=staging_parent))
    environment = os.environ.copy()
    environment["SOURCE_DATE_EPOCH"] = "0"
    try:
        build = subprocess.run(
            [str(mdbook), "build", str(root), "--dest-dir", str(staging)],
            cwd=root,
            env=environment,
            check=False,
        )
    except OSError as error:
        shutil.rmtree(staging, ignore_errors=True)
        print(f"BUILD_FAILED 無法執行 mdBook：{error}", file=sys.stderr)
        return 1
    if build.returncode != 0:
        shutil.rmtree(staging, ignore_errors=True)
        print(f"BUILD_FAILED mdbook build exit {build.returncode}", file=sys.stderr)
        return build.returncode or 1

    output_findings = validate_html_output(root, staging)
    if output_findings:
        shutil.rmtree(staging, ignore_errors=True)
        _print_findings(output_findings)
        return 1
    (staging / OUTPUT_MARKER).write_text(OUTPUT_MARKER_CONTENT, encoding="utf-8")
    try:
        if output_root.exists():
            shutil.rmtree(output_root)
        os.replace(staging, output_root)
    except OSError as error:
        shutil.rmtree(staging, ignore_errors=True)
        print(f"OUTPUT_PUBLISH 無法更新 book/：{error}", file=sys.stderr)
        return 1
    manifest, count = output_manifest(output_root)
    print(f"HTML check: PASS ({count} files)")
    print(f"HTML manifest SHA-256: {manifest}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--mdbook", type=Path, required=True)
    arguments = parser.parse_args()
    return run(arguments.root, arguments.mdbook)


if __name__ == "__main__":
    raise SystemExit(main())
