"""Tests for scripts/check_links.py, served from a throwaway local HTTP server."""

from __future__ import annotations

import contextlib
import http.server
import io
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_links  # noqa: E402


class Handler(http.server.BaseHTTPRequestHandler):
    """A tiny site with every status code the checker has to classify."""

    def do_HEAD(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        self.respond(head_only=True)

    def do_GET(self) -> None:  # noqa: N802
        self.respond(head_only=False)

    def respond(self, head_only: bool) -> None:
        path = self.path.split("?")[0].rstrip("/") or "/"
        routes = {
            "/ok": (200, None),
            "/deep/page": (200, None),
            "/missing": (404, None),
            "/gone": (410, None),
            "/blocked": (403, None),
            "/rate-limited": (429, None),
            "/boom": (500, None),
            "/moved-far": (302, "/deep/page"),
            "/moved": (302, "/ok"),
            "/to-root": (301, "/"),
            "/no-head": (200, None),
            "/": (200, None),
        }
        if path == "/no-head" and head_only:
            status, location = 405, None
        else:
            status, location = routes.get(path, (404, None))

        self.send_response(status)
        if location:
            self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args) -> None:  # silence test output
        return


class HttpCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def check(self, path: str, **kwargs):
        defaults = {"timeout": 5, "retries": 0, "sleep": lambda _seconds: None}
        defaults.update(kwargs)
        return check_links.check_http(self.base + path, **defaults)

    def test_ok(self):
        result = self.check("/ok")
        self.assertEqual(result.status, check_links.STATUS_OK)
        self.assertEqual(result.http_status, 200)

    def test_not_found_is_dead(self):
        result = self.check("/missing")
        self.assertEqual(result.status, check_links.STATUS_DEAD)
        self.assertEqual(result.http_status, 404)

    def test_gone_is_dead(self):
        self.assertEqual(self.check("/gone").status, check_links.STATUS_DEAD)

    def test_forbidden_is_blocked_not_dead(self):
        result = self.check("/blocked")
        self.assertEqual(result.status, check_links.STATUS_BLOCKED)
        self.assertIn("bot protection", result.detail)

    def test_rate_limit_is_blocked(self):
        self.assertEqual(self.check("/rate-limited").status, check_links.STATUS_BLOCKED)

    def test_server_error_is_error(self):
        result = self.check("/boom")
        self.assertEqual(result.status, check_links.STATUS_ERROR)

    def test_head_rejection_falls_back_to_get(self):
        result = self.check("/no-head")
        self.assertEqual(result.status, check_links.STATUS_OK)
        self.assertEqual(result.http_status, 200)

    def test_redirect_to_new_path_is_reported(self):
        result = self.check("/moved-far")
        self.assertEqual(result.status, check_links.STATUS_REDIRECT)
        self.assertIn("redirects to", result.detail)

    def test_retries_transient_errors(self):
        attempts: list[float] = []
        result = self.check("/boom", retries=2, sleep=lambda seconds: attempts.append(seconds))
        self.assertEqual(result.status, check_links.STATUS_ERROR)
        self.assertEqual(result.attempts, 3)
        self.assertEqual(len(attempts), 2)

    def test_dns_failure_is_error(self):
        result = check_links.check_http(
            "http://does-not-exist.invalid", timeout=2, retries=0, sleep=lambda _seconds: None
        )
        self.assertEqual(result.status, check_links.STATUS_ERROR)

    def test_connection_refused_is_error(self):
        result = check_links.check_http(
            "http://127.0.0.1:9/", timeout=2, retries=0, sleep=lambda _seconds: None
        )
        self.assertEqual(result.status, check_links.STATUS_ERROR)


class ClassificationTests(unittest.TestCase):
    def test_classify_status(self):
        self.assertEqual(check_links.classify_status(200), check_links.STATUS_OK)
        self.assertEqual(check_links.classify_status(204), check_links.STATUS_OK)
        self.assertEqual(check_links.classify_status(301), check_links.STATUS_REDIRECT)
        self.assertEqual(check_links.classify_status(404), check_links.STATUS_DEAD)
        self.assertEqual(check_links.classify_status(451), check_links.STATUS_DEAD)
        self.assertEqual(check_links.classify_status(403), check_links.STATUS_BLOCKED)
        self.assertEqual(check_links.classify_status(999), check_links.STATUS_BLOCKED)
        self.assertEqual(check_links.classify_status(503), check_links.STATUS_ERROR)

    def test_trivial_redirects(self):
        self.assertTrue(check_links.is_trivial_redirect("https://clay.com", "https://www.clay.com/"))
        self.assertTrue(check_links.is_trivial_redirect("http://clay.com/x", "https://clay.com/x/"))
        self.assertFalse(check_links.is_trivial_redirect("https://clay.com", "https://clay.com/pricing"))
        self.assertFalse(check_links.is_trivial_redirect("https://clay.com", "https://clay.io"))
        self.assertFalse(check_links.is_trivial_redirect("https://clay.com", ""))


class AllowlistTests(unittest.TestCase):
    def write(self, text: str) -> Path:
        path = Path(tempfile.mkdtemp()) / "allowlist.txt"
        path.write_text(text, encoding="utf-8")
        return path

    def test_parsing(self):
        path = self.write(
            "# comment line\n"
            "linkedin.com  # login wall\n"
            "https://example.com/private  # path scoped\n"
            "\n"
            "*.reddit.com   # wildcard prefix\n"
        )
        entries = check_links.load_allowlist(path)
        self.assertEqual([entry.host for entry in entries], ["linkedin.com", "example.com", "reddit.com"])
        self.assertEqual(entries[0].reason, "login wall")
        self.assertEqual(entries[1].path_prefix, "/private")

    def test_matching_subdomains_and_paths(self):
        entries = [
            check_links.AllowlistEntry("linkedin.com", "", "login wall"),
            check_links.AllowlistEntry("example.com", "/private", "scoped"),
        ]
        self.assertIsNotNone(check_links.match_allowlist("https://www.linkedin.com/in/x", entries))
        self.assertIsNotNone(check_links.match_allowlist("https://example.com/private/x", entries))
        self.assertIsNone(check_links.match_allowlist("https://example.com/public", entries))
        self.assertIsNone(check_links.match_allowlist("https://notlinkedin.com", entries))

    def test_missing_allowlist_file_is_empty(self):
        self.assertEqual(check_links.load_allowlist(Path("/tmp/nope-allowlist.txt")), [])

    def test_allowlisted_urls_are_skipped(self):
        target = check_links.LinkTarget(
            url="https://www.linkedin.com/company/example",
            locations=[check_links.Location("README.md", 3)],
        )
        results = check_links.run_checks(
            [target],
            jobs=1,
            timeout=1,
            retries=0,
            insecure=False,
            allowlist=[check_links.AllowlistEntry("linkedin.com", "", "login wall")],
        )
        self.assertEqual(results[0].status, check_links.STATUS_ALLOWLISTED)
        self.assertEqual(results[0].detail, "login wall")


class CollectionTests(unittest.TestCase):
    def write(self, text: str) -> Path:
        directory = Path(tempfile.mkdtemp())
        (directory / "image").mkdir()
        (directory / "image" / "alg_I.png").write_bytes(b"")
        path = directory / "README.md"
        path.write_text(text, encoding="utf-8")
        return path

    def test_local_and_http_links(self):
        path = self.write(
            "# Title\n\n"
            "![web](image/alg_I.png) [Alpha](https://alpha.example) - First.\n\n"
            "[dup](https://alpha.example) [missing](docs/none.md) [anchor](#nowhere)\n\n"
            "## Title\n"
        )
        targets, problems = check_links.collect_targets([path])
        self.assertEqual(list(targets), ["https://alpha.example"])
        self.assertEqual(len(targets["https://alpha.example"].locations), 2)
        messages = sorted(problem.detail for problem in problems)
        self.assertTrue(any("docs/none.md" in message for message in messages))
        self.assertTrue(any("#nowhere" in message for message in messages))
        self.assertTrue(all(problem.status == check_links.STATUS_DEAD for problem in problems))

    def test_existing_local_files_and_anchors_are_fine(self):
        path = self.write("# Title\n\n![web](image/alg_I.png) - Icon.\n\n[top](#title)\n")
        _, problems = check_links.collect_targets([path])
        self.assertEqual(problems, [])

    def test_changed_lines_against_head_is_empty(self):
        changed = check_links.changed_line_numbers([ROOT / "README.md"], "HEAD")
        self.assertIsNotNone(changed)
        self.assertEqual(changed[str(ROOT / "README.md")], set())

    def test_changed_lines_with_unknown_ref(self):
        self.assertIsNone(check_links.changed_line_numbers([ROOT / "README.md"], "no-such-ref-xyz"))


def silent_run(arguments: list[str]) -> int:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return check_links.run(arguments)


class CliTests(unittest.TestCase):
    def test_local_only_passes_on_clean_repo(self):
        self.assertEqual(silent_run([str(ROOT / "README.md"), "--local-only", "--quiet"]), 0)

    def test_broken_local_link_fails_the_build(self):
        directory = Path(tempfile.mkdtemp())
        path = directory / "README.md"
        path.write_text("# T\n\n[gone](missing.md)\n", encoding="utf-8")
        self.assertEqual(silent_run([str(path), "--local-only", "--quiet"]), 1)

    def test_markdown_report_and_step_summary(self):
        directory = Path(tempfile.mkdtemp())
        path = directory / "README.md"
        output = directory / "report.md"
        summary = directory / "summary.md"
        summary.write_text("", encoding="utf-8")
        path.write_text("# T\n\n[gone](missing.md)\n", encoding="utf-8")

        previous = os.environ.get("GITHUB_STEP_SUMMARY")
        os.environ["GITHUB_STEP_SUMMARY"] = str(summary)
        try:
            silent_run(
                [str(path), "--local-only", "--format", "markdown", "--output", str(output),
                 "--github-summary", "--quiet"]
            )
        finally:
            if previous is None:
                os.environ.pop("GITHUB_STEP_SUMMARY", None)
            else:
                os.environ["GITHUB_STEP_SUMMARY"] = previous

        self.assertIn("## Link check", output.read_text(encoding="utf-8"))
        self.assertIn("missing.md", summary.read_text(encoding="utf-8"))

    def test_no_input_files_returns_two(self):
        self.assertEqual(silent_run(["/tmp/nope.md", "--local-only"]), 2)


if __name__ == "__main__":
    unittest.main()
