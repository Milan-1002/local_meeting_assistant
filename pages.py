"""Wiki pages on disk: where they live, their frontmatter, and editing one in a single read-and-write.

A page is Markdown with a flat `key: value` frontmatter block. Some keys hold lists; how each is written
(`people: a, b`, `needs_recompile: x; y`) is decided here in LISTS, so callers never split strings.

    page = pages.read("people/priya")          # None when missing
    with pages.edit("meetings/<id>") as p:      # written back once, only if something changed
        p.set_list("needs_recompile", [...])
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from config import VAULT_DIR

RAW = VAULT_DIR / "raw" / "meetings"  # Transcripts (not wiki pages; never edited)
WIKI = VAULT_DIR / "wiki"
WEEKLY = WIKI / "weekly"

# How list-valued frontmatter keys are written. Reasons can contain commas, so they use "; ".
LISTS = {"people": ", ", "needs_recompile": "; ", "review_meetings": ", "}


def file(path_rel: str) -> Path:
    """"people/priya" -> <vault>/wiki/people/priya.md"""
    return WIKI / f"{path_rel}.md"


def split_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    meta = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, text[end + 5:]


def join_frontmatter(meta: dict, body: str) -> str:
    head = "\n".join(f"{k}: {str(v).replace(chr(10), ' ')}" for k, v in meta.items())
    return f"---\n{head}\n---\n{body.lstrip(chr(10))}"


def title_of(body: str, fallback: str) -> str:
    m = re.search(r"^# (.+)$", body, re.M)
    return m.group(1).strip() if m else fallback


def plain(text: str) -> str:
    """[[topics/kafka|Kafka]] -> Kafka and **bold** -> bold, for display outside Obsidian."""
    text = re.sub(r"\[\[([^\]|]+\|)?([^\]]+)\]\]", lambda m: m.group(2).split("/")[-1], text)
    return re.sub(r"\*\*(.+?)\*\*", r"\1", text)


def bullet_sections(body: str) -> tuple[str, dict[str, list[str]]]:
    """Title and `## Heading` -> its bullets as plain text (for the dashboard)."""
    title, sections, cur = "", {}, None
    for line in body.splitlines():
        if line.startswith("# ") and not title:
            title = line[2:].strip()
        elif line.startswith("## "):
            cur = sections.setdefault(line[3:].strip(), [])
        elif cur is not None and line.startswith("- "):
            cur.append(plain(line[2:].strip()))
    return title, sections


@dataclass
class Page:
    path: str
    meta: dict = field(default_factory=dict)
    body: str = ""

    @property
    def title(self) -> str:
        return title_of(self.body, self.path.split("/")[-1])

    def get_list(self, key: str) -> list[str]:
        return [x.strip() for x in self.meta.get(key, "").split(LISTS[key].strip()) if x.strip()]

    def set_list(self, key: str, items: list[str]) -> None:
        """Set a list key (duplicates dropped, order kept); an empty list removes the key."""
        items = list(dict.fromkeys(x.strip() for x in items if x.strip()))
        if items:
            self.meta[key] = LISTS[key].join(items)
        else:
            self.meta.pop(key, None)

    def text(self) -> str:
        return join_frontmatter(self.meta, self.body) if self.meta else self.body


def read(path_rel: str) -> Page | None:
    f = file(path_rel)
    if not f.exists():
        return None
    meta, body = split_frontmatter(f.read_text())
    return Page(path_rel, meta, body)


def write(page: Page) -> None:
    f = file(page.path)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(page.text())


@contextmanager
def edit(path_rel: str):
    """Yield the page (None when it doesn't exist); write it back once at the end if anything changed."""
    page = read(path_rel)
    before = page.text() if page else None
    yield page
    if page is not None and page.text() != before:
        write(page)


def title(path_rel: str) -> str:
    """A page's `# Title`, or its file name when it has none or doesn't exist."""
    page = read(path_rel)
    return page.title if page else path_rel.split("/")[-1]
