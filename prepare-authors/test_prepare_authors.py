from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

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
    create_or_update_pr,
    emit_missing_github_warnings,
    ensure_allowed_paths,
    find_existing_entry,
    get_changed_paths,
    get_commits_since,
    github_required_authors,
    load_metadata,
    make_github_login_fn,
    prepare_authors,
    require_github_token,
    save_metadata,
    unresolved_missing_github_keys,
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


def test_build_author_indexes_retains_ambiguous_keys() -> None:
    metadata = [
        {
            "name": "Nicola Soranzo",
            "email": "nicola@example.com",
            "github": "nsoranzo",
        },
        {
            "name": "Philip R. Kensche",
            "email": "philip@example.com",
            "github": "NSoranzo",
        },
        {"name": "Shaun Walbridge", "email": "shaun@example.com"},
        {"name": "Shaun Walbridge", "email": "shaun.alt@example.com"},
        {"name": "Shared One", "email": "shared@example.com"},
        {"name": "Shared Two", "email": "shared@example.com"},
    ]

    by_emails, by_names, by_github, _, _ = build_author_indexes(metadata)

    assert by_github["nsoranzo"] is None
    assert by_names["Shaun Walbridge"] is None
    assert by_emails["shared@example.com"] is None


def test_find_existing_entry_matches_name_or_github() -> None:
    metadata = [
        {"name": "Alice Example", "email": "alice@example.com", "github": "alice"},
        {"name": "Bob Example", "email": "bob@example.com", "github": "bob"},
        {"name": "Carol Example", "email": "carol@example.com"},
    ]
    _, by_names, by_github, _, _ = build_author_indexes(metadata)

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
            "Alice Example",
            "alice",
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
    assert (
        find_existing_entry(
            "Alice Example",
            "unknown",
            by_names,
            by_github,
        )
        is None
    )
    assert (
        find_existing_entry(
            "Alice Example",
            "bob",
            by_names,
            by_github,
        )
        is metadata[1]
    )
    assert (
        find_existing_entry(
            "Carol Example",
            "anyone",
            by_names,
            by_github,
        )
        is metadata[2]
    )


def test_find_existing_entry_prefers_github_over_name_without_github() -> None:
    metadata = [
        {"name": "Alex Smith", "email": "alex@example.com"},
        {"name": "Bob Example", "email": "bob@example.com", "github": "Bob"},
    ]
    _, by_names, by_github, _, _ = build_author_indexes(metadata)

    assert find_existing_entry("Alex Smith", "bob", by_names, by_github) is metadata[1]


def test_find_existing_entry_disambiguates_duplicate_github_by_name() -> None:
    metadata = [
        {
            "name": "Nicola Soranzo",
            "email": "nicola@example.com",
            "github": "nsoranzo",
        },
        {
            "name": "Philip R. Kensche",
            "email": "philip@example.com",
            "github": "nsoranzo",
        },
    ]
    _, by_names, by_github, _, _ = build_author_indexes(metadata)

    assert (
        find_existing_entry("Nicola Soranzo", "NSoranzo", by_names, by_github)
        is metadata[0]
    )
    with pytest.raises(ActionError, match="matches multiple author entries"):
        find_existing_entry("Unknown Name", "nsoranzo", by_names, by_github)


def test_find_existing_entry_requires_github_for_duplicate_name() -> None:
    metadata = [
        {
            "name": "Shaun Walbridge",
            "email": "shaun@example.com",
            "github": "scw",
        },
        {
            "name": "Shaun Walbridge",
            "email": "shaun.alt@example.com",
            "github": "scdub",
        },
    ]
    _, by_names, by_github, _, _ = build_author_indexes(metadata)

    assert (
        find_existing_entry("Shaun Walbridge", "scw", by_names, by_github)
        is metadata[0]
    )
    with pytest.raises(ActionError, match="Author name .* matches multiple"):
        find_existing_entry("Shaun Walbridge", None, by_names, by_github)


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
    by_emails, by_names, by_github, _, known_emails = build_author_indexes(metadata)
    commits = [
        CommitAuthor("abc", "alice.work@example.com", "Alice Example", "fix"),
        CommitAuthor("def", "bob@example.com", "Bob Example", "feat"),
    ]

    def fake_login(_repo: str, commit_hash: str) -> str | None:
        return {"abc": "alice", "def": "bob"}[commit_hash]

    alternate_updates, new_authors = classify_commits(
        commits,
        known_emails,
        by_emails,
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


def test_classify_commits_rejects_name_match_on_conflicting_github() -> None:
    metadata = [
        {"name": "Alice Example", "email": "alice@example.com", "github": "alice"},
    ]
    by_emails, by_names, by_github, _, known_emails = build_author_indexes(metadata)
    commits = [
        CommitAuthor("abc", "bob@example.com", "Alice Example", "fix"),
    ]

    alternate_updates, new_authors = classify_commits(
        commits,
        known_emails,
        by_emails,
        by_names,
        by_github,
        "conda/example",
        lambda _repo, _hash: "bob",
    )

    assert alternate_updates == []
    assert len(new_authors) == 1
    assert new_authors[0]["email"] == "bob@example.com"
    assert new_authors[0]["name"] == "Alice Example"
    assert new_authors[0]["github"] == "bob"


def test_classify_commits_disambiguates_duplicate_github_by_name() -> None:
    metadata = [
        {
            "name": "Nicola Soranzo",
            "email": "nicola@example.com",
            "github": "nsoranzo",
        },
        {
            "name": "Philip R. Kensche",
            "email": "philip@example.com",
            "github": "nsoranzo",
        },
    ]
    by_emails, by_names, by_github, _, known_emails = build_author_indexes(metadata)
    commits = [CommitAuthor("abc", "nicola.work@example.com", "Nicola Soranzo", "fix")]

    alternate_updates, new_authors = classify_commits(
        commits,
        known_emails,
        by_emails,
        by_names,
        by_github,
        "conda/example",
        lambda _repo, _hash: "nsoranzo",
    )

    assert new_authors == []
    assert len(alternate_updates) == 1
    assert alternate_updates[0][0] is metadata[0]


def test_classify_commits_rejects_duplicate_email() -> None:
    metadata = [
        {"name": "Alice Example", "email": "shared@example.com", "github": "alice"},
        {"name": "Bob Example", "email": "shared@example.com", "github": "bob"},
    ]
    by_emails, by_names, by_github, _, known_emails = build_author_indexes(metadata)

    with pytest.raises(ActionError, match="Author email .* matches multiple"):
        classify_commits(
            [CommitAuthor("abc", "shared@example.com", "Alice Example", "fix")],
            known_emails,
            by_emails,
            by_names,
            by_github,
            "conda/example",
            lambda _repo, _hash: pytest.fail("ambiguous email must fail first"),
        )


def test_classify_commits_uses_later_github_evidence_for_same_email() -> None:
    metadata = [
        {"name": "Alex Example", "email": "alex@example.com"},
        {"name": "Bob Example", "email": "bob@example.com", "github": "bob"},
    ]
    by_emails, by_names, by_github, _, known_emails = build_author_indexes(metadata)
    commits = [
        CommitAuthor("miss", "new@example.com", "Alex Example", "fix"),
        CommitAuthor("hit", "new@example.com", "Alex Example", "docs"),
    ]
    calls: list[str] = []

    def fake_login(_repo: str, commit_hash: str) -> str | None:
        calls.append(commit_hash)
        return "bob" if commit_hash == "hit" else None

    alternate_updates, new_authors = classify_commits(
        commits,
        known_emails,
        by_emails,
        by_names,
        by_github,
        "conda/example",
        fake_login,
    )

    assert calls == ["miss", "hit"]
    assert new_authors == []
    assert len(alternate_updates) == 1
    assert alternate_updates[0][0] is metadata[1]


@pytest.mark.parametrize("primary_first", [True, False])
def test_classify_commits_merges_known_identity_in_either_order(
    primary_first: bool,
) -> None:
    metadata = [{"name": "Bob Example", "email": "bob@example.com"}]
    by_emails, by_names, by_github, _, known_emails = build_author_indexes(metadata)
    primary = CommitAuthor("primary", "bob@example.com", "Bob Example", "fix")
    alternate = CommitAuthor(
        "alternate",
        "bob.work@example.com",
        "Robert Example",
        "docs",
    )
    commits = [primary, alternate] if primary_first else [alternate, primary]

    alternate_updates, new_authors = classify_commits(
        commits,
        known_emails,
        by_emails,
        by_names,
        by_github,
        "conda/example",
        lambda _repo, _hash: "bob",
    )

    assert new_authors == []
    analysis = AuthorAnalysis(
        alternate_email_updates=alternate_updates,
        new_authors=new_authors,
        missing_github_keys=[],
        email_to_hashes={},
        since_label="tag 1.0.0",
    )
    assert apply_updates(
        metadata,
        analysis,
        repo_full="conda/example",
        get_github_login_fn=lambda *_: pytest.fail("login already resolved"),
    )
    assert metadata == [
        {
            "name": "Bob Example",
            "email": "bob@example.com",
            "github": "bob",
            "alternate_emails": ["bob.work@example.com"],
            "aliases": ["Robert Example"],
        }
    ]


@pytest.mark.parametrize("alias_first", [True, False])
def test_classify_commits_merges_pending_alias_into_existing_name(
    alias_first: bool,
) -> None:
    metadata = [{"name": "Alice Example", "email": "alice@example.com"}]
    by_emails, by_names, by_github, _, known_emails = build_author_indexes(metadata)
    alias = CommitAuthor(
        "alias",
        "alias@example.com",
        "A. Alias",
        "docs",
    )
    exact_name = CommitAuthor(
        "exact",
        "work@example.com",
        "Alice Example",
        "fix",
    )
    commits = [alias, exact_name] if alias_first else [exact_name, alias]

    alternate_updates, new_authors = classify_commits(
        commits,
        known_emails,
        by_emails,
        by_names,
        by_github,
        "conda/example",
        lambda _repo, _hash: "alice",
    )

    assert new_authors == []
    analysis = AuthorAnalysis(
        alternate_email_updates=alternate_updates,
        new_authors=new_authors,
        missing_github_keys=[],
        email_to_hashes={},
        since_label="tag 1.0.0",
    )
    assert apply_updates(
        metadata,
        analysis,
        repo_full="conda/example",
        get_github_login_fn=lambda *_: pytest.fail("login already resolved"),
    )
    assert metadata[0]["github"] == "alice"
    assert set(metadata[0]["alternate_emails"]) == {
        "alias@example.com",
        "work@example.com",
    }
    assert metadata[0]["aliases"] == ["A. Alias"]


def test_classify_commits_merges_pending_new_author_by_github() -> None:
    by_emails, by_names, by_github, _, known_emails = build_author_indexes([])
    commits = [
        CommitAuthor("abc", "bob@example.com", "Bob Example", "feat"),
        CommitAuthor("def", "bob.work@example.com", "Robert Example", "fix"),
    ]

    alternate_updates, new_authors = classify_commits(
        commits,
        known_emails,
        by_emails,
        by_names,
        by_github,
        "conda/example",
        lambda _repo, _hash: "bob",
    )

    assert alternate_updates == []
    assert len(new_authors) == 1
    assert new_authors[0]["email"] == "bob@example.com"
    assert new_authors[0]["name"] == "Bob Example"
    assert new_authors[0]["github"] == "bob"
    assert new_authors[0]["alternate_emails"] == ["bob.work@example.com"]
    assert new_authors[0]["aliases"] == ["Robert Example"]


def test_classify_commits_preserves_aliases_for_new_email() -> None:
    by_emails, by_names, by_github, _, known_emails = build_author_indexes([])
    commits = [
        CommitAuthor("abc", "bob@example.com", "Bob Example", "feat"),
        CommitAuthor("def", "bob@example.com", "Robert Example", "fix"),
    ]

    alternate_updates, new_authors = classify_commits(
        commits,
        known_emails,
        by_emails,
        by_names,
        by_github,
        "conda/example",
        lambda _repo, commit_hash: None if commit_hash == "abc" else "bob",
    )

    assert alternate_updates == []
    assert len(new_authors) == 1
    assert new_authors[0]["github"] == "bob"
    assert new_authors[0]["aliases"] == ["Robert Example"]


def test_classify_commits_retains_later_resolved_github_login() -> None:
    by_emails, by_names, by_github, _, known_emails = build_author_indexes([])
    commits = [
        CommitAuthor("abc", "bob@example.com", "Bob Example", "feat"),
        CommitAuthor("def", "bob.work@example.com", "Bob Example", "fix"),
    ]

    alternate_updates, new_authors = classify_commits(
        commits,
        known_emails,
        by_emails,
        by_names,
        by_github,
        "conda/example",
        lambda _repo, commit_hash: None if commit_hash == "abc" else "bob",
    )

    assert alternate_updates == []
    assert len(new_authors) == 1
    assert new_authors[0]["github"] == "bob"
    assert new_authors[0]["alternate_emails"] == ["bob.work@example.com"]


def test_apply_updates_retains_resolved_github_login() -> None:
    metadata = [{"name": "Bob Example", "email": "bob@example.com"}]
    by_emails, by_names, by_github, _, known_emails = build_author_indexes(metadata)
    commits = [
        CommitAuthor("abc", "bob.work@example.com", "Bob Example", "fix"),
    ]

    alternate_updates, new_authors = classify_commits(
        commits,
        known_emails,
        by_emails,
        by_names,
        by_github,
        "conda/example",
        lambda _repo, _hash: "bob",
    )

    assert "github" not in metadata[0]
    analysis = AuthorAnalysis(
        alternate_email_updates=alternate_updates,
        new_authors=new_authors,
        missing_github_keys=[("bob@example.com", "Bob Example")],
        email_to_hashes={"bob@example.com": ["def"]},
        since_label="tag 1.0.0",
    )
    assert apply_updates(
        metadata,
        analysis,
        repo_full="conda/example",
        get_github_login_fn=lambda *_: pytest.fail("GitHub login already resolved"),
    )
    assert metadata[0]["github"] == "bob"
    assert metadata[0]["alternate_emails"] == ["bob.work@example.com"]


def test_classify_commits_projects_existing_identity_updates() -> None:
    metadata = [{"name": "Bob Example", "email": "bob@example.com"}]
    by_emails, by_names, by_github, _, known_emails = build_author_indexes(metadata)
    commits = [
        CommitAuthor("a", "bob.work@example.com", "Bob Example", "fix"),
        CommitAuthor("b", "bob.other@example.com", "Robert Example", "docs"),
        CommitAuthor("c", "other@example.com", "Bob Example", "feat"),
        CommitAuthor("d", "bob.third@example.com", "Robert Example", "test"),
    ]

    alternate_updates, new_authors = classify_commits(
        commits,
        known_emails,
        by_emails,
        by_names,
        by_github,
        "conda/example",
        lambda _repo, commit_hash: {"a": "bob", "b": "bob", "c": "other"}.get(
            commit_hash
        ),
    )

    assert [update[1].hash for update in alternate_updates] == ["a", "b", "d"]
    assert all(update[0] is metadata[0] for update in alternate_updates)
    assert len(new_authors) == 1
    assert new_authors[0]["email"] == "other@example.com"
    assert new_authors[0]["github"] == "other"


@pytest.mark.parametrize(
    "second_email",
    ["new@example.com", "new.work@example.com"],
)
def test_classify_commits_merges_delayed_github_match(second_email: str) -> None:
    metadata = [
        {"name": "Alice Example", "email": "alice@example.com", "github": "alice"}
    ]
    by_emails, by_names, by_github, _, known_emails = build_author_indexes(metadata)
    commits = [
        CommitAuthor("a", "new@example.com", "New Name", "fix"),
        CommitAuthor("b", second_email, "New Name", "docs"),
    ]

    alternate_updates, new_authors = classify_commits(
        commits,
        known_emails,
        by_emails,
        by_names,
        by_github,
        "conda/example",
        lambda _repo, commit_hash: None if commit_hash == "a" else "alice",
    )

    assert new_authors == []
    assert {update[1].email for update in alternate_updates} == {
        "new@example.com",
        second_email,
    }
    assert all(update[0] is metadata[0] for update in alternate_updates)
    assert metadata == [
        {"name": "Alice Example", "email": "alice@example.com", "github": "alice"}
    ]
    analysis = AuthorAnalysis(
        alternate_email_updates=alternate_updates,
        new_authors=new_authors,
        missing_github_keys=[],
        email_to_hashes={},
        since_label="tag 1.0.0",
    )
    assert apply_updates(
        metadata,
        analysis,
        repo_full="conda/example",
        get_github_login_fn=lambda *_: None,
    )
    assert metadata[0]["alternate_emails"] == list(
        dict.fromkeys(["new@example.com", second_email])
    )
    assert metadata[0]["aliases"] == ["New Name"]


def test_classify_commits_merges_pending_authors_by_later_github() -> None:
    by_emails, by_names, by_github, _, known_emails = build_author_indexes([])
    commits = [
        CommitAuthor("a", "a@example.com", "Author A", "fix"),
        CommitAuthor("b", "b@example.com", "Author B", "docs"),
        CommitAuthor("c", "a.work@example.com", "Author A", "feat"),
        CommitAuthor("d", "b.work@example.com", "Author B", "test"),
    ]

    alternate_updates, new_authors = classify_commits(
        commits,
        known_emails,
        by_emails,
        by_names,
        by_github,
        "conda/example",
        lambda _repo, commit_hash: "shared" if commit_hash in {"c", "d"} else None,
    )

    assert alternate_updates == []
    assert len(new_authors) == 1
    assert new_authors[0]["github"] == "shared"
    assert new_authors[0]["alternate_emails"] == [
        "a.work@example.com",
        "b@example.com",
        "b.work@example.com",
    ]
    assert new_authors[0]["aliases"] == ["Author B"]


def test_classify_commits_queues_alias_for_known_email_new_name() -> None:
    metadata = [
        {"name": "Alice Example", "email": "alice@example.com", "github": "alice"},
    ]
    by_emails, by_names, by_github, _, known_emails = build_author_indexes(metadata)
    commits = [
        CommitAuthor("abc", "alice@example.com", "Alice E", "fix"),
        CommitAuthor("def", "alice@example.com", "Alice E", "docs"),
    ]

    def fake_login(_repo: str, _commit_hash: str) -> str | None:
        raise AssertionError("GitHub lookup should be skipped for known emails")

    alternate_updates, new_authors = classify_commits(
        commits,
        known_emails,
        by_emails,
        by_names,
        by_github,
        "conda/example",
        fake_login,
    )

    assert new_authors == []
    assert len(alternate_updates) == 1
    entry, commit, _github_login = alternate_updates[0]
    assert entry is metadata[0]
    assert commit.name == "Alice E"
    assert update_existing_entry(entry, commit.email, commit.name) is True
    assert entry["aliases"] == ["Alice E"]
    assert "alternate_emails" not in entry


def test_classify_commits_skips_known_email_with_existing_name_or_alias() -> None:
    metadata = [
        {
            "name": "Alice Example",
            "email": "alice@example.com",
            "aliases": ["Alice A"],
        },
    ]
    by_emails, by_names, by_github, _, known_emails = build_author_indexes(metadata)
    commits = [
        CommitAuthor("abc", "alice@example.com", "Alice Example", "fix"),
        CommitAuthor("def", "alice@example.com", "Alice A", "docs"),
    ]

    alternate_updates, new_authors = classify_commits(
        commits,
        known_emails,
        by_emails,
        by_names,
        by_github,
        "",
        lambda _repo, _hash: None,
    )

    assert alternate_updates == []
    assert new_authors == []


def test_classify_commits_queues_alias_for_known_alternate_email() -> None:
    metadata = [
        {
            "name": "Alice Example",
            "email": "alice@example.com",
            "alternate_emails": ["alice.alt@example.com"],
        },
    ]
    by_emails, by_names, by_github, _, known_emails = build_author_indexes(metadata)
    commits = [
        CommitAuthor("abc", "alice.alt@example.com", "Alice E", "fix"),
    ]

    alternate_updates, new_authors = classify_commits(
        commits,
        known_emails,
        by_emails,
        by_names,
        by_github,
        "",
        lambda _repo, _hash: None,
    )

    assert new_authors == []
    assert len(alternate_updates) == 1
    entry, commit, _github_login = alternate_updates[0]
    assert update_existing_entry(entry, commit.email, commit.name) is True
    assert entry["aliases"] == ["Alice E"]
    assert entry["alternate_emails"] == ["alice.alt@example.com"]


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
        email_to_hashes={"bob@example.com": ["def"]},
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


def test_apply_updates_tries_hashes_until_github_resolves() -> None:
    metadata = [
        {"name": "Alice Example", "email": "alice@example.com", "github": "alice"},
        {"name": "Bob Example", "email": "bob@example.com"},
    ]
    analysis = AuthorAnalysis(
        alternate_email_updates=[],
        new_authors=[],
        missing_github_keys=[("bob@example.com", "Bob Example")],
        email_to_hashes={"bob@example.com": ["miss", "hit", "unused"]},
        since_label="tag 1.0.0",
    )
    calls: list[str] = []

    def fake_login(_repo: str, commit_hash: str) -> str | None:
        calls.append(commit_hash)
        return "bob" if commit_hash == "hit" else None

    assert apply_updates(
        metadata,
        analysis,
        repo_full="conda/example",
        get_github_login_fn=fake_login,
    )
    assert calls == ["miss", "hit"]
    assert metadata[1]["github"] == "bob"


def test_apply_updates_rejects_duplicate_github_key() -> None:
    metadata = [
        {"name": "Alice Example", "email": "alice@example.com", "github": "Alice"},
        {"name": "Bob Example", "email": "bob@example.com"},
    ]
    analysis = AuthorAnalysis(
        alternate_email_updates=[],
        new_authors=[],
        missing_github_keys=[("bob@example.com", "Bob Example")],
        email_to_hashes={"bob@example.com": ["abc"]},
        since_label="tag 1.0.0",
    )

    assert not apply_updates(
        metadata,
        analysis,
        repo_full="conda/example",
        get_github_login_fn=lambda _repo, _hash: "alice",
    )
    assert "github" not in metadata[1]


def test_apply_updates_rejects_conflicting_github_update() -> None:
    metadata = [
        {"name": "Alice Example", "email": "alice@example.com", "github": "alice"},
        {"name": "Bob Example", "email": "bob@example.com"},
    ]
    analysis = AuthorAnalysis(
        alternate_email_updates=[],
        new_authors=[],
        missing_github_keys=[],
        email_to_hashes={},
        since_label="tag 1.0.0",
        github_updates=[(metadata[1], "ALICE")],
    )

    with pytest.raises(ActionError, match="matches another author entry"):
        apply_updates(
            metadata,
            analysis,
            repo_full="conda/example",
            get_github_login_fn=lambda *_: None,
        )
    assert "github" not in metadata[1]


def test_apply_updates_preserves_alternate_emails_and_aliases_on_new_author() -> None:
    metadata: list[dict[str, Any]] = []
    analysis = AuthorAnalysis(
        alternate_email_updates=[],
        new_authors=[
            {
                "name": "Bob Example",
                "email": "bob@example.com",
                "github": "bob",
                "alternate_emails": ["bob.work@example.com"],
                "aliases": ["Robert Example"],
            }
        ],
        missing_github_keys=[],
        email_to_hashes={},
        since_label="tag 1.0.0",
    )

    assert apply_updates(
        metadata,
        analysis,
        repo_full="conda/example",
        get_github_login_fn=lambda *_: None,
    )
    assert metadata == [
        {
            "name": "Bob Example",
            "email": "bob@example.com",
            "github": "bob",
            "alternate_emails": ["bob.work@example.com"],
            "aliases": ["Robert Example"],
        }
    ]


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


@pytest.mark.parametrize("subject", ["", "---"])
def test_get_commits_since_preserves_delimiter_like_subjects(
    subject: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_repo(tmp_path, monkeypatch)
    subprocess.run(["git", "tag", "1.0.0"], check=True)
    subprocess.run(
        [
            "git",
            "commit",
            "--allow-empty",
            "--allow-empty-message",
            "-m",
            subject,
            "--author",
            "New Author <new@example.com>",
        ],
        check=True,
        capture_output=True,
    )

    commits, since_label = get_commits_since("tag")

    assert since_label == "tag 1.0.0"
    assert len(commits) == 1
    assert commits[0].email == "new@example.com"
    assert commits[0].subject == subject


def test_get_commits_since_prefers_calver_over_legacy_v_tag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_repo(tmp_path, monkeypatch)
    subprocess.run(["git", "tag", "v3.20.4"], check=True)
    (tmp_path / "mid.txt").write_text("mid\n", encoding="utf-8")
    subprocess.run(["git", "add", "mid.txt"], check=True)
    subprocess.run(
        ["git", "commit", "-m", "mid", "--author", "Mid Author <mid@example.com>"],
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "tag", "26.7.0"], check=True)
    (tmp_path / "change.txt").write_text("change\n", encoding="utf-8")
    subprocess.run(["git", "add", "change.txt"], check=True)
    subprocess.run(
        ["git", "commit", "-m", "feature", "--author", "New Author <new@example.com>"],
        check=True,
        capture_output=True,
    )

    commits, since_label = get_commits_since("tag")

    assert since_label == "tag 26.7.0"
    assert len(commits) == 1
    assert commits[0].email == "new@example.com"


def test_get_commits_since_no_tags_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_repo(tmp_path, monkeypatch)

    with pytest.raises(ActionError, match="No final release tags found"):
        get_commits_since("tag")


def test_get_commits_since_no_release_shaped_tags_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_repo(tmp_path, monkeypatch)
    subprocess.run(["git", "tag", "26.8.0rc1"], check=True)
    subprocess.run(["git", "tag", "pre-commit-hooks-v1"], check=True)

    with pytest.raises(ActionError, match="No final release tags found"):
        get_commits_since("tag")


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


def test_analyze_authors_maps_alternate_email_hash_to_primary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = [
        {"name": "Alice Example", "email": "alice@example.com", "github": "alice"},
        {
            "name": "Bob Example",
            "email": "bob@example.com",
            "alternate_emails": ["bob.work@example.com"],
        },
    ]
    commits = [
        CommitAuthor("abc", "bob.work@example.com", "Bob Example", "fix"),
        CommitAuthor("def", "bob.work@example.com", "Bob Example", "docs"),
    ]
    monkeypatch.setattr(
        "prepare_authors.get_commits_since",
        lambda _since: (commits, "tag 1.0.0"),
    )

    analysis = analyze_authors(
        metadata,
        since="tag",
        repo_full="conda/example",
        get_github_login_fn=lambda *_: None,
    )

    assert analysis.email_to_hashes["bob@example.com"] == ["abc", "def"]
    assert apply_updates(
        metadata,
        analysis,
        repo_full="conda/example",
        get_github_login_fn=lambda _repo, commit_hash: (
            "bob" if commit_hash == "def" else None
        ),
    )
    assert metadata[1]["github"] == "bob"


def test_analyze_authors_retains_resolved_github_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = [
        {"name": "Alice Example", "email": "alice@example.com", "github": "alice"},
        {"name": "Bob Example", "email": "bob@example.com"},
    ]
    commits = [CommitAuthor("abc", "bob@example.com", "Bob Example", "fix")]
    monkeypatch.setattr(
        "prepare_authors.get_commits_since",
        lambda _since: (commits, "tag 1.0.0"),
    )
    calls: list[str] = []

    def stateful_login(_repo: str, commit_hash: str) -> str | None:
        calls.append(commit_hash)
        return "bob" if len(calls) == 1 else None

    analysis = analyze_authors(
        metadata,
        since="tag",
        repo_full="conda/example",
        get_github_login_fn=stateful_login,
    )

    assert calls == ["abc"]
    assert "github" not in metadata[1]
    assert analysis.github_updates == [(metadata[1], "bob")]
    assert apply_updates(
        metadata,
        analysis,
        repo_full="conda/example",
        get_github_login_fn=stateful_login,
    )
    assert calls == ["abc"]
    assert metadata[1]["github"] == "bob"


def test_check_authors_fails_when_updates_needed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_repo(tmp_path, monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "read-token")
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
    monkeypatch.setenv("GITHUB_TOKEN", "read-token")
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


def test_unresolved_missing_github_keys() -> None:
    metadata = [
        {"name": "Alice Example", "email": "alice@example.com", "github": "alice"},
        {"name": "Bob Example", "email": "bob@example.com", "github": "bob"},
        {"name": "Carol Example", "email": "carol@example.com"},
    ]
    missing = [
        ("bob@example.com", "Bob Example"),
        ("carol@example.com", "Carol Example"),
    ]

    assert unresolved_missing_github_keys(metadata, missing) == [
        ("carol@example.com", "Carol Example"),
    ]
    assert unresolved_missing_github_keys(metadata, []) == []


def test_github_required_authors() -> None:
    analysis = AuthorAnalysis(
        alternate_email_updates=[],
        new_authors=[
            {
                "name": "Carol Example",
                "email": "carol@example.com",
                "github": "carol",
            },
            {
                "name": "Dave Example",
                "email": "dave@example.com",
            },
        ],
        missing_github_keys=[("bob@example.com", "Bob Example")],
        email_to_hashes={},
        since_label="tag 1.0.0",
    )

    required = github_required_authors(analysis)
    assert required == [
        ("bob@example.com", "Bob Example"),
        ("carol@example.com", "Carol Example"),
        ("dave@example.com", "Dave Example"),
    ]

    metadata: list[dict[str, Any]] = [
        {"name": "Alice Example", "email": "alice@example.com", "github": "alice"},
        {"name": "Bob Example", "email": "bob@example.com"},
        {"name": "Carol Example", "email": "carol@example.com", "github": "carol"},
        {"name": "Dave Example", "email": "dave@example.com"},
    ]
    assert unresolved_missing_github_keys(metadata, required) == [
        ("bob@example.com", "Bob Example"),
        ("dave@example.com", "Dave Example"),
    ]


def test_emit_missing_github_warnings_empty() -> None:
    assert emit_missing_github_warnings([]) == []


def test_check_authors_warns_but_passes_on_missing_github_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    init_repo(tmp_path, monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "read-token")
    authors = tmp_path / ".authors.yml"
    write_authors(
        authors,
        (
            "- name: Alice Example\n"
            "  email: alice@example.com\n"
            "  github: alice\n"
            "- name: Bob Example\n"
            "  email: bob@example.com\n"
        ),
    )
    subprocess.run(["git", "add", ".authors.yml"], check=True)
    subprocess.run(["git", "commit", "-m", "authors"], check=True, capture_output=True)
    subprocess.run(["git", "tag", "1.0.0"], check=True)

    class Args:
        authors_path = ".authors.yml"
        since = "tag"
        git_remote = "origin"

    check_authors(Args())
    captured = capsys.readouterr()
    assert "missing a github key" in captured.err
    assert "Bob Example" in captured.err


def test_check_authors_does_not_apply_resolved_github_update(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "read-token")
    github_output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(github_output))
    metadata = [
        {"name": "Alice Example", "email": "alice@example.com", "github": "alice"},
        {"name": "Bob Example", "email": "bob@example.com"},
    ]
    commits = [CommitAuthor("abc", "bob@example.com", "Bob Example", "fix")]

    class Args:
        authors_path = ".authors.yml"
        since = "tag"
        git_remote = "origin"

    with (
        patch("prepare_authors.load_metadata", return_value=(metadata, None)),
        patch("prepare_authors.get_repo_full", return_value="conda/example"),
        patch(
            "prepare_authors.get_commits_since",
            return_value=(commits, "tag 1.0.0"),
        ),
        patch(
            "prepare_authors.make_github_login_fn",
            return_value=lambda _repo, _hash: "bob",
        ),
    ):
        check_authors(Args())

    captured = capsys.readouterr()
    assert "missing a github key" in captured.err
    assert "changed=false" in github_output.read_text(encoding="utf-8")
    assert "github" not in metadata[1]


def test_prepare_authors_rejects_empty_branch_prefix_before_mutation() -> None:
    class Args:
        base_branch = "main"
        branch_prefix = ""

    with (
        patch("prepare_authors.load_metadata") as load_metadata_mock,
        patch("prepare_authors.run") as run_mock,
        pytest.raises(ActionError, match="branch-prefix must not be empty"),
    ):
        prepare_authors(Args())

    load_metadata_mock.assert_not_called()
    run_mock.assert_not_called()


def test_prepare_authors_fails_when_missing_github_unresolved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    init_repo(tmp_path, monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "read-token")
    authors = tmp_path / ".authors.yml"
    write_authors(
        authors,
        (
            "- name: Alice Example\n"
            "  email: alice@example.com\n"
            "  github: alice\n"
            "- name: Bob Example\n"
            "  email: bob@example.com\n"
        ),
    )
    subprocess.run(["git", "add", ".authors.yml"], check=True)
    subprocess.run(["git", "commit", "-m", "authors"], check=True, capture_output=True)
    subprocess.run(["git", "tag", "1.0.0"], check=True)
    github_output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(github_output))

    class Args:
        authors_path = ".authors.yml"
        since = "tag"
        git_remote = "origin"
        base_branch = "main"
        branch_prefix = "prepare-authors-"
        git_author_name = "Conda Bot"
        git_author_email = "bot@example.com"
        repository = "conda/example"
        token = "write-token"

    with pytest.raises(ActionError, match="missing github keys"):
        prepare_authors(Args())

    captured = capsys.readouterr()
    assert "missing a github key" in captured.err
    assert "already complete" not in captured.out
    assert "changed=false" in github_output.read_text(encoding="utf-8")
    bob = load_metadata(authors)[0][1]
    assert "github" not in bob


def test_prepare_authors_fails_when_new_author_github_unresolved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    init_repo(tmp_path, monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "read-token")
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
    github_output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(github_output))

    class Args:
        authors_path = ".authors.yml"
        since = "tag"
        git_remote = "origin"
        base_branch = "main"
        branch_prefix = "prepare-authors-"
        git_author_name = "Conda Bot"
        git_author_email = "bot@example.com"
        repository = "conda/example"
        token = "write-token"

    with (
        patch("prepare_authors.get_repo_full", return_value="conda/example"),
        patch("prepare_authors.get_github_login", return_value=None),
        pytest.raises(ActionError, match="missing github keys"),
    ):
        prepare_authors(Args())

    captured = capsys.readouterr()
    assert "missing a github key" in captured.err
    assert "Bob Example" in captured.err
    assert "changed=false" in github_output.read_text(encoding="utf-8")
    updated, _ = load_metadata(authors)
    assert len(updated) == 1
    assert updated[0]["email"] == "alice@example.com"
    listed = subprocess.run(
        ["git", "branch", "--list", "prepare-authors-main"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert listed.stdout.strip() == ""


def test_prepare_authors_succeeds_when_missing_github_filled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_repo(tmp_path, monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "read-token")
    authors = tmp_path / ".authors.yml"
    write_authors(
        authors,
        (
            "- name: Alice Example\n"
            "  email: alice@example.com\n"
            "  github: alice\n"
            "- name: Bob Example\n"
            "  email: bob@example.com\n"
        ),
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
    github_output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(github_output))

    class Args:
        authors_path = ".authors.yml"
        since = "tag"
        git_remote = "origin"
        base_branch = "main"
        branch_prefix = "prepare-authors-"
        git_author_name = "Conda Bot"
        git_author_email = "bot@example.com"
        repository = "conda/example"
        token = "write-token"

    from prepare_authors import run as real_run

    def selective_run(
        command: list[str],
        *,
        capture: bool = False,
        env: dict[str, str] | None = None,
    ) -> str:
        if command[0] == "gh" or command[:2] == ["git", "push"]:
            return ""
        return real_run(command, capture=capture, env=env)

    with (
        patch("prepare_authors.get_repo_full", return_value="conda/example"),
        patch("prepare_authors.get_github_login", return_value="bob"),
        patch(
            "prepare_authors.create_or_update_pr",
            return_value="https://example/pr/1",
        ),
        patch("prepare_authors.run", side_effect=selective_run),
    ):
        prepare_authors(Args())

    updated, _ = load_metadata(authors)
    assert updated[1]["github"] == "bob"
    assert "changed=true" in github_output.read_text(encoding="utf-8")


def test_require_github_token_fails_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(ActionError, match="GITHUB_TOKEN is required"):
        require_github_token()


def test_check_authors_requires_github_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_repo(tmp_path, monkeypatch)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    authors = tmp_path / ".authors.yml"
    write_authors(authors, "- name: Test User\n  email: test@example.com\n")

    class Args:
        authors_path = ".authors.yml"
        since = "tag"
        git_remote = "origin"

    with pytest.raises(ActionError, match="GITHUB_TOKEN is required"):
        check_authors(Args())


def test_make_github_login_fn_passes_read_token_as_gh_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_get_github_login(
        repo: str,
        commit_hash: str,
        *,
        env: dict[str, str],
    ) -> str | None:
        captured["repo"] = repo
        captured["commit_hash"] = commit_hash
        captured["env"] = env
        return "alice"

    monkeypatch.setenv("GITHUB_TOKEN", "job-token")
    monkeypatch.setattr("prepare_authors.get_github_login", fake_get_github_login)

    login_fn = make_github_login_fn("read-token")

    assert login_fn("conda/example", "abc123") == "alice"
    assert captured["repo"] == "conda/example"
    assert captured["commit_hash"] == "abc123"
    assert captured["env"]["GH_TOKEN"] == "read-token"
    assert captured["env"]["GITHUB_TOKEN"] == "job-token"


def test_create_or_update_pr_scopes_lookup_to_repository_owner() -> None:
    calls: list[tuple[list[str], bool, dict[str, str] | None]] = []

    def fake_run(
        command: list[str],
        *,
        capture: bool = False,
        env: dict[str, str] | None = None,
    ) -> str:
        calls.append((command, capture, env))
        if command[:2] == ["gh", "api"]:
            return '[{"number": 7, "html_url": "https://example/pr/7"}]'
        return ""

    with patch("prepare_authors.run", side_effect=fake_run):
        url = create_or_update_pr(
            repository="conda/example",
            branch="prepare-authors-main",
            base_branch="main",
            since_label="tag 1.0.0",
            token="write-token",
        )

    assert url == "https://example/pr/7"
    assert calls[0][0] == [
        "gh",
        "api",
        "--method",
        "GET",
        "repos/conda/example/pulls",
        "-f",
        "state=open",
        "-f",
        "head=conda:prepare-authors-main",
        "-f",
        "base=main",
    ]
    assert calls[0][1] is True
    assert calls[0][2] is not None
    assert calls[0][2]["GH_TOKEN"] == "write-token"
    assert calls[1][0][:5] == ["gh", "pr", "edit", "7", "--repo"]


def test_create_or_update_pr_creates_repository_owned_branch() -> None:
    calls: list[list[str]] = []

    def fake_run(
        command: list[str],
        *,
        capture: bool = False,
        env: dict[str, str] | None = None,
    ) -> str:
        del capture, env
        calls.append(command)
        if command[:2] == ["gh", "api"]:
            return "[]"
        return "https://example/pr/8\n"

    with patch("prepare_authors.run", side_effect=fake_run):
        url = create_or_update_pr(
            repository="conda/example",
            branch="prepare-authors-main",
            base_branch="main",
            since_label="tag 1.0.0",
            token="write-token",
        )

    assert url == "https://example/pr/8"
    assert calls[1][0:2] == ["gh", "pr"]
    assert calls[1][calls[1].index("--head") + 1] == "prepare-authors-main"


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


@pytest.mark.parametrize("authors_name", [".authors.yml", "authors file.yml"])
def test_get_changed_paths_preserves_authors_path(
    authors_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_repo(tmp_path, monkeypatch)
    authors = tmp_path / authors_name
    write_authors(authors, "- name: Alice Example\n  email: alice@example.com\n")
    subprocess.run(["git", "add", authors_name], check=True)
    subprocess.run(["git", "commit", "-m", "authors"], check=True, capture_output=True)
    write_authors(
        authors,
        "- name: Alice Example\n  email: alice@example.com\n  github: alice\n",
    )

    assert get_changed_paths() == [Path(authors_name)]
    ensure_allowed_paths(get_changed_paths(), authors_path=Path(authors_name))
