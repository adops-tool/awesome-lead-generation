"""Tests for scripts/check_pr_title.py."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_pr_title  # noqa: E402


def silent_run(arguments: list[str]) -> int:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return check_pr_title.run(arguments)


class ValidationTests(unittest.TestCase):
    def test_accepted_titles(self):
        for title in [
            "Add Clay",
            "Add [Apify] to Web Scraping",
            "Update Brand24 description",
            "Fix dead link for Syften",
            "Remove abandoned tool",
            "Docs: explain the icon legend",
        ]:
            with self.subTest(title=title):
                self.assertEqual(check_pr_title.validate(title), [])

    def test_title_without_verb_is_rejected(self):
        problems = check_pr_title.validate("clay.com is a great tool")
        self.assertEqual([severity for severity, _ in problems], ["error"])

    def test_empty_title_is_rejected(self):
        self.assertEqual(check_pr_title.validate("")[0][0], "error")

    def test_trailing_period_is_a_warning(self):
        problems = check_pr_title.validate("Add Clay.")
        self.assertIn("warning", [severity for severity, _ in problems])

    def test_draft_titles_are_flagged(self):
        problems = check_pr_title.validate("Add WIP tool")
        self.assertIn("warning", [severity for severity, _ in problems])

    def test_long_titles_are_flagged(self):
        problems = check_pr_title.validate("Add " + "x" * 120)
        self.assertIn("warning", [severity for severity, _ in problems])


class CliTests(unittest.TestCase):
    def test_good_title_exits_zero(self):
        self.assertEqual(silent_run(["--title", "Add Clay"]), 0)

    def test_bad_title_exits_one(self):
        self.assertEqual(silent_run(["--title", "clay"]), 1)

    def test_strict_mode_fails_on_warnings(self):
        self.assertEqual(silent_run(["--title", "Add Clay."]), 0)
        self.assertEqual(silent_run(["--title", "Add Clay.", "--strict"]), 1)

    def test_reads_title_from_environment(self):
        previous = os.environ.get("PR_TITLE")
        os.environ["PR_TITLE"] = "Add Clay"
        try:
            self.assertEqual(silent_run([]), 0)
        finally:
            if previous is None:
                os.environ.pop("PR_TITLE", None)
            else:
                os.environ["PR_TITLE"] = previous

    def test_missing_title_returns_two(self):
        # CI sets GITHUB_EVENT_PATH, so clear every source, not just PR_TITLE.
        with mock.patch.dict(os.environ, {}, clear=False):
            for variable in ("PR_TITLE", "GITHUB_PR_TITLE", "GITHUB_EVENT_PATH"):
                os.environ.pop(variable, None)
            self.assertEqual(silent_run([]), 2)

    def test_reads_title_from_the_actions_event_payload(self):
        payload = Path(tempfile.mkdtemp()) / "event.json"
        payload.write_text(json.dumps({"pull_request": {"title": "Add Clay"}}), encoding="utf-8")
        with mock.patch.dict(os.environ, {"GITHUB_EVENT_PATH": str(payload)}, clear=False):
            os.environ.pop("PR_TITLE", None)
            os.environ.pop("GITHUB_PR_TITLE", None)
            self.assertEqual(silent_run([]), 0)

    def test_explicit_title_wins_over_the_environment(self):
        with mock.patch.dict(os.environ, {"PR_TITLE": "nope"}, clear=False):
            self.assertEqual(silent_run(["--title", "Add Clay"]), 0)


if __name__ == "__main__":
    unittest.main()
