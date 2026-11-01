#!/usr/bin/env python3
"""Structure and style linter for the awesome-lead-generation list.

The goal is simple: any pull request that would break the list (wrong icon,
out-of-order entry, dead Markdown, stale table of contents, tracking links…)
fails here with an actionable message, before a human has to look at it.

Usage:
    python3 scripts/lint_readme.py                     # lint README.md
    python3 scripts/lint_readme.py --strict             # warnings fail too
    python3 scripts/lint_readme.py --fix --sort         # auto-fix + sort
    python3 scripts/lint_readme.py --list-rules         # show every rule

Exit codes: 0 = clean, 1 = errors (or warnings in --strict), 2 = usage error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

import awesome_md as md  # noqa: E402  (local import after sys.path tweak)

# --------------------------------------------------------------------------- #
# Rule registry
# --------------------------------------------------------------------------- #

ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class Rule:
    code: str
    severity: str
    summary: str


def _rule(code: str, severity: str, summary: str) -> Rule:
    return Rule(code, severity, summary)


RULES: dict[str, Rule] = {
    rule.code: rule
    for rule in [
        # repository level
        _rule("REPO001", ERROR, "A required repository file is missing."),
        # entry format
        _rule("FMT001", ERROR, "Entry line does not match the required format."),
        _rule("FMT002", ERROR, "Icon image must be image/alg_I.png (web) or image/alg_R.png (github)."),
        _rule("FMT003", ERROR, "Icon label must be 'web' or 'github'."),
        _rule("FMT004", ERROR, "Icon label and icon image do not match each other."),
        _rule("FMT005", ERROR, "Description must be preceded by ' - '."),
        _rule("FMT006", WARNING, "Use ' - ' (hyphen) as the description separator."),
        _rule("FMT007", WARNING, "Entry is not inside a category heading (## or ###)."),
        _rule("FMT008", ERROR, "Tool name must be plain text, not a Markdown link or image."),
        # urls
        _rule("URL001", ERROR, "Link must be an absolute http(s) URL."),
        _rule("URL002", ERROR, "Affiliate, referral and tracking parameters are not allowed."),
        _rule("URL003", ERROR, "URL is already in the list."),
        _rule("URL004", ERROR, "Tool name is already in the list."),
        _rule("URL005", WARNING, "Prefer https:// over http://."),
        _rule("URL006", WARNING, "github icon used but the URL is not a github.com repository."),
        _rule("URL007", WARNING, "web icon used but the URL points to a GitHub repository."),
        _rule("URL008", ERROR, "URL contains whitespace or angle brackets."),
        # descriptions
        _rule("DESC001", ERROR, "Entry has no description."),
        _rule("DESC002", WARNING, "Description should end with a period."),
        _rule("DESC003", WARNING, "Description uses marketing language / superlatives."),
        _rule("DESC004", WARNING, "Description is longer than 220 characters."),
        _rule("DESC005", WARNING, "Description is shorter than 25 characters."),
        _rule("DESC006", WARNING, "Description duplicates another entry."),
        _rule("DESC007", WARNING, "Avoid exclamation marks and emoji in descriptions."),
        # ordering
        _rule("ORDER001", ERROR, "Entries must be in strict alphabetical order."),
        # table of contents
        _rule("TOC001", ERROR, "Missing '## Contents' section."),
        _rule("TOC002", ERROR, "TOC links to a heading that does not exist."),
        _rule("TOC003", ERROR, "A heading is missing from the TOC."),
        _rule("TOC004", ERROR, "TOC entries are in the wrong order."),
        _rule("TOC005", WARNING, "TOC indentation is inconsistent (2 spaces for ##, 6 spaces for ###)."),
        _rule("TOC006", ERROR, "TOC anchor does not match the heading anchor."),
        _rule("TOC007", WARNING, "TOC label does not match the heading text."),
        # local links
        _rule("LINK001", ERROR, "Local link or image points to a missing file."),
        _rule("LINK002", ERROR, "Anchor link points to a heading that does not exist."),
        # hygiene
        _rule("WS001", WARNING, "Trailing whitespace."),
        _rule("WS002", WARNING, "Tab character in prose."),
        _rule("WS003", WARNING, "File must end with a single newline."),
        _rule("WS004", WARNING, "Multiple consecutive blank lines."),
        _rule("H001", WARNING, "Duplicate heading text (anchors collide)."),
        _rule("H002", WARNING, "Heading level jumps by more than one."),
    ]
}

REQUIRED_FILES = ["README.md", "CONTRIBUTING.md", "CODE_OF_CONDUCT.md", "LICENSE"]

SUPERLATIVE_RE = re.compile(
    r"\b("
    r"best|world'?s|#1|number\s+one|leading|revolutionar\w*|cutting[- ]edge|game[- ]chang\w*|"
    r"ultimate|unparalleled|unmatched|seamless\w*|supercharg\w*|blazing|insanely|effortless\w*|"
    r"state[- ]of[- ]the[- ]art|industry[- ]leading|must[- ]have|amazing|incredible"
    r")\b",
    re.IGNORECASE,
)

TOC_ITEM_RE = re.compile(r"^(?P<indent>[ \t]*)[-*][ \t]+\[(?P<text>[^\]]+)\]\(#(?P<anchor>[^)\s]+)\)\s*$")

MAX_DESCRIPTION = 220
MIN_DESCRIPTION = 25


# --------------------------------------------------------------------------- #
# Issues
# --------------------------------------------------------------------------- #


@dataclass
class Issue:
    code: str
    severity: str
    line: int
    message: str

    def as_dict(self) -> dict:
        return asdict(self)


class Report:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.issues: list[Issue] = []

    def add(self, code: str, line: int, message: str) -> None:
        rule = RULES[code]
        self.issues.append(
            Issue(code=code, severity=rule.severity, line=max(1, line), message=message)
        )

    @property
    def errors(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity == ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity == WARNING]

    def sorted_issues(self) -> list[Issue]:
        return sorted(self.issues, key=lambda issue: (issue.line, issue.code))


# --------------------------------------------------------------------------- #
# Entry checks
# --------------------------------------------------------------------------- #


def check_entries(document: md.Document, report: Report) -> None:
    for line_no, raw in document.malformed:
        report.add(
            "FMT001",
            line_no,
            "expected `![web|github](image/alg_I.png|image/alg_R.png) [Name](url) - Description.`; "
            f"got `{md.shorten(raw)}`",
        )

    valid_images = set(md.ICON_IMAGES.values())
    seen_urls: dict[str, md.Entry] = {}
    seen_names: dict[str, md.Entry] = {}
    seen_descriptions: dict[str, md.Entry] = {}

    for entry in document.entries:
        # -- format ---------------------------------------------------------
        if entry.image not in valid_images:
            report.add(
                "FMT002",
                entry.line,
                f"unexpected icon image `{entry.image}`; use `image/alg_I.png` for web or "
                "`image/alg_R.png` for GitHub tools",
            )
        if entry.icon not in md.ICON_IMAGES:
            report.add(
                "FMT003",
                entry.line,
                f"unexpected icon label `{entry.icon}`; use `web` or `github`",
            )
        elif entry.image in valid_images and md.ICON_IMAGES[entry.icon] != entry.image:
            report.add(
                "FMT004",
                entry.line,
                f"icon `{entry.icon}` must use `{md.ICON_IMAGES[entry.icon]}`, not `{entry.image}`",
            )

        if entry.separator is None:
            report.add(
                "FMT005",
                entry.line,
                "description is not separated from the link; add ` - ` before it",
            )
        elif entry.separator != "-":
            report.add(
                "FMT006",
                entry.line,
                "use a hyphen as separator: `[Name](url) - Description.`",
            )

        if entry.section is None or entry.section == "Contents":
            report.add(
                "FMT007",
                entry.line,
                "entry is not under a category heading; move it into the right `##` section",
            )

        if re.search(r"\[[^\]]*\]\(|<img\b", entry.name):
            report.add("FMT008", entry.line, f"tool name `{entry.name}` contains Markdown")

        # -- url ------------------------------------------------------------
        if re.search(r"[\s<>]", entry.url):
            report.add("URL008", entry.line, f"URL `{md.shorten(entry.url)}` contains whitespace")
        if not md.is_http_url(entry.url):
            report.add(
                "URL001",
                entry.line,
                f"`{md.shorten(entry.url)}` is not an absolute http(s) URL "
                "(no relative paths, no bare domains)",
            )
        else:
            if md.has_tracking_params(entry.url):
                report.add(
                    "URL002",
                    entry.line,
                    f"tracking or affiliate parameters found in `{md.shorten(entry.url)}`",
                )
            if entry.url.startswith("http://"):
                report.add(
                    "URL005",
                    entry.line,
                    f"`{md.shorten(entry.url)}` uses http://; use https:// when the site supports it",
                )

            host = (urlsplit(entry.url).hostname or "").lower()
            is_github_repo = host in {"github.com", "www.github.com"} and urlsplit(entry.url).path.count("/") >= 2
            if entry.icon == "github" and not is_github_repo:
                report.add(
                    "URL006",
                    entry.line,
                    f"github icon requires a github.com repository URL, got `{md.shorten(entry.url)}`",
                )
            if entry.icon == "web" and is_github_repo:
                report.add(
                    "URL007",
                    entry.line,
                    f"this looks like a GitHub repository; use the `github` icon (`{md.shorten(entry.url)}`)",
                )

            key = md.url_without_fragment(entry.url).rstrip("/").lower()
            if key in seen_urls:
                other = seen_urls[key]
                report.add(
                    "URL003",
                    entry.line,
                    f"`{md.shorten(entry.url)}` is already listed on line {other.line}",
                )
            else:
                seen_urls[key] = entry

        # -- name -----------------------------------------------------------
        name_key = md.normalize_name(entry.name)
        if name_key in seen_names:
            report.add(
                "URL004",
                entry.line,
                f"tool `{entry.name}` is already listed on line {seen_names[name_key].line}",
            )
        else:
            seen_names[name_key] = entry

        # -- description ----------------------------------------------------
        description = entry.description.strip()
        if not description:
            report.add("DESC001", entry.line, f"`{entry.name}` has no description")
            continue

        if not description.endswith((".", "!", "?")):
            report.add(
                "DESC002",
                entry.line,
                f"description for `{entry.name}` should end with a period",
            )
        superlative = SUPERLATIVE_RE.search(description)
        if superlative:
            report.add(
                "DESC003",
                entry.line,
                f"`{superlative.group(0)}` is marketing language; describe what the tool does instead",
            )
        if len(description) > MAX_DESCRIPTION:
            report.add(
                "DESC004",
                entry.line,
                f"description is {len(description)} characters (limit {MAX_DESCRIPTION}); "
                "keep it to one sentence",
            )
        if len(description) < MIN_DESCRIPTION:
            report.add(
                "DESC005",
                entry.line,
                f"description is only {len(description)} characters; explain the core utility",
            )
        if "!" in description or re.search(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", description):
            report.add(
                "DESC007",
                entry.line,
                f"description for `{entry.name}` contains an exclamation mark or emoji",
            )
        description_key = description.casefold()
        if description_key in seen_descriptions:
            report.add(
                "DESC006",
                entry.line,
                f"description duplicates the one on line {seen_descriptions[description_key].line}",
            )
        else:
            seen_descriptions[description_key] = entry


def check_ordering(document: md.Document, report: Report) -> None:
    groups: dict[tuple[Optional[str], Optional[str]], list[md.Entry]] = {}
    for entry in document.entries:
        groups.setdefault(entry.group, []).append(entry)

    for entries in groups.values():
        keys = [entry.sort_key for entry in entries]
        for current, following in zip(entries, entries[1:]):
            if current.sort_key > following.sort_key:
                report.add(
                    "ORDER001",
                    following.line,
                    f"`{following.name}` should come before `{current.name}` "
                    f"(line {current.line}); sections are alphabetical, case-insensitive",
                )


# --------------------------------------------------------------------------- #
# Table of contents
# --------------------------------------------------------------------------- #


@dataclass
class TocItem:
    line: int
    indent: int
    text: str
    anchor: str


def parse_toc(document: md.Document) -> Optional[tuple[list[TocItem], int, int]]:
    """Return (items, first_index, last_index) or ``None`` when there is no TOC."""

    contents_index = None
    for index, heading in enumerate(document.headings):
        if heading.title.lower() == "contents" and heading.level == 2:
            contents_index = heading.line - 1
            break
    if contents_index is None:
        return None

    first = last = None
    items: list[TocItem] = []
    for index in range(contents_index + 1, len(document.lines)):
        line = document.lines[index]
        if not line.strip():
            continue
        match = TOC_ITEM_RE.match(line)
        if not match:
            break
        if first is None:
            first = index
        last = index
        items.append(
            TocItem(
                line=index + 1,
                indent=len(match.group("indent").expandtabs(4)),
                text=match.group("text").strip(),
                anchor=match.group("anchor"),
            )
        )
    if first is None or last is None:
        return None
    return items, first, last


def expected_toc_lines(document: md.Document, top_indent: int = 2, sub_indent: int = 6) -> list[str]:
    lines: list[str] = []
    for heading in document.headings:
        if heading.level == 2 and heading.title.lower() == "contents":
            continue
        if heading.level == 2:
            lines.append(f"{' ' * top_indent}- [{heading.title}](#{heading.anchor})")
        elif heading.level == 3:
            lines.append(f"{' ' * sub_indent}- [{heading.title}](#{heading.anchor})")
    return lines


def check_toc(document: md.Document, report: Report) -> None:
    parsed = parse_toc(document)
    if parsed is None:
        report.add(
            "TOC001",
            document.headings[0].line if document.headings else 1,
            "add a `## Contents` section with a bullet list of every `##`/`###` heading",
        )
        return

    items, _first, _last = parsed
    headings = [
        heading
        for heading in document.headings
        if heading.level in (2, 3)
        and not (heading.level == 2 and heading.title.lower() == "contents")
    ]

    by_anchor = {heading.anchor: heading for heading in headings}
    by_title = {heading.title: heading for heading in headings}
    toc_titles = {item.text for item in items}
    toc_anchors = {item.anchor for item in items}

    for item in items:
        heading = by_title.get(item.text)
        if heading is not None:
            if heading.anchor != item.anchor:
                report.add(
                    "TOC006",
                    item.line,
                    f"`{item.text}` should link to `#{heading.anchor}`, not `#{item.anchor}`",
                )
        elif item.anchor in by_anchor:
            report.add(
                "TOC007",
                item.line,
                f"TOC label `{item.text}` should match the heading `{by_anchor[item.anchor].title}`",
            )
        else:
            report.add(
                "TOC002",
                item.line,
                f"TOC entry `{item.text}` points to `#{item.anchor}`, but no heading produces that anchor",
            )

    for heading in headings:
        if heading.title in toc_titles or heading.anchor in toc_anchors:
            continue
        report.add(
            "TOC003",
            heading.line,
            f"heading `{heading.title}` is missing from the TOC (anchor `#{heading.anchor}`)",
        )

    # Order: resolve every item to a heading, then compare with document order.
    resolved: list[tuple[TocItem, md.Heading]] = []
    for item in items:
        heading = by_title.get(item.text) or by_anchor.get(item.anchor)
        if heading is not None:
            resolved.append((item, heading))

    expected_order = [heading for _, heading in resolved]
    document_order = [heading for heading in headings if heading in expected_order]
    for position, ((item, heading), actual) in enumerate(zip(resolved, document_order)):
        if heading is not actual:
            report.add(
                "TOC004",
                item.line,
                f"TOC order differs from the document: expected `{actual.title}` "
                f"at position {position + 1}, found `{heading.title}`",
            )
            break

    expected_indent = {2: 2, 3: 6}
    for item in items:
        heading = by_title.get(item.text) or by_anchor.get(item.anchor)
        if heading is None:
            continue
        if item.indent != expected_indent[heading.level]:
            report.add(
                "TOC005",
                item.line,
                f"`{item.text}` should be indented by {expected_indent[heading.level]} spaces",
            )


# --------------------------------------------------------------------------- #
# Links, headings, hygiene
# --------------------------------------------------------------------------- #


def check_local_links(document: md.Document, report: Report) -> None:
    base_dir = document.path.parent
    anchors = {heading.anchor for heading in document.headings}
    known_files: dict[Path, set[str]] = {}

    parsed_toc = parse_toc(document)
    toc_lines = {item.line for item in parsed_toc[0]} if parsed_toc else set()

    for link in md.extract_links(document):
        if link.line in toc_lines:
            continue
        target = link.target
        if not target or target.startswith(("mailto:", "tel:", "http://", "https://", "//")):
            continue

        parts = urlsplit(target)
        fragment = parts.fragment
        path = parts.path

        if not path:
            if fragment and fragment not in anchors:
                report.add("LINK002", link.line, f"`#{fragment}` does not match any heading anchor")
            continue

        candidate = (base_dir / path.lstrip("/")).resolve()
        if not candidate.exists():
            report.add("LINK001", link.line, f"`{target}` points to a missing file")
            continue

        if fragment:
            if candidate not in known_files:
                try:
                    known_files[candidate] = {
                        heading.anchor for heading in md.load(candidate).headings
                    }
                except (OSError, UnicodeDecodeError):
                    known_files[candidate] = set()
            if fragment not in known_files[candidate]:
                report.add(
                    "LINK002",
                    link.line,
                    f"`{target}` points to a heading anchor that does not exist",
                )


def check_headings(document: md.Document, report: Report) -> None:
    seen: dict[str, md.Heading] = {}
    previous_level = 0
    for heading in document.headings:
        if heading.title in seen:
            report.add(
                "H001",
                heading.line,
                f"heading `{heading.title}` already appears on line {seen[heading.title].line}",
            )
        else:
            seen[heading.title] = heading

        if previous_level and heading.level > previous_level + 1:
            report.add(
                "H002",
                heading.line,
                f"heading level jumps from h{previous_level} to h{heading.level}",
            )
        previous_level = heading.level


def check_whitespace(document: md.Document, report: Report) -> None:
    blank_run = 0
    for index, line in enumerate(document.lines):
        line_no = index + 1
        trailing = line[len(line.rstrip()) :]
        if trailing and trailing != "  ":
            report.add("WS001", line_no, f"trailing whitespace: `{md.shorten(line, 60)}`")
        if "\t" in line:
            report.add("WS002", line_no, "tab character found; use spaces")
        if not line.strip():
            blank_run += 1
            if blank_run >= 2:
                report.add("WS004", line_no, "more than one consecutive blank line")
        else:
            blank_run = 0

    if document.text and not document.text.endswith("\n"):
        report.add("WS003", len(document.lines), "file does not end with a newline")
    elif document.text.endswith("\n\n"):
        report.add("WS003", len(document.lines), "file ends with extra blank lines")


def find_repo_root(start: Path) -> Optional[Path]:
    """Walk up until a ``.git`` entry is found; ``None`` outside a checkout."""

    for candidate in [start, *start.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def check_repository(document: md.Document, report: Report) -> None:
    """Community files are only required in a real checkout, not in fixtures."""

    repo_root = find_repo_root(document.path.parent)
    if repo_root is None:
        return
    for filename in REQUIRED_FILES:
        if not (repo_root / filename).exists():
            report.add("REPO001", 1, f"required file `{filename}` is missing")


# --------------------------------------------------------------------------- #
# Fixers
# --------------------------------------------------------------------------- #


def fix_whitespace(document: md.Document) -> list[str]:
    original = document.lines
    fixed = [line.rstrip() for line in original]

    collapsed: list[str] = []
    blank_run = 0
    for line in fixed:
        if not line:
            blank_run += 1
            if blank_run > 1:
                continue
        else:
            blank_run = 0
        collapsed.append(line)

    if collapsed == original:
        return []
    document.lines[:] = collapsed
    return ["trimmed trailing whitespace and collapsed blank lines"]


def fix_toc(document: md.Document) -> list[str]:
    parsed = parse_toc(document)
    if parsed is None:
        return []
    items, first, last = parsed
    top_indent = items[0].indent if items else 2
    sub_indent = next((item.indent for item in items if item.indent > top_indent), 6)
    expected = expected_toc_lines(document, top_indent, sub_indent)
    current = document.lines[first : last + 1]
    if current == expected:
        return []
    document.lines[first : last + 1] = expected
    return ["regenerated the table of contents"]


def sort_entries(document: md.Document) -> list[str]:
    groups: dict[tuple[Optional[str], Optional[str]], list[md.Entry]] = {}
    for entry in document.entries:
        groups.setdefault(entry.group, []).append(entry)

    changed_groups = 0
    for entries in groups.values():
        indices = [entry.line - 1 for entry in entries]
        texts = [document.lines[index] for index in indices]
        ordering = sorted(range(len(texts)), key=lambda position: entries[position].sort_key)
        sorted_texts = [texts[position] for position in ordering]
        if sorted_texts != texts:
            changed_groups += 1
            for index, text in zip(indices, sorted_texts):
                document.lines[index] = text

    if not changed_groups:
        return []
    return [f"re-sorted entries in {changed_groups} section(s)"]


def apply_fixes(document: md.Document, do_sort: bool) -> list[str]:
    changes: list[str] = []
    changes.extend(sort_entries(document) if do_sort else [])
    changes.extend(fix_toc(document))
    changes.extend(fix_whitespace(document))
    document.text = "\n".join(document.lines) + "\n"
    return changes


# --------------------------------------------------------------------------- #
# Runner / CLI
# --------------------------------------------------------------------------- #


def lint(path: Path) -> tuple[md.Document, Report]:
    document = md.load(path)
    report = Report(path)
    check_repository(document, report)
    check_entries(document, report)
    check_ordering(document, report)
    check_toc(document, report)
    check_headings(document, report)
    check_local_links(document, report)
    check_whitespace(document, report)
    return document, report


def emit_text(path: Path, report: Report, stream=None) -> None:
    stream = stream or sys.stdout
    for issue in report.sorted_issues():
        marker = "✖" if issue.severity == ERROR else "⚠"
        print(f"{path}:{issue.line}: {marker} {issue.severity} [{issue.code}] {issue.message}", file=stream)


def emit_annotations(path: Path, report: Report) -> None:
    for issue in report.sorted_issues():
        message = issue.message.replace("%", "%25").replace("\n", "%0A")
        print(
            f"::{issue.severity} file={path.name},line={issue.line},"
            f"title={issue.code}::{message}"
        )


def format_summary(report: Report, entries: int, headings: int) -> str:
    errors = len(report.errors)
    warnings = len(report.warnings)
    verdict = "failed" if errors else "passed"
    return (
        f"{report.path.name}: {entries} entries, {headings} headings — "
        f"{errors} error(s), {warnings} warning(s) — {verdict}"
    )


def run(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Lint the structure, ordering and style of the awesome list.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("path", nargs="?", default="README.md", help="Markdown file to lint")
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures")
    parser.add_argument("--fix", action="store_true", help="rewrite the TOC and clean whitespace")
    parser.add_argument("--sort", action="store_true", help="alphabetically sort entries (implies --fix)")
    parser.add_argument(
        "--github-annotations",
        action="store_true",
        help="emit ::error/::warning workflow commands for the GitHub Actions UI",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--quiet", action="store_true", help="only print the summary")
    parser.add_argument("--list-rules", action="store_true", help="print all rules and exit")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.list_rules:
        for rule in RULES.values():
            print(f"{rule.code}  {rule.severity:<7} {rule.summary}")
        return 0

    path = Path(args.path)
    if not path.exists():
        print(f"error: {path} does not exist", file=sys.stderr)
        return 2

    document, report = lint(path)

    if args.fix or args.sort:
        changes = apply_fixes(document, do_sort=args.sort)
        if changes:
            path.write_text(document.text, encoding="utf-8")
            for change in changes:
                print(f"fixed: {change}")
        else:
            print("fixed: nothing to change")
        document, report = lint(path)

    if args.json:
        print(
            json.dumps(
                {
                    "file": str(path),
                    "entries": len(document.entries),
                    "headings": len(document.headings),
                    "errors": [issue.as_dict() for issue in report.errors],
                    "warnings": [issue.as_dict() for issue in report.warnings],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        if not args.quiet:
            emit_text(path, report)
        print(format_summary(report, len(document.entries), len(document.headings)))
        if args.github_annotations:
            emit_annotations(path, report)

    failed = bool(report.errors) or (args.strict and bool(report.warnings))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())
