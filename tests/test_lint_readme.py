"""Tests for scripts/lint_readme.py."""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import awesome_md as md  # noqa: E402
import lint_readme  # noqa: E402

VALID = """
## Contents

  - [Section](#section)
      - [Sub](#sub)
  - [Contributing](#contributing)

## Section

*Tools that do things.*

### Sub

![web](image/alg_I.png) [Alpha](https://alpha.example) - Collects signals from public communities.

![web](image/alg_I.png) [Beta](https://beta.example) - Enriches contact records with company data.

## Contributing

Contributions are welcome.
"""

ALPHA = "![web](image/alg_I.png) [Alpha](https://alpha.example) - Collects signals from public communities."
BETA = "![web](image/alg_I.png) [Beta](https://beta.example) - Enriches contact records with company data."


def write_fixture(text: str, *, with_git: bool = False) -> Path:
    """Write a README plus the icon files it references into a temp directory."""

    directory = Path(tempfile.mkdtemp())
    (directory / "README.md").write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")
    images = directory / "image"
    images.mkdir()
    for name in ("alg_I.png", "alg_R.png"):
        (images / name).write_bytes(b"")
    if with_git:
        (directory / ".git").mkdir()
    return directory / "README.md"


def codes(report: lint_readme.Report) -> list[str]:
    return [issue.code for issue in report.issues]


def silent_run(arguments: list[str]) -> int:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return lint_readme.run(arguments)


class LintTestCase(unittest.TestCase):
    def lint(self, text: str) -> tuple[md.Document, lint_readme.Report]:
        return lint_readme.lint(write_fixture(text))

    def assert_clean(self, text: str) -> None:
        _, report = self.lint(text)
        self.assertEqual(
            [(issue.code, issue.message) for issue in report.issues], [], "expected no issues"
        )


class BaselineTests(unittest.TestCase):
    def test_repository_readme_is_clean(self):
        document, report = lint_readme.lint(ROOT / "README.md")
        self.assertEqual(len(document.entries), 162)
        self.assertEqual(
            [(issue.code, issue.line, issue.message) for issue in report.issues],
            [],
            "README.md must pass the linter without errors or warnings",
        )

    def test_required_files_exist(self):
        for name in lint_readme.REQUIRED_FILES:
            self.assertTrue((ROOT / name).exists(), f"{name} is required")


class ValidFixtureTests(LintTestCase):
    def test_valid_document_is_clean(self):
        self.assert_clean(VALID)


class EntryRuleTests(LintTestCase):
    def test_malformed_entry(self):
        _, report = self.lint(VALID.replace(BETA, "- [Beta](https://beta.example) - Enriches data."))
        self.assertIn("FMT001", codes(report))

    def test_icon_image_must_match_label(self):
        _, report = self.lint(VALID.replace("![web](image/alg_I.png) [Beta]", "![web](image/alg_R.png) [Beta]"))
        self.assertIn("FMT004", codes(report))

    def test_unknown_icon(self):
        _, report = self.lint(VALID.replace("![web](image/alg_I.png) [Beta]", "![app](image/alg_I.png) [Beta]"))
        self.assertIn("FMT003", codes(report))

    def test_missing_description(self):
        _, report = self.lint(VALID.replace(BETA, "![web](image/alg_I.png) [Beta](https://beta.example)"))
        self.assertIn("FMT005", codes(report))

    def test_tracking_parameters_are_rejected(self):
        _, report = self.lint(VALID.replace("https://beta.example", "https://beta.example/?utm_source=awesome"))
        self.assertIn("URL002", codes(report))

    def test_duplicate_url_is_rejected(self):
        _, report = self.lint(VALID.replace("https://beta.example", "https://alpha.example"))
        self.assertIn("URL003", codes(report))

    def test_duplicate_name_is_rejected(self):
        _, report = self.lint(VALID.replace("[Beta]", "[Alpha]"))
        self.assertIn("URL004", codes(report))

    def test_relative_url_is_rejected(self):
        _, report = self.lint(VALID.replace("https://beta.example", "beta.example"))
        self.assertIn("URL001", codes(report))

    def test_github_icon_requires_github_url(self):
        _, report = self.lint(
            VALID.replace(
                "![web](image/alg_I.png) [Beta](https://beta.example)",
                "![github](image/alg_R.png) [Beta](https://beta.example)",
            )
        )
        self.assertIn("URL006", codes(report))

    def test_web_icon_on_github_repo_is_flagged(self):
        _, report = self.lint(
            VALID.replace("https://beta.example", "https://github.com/example/beta")
        )
        self.assertIn("URL007", codes(report))

    def test_alphabetical_order(self):
        _, report = self.lint(VALID.replace(f"{ALPHA}\n\n{BETA}", f"{BETA}\n\n{ALPHA}"))
        self.assertIn("ORDER001", codes(report))

    def test_marketing_language_is_flagged(self):
        _, report = self.lint(VALID.replace("Enriches contact records with company data.", "The world's best enrichment tool."))
        self.assertIn("DESC003", codes(report))

    def test_description_needs_a_period(self):
        _, report = self.lint(VALID.replace("Enriches contact records with company data.", "Enriches contact records"))
        self.assertIn("DESC002", codes(report))


class TocRuleTests(LintTestCase):
    def test_missing_heading_in_toc(self):
        _, report = self.lint(VALID.replace("  - [Contributing](#contributing)\n", ""))
        self.assertIn("TOC003", codes(report))

    def test_stale_toc_entry(self):
        _, report = self.lint(
            VALID.replace("  - [Contributing](#contributing)", "  - [Contributing](#contributing)\n  - [Ghost](#ghost)")
        )
        self.assertIn("TOC002", codes(report))

    def test_wrong_anchor_for_existing_heading(self):
        _, report = self.lint(VALID.replace("(#contributing)", "(#contributing-typo)"))
        self.assertIn("TOC006", codes(report))
        self.assertNotIn("TOC003", codes(report))

    def test_wrong_label_for_existing_anchor(self):
        _, report = self.lint(VALID.replace("[Sub](#sub)", "[Subsection](#sub)"))
        self.assertIn("TOC007", codes(report))

    def test_wrong_order(self):
        text = VALID.replace(
            "  - [Section](#section)\n      - [Sub](#sub)\n  - [Contributing](#contributing)",
            "  - [Contributing](#contributing)\n  - [Section](#section)\n      - [Sub](#sub)",
        )
        _, report = self.lint(text)
        self.assertIn("TOC004", codes(report))

    def test_wrong_indent(self):
        _, report = self.lint(VALID.replace("      - [Sub](#sub)", "  - [Sub](#sub)"))
        self.assertIn("TOC005", codes(report))

    def test_missing_contents_section(self):
        _, report = self.lint(VALID.replace("## Contents", "## Overview"))
        self.assertIn("TOC001", codes(report))

    def test_fix_regenerates_toc(self):
        path = write_fixture(
            VALID.replace("  - [Contributing](#contributing)", "  - [Contributing](#contributing)\n  - [Ghost](#ghost)")
        )
        self.assertEqual(silent_run([str(path)]), 1)
        self.assertEqual(silent_run([str(path), "--fix"]), 0)
        self.assertNotIn("Ghost", path.read_text(encoding="utf-8"))

    def test_sort_fixes_order(self):
        path = write_fixture(VALID.replace(f"{ALPHA}\n\n{BETA}", f"{BETA}\n\n{ALPHA}"))
        self.assertEqual(silent_run([str(path), "--fix", "--sort"]), 0)
        content = path.read_text(encoding="utf-8")
        self.assertLess(content.index("[Alpha]"), content.index("[Beta]"))


class LinkRuleTests(LintTestCase):
    def test_missing_local_file(self):
        _, report = self.lint(VALID.replace("## Contributing\n\nContributions are welcome.", "See [guide](docs/missing.md)."))
        self.assertIn("LINK001", codes(report))

    def test_bad_anchor(self):
        _, report = self.lint(VALID.replace("## Contributing\n\nContributions are welcome.", "See [guide](#nowhere)."))
        self.assertIn("LINK002", codes(report))

    def test_missing_code_of_conduct(self):
        path = write_fixture(VALID, with_git=True)
        _, report = lint_readme.lint(path)
        missing = [issue.message for issue in report.issues if issue.code == "REPO001"]
        self.assertTrue(any("CODE_OF_CONDUCT.md" in message for message in missing))

    def test_repository_files_are_not_required_outside_a_checkout(self):
        _, report = self.lint(VALID)
        self.assertNotIn("REPO001", codes(report))


class HygieneRuleTests(LintTestCase):
    def test_trailing_whitespace(self):
        _, report = self.lint(VALID.replace("Contributions are welcome.", "Contributions are welcome.   "))
        self.assertIn("WS001", codes(report))

    def test_two_space_hard_break_is_allowed(self):
        self.assert_clean(VALID.replace("Contributions are welcome.", "Contributions are welcome.  "))

    def test_consecutive_blank_lines(self):
        _, report = self.lint(VALID.replace("## Contributing", "\n\n## Contributing"))
        self.assertIn("WS004", codes(report))

    def test_duplicate_heading(self):
        _, report = self.lint(VALID + "\n## Section\n\nMore text.\n")
        self.assertIn("H001", codes(report))


class RuleRegistryTests(unittest.TestCase):
    def test_every_rule_has_metadata(self):
        for code, rule in lint_readme.RULES.items():
            self.assertEqual(code, rule.code)
            self.assertIn(rule.severity, (lint_readme.ERROR, lint_readme.WARNING))
            self.assertTrue(rule.summary)

    def test_every_emitted_code_exists_in_the_registry(self):
        _, report = lint_readme.lint(ROOT / "README.md")
        for issue in report.issues:
            self.assertIn(issue.code, lint_readme.RULES)

    def test_list_rules_exits_zero(self):
        self.assertEqual(silent_run(["--list-rules"]), 0)

    def test_missing_file_returns_two(self):
        self.assertEqual(silent_run(["/tmp/definitely-not-here.md"]), 2)


if __name__ == "__main__":
    unittest.main()
