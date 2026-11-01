#!/usr/bin/env python3
"""Check that a pull request title follows the convention from CONTRIBUTING.md.

Examples of accepted titles:
    Add Clay
    Add [Apify] to Web Scraping
    Update Brand24 description
    Fix dead link for Syften
    Remove abandoned tool
    Docs: explain the icon legend

Usage:
    python3 scripts/check_pr_title.py --title "Add Clay"
    python3 scripts/check_pr_title.py                 # uses $PR_TITLE or the Actions event payload

Exit codes: 0 = accepted, 1 = rejected, 2 = no title available.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

ALLOWED_VERBS = ["Add", "Update", "Fix", "Remove", "Docs", "Chore", "Refactor", "Revert"]
TITLE_RE = re.compile(
    r"^(?P<verb>" + "|".join(ALLOWED_VERBS) + r")(?:\((?P<scope>[^)]+)\))?!?:?\s+(?P<subject>\S.*)$"
)
MAX_LENGTH = 90


def read_title(explicit: Optional[str]) -> Optional[str]:
    if explicit:
        return explicit.strip()

    for variable in ("PR_TITLE", "GITHUB_PR_TITLE"):
        value = os.environ.get(variable)
        if value:
            return value.strip()

    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if event_path and Path(event_path).exists():
        try:
            payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        title = (payload.get("pull_request") or {}).get("title")
        if title:
            return str(title).strip()

    return None


def validate(title: str) -> list[tuple[str, str]]:
    """Return a list of (severity, message) problems."""

    problems: list[tuple[str, str]] = []
    if not title:
        return [("error", "pull request title is empty")]

    match = TITLE_RE.match(title)
    if not match:
        problems.append(
            (
                "error",
                f"title `{title}` must start with one of "
                f"{', '.join(ALLOWED_VERBS)} followed by a description, "
                "e.g. `Add [Tool Name]` (see CONTRIBUTING.md)",
            )
        )
    else:
        subject = match.group("subject").strip()
        if len(subject) < 3:
            problems.append(("error", "the description after the verb is too short"))

    if title.endswith("."):
        problems.append(("warning", "drop the trailing period from the title"))
    if len(title) > MAX_LENGTH:
        problems.append(("warning", f"title is {len(title)} characters; keep it under {MAX_LENGTH}"))
    if re.search(r"\b(wip|tmp|temp|asdf|test)\b", title, re.IGNORECASE):
        problems.append(("warning", "looks like a draft title; rename it before review"))

    return problems


def run(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--title", default=None, help="PR title (otherwise $PR_TITLE / event payload)")
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures")
    args = parser.parse_args(argv)

    title = read_title(args.title)
    if title is None:
        print("error: no pull request title found; pass --title or set $PR_TITLE", file=sys.stderr)
        return 2

    problems = validate(title)
    errors = [message for severity, message in problems if severity == "error"]
    warnings = [message for severity, message in problems if severity == "warning"]

    for severity, message in problems:
        print(f"::{severity} title=PR title::PR title: {message}")

    if errors:
        print(f"PR title check failed: {title}", file=sys.stderr)
        return 1
    if warnings and args.strict:
        print(f"PR title check failed (strict): {title}", file=sys.stderr)
        return 1

    print(f"PR title check passed: {title}")
    return 0


if __name__ == "__main__":
    sys.exit(run())
