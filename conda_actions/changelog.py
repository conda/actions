from __future__ import annotations

import re
from dataclasses import dataclass

from conda_actions.news import SECTION_ORDER

__all__ = [
    "CONTRIBUTORS_SECTION",
    "CURRENT_DEVELOPMENTS",
    "ReleaseEntry",
    "find_release_entry",
    "merge_changelog_entry",
]

CURRENT_DEVELOPMENTS = "[//]: # (current developments)"
CONTRIBUTORS_SECTION = "Contributors"
SECTION_HEADING_RE = re.compile(r"^###\s+(?P<title>.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class ReleaseEntry:
    version: str
    date: str
    body: str
    raw: str
    start: int
    end: int


def find_release_entry(text: str, version: str) -> ReleaseEntry | None:
    """Return the ``## <version> (<date>)`` section of a changelog."""
    heading = re.search(
        rf"^##\s+{re.escape(version)}\s+\((?P<date>[^)\n]*)\)\s*$",
        text,
        flags=re.MULTILINE,
    )
    if not heading:
        return None

    following = re.search(r"^##\s+", text[heading.end() :], re.MULTILINE)
    end = heading.end() + following.start() if following else len(text)
    return ReleaseEntry(
        version=version,
        date=heading.group("date"),
        body=text[heading.end() : end].strip(),
        raw=text[heading.start() : end],
        start=heading.start(),
        end=end,
    )


def merge_changelog_entry(release: str, entry: str) -> str:
    entry_headings = list(SECTION_HEADING_RE.finditer(entry))
    incoming = {
        match.group("title"): entry[
            match.end() : (
                entry_headings[index + 1].start()
                if index + 1 < len(entry_headings)
                else len(entry)
            )
        ].strip()
        for index, match in enumerate(entry_headings)
        if match.group("title") in (*SECTION_ORDER, CONTRIBUTORS_SECTION)
    }

    for section in SECTION_ORDER:
        body = incoming.get(section)
        if not body:
            continue

        headings = list(SECTION_HEADING_RE.finditer(release))
        existing = next(
            (match for match in headings if match.group("title") == section),
            None,
        )
        if existing:
            index = headings.index(existing)
            section_end = (
                headings[index + 1].start()
                if index + 1 < len(headings)
                else len(release)
            )
            insert_at = len(release[:section_end].rstrip())
            separator = "\n" if release[existing.end() : insert_at].strip() else "\n\n"
            release = release[:insert_at] + separator + body + release[insert_at:]
            continue

        section_index = SECTION_ORDER.index(section)
        insert_before = next(
            (
                match
                for match in headings
                if match.group("title") not in SECTION_ORDER
                or SECTION_ORDER.index(match.group("title")) > section_index
            ),
            None,
        )
        block = f"### {section}\n\n{body}"
        if insert_before:
            release = (
                release[: insert_before.start()]
                + block
                + "\n\n"
                + release[insert_before.start() :]
            )
        else:
            insert_at = len(release.rstrip())
            release = release[:insert_at] + "\n\n" + block + release[insert_at:]

    contributors = incoming.get(CONTRIBUTORS_SECTION)
    if contributors:
        release = merge_contributors_section(release, contributors)

    return release


def merge_contributors_section(release: str, body: str) -> str:
    headings = list(SECTION_HEADING_RE.finditer(release))
    existing = next(
        (match for match in headings if match.group("title") == CONTRIBUTORS_SECTION),
        None,
    )
    if existing:
        index = headings.index(existing)
        section_end = (
            headings[index + 1].start() if index + 1 < len(headings) else len(release)
        )
        heading_end = len(release[: existing.end()].rstrip())
        trailing = release[len(release[:section_end].rstrip()) : section_end]
        return release[:heading_end] + "\n\n" + body + trailing + release[section_end:]

    insert_at = len(release.rstrip())
    block = f"### {CONTRIBUTORS_SECTION}\n\n{body}"
    return release[:insert_at] + "\n\n" + block + release[insert_at:]
