#!/usr/bin/env python3
"""Shared Markdown parsing helpers for the awesome-lead-generation tooling.

The list has a very strict entry format, which is what makes it machine
checkable. Everything in this module is standard library only, so the checks
can run locally without installing anything.

Entry format (one line, no wrapping):

    ![web](image/alg_I.png) [Tool Name](https://example.com) - What it does.

    ![github](image/alg_R.png) [owner/repo](https://github.com/owner/repo) - What it does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Optional
from urllib.parse import unquote, urlsplit

# --------------------------------------------------------------------------- #
# Regexes
# --------------------------------------------------------------------------- #

HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")

# Full entry line -> icon label, icon image, tool name, url and the remainder.
ENTRY_RE = re.compile(
    r"^!\[(?P<icon>[^\]\n]*)\]\((?P<image>[^)\s]+)\)"
    r"[ \t]+\[(?P<name>[^\]\n]+)\]\((?P<url>[^)\s]+)\)"
    r"(?P<tail>.*)$"
)

# Inline markdown links and images are parsed by hand below, because a regex
# cannot handle nested brackets such as `[![alt](image.svg)](https://target)`.

HTML_IMG_RE = re.compile(r"<img[^>]*\bsrc\s*=\s*[\"'](?P<url>[^\"']+)[\"']", re.IGNORECASE)

# Reference-style link definitions: `[label]: https://example.com`
REF_LINK_RE = re.compile(r"^ {0,3}\[[^\]\n]+\]:\s*<?(?P<url>\S+?)>?(?:\s+\"[^\"]*\")?\s*$")

CODE_FENCE_RE = re.compile(r"^ {0,3}(```|~~~)")

ICON_IMAGES = {"web": "image/alg_I.png", "github": "image/alg_R.png"}

TRACKING_PARAM_RE = re.compile(
    r"(?:[?&#](?:utm_[a-z_]+|gclid|fbclid|msclkid|yclid|dclid|mc_cid|mc_eid|ref|ref_src|"
    r"aff|aff_id|affiliate_id|via|source|src)=)",
    re.IGNORECASE,
)

UNESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|>~])")


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #


@dataclass
class Heading:
    """An ATX heading with the 1-based line number it appears on."""

    line: int
    level: int
    title: str

    @property
    def anchor(self) -> str:
        return slugify(self.title)


@dataclass
class Entry:
    """A single tool entry from the list."""

    line: int
    raw: str
    icon: str
    image: str
    name: str
    url: str
    separator: Optional[str]
    description: str
    section: Optional[str]
    subsection: Optional[str]

    @property
    def group(self) -> tuple[Optional[str], Optional[str]]:
        return (self.section, self.subsection)

    @property
    def sort_key(self) -> str:
        return normalize_name(self.name)


@dataclass
class Document:
    """A parsed Markdown file."""

    path: Path
    text: str
    lines: list[str]
    headings: list[Heading] = field(default_factory=list)
    entries: list[Entry] = field(default_factory=list)
    malformed: list[tuple[int, str]] = field(default_factory=list)

    def title_of(self, anchor: str) -> Optional[str]:
        for heading in self.headings:
            if heading.anchor == anchor:
                return heading.title
        return None


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def slugify(title: str) -> str:
    """Reproduce GitHub's heading anchor algorithm closely enough for our TOC.

    Lowercase, drop inline code formatting, strip punctuation, then turn every
    space into a hyphen. Note that consecutive spaces stay consecutive hyphens,
    which is why "A & B" becomes ``a--b``.
    """

    text = re.sub(r"`([^`]*)`", r"\1", title)
    text = unescape_md(text).strip().lower()
    text = re.sub(r"[^\w\- ]", "", text, flags=re.UNICODE)
    return text.replace(" ", "-")


def unescape_md(text: str) -> str:
    """Turn ``Ai\\_Web\\_Scraper`` back into ``Ai_Web_Scraper``."""

    return UNESCAPE_RE.sub(r"\1", text)


def normalize_name(name: str) -> str:
    """Comparison key for alphabetical ordering and duplicate detection."""

    return unescape_md(name).strip().casefold()


def split_lines(text: str) -> list[str]:
    """Split text into lines, dropping a single trailing newline artefact."""

    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def is_fence(line: str) -> bool:
    return bool(CODE_FENCE_RE.match(line))


def looks_like_entry(line: str) -> bool:
    """Best-effort guess that a line was *meant* to be a tool entry."""

    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith("!["):
        return True

    bullet = re.match(r"^[-*+]\s+\[[^\]\n]+\]\((?P<url>[^)\s]+)\)", stripped)
    if bullet:
        # `- [Title](#anchor)` is a table-of-contents entry, not a tool entry.
        return not bullet.group("url").startswith("#")

    return bool(re.match(r"^\[[^\]\n]+\]\([^)\s]+\)\s*[-–—:]", stripped))


def parse_entry_line(
    line: str,
    line_no: int,
    section: Optional[str] = None,
    subsection: Optional[str] = None,
) -> Optional[Entry]:
    """Parse a tool entry line, returning ``None`` when it does not match."""

    match = ENTRY_RE.match(line)
    if not match:
        return None

    tail = match.group("tail").strip()
    separator: Optional[str] = None
    description = ""
    if tail:
        first = tail[0]
        if first in "-–—:":
            separator = "-" if first in "-–—" else ":"
            description = tail[1:].strip()
        else:
            description = tail

    return Entry(
        line=line_no,
        raw=line,
        icon=match.group("icon").strip(),
        image=match.group("image").strip(),
        name=match.group("name").strip(),
        url=match.group("url").strip(),
        separator=separator,
        description=description,
        section=section,
        subsection=subsection,
    )


# --------------------------------------------------------------------------- #
# Document loading
# --------------------------------------------------------------------------- #


def load(path: str | Path) -> Document:
    """Read and parse a Markdown file."""

    path = Path(path)
    text = path.read_text(encoding="utf-8")
    lines = split_lines(text)

    document = Document(path=path, text=text, lines=lines)
    section: Optional[str] = None
    subsection: Optional[str] = None
    in_fence = False

    for index, line in enumerate(lines):
        line_no = index + 1

        if is_fence(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        heading_match = HEADING_RE.match(line)
        if heading_match:
            level = len(heading_match.group(1))
            title = heading_match.group(2).strip()
            document.headings.append(Heading(line=line_no, level=level, title=title))
            if level <= 2:
                section, subsection = title, None
            elif level == 3:
                subsection = title
            continue

        if not looks_like_entry(line):
            continue

        entry = parse_entry_line(line, line_no, section, subsection)
        if entry is None:
            document.malformed.append((line_no, line))
        else:
            document.entries.append(entry)

    return document


# --------------------------------------------------------------------------- #
# Link extraction
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RawLink:
    """A link found in a Markdown file, with where it was found."""

    target: str
    line: int
    kind: str  # "markdown" | "html"


def iter_inline_links(line: str) -> Iterator[str]:
    """Yield link/image targets from one line, including nested badge links.

    ``[![alt](image.svg)](https://target)`` yields both the image and the target.
    """

    position = 0
    length = len(line)
    while position < length:
        marker = line.find("](", position)
        if marker == -1:
            return

        # Walk backwards to the matching opening bracket.
        depth = 0
        start = -1
        index = marker - 1
        while index >= 0:
            character = line[index]
            if character == "]":
                depth += 1
            elif character == "[":
                if depth == 0:
                    start = index
                    break
                depth -= 1
            index -= 1

        # Walk forwards over the target, honouring <brackets> and nested parens.
        cursor = marker + 2
        while cursor < length and line[cursor] in " \t":
            cursor += 1

        if start != -1 and cursor < length:
            if line[cursor] == "<":
                closing = line.find(">", cursor)
                if closing != -1:
                    target = line[cursor + 1 : closing].strip()
                    end = line.find(")", closing)
                    if target and end != -1:
                        yield target
                        position = end + 1
                        continue
            else:
                target_chars: list[str] = []
                paren_depth = 0
                index = cursor
                while index < length:
                    character = line[index]
                    if character in " \t":
                        break
                    if character == "(":
                        paren_depth += 1
                    elif character == ")":
                        if paren_depth == 0:
                            break
                        paren_depth -= 1
                    target_chars.append(character)
                    index += 1
                target = "".join(target_chars).strip()
                end = line.find(")", index)
                if target and target != "#" and end != -1:
                    yield target
                    position = end + 1
                    continue

        position = marker + 2


def extract_links(document: Document) -> Iterator[RawLink]:
    """Yield every inline link/image target in a document (fenced code ignored)."""

    in_fence = False
    for index, line in enumerate(document.lines):
        if is_fence(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for target in iter_inline_links(line):
            yield RawLink(target, index + 1, "markdown")
        for match in HTML_IMG_RE.finditer(line):
            yield RawLink(match.group("url").strip(), index + 1, "html")
        reference = REF_LINK_RE.match(line)
        if reference:
            yield RawLink(reference.group("url").strip(), index + 1, "reference")


def all_headings_anchors(documents: Iterable[Document]) -> set[str]:
    anchors: set[str] = set()
    for document in documents:
        anchors.update(heading.anchor for heading in document.headings)
    return anchors


# --------------------------------------------------------------------------- #
# URL helpers
# --------------------------------------------------------------------------- #


def split_url(url: str):
    return urlsplit(url)


def url_without_fragment(url: str) -> str:
    parts = urlsplit(url)
    return parts._replace(fragment="").geturl()


def has_tracking_params(url: str) -> bool:
    """True for affiliate/tracking/referral parameters."""

    parts = urlsplit(url)
    haystack = f"{parts.query}&{parts.fragment}".lower()
    return bool(TRACKING_PARAM_RE.search(f"?{haystack}"))


def is_http_url(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme.lower() in {"http", "https"} and bool(parts.netloc)


def local_path_from_url(url: str, base_dir: Path) -> Optional[Path]:
    """Resolve a relative link to a file on disk, or ``None`` if not local."""

    parts = urlsplit(url)
    if parts.scheme or parts.netloc:
        return None
    path = unquote(parts.path)
    if not path:
        return None
    candidate = (base_dir / path).resolve()
    return candidate


def shorten(text: str, limit: int = 96) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
