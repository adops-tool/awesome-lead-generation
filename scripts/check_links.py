#!/usr/bin/env python3
"""Dead-link checker for the awesome-lead-generation list.

Checks every inline link in the Markdown sources, plus local files and heading
anchors. No third-party dependencies, so it runs locally and in CI with the
stock Python installation.

Examples:
    python3 scripts/check_links.py                       # full check of all .md files
    python3 scripts/check_links.py --local-only          # files + anchors only, no network
    python3 scripts/check_links.py --changed-only --base origin/main
    python3 scripts/check_links.py --format markdown --output link-report.md

Classification:
    ok          HTTP 2xx
    redirect    HTTP 2xx but the URL permanently points somewhere else
    blocked     HTTP 401/403/405/429/999 — bot protection, not a dead link
    dead        HTTP 404/410/451 or another client error — must be fixed
    error       timeout, DNS failure, 5xx after retries
    allowlisted skipped on purpose, see .github/link-check-allowlist.txt

Exit codes: 0 = every link answered, 1 = dead/broken links (or blocked links with
--fail-on-blocked), 2 = usage error.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import random
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import awesome_md as md  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FILES = ["README.md", "CONTRIBUTING.md", "CODE_OF_CONDUCT.md"]
DEFAULT_ALLOWLIST = ".github/link-check-allowlist.txt"
USER_AGENT = (
    "Mozilla/5.0 (compatible; awesome-lead-generation-link-check/1.0; "
    "+https://github.com/adops-tool/awesome-lead-generation)"
)

STATUS_OK = "ok"
STATUS_REDIRECT = "redirect"
STATUS_BLOCKED = "blocked"
STATUS_DEAD = "dead"
STATUS_ERROR = "error"
STATUS_ALLOWLISTED = "allowlisted"
STATUS_SKIPPED = "skipped"

# Servers that answer HEAD with one of these usually answer GET just fine.
HEAD_FALLBACK_STATUSES = {400, 401, 403, 405, 406, 409, 411, 429, 501, 999}
BOT_PROTECTION_STATUSES = {401, 402, 403, 405, 406, 409, 418, 429, 999}
DEAD_STATUSES = {404, 410, 451}

STATUS_ICONS = {
    STATUS_OK: "✓",
    STATUS_REDIRECT: "↪",
    STATUS_BLOCKED: "⚠",
    STATUS_DEAD: "✖",
    STATUS_ERROR: "✖",
    STATUS_ALLOWLISTED: "·",
    STATUS_SKIPPED: "·",
}


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Location:
    file: str
    line: int


@dataclass
class LinkTarget:
    url: str
    locations: list[Location] = field(default_factory=list)

    @property
    def display(self) -> str:
        first = self.locations[0]
        suffix = f" (+{len(self.locations) - 1} more)" if len(self.locations) > 1 else ""
        return f"{first.file}:{first.line}{suffix}"


@dataclass
class CheckResult:
    target: LinkTarget
    status: str
    http_status: Optional[int] = None
    detail: str = ""
    final_url: Optional[str] = None
    attempts: int = 0

    @property
    def url(self) -> str:
        return self.target.url


# --------------------------------------------------------------------------- #
# Allowlist
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AllowlistEntry:
    host: str
    path_prefix: str
    reason: str


def load_allowlist(path: Path) -> list[AllowlistEntry]:
    """Parse ``domain`` / ``https://domain/path`` lines, ``#`` starts a comment."""

    if not path.exists():
        return []

    entries: list[AllowlistEntry] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        pieces = re.split(r"\s+#\s*", line, maxsplit=1)
        value = pieces[0].strip()
        reason = pieces[1].strip() if len(pieces) > 1 else ""
        if not value:
            continue
        if "://" in value:
            parts = urllib.parse.urlsplit(value)
            host = (parts.hostname or "").lower()
            prefix = parts.path or ""
        else:
            host, _, prefix = value.partition("/")
            host = host.lower()
            prefix = f"/{prefix}" if prefix else ""
        host = host.removeprefix("*.")
        if host:
            entries.append(AllowlistEntry(host=host, path_prefix=prefix, reason=reason.strip()))
    return entries


def match_allowlist(url: str, entries: Iterable[AllowlistEntry]) -> Optional[AllowlistEntry]:
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    path = parts.path or "/"
    for entry in entries:
        if host == entry.host or host.endswith("." + entry.host):
            if not entry.path_prefix or path.startswith(entry.path_prefix):
                return entry
    return None


# --------------------------------------------------------------------------- #
# Link collection
# --------------------------------------------------------------------------- #


def collect_targets(files: Iterable[Path]) -> tuple[dict[str, LinkTarget], list[CheckResult]]:
    """Group HTTP links by URL and report local/anchor problems immediately."""

    targets: dict[str, LinkTarget] = {}
    local_problems: list[CheckResult] = []

    for path in files:
        if not path.exists():
            continue
        document = md.load(path)
        anchors = {heading.anchor for heading in document.headings}
        cache: dict[Path, set[str]] = {}

        for link in md.extract_links(document):
            raw = link.target.strip().strip("<>")
            if not raw or raw.startswith(("mailto:", "tel:", "data:", "javascript:")):
                continue

            parts = urllib.parse.urlsplit(raw)
            location = Location(str(path), link.line)

            if parts.scheme in ("http", "https"):
                if not parts.netloc:
                    continue
                key = md.url_without_fragment(raw)
                targets.setdefault(key, LinkTarget(url=key)).locations.append(location)
                continue

            if parts.scheme and parts.scheme not in ("", "file"):
                continue

            # Local file / anchor.
            local_path = (path.parent / urllib.parse.unquote(parts.path)).resolve()
            fragment = parts.fragment

            if parts.path and not local_path.exists():
                local_problems.append(
                    CheckResult(
                        target=LinkTarget(url=raw, locations=[location]),
                        status=STATUS_DEAD,
                        detail=f"local file `{parts.path}` does not exist",
                    )
                )
                continue

            if fragment:
                if parts.path:
                    if local_path not in cache:
                        try:
                            cache[local_path] = {
                                heading.anchor for heading in md.load(local_path).headings
                            }
                        except (OSError, UnicodeDecodeError):
                            cache[local_path] = set()
                    available = cache[local_path]
                else:
                    available = anchors
                if fragment not in available:
                    local_problems.append(
                        CheckResult(
                            target=LinkTarget(url=raw, locations=[location]),
                            status=STATUS_DEAD,
                            detail=f"anchor `#{fragment}` does not exist",
                        )
                    )

    return targets, local_problems


def changed_line_numbers(files: Iterable[Path], base: str) -> Optional[dict[str, set[int]]]:
    """Line numbers added/modified against ``base``; ``None`` if git is unusable."""

    changed: dict[str, set[int]] = {}
    for path in files:
        relative = os.path.relpath(Path(path).resolve(), REPO_ROOT)
        command = [
            "git",
            "-C",
            str(REPO_ROOT),
            "diff",
            "--unified=0",
            "--no-color",
            f"{base}...HEAD",
            "--",
            relative,
        ]
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True, check=False, timeout=60
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if completed.returncode != 0:
            return None

        line_numbers: set[int] = set()
        new_line: Optional[int] = None
        for line in completed.stdout.splitlines():
            hunk = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
            if hunk:
                new_line = int(hunk.group(1))
                continue
            if new_line is None:
                continue
            if line.startswith("+"):
                line_numbers.add(new_line)
                new_line += 1
            elif line.startswith("-"):
                continue
            elif line.startswith(" "):
                new_line += 1
        changed[str(path)] = line_numbers

    return changed


# --------------------------------------------------------------------------- #
# HTTP checking
# --------------------------------------------------------------------------- #


def _request(url: str, method: str, timeout: float, insecure: bool) -> tuple[int, str]:
    request = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})
    context = ssl.create_default_context()
    if insecure:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        final_url = response.geturl()
        response.close()
        return int(response.status), final_url


def check_http(
    url: str,
    *,
    timeout: float = 15.0,
    retries: int = 2,
    insecure: bool = False,
    sleep=time.sleep,
) -> CheckResult:
    """Request a URL, retrying transient failures. Never raises."""

    attempts = 0
    last_status: Optional[int] = None
    last_error = ""

    for attempt in range(retries + 1):
        attempts = attempt + 1
        try:
            status, final_url = _request(url, "HEAD", timeout, insecure)
            if status in HEAD_FALLBACK_STATUSES:
                status, final_url = _request(url, "GET", timeout, insecure)
        except urllib.error.HTTPError as error:
            status = int(error.code)
            final_url = error.geturl()
            last_error = f"HTTP {status}"
            if status in HEAD_FALLBACK_STATUSES:
                try:
                    status, final_url = _request(url, "GET", timeout, insecure)
                except urllib.error.HTTPError as get_error:
                    status = int(get_error.code)
                    final_url = get_error.geturl()
                except Exception as get_exc:  # noqa: BLE001 - network errors are expected
                    last_status = status
                    last_error = f"{type(get_exc).__name__}: {get_exc}"
                    status = 0
        except Exception as exc:  # noqa: BLE001 - network errors are expected
            status = 0
            final_url = None
            last_error = f"{type(exc).__name__}: {exc}"

        last_status = status
        kind = classify_status(status) if status else STATUS_ERROR

        if kind in (STATUS_OK, STATUS_REDIRECT):
            # urllib follows redirects, so a 2xx can still mean "this link moved".
            moved = bool(final_url) and not is_trivial_redirect(url, final_url)
            return CheckResult(
                target=LinkTarget(url=url),
                status=STATUS_REDIRECT if moved else STATUS_OK,
                http_status=status,
                detail=f"redirects to {final_url}" if moved else "",
                final_url=final_url,
                attempts=attempts,
            )
        if kind == STATUS_DEAD:
            return CheckResult(
                target=LinkTarget(url=url),
                status=STATUS_DEAD,
                http_status=status,
                detail=last_error or f"HTTP {status}",
                final_url=final_url,
                attempts=attempts,
            )
        if kind == STATUS_BLOCKED and status == 403:
            # 403s are usually bot protection; retrying rarely helps.
            return CheckResult(
                target=LinkTarget(url=url),
                status=STATUS_BLOCKED,
                http_status=status,
                detail=f"HTTP {status} (bot protection)",
                final_url=final_url,
                attempts=attempts,
            )

        if attempt < retries:
            sleep(min(8.0, 0.75 * (2**attempt)) + random.uniform(0, 0.25))

    kind = classify_status(last_status) if last_status else STATUS_ERROR
    detail = last_error or (f"HTTP {last_status}" if last_status else "no response")
    return CheckResult(
        target=LinkTarget(url=url),
        status=kind if kind in {STATUS_BLOCKED, STATUS_DEAD, STATUS_ERROR} else STATUS_ERROR,
        http_status=last_status or None,
        detail=detail,
        attempts=attempts,
    )


def classify_status(status: int) -> str:
    if 200 <= status < 300:
        return STATUS_OK
    if 300 <= status < 400:
        return STATUS_REDIRECT
    if status in BOT_PROTECTION_STATUSES:
        return STATUS_BLOCKED
    if status in DEAD_STATUSES or 400 <= status < 500:
        return STATUS_DEAD
    if status >= 500:
        return STATUS_ERROR
    return STATUS_ERROR


def is_trivial_redirect(original: str, final: str) -> bool:
    """True when the only difference is www / trailing slash / scheme."""

    if not final:
        return False
    first, second = urllib.parse.urlsplit(original), urllib.parse.urlsplit(final)

    def host(parts) -> str:
        return (parts.hostname or "").lower().removeprefix("www.")

    def path(parts) -> str:
        return (parts.path or "/").rstrip("/") or "/"

    return (
        host(first) == host(second)
        and path(first) == path(second)
        and first.query == second.query
    )


def run_checks(
    targets: Iterable[LinkTarget],
    *,
    jobs: int,
    timeout: float,
    retries: int,
    insecure: bool,
    allowlist: list[AllowlistEntry],
) -> list[CheckResult]:
    results: list[CheckResult] = []
    pending: list[LinkTarget] = []

    for target in targets:
        entry = match_allowlist(target.url, allowlist)
        if entry is not None:
            reason = entry.reason or "host is known to block automated checks"
            results.append(
                CheckResult(
                    target=target,
                    status=STATUS_ALLOWLISTED,
                    detail=reason,
                )
            )
        else:
            pending.append(target)

    if not pending:
        return results

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = {
            pool.submit(
                check_http, target.url, timeout=timeout, retries=retries, insecure=insecure
            ): target
            for target in pending
        }
        for future in concurrent.futures.as_completed(futures):
            target = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001 - defensive programming
                result = CheckResult(
                    target=target, status=STATUS_ERROR, detail=f"{type(exc).__name__}: {exc}"
                )
            result.target = target
            results.append(result)

    results.sort(key=lambda item: (ORDER.get(item.status, 9), item.url))
    return results


ORDER = {
    STATUS_DEAD: 0,
    STATUS_ERROR: 1,
    STATUS_BLOCKED: 2,
    STATUS_REDIRECT: 3,
    STATUS_OK: 4,
    STATUS_ALLOWLISTED: 5,
    STATUS_SKIPPED: 6,
}


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


def count_by_status(results: Iterable[CheckResult]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    return counts


def result_line(result: CheckResult) -> str:
    icon = STATUS_ICONS.get(result.status, "?")
    detail = f" — {result.detail}" if result.detail else ""
    return f"{icon} {result.target.display}  {result.url}{detail}"


def render_text(results: list[CheckResult], files: list[str], elapsed: float) -> str:
    counts = count_by_status(results)
    total = len(results)
    lines = [
        f"Checked {total} links in {', '.join(files)} in {elapsed:.1f}s",
        "",
    ]

    sections = [
        (STATUS_DEAD, "Dead links"),
        (STATUS_ERROR, "Unreachable links"),
        (STATUS_BLOCKED, "Blocked by bot protection (not counted as failures)"),
        (STATUS_REDIRECT, "Redirects"),
    ]
    for status, title in sections:
        subset = [result for result in results if result.status == status]
        if not subset:
            continue
        lines.append(f"{title} ({len(subset)}):")
        for result in subset:
            lines.append(f"  {result_line(result)}")
        lines.append("")

    summary = " ".join(
        f"{counts[status]} {status}" for status in ORDER if status in counts
    )
    lines.append(f"Summary: {summary or 'no links'} — {'FAILED' if has_failures(results) else 'passed'}")
    return "\n".join(lines)


def render_markdown(results: list[CheckResult], files: list[str], elapsed: float) -> str:
    counts = count_by_status(results)
    total = len(results)
    lines = [
        "## Link check",
        "",
        f"Checked **{total}** unique links in {', '.join(f'`{name}`' for name in files)} "
        f"in {elapsed:.1f}s.",
        "",
        "| Status | Count |",
        "| --- | --- |",
    ]
    for status in ORDER:
        if status in counts:
            lines.append(f"| {STATUS_ICONS.get(status, '')} {status} | {counts[status]} |")

    failures = [result for result in results if result.status in (STATUS_DEAD, STATUS_ERROR)]
    if failures:
        lines += ["", "### Links that need fixing", "", "| File | URL | Result |", "| --- | --- | --- |"]
        for result in failures:
            lines.append(
                f"| {result.target.display} | <{result.url}> | {result.detail or result.status} |"
            )

    blocked = [result for result in results if result.status == STATUS_BLOCKED]
    if blocked:
        lines += [
            "",
            "<details><summary>Blocked by bot protection</summary>",
            "",
            "| File | URL | HTTP |",
            "| --- | --- | --- |",
        ]
        for result in blocked:
            lines.append(f"| {result.target.display} | <{result.url}> | {result.http_status} |")
        lines += ["", "</details>"]

    return "\n".join(lines)


def render_json(results: list[CheckResult]) -> str:
    payload = {
        "summary": count_by_status(results),
        "failed": has_failures(results),
        "results": [
            {
                "url": result.url,
                "status": result.status,
                "http_status": result.http_status,
                "detail": result.detail,
                "final_url": result.final_url,
                "locations": [
                    {"file": location.file, "line": location.line}
                    for location in result.target.locations
                ],
            }
            for result in results
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def emit_annotations(results: Iterable[CheckResult]) -> None:
    for result in results:
        if result.status not in (STATUS_DEAD, STATUS_ERROR):
            continue
        level = "error"
        message = f"{result.url} — {result.detail or result.status}".replace("%", "%25")
        for location in result.target.locations:
            print(
                f"::{level} file={location.file},line={location.line},"
                f"title=dead link::{message}"
            )


def has_failures(results: Iterable[CheckResult], fail_on_blocked: bool = False, fail_on_error: bool = True) -> bool:
    for result in results:
        if result.status == STATUS_DEAD:
            return True
        if result.status == STATUS_ERROR and fail_on_error:
            return True
        if result.status == STATUS_BLOCKED and fail_on_blocked:
            return True
    return False


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def resolve_base(explicit: Optional[str]) -> Optional[str]:
    if explicit:
        return explicit
    for candidate in ("origin/main", "main", "HEAD~1"):
        try:
            completed = subprocess.run(
                ["git", "-C", str(REPO_ROOT), "rev-parse", "--verify", "--quiet", candidate],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if completed.returncode == 0:
            return candidate
    return None


def run(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check that every link in the list still works.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("files", nargs="*", help=f"Markdown files (default: {' '.join(DEFAULT_FILES)})")
    parser.add_argument("--jobs", type=int, default=8, help="parallel requests (default: 8)")
    parser.add_argument("--timeout", type=float, default=15.0, help="per-request timeout in seconds")
    parser.add_argument("--retries", type=int, default=2, help="retries for timeouts and 5xx")
    parser.add_argument(
        "--allowlist",
        default=DEFAULT_ALLOWLIST,
        help=f"file with hosts that block bots (default: {DEFAULT_ALLOWLIST})",
    )
    parser.add_argument("--no-allowlist", action="store_true", help="ignore the allowlist file")
    parser.add_argument(
        "--local-only", action="store_true", help="skip network checks (files and anchors only)"
    )
    parser.add_argument(
        "--changed-only",
        action="store_true",
        help="only check links on lines added/modified since --base (fast PR mode)",
    )
    parser.add_argument("--base", default=None, help="git ref to diff against for --changed-only")
    parser.add_argument(
        "--format",
        choices=["text", "markdown", "json"],
        default="text",
        help="report format (default: text)",
    )
    parser.add_argument("--output", default=None, help="also write the report to this file")
    parser.add_argument(
        "--github-annotations", action="store_true", help="emit ::error annotations for broken links"
    )
    parser.add_argument(
        "--github-summary",
        action="store_true",
        help="append the markdown report to $GITHUB_STEP_SUMMARY",
    )
    parser.add_argument(
        "--fail-on-blocked", action="store_true", help="fail when a host blocks the checker"
    )
    parser.add_argument(
        "--no-fail-on-error",
        action="store_true",
        help="do not fail on timeouts/5xx (only on real 404s)",
    )
    parser.add_argument("--insecure", action="store_true", help="skip TLS verification")
    parser.add_argument("--quiet", action="store_true", help="only print the summary")
    args = parser.parse_args(list(argv) if argv is not None else None)

    files = [Path(name) for name in (args.files or DEFAULT_FILES)]
    files = [path for path in files if path.exists()]
    if not files:
        print("error: no input files found", file=sys.stderr)
        return 2

    started = time.time()
    targets, local_problems = collect_targets(files)

    if args.changed_only:
        base = resolve_base(args.base)
        changed = changed_line_numbers(files, base) if base else None
        if changed is None:
            print(
                "warning: could not diff against the base ref; checking every link",
                file=sys.stderr,
            )
        else:
            targets = {
                url: target
                for url, target in targets.items()
                if any(location.line in changed.get(location.file, set()) for location in target.locations)
            }

    allowlist: list[AllowlistEntry] = []
    if not args.no_allowlist:
        allowlist = load_allowlist(Path(args.allowlist))

    if args.local_only:
        results = [
            CheckResult(target=target, status=STATUS_SKIPPED, detail="network checks disabled (--local-only)")
            for target in targets.values()
        ]
    else:
        results = run_checks(
            targets.values(),
            jobs=args.jobs,
            timeout=args.timeout,
            retries=args.retries,
            insecure=args.insecure,
            allowlist=allowlist,
        )

    results.extend(local_problems)
    results.sort(key=lambda item: (ORDER.get(item.status, 9), item.url))
    elapsed = time.time() - started

    file_names = [str(path) for path in files]
    if args.format == "json":
        report = render_json(results)
    elif args.format == "markdown":
        report = render_markdown(results, file_names, elapsed)
    else:
        report = render_text(results, file_names, elapsed)

    if args.quiet and args.format == "text":
        report = report.splitlines()[-1]

    print(report)

    if args.output:
        Path(args.output).write_text(report + "\n", encoding="utf-8")

    if args.github_summary:
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with open(summary_path, "a", encoding="utf-8") as handle:
                handle.write(render_markdown(results, file_names, elapsed) + "\n")

    if args.github_annotations:
        emit_annotations(results)

    failed = has_failures(
        results,
        fail_on_blocked=args.fail_on_blocked,
        fail_on_error=not args.no_fail_on_error,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())
