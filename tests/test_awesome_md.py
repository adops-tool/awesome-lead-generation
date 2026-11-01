"""Tests for the shared Markdown parser."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import awesome_md as md  # noqa: E402


class SlugTests(unittest.TestCase):
    def test_ampersand_produces_double_hyphen(self):
        self.assertEqual(slugify("Social Listening & Intent Signals"), "social-listening--intent-signals")

    def test_parentheses_are_dropped(self):
        self.assertEqual(
            slugify("LinkedIn & People Data (Open Source)"),
            "linkedin--people-data-open-source",
        )

    def test_inline_code_is_unwrapped(self):
        self.assertEqual(slugify("The `make check` target"), "the-make-check-target")

    def test_numbers_and_case(self):
        self.assertEqual(slugify("B2B Data & Enrichment"), "b2b-data--enrichment")


def slugify(title: str) -> str:  # local alias so tests read nicely
    return md.slugify(title)


class EntryParsingTests(unittest.TestCase):
    def test_web_entry(self):
        entry = md.parse_entry_line(
            "![web](image/alg_I.png) [Clay](https://clay.com) - Data enrichment spreadsheet.",
            10,
            "B2B Data & Enrichment",
            "Contact Databases & Sales Intelligence",
        )
        self.assertIsNotNone(entry)
        self.assertEqual(entry.icon, "web")
        self.assertEqual(entry.image, "image/alg_I.png")
        self.assertEqual(entry.name, "Clay")
        self.assertEqual(entry.url, "https://clay.com")
        self.assertEqual(entry.separator, "-")
        self.assertEqual(entry.description, "Data enrichment spreadsheet.")
        self.assertEqual(entry.sort_key, "clay")
        self.assertEqual(entry.group, ("B2B Data & Enrichment", "Contact Databases & Sales Intelligence"))

    def test_github_entry(self):
        entry = md.parse_entry_line(
            "![github](image/alg_R.png) [Scrapy](https://github.com/scrapy/scrapy) - Python crawler.", 1
        )
        assert entry is not None
        self.assertEqual(entry.icon, "github")
        self.assertEqual(entry.description, "Python crawler.")

    def test_missing_separator(self):
        entry = md.parse_entry_line("![web](image/alg_I.png) [X](https://x.com) no dash", 1)
        assert entry is not None
        self.assertIsNone(entry.separator)

    def test_malformed_entry_returns_none(self):
        self.assertIsNone(md.parse_entry_line("- [Clay](https://clay.com) - Description.", 1))


class DetectionTests(unittest.TestCase):
    def test_toc_line_is_not_an_entry(self):
        self.assertFalse(md.looks_like_entry("  - [Contents](#contents)"))

    def test_bullet_entry_with_url_is_an_entry(self):
        self.assertTrue(md.looks_like_entry("- [Clay](https://clay.com) - Data enrichment."))

    def test_plain_prose_is_not_an_entry(self):
        self.assertFalse(md.looks_like_entry("Just a sentence about tools."))

    def test_tracking_parameters(self):
        self.assertTrue(md.has_tracking_params("https://clay.com/?utm_source=awesome"))
        self.assertTrue(md.has_tracking_params("https://clay.com/?ref=friend"))
        self.assertTrue(md.has_tracking_params("https://clay.com/?gclid=abc"))
        self.assertFalse(md.has_tracking_params("https://clay.com/pricing"))
        self.assertFalse(md.has_tracking_params("https://github.com/org/repo?tab=readme"))

    def test_http_url_detection(self):
        self.assertTrue(md.is_http_url("https://clay.com"))
        self.assertTrue(md.is_http_url("http://clay.com"))
        self.assertFalse(md.is_http_url("clay.com"))
        self.assertFalse(md.is_http_url("mailto:hi@clay.com"))
        self.assertFalse(md.is_http_url("#anchor"))

    def test_unescape_markdown(self):
        self.assertEqual(md.unescape_md("Ai\\_Web\\_Scraper"), "Ai_Web_Scraper")


class LinkExtractionTests(unittest.TestCase):
    def parse(self, text: str) -> md.Document:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "README.md"
            path.write_text(text, encoding="utf-8")
            return md.load(path)

    def test_extracts_images_links_and_references(self):
        document = self.parse(
            "![web](image/alg_I.png) [Clay](https://clay.com) - Enrichment.\n"
            "[![Badge](https://img.shields.io/badge.svg)](https://awesome.re)\n"
            "[link]: https://contributor-covenant.org\n"
        )
        targets = [link.target for link in md.extract_links(document)]
        self.assertIn("image/alg_I.png", targets)
        self.assertIn("https://clay.com", targets)
        self.assertIn("https://awesome.re", targets)
        self.assertIn("https://contributor-covenant.org", targets)

    def test_code_fences_are_ignored(self):
        document = self.parse("```markdown\n[Tool](https://example.com) - X.\n```\n")
        self.assertEqual([link.target for link in md.extract_links(document)], [])

    def test_headings_and_entries_are_scoped(self):
        document = self.parse(
            "## Section A\n\n### Sub A\n\n"
            "![web](image/alg_I.png) [Alpha](https://a.com) - First.\n\n"
            "## Section B\n\n"
            "![web](image/alg_I.png) [Beta](https://b.com) - Second.\n"
        )
        self.assertEqual([entry.section for entry in document.entries], ["Section A", "Section B"])
        self.assertEqual([entry.subsection for entry in document.entries], ["Sub A", None])
        self.assertEqual([heading.title for heading in document.headings], ["Section A", "Sub A", "Section B"])


if __name__ == "__main__":
    unittest.main()
