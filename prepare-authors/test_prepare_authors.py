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
    emit_missing_github_warnings,
    ensure_allowed_paths,
    find_existing_entry,
    get_changed_paths,
    get_commits_since,
    get_github_login,
    github_required_authors,
    load_metadata,
    normalize_release_tag,
    prepare_authors,
    require_github_token,
    save_metadata,
    select_latest_release_tag,
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


def test_normalize_release_tag() -> None:
    assert normalize_release_tag("26.7.0") == (26, 7, 0)
    assert normalize_release_tag("v3.20.4") == (3, 20, 4)
    assert normalize_release_tag("1.10.0") == (1, 10, 0)
    assert normalize_release_tag("26.8.0rc1") is None
    assert normalize_release_tag("4.14.0b2") is None
    assert normalize_release_tag("pre-commit-hooks-v1") is None


def test_select_latest_release_tag_prefers_numeric_over_v_prefix() -> None:
    assert (
        select_latest_release_tag(["v3.20.4", "26.7.0", "25.1.0", "26.7.0rc1"])
        == "26.7.0"
    )
    assert select_latest_release_tag(["1.9.0", "1.10.0"]) == "1.10.0"
    assert select_latest_release_tag(["v1.2.3", "1.2.3"]) in {"v1.2.3", "1.2.3"}
    assert select_latest_release_tag([]) == ""
    assert select_latest_release_tag(["26.8.0rc1", "hooks-v1"]) == ""


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
        email_to_hash={},
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


def test_get_github_login_passes_read_token_as_gh_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_run_json(command: list[str], *, env: dict[str, str] | None = None):
        captured["command"] = command
        captured["env"] = env
        return {"author": {"login": "alice"}}

    monkeypatch.setenv("GITHUB_TOKEN", "job-token")
    with patch("prepare_authors.run_json", side_effect=fake_run_json):
        login = get_github_login(
            "conda/example",
            "abc123",
            token="read-token",
        )

    assert login == "alice"
    assert captured["command"] == ["gh", "api", "repos/conda/example/commits/abc123"]
    assert captured["env"] is not None
    assert captured["env"]["GH_TOKEN"] == "read-token"
    assert captured["env"]["GITHUB_TOKEN"] == "job-token"


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


def test_get_changed_paths_preserves_dotfile_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Porcelain ' M .authors.yml' must not become 'authors.yml' via strip()."""
    init_repo(tmp_path, monkeypatch)
    authors = tmp_path / ".authors.yml"
    write_authors(authors, "- name: Alice Example\n  email: alice@example.com\n")
    subprocess.run(["git", "add", ".authors.yml"], check=True)
    subprocess.run(["git", "commit", "-m", "authors"], check=True, capture_output=True)
    write_authors(
        authors,
        "- name: Alice Example\n  email: alice@example.com\n  github: alice\n",
    )

    assert get_changed_paths() == [Path(".authors.yml")]
    ensure_allowed_paths(get_changed_paths(), authors_path=Path(".authors.yml"))
