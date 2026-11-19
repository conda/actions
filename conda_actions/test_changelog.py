from __future__ import annotations

from conda_actions.changelog import find_release_entry, merge_changelog_entry

CHANGELOG = (
    "[//]: # (current developments)\n\n"
    "## 3.0.0 (2026-10-02)\n\n"
    "### Bug fixes\n\n"
    "* Fix the newest thing. (#30)\n\n\n"
    "## 1.2.30 (2026-10-01)\n\n"
    "### Enhancements\n\n"
    "* Prefix-adjacent version. (#29)\n\n\n"
    "## 1.2.3 (2026-09-01)\n\n"
    "### Enhancements\n\n"
    "* Add the thing. (#12)\n\n"
    "### Contributors\n\n"
    "* @alice\n"
)


def test_find_release_entry_returns_notes_and_bounds() -> None:
    entry = find_release_entry(CHANGELOG, "1.2.3")

    assert entry is not None
    assert entry.version == "1.2.3"
    assert entry.date == "2026-09-01"
    assert entry.body == (
        "### Enhancements\n\n* Add the thing. (#12)\n\n### Contributors\n\n* @alice"
    )
    assert entry.raw == CHANGELOG[entry.start : entry.end]
    assert entry.raw.startswith("## 1.2.3 (2026-09-01)\n")
    assert entry.end == len(CHANGELOG)


def test_find_release_entry_stops_at_next_version() -> None:
    entry = find_release_entry(CHANGELOG, "1.2.30")

    assert entry is not None
    assert entry.date == "2026-10-01"
    assert entry.body == "### Enhancements\n\n* Prefix-adjacent version. (#29)"
    assert CHANGELOG[entry.end :].startswith("## 1.2.3 (")


def test_find_release_entry_requires_exact_version() -> None:
    assert find_release_entry(CHANGELOG, "1.2") is None
    assert find_release_entry(CHANGELOG, "1.2.4") is None
    assert find_release_entry(CHANGELOG, "3.0.0\n") is None


def test_find_release_entry_requires_dated_heading() -> None:
    assert find_release_entry("## 1.2.3\n\n* Undated.\n", "1.2.3") is None


def test_merge_changelog_entry_appends_missing_section() -> None:
    release = "## 1.2.3 (2026-09-01)\n\n### Enhancements\n\n* Existing.\n\n\n"
    entry = "## 1.2.3 (2026-09-01)\n\n### Bug fixes\n\n* New fix.\n\n\n"

    assert merge_changelog_entry(release, entry) == (
        "## 1.2.3 (2026-09-01)\n\n"
        "### Enhancements\n\n"
        "* Existing.\n\n"
        "### Bug fixes\n\n"
        "* New fix.\n\n\n"
    )
