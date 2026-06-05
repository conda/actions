from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

SECTION_TYPES: dict[str, str] = {
    "Enhancements": "enhancement",
    "Bug fixes": "bugfix",
    "Deprecations": "deprecation",
    "Docs": "doc",
    "Other": "other",
}
SECTION_ORDER: tuple[str, ...] = tuple(SECTION_TYPES)

HEADING_RE = re.compile(r"^(?P<level>#{2,6})\s+(?P<title>.+?)\s*$")
BULLET_RE = re.compile(r"^\s*[*-]\s+(?P<body>.*\S)\s*$")
PR_RE = re.compile(r"(?<!\d)#?(?P<number>\d+)(?!\d)")


@dataclass(frozen=True)
class NewsFragment:
    path: Path
    sections: dict[str, list[str]] = field(
        default_factory=lambda: {section: [] for section in SECTION_ORDER}
    )
    errors: tuple[str, ...] = ()

    @property
    def item_count(self) -> int:
        return sum(len(items) for items in self.sections.values())


def normalize_heading(value: str) -> str:
    return " ".join(value.strip().strip("#").strip().casefold().split())


SECTION_ALIASES = {
    normalize_heading(section): section
    for section in SECTION_ORDER
}


def is_news_fragment(path: str | Path, news_directory: str | Path = "news") -> bool:
    path = Path(path)
    news_directory = Path(news_directory)

    try:
        relative = path.relative_to(news_directory)
    except ValueError:
        return False

    if len(relative.parts) != 1:
        return False

    if relative.name.startswith("."):
        return False

    if relative.name in {"TEMPLATE", "TEMPLATE.md"}:
        return False

    return relative.suffix in {"", ".md"}


def iter_news_fragments(news_directory: str | Path = "news") -> list[Path]:
    news_directory = Path(news_directory)
    if not news_directory.is_dir():
        return []
    return sorted(
        path
        for path in news_directory.iterdir()
        if path.is_file() and is_news_fragment(path, news_directory)
    )


def parse_sectioned_news(path: str | Path, text: str) -> NewsFragment:
    path = Path(path)
    errors: list[str] = []
    raw_sections: dict[str, list[str]] = {section: [] for section in SECTION_ORDER}
    seen_sections: set[str] = set()
    current_section: str | None = None
    saw_heading = False
    inside_unknown = False

    for lineno, line in enumerate(text.splitlines(), start=1):
        if match := HEADING_RE.match(line):
            saw_heading = True
            title = normalize_heading(match.group("title"))
            current_section = SECTION_ALIASES.get(title)
            inside_unknown = current_section is None

            if current_section is None:
                errors.append(
                    f"{path}:{lineno}: unknown news heading "
                    f"{match.group('title')!r}"
                )
            elif current_section in seen_sections:
                errors.append(
                    f"{path}:{lineno}: duplicate news heading "
                    f"{current_section!r}"
                )
            else:
                seen_sections.add(current_section)
            continue

        if current_section is None:
            if line.strip() and not inside_unknown:
                errors.append(
                    f"{path}:{lineno}: content appears before a known news heading"
                )
            continue

        raw_sections[current_section].append(line.rstrip())

    if not saw_heading:
        errors.append(f"{path}: no news headings found")

    sections: dict[str, list[str]] = {section: [] for section in SECTION_ORDER}
    for section, lines in raw_sections.items():
        items, item_errors = _extract_items(path, section, lines)
        sections[section] = items
        errors.extend(item_errors)

    if not errors and not any(sections.values()):
        errors.append(f"{path}: no real news items found")

    return NewsFragment(path=path, sections=sections, errors=tuple(errors))


def _extract_items(
    path: Path,
    section: str,
    lines: list[str],
) -> tuple[list[str], list[str]]:
    items: list[str] = []
    errors: list[str] = []
    block: list[str] = []
    block_start = 0

    def flush() -> None:
        nonlocal block
        while block and not block[-1].strip():
            block.pop()
        if not block:
            return

        match = BULLET_RE.match(block[0])
        if match and not _is_placeholder(match.group("body")):
            items.append("\n".join(block))
        block = []

    for offset, line in enumerate(lines, start=1):
        if BULLET_RE.match(line):
            flush()
            block = [line]
            block_start = offset
            continue

        if not line.strip():
            if block:
                block.append("")
            continue

        if block and (line.startswith(" ") or line.startswith("\t")):
            block.append(line)
            continue

        errors.append(
            f"{path}: non-bullet content in {section!r} near section line "
            f"{block_start or offset}: {line.strip()!r}"
        )

    flush()
    return items, errors


def _is_placeholder(value: str) -> bool:
    return normalize_heading(value).strip("<>") == "news item"


def fragment_mentions_pr(path: str | Path, text: str, pr_number: int | str) -> bool:
    pr_number = str(pr_number)
    for value in (Path(path).name, text):
        for match in PR_RE.finditer(value):
            if match.group("number") == pr_number:
                return True
    return False
