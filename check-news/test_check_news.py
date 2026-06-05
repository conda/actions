from __future__ import annotations

import json
from argparse import Namespace
from typing import TYPE_CHECKING

import pytest

from check_news import ActionError, check_news
from news_common import (
    fragment_mentions_pr,
    is_news_fragment,
    parse_sectioned_news,
)

if TYPE_CHECKING:
    from pathlib import Path

TEMPLATE = """\
### Enhancements

* <news item>

### Bug fixes

* <news item>

### Deprecations

* <news item>

### Docs

* <news item>

### Other

* <news item>
"""


def write_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    number: int = 123,
    labels: list[str] | None = None,
    author: str = "contributor",
) -> None:
    event = {
        "number": number,
        "pull_request": {
            "number": number,
            "labels": [{"name": label} for label in labels or []],
            "user": {"login": author},
        },
    }
    path = tmp_path / "event.json"
    path.write_text(json.dumps(event), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(path))


def args(**kwargs: object) -> Namespace:
    defaults = {
        "news_directory": "news",
        "skip_label": "no-news",
        "require_pr_number": "true",
        "fragment_format": "sectioned",
        "exempt_authors": (
            "pre-commit-ci[bot],dependabot[bot],conda-bot,github-actions[bot]"
        ),
        "changed_file": [],
    }
    defaults.update(kwargs)
    return Namespace(**defaults)


def test_is_news_fragment() -> None:
    assert is_news_fragment("news/123-fix")
    assert is_news_fragment("news/123-fix.md")
    assert not is_news_fragment("news/TEMPLATE")
    assert not is_news_fragment("news/TEMPLATE.md")
    assert not is_news_fragment("news/.DS_Store")
    assert not is_news_fragment("news/nested/123-fix")
    assert not is_news_fragment("docs/123-fix")


def test_parse_valid_single_section() -> None:
    fragment = parse_sectioned_news(
        "news/123-fix",
        "### Bug fixes\n\n* Fix the thing. (#123)\n",
    )

    assert not fragment.errors
    assert fragment.item_count == 1
    assert fragment.sections["Bug fixes"] == ["* Fix the thing. (#123)"]


def test_parse_valid_multi_section_with_wrapped_bullet() -> None:
    fragment = parse_sectioned_news(
        "news/123-feature",
        "### Enhancements\n\n* Add a feature\n  with details. (#123)\n\n"
        "### Docs\n\n* Document it. (#123)\n",
    )

    assert not fragment.errors
    assert fragment.item_count == 2
    assert fragment.sections["Enhancements"] == [
        "* Add a feature\n  with details. (#123)"
    ]
    assert fragment.sections["Docs"] == ["* Document it. (#123)"]


def test_parse_placeholder_only_fails() -> None:
    fragment = parse_sectioned_news("news/123-empty", TEMPLATE)

    assert fragment.errors == ("news/123-empty: no real news items found",)


def test_parse_unknown_heading_fails() -> None:
    fragment = parse_sectioned_news(
        "news/123-heading",
        "### Fixes\n\n* Fix the thing. (#123)\n",
    )

    assert "unknown news heading 'Fixes'" in fragment.errors[0]


def test_parse_empty_file_fails() -> None:
    fragment = parse_sectioned_news("news/123-empty", "")

    assert fragment.errors == ("news/123-empty: no news headings found",)


def test_fragment_mentions_pr() -> None:
    assert fragment_mentions_pr("news/123-fix", "", 123)
    assert fragment_mentions_pr("news/fix", "* Fix it. (#123)", 123)
    assert not fragment_mentions_pr("news/1234-fix", "* Fix it. (#1234)", 123)


def test_check_news_passes_with_valid_fragment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    write_event(tmp_path, monkeypatch, number=123)
    news = tmp_path / "news"
    news.mkdir()
    fragment = news / "123-fix"
    fragment.write_text("### Bug fixes\n\n* Fix the thing. (#123)\n", encoding="utf-8")

    check_news(args(changed_file=[str(fragment.relative_to(tmp_path))]))


def test_check_news_skip_label_passes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    write_event(tmp_path, monkeypatch, labels=["no-news"])

    check_news(args(changed_file=[]))


def test_check_news_exempt_author_passes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    write_event(tmp_path, monkeypatch, author="github-actions[bot]")

    check_news(args(changed_file=[]))


def test_check_news_requires_fragment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    write_event(tmp_path, monkeypatch)

    with pytest.raises(ActionError, match="needs a news fragment"):
        check_news(args(changed_file=["conda/example.py"]))


def test_check_news_requires_matching_pr_number(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    write_event(tmp_path, monkeypatch, number=123)
    news = tmp_path / "news"
    news.mkdir()
    fragment = news / "456-fix"
    fragment.write_text("### Bug fixes\n\n* Fix the thing. (#456)\n", encoding="utf-8")

    message = "expected the filename or contents to mention PR #123"
    with pytest.raises(ActionError, match=message):
        check_news(args(changed_file=[str(fragment.relative_to(tmp_path))]))
