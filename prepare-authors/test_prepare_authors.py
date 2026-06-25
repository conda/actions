from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from prepare_authors import (
    ActionError,
    AuthorAnalysis,
    CommitAuthor,
    analyze_authors,
    apply_updates,
    build_author_indexes,
    check_authors,
    classify_commits,
    ensure_allowed_paths,
    find_existing_entry,
    get_commits_since,
    load_metadata,
    save_metadata,
    update_existing_entry,
)


def init_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    subprocess.run(["git", "init"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)
    (tmp_path / "README.md").write_text("test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], check=True)
    subprocess.run(["git", "commit", "-m", "init"], check=True, capture_output=True)


def write_authors(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_build_author_indexes_tracks_alternate_emails_and_github() -> None:
    metadata = [
        {
            "name": "Alice Example",
            "email": "alice@example.com",
            "github": "alice",
            "alternate_emails": ["alice.alt@example.com"],
            "aliases": ["Alice A"],
        },
        {"name": "Bob Example", "email": "bob@example.com"},
    ]

    by_emails, by_names, by_github, last_github_index, known_emails = (
        build_author_indexes(metadata)
    )

    assert by_emails["alice.alt@example.com"] is metadata[0]
    assert by_names["Alice A"] is metadata[0]
    assert by_github["alice"] is metadata[0]
    assert last_github_index == 0
    assert known_emails == {
        "alice@example.com",
        "alice.alt@example.com",
        "bob@example.com",
    }


def test_find_existing_entry_matches_name_or_github() -> None:
    metadata = [
        {"name": "Alice Example", "email": "alice@example.com", "github": "alice"},
    ]
    by_emails, by_names, by_github, _, _ = build_author_indexes(metadata)

    assert (
        find_existing_entry(
            "Alice Example",
            None,
            by_names,
            by_github,
        )
        is metadata[0]
    )
    assert (
        find_existing_entry(
            "Other Name",
            "alice",
            by_names,
            by_github,
        )
        is metadata[0]
    )


def test_update_existing_entry_adds_alternate_email_and_alias() -> None:
    entry: dict[str, Any] = {"name": "Alice Example", "email": "alice@example.com"}

    assert update_existing_entry(entry, "alice.alt@example.com", "Alice A") is True
    assert entry["alternate_emails"] == ["alice.alt@example.com"]
    assert entry["aliases"] == ["Alice A"]
    assert update_existing_entry(entry, "alice.alt@example.com", "Alice A") is False


def test_classify_commits_splits_new_and_existing_authors() -> None:
    metadata = [
        {"name": "Alice Example", "email": "alice@example.com", "github": "alice"},
    ]
    _, by_names, by_github, _, known_emails = build_author_indexes(metadata)
    commits = [
        CommitAuthor("abc", "alice.work@example.com", "Alice Example", "fix"),
        CommitAuthor("def", "bob@example.com", "Bob Example", "feat"),
    ]

    def fake_login(_repo: str, _commit_hash: str) -> str | None:
        return "bob"

    alternate_updates, new_authors = classify_commits(
        commits,
        known_emails,
        by_names,
        by_github,
        "conda/example",
        fake_login,
    )

    assert len(alternate_updates) == 1
    assert alternate_updates[0][1].email == "alice.work@example.com"
    assert len(new_authors) == 1
    assert new_authors[0]["email"] == "bob@example.com"
    assert new_authors[0]["github"] == "bob"


def test_apply_updates_appends_new_author_and_github_key() -> None:
    metadata: list[dict[str, Any]] = [
        {"name": "Alice Example", "email": "alice@example.com", "github": "alice"},
        {"name": "Bob Example", "email": "bob@example.com"},
    ]
    analysis = AuthorAnalysis(
        alternate_email_updates=[],
        new_authors=[
            {
                "name": "Carol Example",
                "email": "carol@example.com",
                "github": "carol",
            }
        ],
        missing_github_keys=[("bob@example.com", "Bob Example")],
        email_to_hash={"bob@example.com": "def"},
        since_label="tag 1.0.0",
    )

    def fake_login(_repo: str, commit_hash: str) -> str | None:
        return "bob" if commit_hash == "def" else None

    assert apply_updates(
        metadata,
        analysis,
        repo_full="conda/example",
        get_github_login_fn=fake_login,
    )
    assert metadata[-1]["github"] == "carol"
    assert metadata[1]["github"] == "bob"


def test_get_commits_since_tag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_repo(tmp_path, monkeypatch)
    subprocess.run(["git", "tag", "1.0.0"], check=True)
    (tmp_path / "change.txt").write_text("change\n", encoding="utf-8")
    subprocess.run(["git", "add", "change.txt"], check=True)
    subprocess.run(
        ["git", "commit", "-m", "feature", "--author", "New Author <new@example.com>"],
        check=True,
        capture_output=True,
    )

    commits, since_label = get_commits_since("tag")

    assert since_label == "tag 1.0.0"
    assert len(commits) == 1
    assert commits[0].email == "new@example.com"


def test_get_commits_since_no_tags_is_noop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_repo(tmp_path, monkeypatch)

    commits, since_label = get_commits_since("tag")

    assert commits == []
    assert since_label == "no tags"


def test_analyze_authors_detects_new_contributor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_repo(tmp_path, monkeypatch)
    authors = tmp_path / ".authors.yml"
    write_authors(
        authors,
        "- name: Alice Example\n  email: alice@example.com\n  github: alice\n",
    )
    subprocess.run(["git", "add", ".authors.yml"], check=True)
    subprocess.run(["git", "commit", "-m", "authors"], check=True, capture_output=True)
    subprocess.run(["git", "tag", "1.0.0"], check=True)
    (tmp_path / "feature.txt").write_text("feature\n", encoding="utf-8")
    subprocess.run(["git", "add", "feature.txt"], check=True)
    subprocess.run(
        ["git", "commit", "-m", "feature", "--author", "Bob Example <bob@example.com>"],
        check=True,
        capture_output=True,
    )

    metadata, _ = load_metadata(authors)
    analysis = analyze_authors(
        metadata,
        since="tag",
        repo_full="",
        get_github_login_fn=lambda *_: None,
    )

    assert len(analysis.new_authors) == 1
    assert analysis.new_authors[0]["email"] == "bob@example.com"


def test_check_authors_fails_when_updates_needed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_repo(tmp_path, monkeypatch)
    authors = tmp_path / ".authors.yml"
    write_authors(authors, "- name: Alice Example\n  email: alice@example.com\n")
    subprocess.run(["git", "add", ".authors.yml"], check=True)
    subprocess.run(["git", "commit", "-m", "authors"], check=True, capture_output=True)
    subprocess.run(["git", "tag", "1.0.0"], check=True)
    (tmp_path / "feature.txt").write_text("feature\n", encoding="utf-8")
    subprocess.run(["git", "add", "feature.txt"], check=True)
    subprocess.run(
        ["git", "commit", "-m", "feature", "--author", "Bob Example <bob@example.com>"],
        check=True,
        capture_output=True,
    )

    class Args:
        authors_path = ".authors.yml"
        since = "tag"
        git_remote = "origin"

    with pytest.raises(ActionError, match="new contributor"):
        check_authors(Args())


def test_check_authors_passes_when_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_repo(tmp_path, monkeypatch)
    authors = tmp_path / ".authors.yml"
    write_authors(authors, "- name: Test User\n  email: test@example.com\n")
    subprocess.run(["git", "add", ".authors.yml"], check=True)
    subprocess.run(["git", "commit", "-m", "authors"], check=True, capture_output=True)
    subprocess.run(["git", "tag", "1.0.0"], check=True)

    class Args:
        authors_path = ".authors.yml"
        since = "tag"
        git_remote = "origin"

    check_authors(Args())


def test_load_and_save_metadata_roundtrip(tmp_path: Path) -> None:
    authors = tmp_path / ".authors.yml"
    write_authors(
        authors,
        "- name: Alice Example\n  email: alice@example.com\n  github: alice\n",
    )

    metadata, yaml_engine = load_metadata(authors)
    metadata.append({"name": "Bob Example", "email": "bob@example.com"})
    save_metadata(metadata, yaml_engine, authors)

    updated, _ = load_metadata(authors)
    assert updated[-1]["email"] == "bob@example.com"


def test_load_and_save_metadata_preserves_garbled_email(tmp_path: Path) -> None:
    garbled_email = (
        "jhultman@novateurresearch.comgit config --global user.email "
        "jhultman@novateurresearch.com"
    )
    authors = tmp_path / ".authors.yml"
    write_authors(
        authors,
        (
            "- name: Jacob Hultman\n"
            "  email: jhultman@novateurresearch.com\n"
            "  alternate_emails:\n"
            f"  - {garbled_email}\n"
        ),
    )

    metadata, yaml_engine = load_metadata(authors)
    save_metadata(metadata, yaml_engine, authors)

    updated, _ = load_metadata(authors)
    assert updated[0]["alternate_emails"] == [garbled_email]


def test_ensure_allowed_paths() -> None:
    ensure_allowed_paths([Path(".authors.yml")], authors_path=Path(".authors.yml"))

    with pytest.raises(ActionError, match="unexpected file changes"):
        ensure_allowed_paths([Path("README.md")], authors_path=Path(".authors.yml"))
