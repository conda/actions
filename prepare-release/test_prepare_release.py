from __future__ import annotations

import json
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest

import prepare_release as prepare_release_module
import release_common as release_common_module
from prepare_release import (
    ActionError,
    ContributorCommit,
    collect_contributors,
    collect_fragments,
    ensure_allowed_paths,
    first_merged_pr_url,
    get_contributor_commits,
    infer_next_version,
    is_first_timer,
    merge_changelog_entry,
    prepare_release,
    render_changelog_entry,
    render_contributors,
    resolve_logins,
    update_changelog,
    verify_context,
)


def patch_run(monkeypatch: pytest.MonkeyPatch, fake_run: object) -> None:
    monkeypatch.setattr(prepare_release_module, "run", fake_run)
    monkeypatch.setattr(release_common_module, "run", fake_run)


def write_workflow_run_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    conclusion: str = "success",
    event: str = "push",
    repository: str = "conda/conda",
    head_repository: str = "conda/conda",
    branch: str = "26.7.x",
    sha: str = "abc123",
) -> None:
    payload = {
        "workflow_run": {
            "conclusion": conclusion,
            "event": event,
            "head_branch": branch,
            "head_sha": sha,
            "head_repository": {"full_name": head_repository},
        }
    }
    path = tmp_path / "event.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(path))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_run")
    monkeypatch.setenv("GITHUB_REPOSITORY", repository)


def prepare_args() -> Namespace:
    return Namespace(
        release_branch_pattern="[0-9]*.[0-9]*.x",
        news_directory="news",
        changelog_path="CHANGELOG.md",
        branch_prefix="release-notes-",
        git_author_name="Conda Bot",
        git_author_email="conda-bot@example.com",
        repository="conda/conda",
        token="test-token",
    )


def write_release_files(tmp_path: Path) -> None:
    news = tmp_path / "news"
    news.mkdir()
    (news / "123-fix").write_text(
        "### Bug fixes\n\n* Fix the thing. (#123)\n",
        encoding="utf-8",
    )
    (tmp_path / "CHANGELOG.md").write_text(
        "[//]: # (current developments)\n",
        encoding="utf-8",
    )


def mock_prepare_commands(
    monkeypatch: pytest.MonkeyPatch,
    *,
    remote_sha: str = "a" * 40,
    auth_error: bool = False,
    lookup_error: bool = False,
) -> tuple[list[tuple[list[str], dict[str, str] | None]], list[dict[str, object]]]:
    calls: list[tuple[list[str], dict[str, str] | None]] = []
    pull_requests: list[dict[str, object]] = []

    def fake_run(
        command: list[str],
        *,
        capture: bool = False,
        env: dict[str, str] | None = None,
    ) -> str:
        calls.append((command, env))
        if command == ["gh", "auth", "setup-git"] and auth_error:
            raise ActionError("GitHub authentication failed.")
        if command[:3] == ["git", "tag", "--list"]:
            return ""
        if command[:3] == ["git", "status", "--porcelain"]:
            return " M CHANGELOG.md\n D news/123-fix\n"
        if command[:2] == ["git", "ls-remote"]:
            if lookup_error:
                raise ActionError("Remote branch lookup failed.")
            return f"{remote_sha}\trefs/heads/26.7.x\n"
        return ""

    def fake_create_or_update_pr(**kwargs: object) -> str:
        pull_requests.append(kwargs)
        return "https://github.com/conda/conda/pull/123"

    monkeypatch.setattr(prepare_release_module, "run", fake_run)
    monkeypatch.setattr(release_common_module, "run", fake_run)
    monkeypatch.setattr(
        prepare_release_module,
        "create_or_update_pr",
        fake_create_or_update_pr,
    )
    return calls, pull_requests


def test_verify_context_accepts_trusted_release_push(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_workflow_run_event(tmp_path, monkeypatch)

    assert verify_context("[0-9]*.[0-9]*.x") == {
        "head_branch": "26.7.x",
        "head_sha": "abc123",
    }


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("conclusion", "failure", "did not conclude successfully"),
        ("event", "pull_request", "must come from a push"),
        ("head_repository", "someone/conda", "must come from this repository"),
        ("branch", "main", "does not match"),
    ],
)
def test_verify_context_rejects_untrusted_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
    message: str,
) -> None:
    kwargs = {field: value}
    write_workflow_run_event(tmp_path, monkeypatch, **kwargs)

    with pytest.raises(ActionError, match=message):
        verify_context("[0-9]*.[0-9]*.x")


def test_prepare_release_noops_without_fragments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    write_workflow_run_event(tmp_path, monkeypatch)
    news = tmp_path / "news"
    news.mkdir()
    (news / "TEMPLATE").write_text("* <news item>\n", encoding="utf-8")
    monkeypatch.setattr(
        prepare_release_module,
        "run",
        lambda *args, **kwargs: pytest.fail("No commands should run."),
    )

    prepare_release(prepare_args())

    assert (
        "No news fragments found under 'news'. Nothing to do."
        in capsys.readouterr().out
    )


def test_prepare_release_rejects_missing_news_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    write_workflow_run_event(tmp_path, monkeypatch)

    with pytest.raises(ActionError, match="News directory does not exist: news"):
        prepare_release(prepare_args())


def test_prepare_release_rejects_malformed_fragment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    write_workflow_run_event(tmp_path, monkeypatch)
    news = tmp_path / "news"
    news.mkdir()
    (news / "123-fix").write_text("not a news fragment\n", encoding="utf-8")

    with pytest.raises(ActionError, match="no news headings found"):
        prepare_release(prepare_args())


def test_prepare_release_skips_stale_workflow_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    write_workflow_run_event(tmp_path, monkeypatch, sha="a" * 40)
    write_release_files(tmp_path)
    calls, pull_requests = mock_prepare_commands(monkeypatch, remote_sha="b" * 40)

    prepare_release(prepare_args())

    commands = [command for command, _ in calls]
    assert commands[-2:] == [
        ["gh", "auth", "setup-git"],
        [
            "git",
            "ls-remote",
            "--exit-code",
            "--heads",
            "origin",
            "refs/heads/26.7.x",
        ],
    ]
    assert not any(command[:2] == ["git", "push"] for command in commands)
    assert not pull_requests
    assert "Skipping stale workflow run for 26.7.x" in capsys.readouterr().out


def test_prepare_release_publishes_when_remote_head_matches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    write_workflow_run_event(tmp_path, monkeypatch, sha="a" * 40)
    write_release_files(tmp_path)
    calls, pull_requests = mock_prepare_commands(monkeypatch)

    prepare_release(prepare_args())

    commands = [command for command, _ in calls]
    assert commands[-3:] == [
        ["gh", "auth", "setup-git"],
        [
            "git",
            "ls-remote",
            "--exit-code",
            "--heads",
            "origin",
            "refs/heads/26.7.x",
        ],
        [
            "git",
            "push",
            "--force-with-lease",
            "origin",
            "release-notes-26.7.0",
        ],
    ]
    lookup_env = calls[-2][1]
    assert lookup_env is not None
    assert lookup_env["GH_TOKEN"] == "test-token"
    assert pull_requests == [
        {
            "repository": "conda/conda",
            "branch": "release-notes-26.7.0",
            "base_branch": "26.7.x",
            "version": "26.7.0",
            "token": "test-token",
        }
    ]


@pytest.mark.parametrize(
    ("auth_error", "lookup_error", "message"),
    [
        (True, False, "GitHub authentication failed"),
        (False, True, "Remote branch lookup failed"),
    ],
)
def test_prepare_release_fails_closed_when_publish_check_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    auth_error: bool,
    lookup_error: bool,
    message: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    write_workflow_run_event(tmp_path, monkeypatch, sha="a" * 40)
    write_release_files(tmp_path)
    calls, pull_requests = mock_prepare_commands(
        monkeypatch,
        auth_error=auth_error,
        lookup_error=lookup_error,
    )

    with pytest.raises(ActionError, match=message):
        prepare_release(prepare_args())

    commands = [command for command, _ in calls]
    assert ["gh", "auth", "setup-git"] in commands
    assert not any(command[:2] == ["git", "push"] for command in commands)
    assert not pull_requests


def test_infer_next_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)
    (tmp_path / "README.md").write_text("test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], check=True)
    subprocess.run(["git", "commit", "-m", "init"], check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "tag", "26.7.0"], check=True)
    subprocess.run(["git", "tag", "v26.7.1"], check=True)
    subprocess.run(["git", "tag", "26.8.0"], check=True)

    assert infer_next_version("26.7.x") == "26.7.2"


def test_collect_fragments_preserves_sections(tmp_path: Path) -> None:
    news = tmp_path / "news"
    news.mkdir()
    (news / "123-feature").write_text(
        "### Enhancements\n\n* Add feature. (#123)\n\n"
        "### Bug fixes\n\n* Fix bug. (#123)\n",
        encoding="utf-8",
    )
    (news / "TEMPLATE").write_text("* <news item>\n", encoding="utf-8")
    (news / ".DS_Store").write_text("", encoding="utf-8")

    assert collect_fragments([news / "123-feature"]) == {
        "Enhancements": ["* Add feature. (#123)"],
        "Bug fixes": ["* Fix bug. (#123)"],
    }


def test_render_changelog_entry() -> None:
    entry = render_changelog_entry(
        "26.7.0",
        "2026-06-05",
        {
            "Enhancements": ["* Add feature. (#123)"],
            "Docs": ["* Document feature. (#123)"],
        },
    )

    assert entry == (
        "## 26.7.0 (2026-06-05)\n\n"
        "### Enhancements\n\n"
        "* Add feature. (#123)\n\n"
        "### Docs\n\n"
        "* Document feature. (#123)\n\n\n"
    )


def test_update_changelog_inserts_after_current_developments(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "[//]: # (current developments)\n\n## 26.6.0 (2026-05-01)\n",
        encoding="utf-8",
    )

    update_changelog(changelog, "## 26.7.0 (2026-06-05)\n\n\n", "26.7.0")

    assert changelog.read_text(encoding="utf-8").startswith(
        "[//]: # (current developments)\n\n"
        "## 26.7.0 (2026-06-05)\n\n\n"
        "## 26.6.0 (2026-05-01)\n"
    )


def test_update_changelog_amends_existing_version(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "[//]: # (current developments)\n\n"
        "## 26.7.0 (2026-06-05)\n\n"
        "### Enhancements\n\n"
        "* Existing enhancement. (#100)\n\n"
        "### Contributors\n\n"
        "* @alice\n\n\n"
        "## 26.5.1 (2026-05-26)\n\n"
        "### Bug fixes\n\n"
        "* Older fix. (#90)\n",
        encoding="utf-8",
    )

    update_changelog(
        changelog,
        "## 26.7.0 (2026-08-12)\n\n"
        "### Enhancements\n\n"
        "* New enhancement. (#123)\n\n"
        "### Bug fixes\n\n"
        "* New fix. (#124)\n\n\n",
        "26.7.0",
    )

    assert changelog.read_text(encoding="utf-8") == (
        "[//]: # (current developments)\n\n"
        "## 26.7.0 (2026-06-05)\n\n"
        "### Enhancements\n\n"
        "* Existing enhancement. (#100)\n"
        "* New enhancement. (#123)\n\n"
        "### Bug fixes\n\n"
        "* New fix. (#124)\n\n"
        "### Contributors\n\n"
        "* @alice\n\n\n"
        "## 26.5.1 (2026-05-26)\n\n"
        "### Bug fixes\n\n"
        "* Older fix. (#90)\n"
    )


def test_ensure_allowed_paths() -> None:
    ensure_allowed_paths(
        [Path("CHANGELOG.md"), Path("news/123-fix")],
        changelog_path=Path("CHANGELOG.md"),
        news_paths=[Path("news/123-fix")],
    )

    with pytest.raises(ActionError, match="unexpected file changes"):
        ensure_allowed_paths(
            [Path("conda/example.py")],
            changelog_path=Path("CHANGELOG.md"),
            news_paths=[Path("news/123-fix")],
        )

    with pytest.raises(ActionError, match="unexpected file changes"):
        ensure_allowed_paths(
            [Path("news/.DS_Store")],
            changelog_path=Path("CHANGELOG.md"),
            news_paths=[Path("news/123-fix")],
        )


def test_get_contributor_commits(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> str:
        commands.append(command)
        return "sha1\0alice@example.com\0sha2\0bob@example.com\0"

    monkeypatch.setattr(prepare_release_module, "run", fake_run)

    assert get_contributor_commits("26.6.1") == [
        ContributorCommit(hash="sha1", email="alice@example.com"),
        ContributorCommit(hash="sha2", email="bob@example.com"),
    ]
    assert commands[0][-1] == "26.6.1..HEAD"

    commands.clear()
    get_contributor_commits("")
    assert commands[0][-1] == "HEAD"


def test_resolve_logins_caches_and_skips_unresolvable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> str:
        calls.append(command)
        sha = command[-1].rsplit("/", 1)[-1]
        if sha == "sha2":
            raise ActionError("lookup failed")
        return json.dumps({"author": {"login": f"user-{sha}"}})

    monkeypatch.setattr(release_common_module, "run", fake_run)
    commits = [
        ContributorCommit(hash="sha1", email="a@example.com"),
        ContributorCommit(hash="sha2", email="b@example.com"),
        ContributorCommit(hash="sha3", email="a@example.com"),
    ]

    assert resolve_logins(commits, "conda/conda", {}) == {
        "user-sha1": "user-sha1",
    }
    assert len(calls) == 2
    assert "::warning::Failed to resolve GitHub login" in capsys.readouterr().err


def test_resolve_logins_tries_all_hashes_per_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_run(command: list[str], **kwargs: object) -> str:
        sha = command[-1].rsplit("/", 1)[-1]
        calls.append(sha)
        if sha == "sha1":
            return json.dumps({"author": None})
        return json.dumps({"author": {"login": "alice"}})

    monkeypatch.setattr(release_common_module, "run", fake_run)
    commits = [
        ContributorCommit(hash="sha1", email="a@example.com"),
        ContributorCommit(hash="sha2", email="a@example.com"),
    ]

    assert resolve_logins(commits, "conda/conda", {}) == {"alice": "alice"}
    assert calls == ["sha1", "sha2"]


def test_is_first_timer_without_previous_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_run(
        monkeypatch,
        lambda *args, **kwargs: pytest.fail("No commands should run."),
    )

    assert is_first_timer("alice", "", "conda/conda", {}, "26.7.x")


@pytest.mark.parametrize(
    ("payload", "expected"),
    [("[]", True), ('[{"sha": "old"}]', False)],
)
def test_is_first_timer_queries_prior_commits_on_release_branch(
    monkeypatch: pytest.MonkeyPatch,
    payload: str,
    expected: bool,
) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> str:
        commands.append(command)
        return payload

    monkeypatch.setattr(release_common_module, "run", fake_run)

    assert (
        is_first_timer(
            "alice",
            "2026-05-01T00:00:00+00:00",
            "conda/conda",
            {},
            "26.7.x",
        )
        is expected
    )
    assert len(commands) == 1
    assert "sha=26.7.x" in commands[0][-1]


def test_is_first_timer_degrades_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_run(*args: object, **kwargs: object) -> str:
        raise ActionError("lookup failed")

    monkeypatch.setattr(release_common_module, "run", fake_run)

    assert not is_first_timer(
        "alice",
        "2026-05-01T00:00:00+00:00",
        "conda/conda",
        {},
        "26.7.x",
    )
    assert "::warning::" in capsys.readouterr().err


def test_first_merged_pr_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        release_common_module,
        "run",
        lambda *args, **kwargs: json.dumps(
            [{"url": "https://github.com/conda/conda/pull/42"}]
        ),
    )

    assert (
        first_merged_pr_url("alice", "conda/conda", {})
        == "https://github.com/conda/conda/pull/42"
    )


def test_first_merged_pr_url_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release_common_module, "run", lambda *args, **kwargs: "[]")
    assert first_merged_pr_url("alice", "conda/conda", {}) is None

    def fake_run(*args: object, **kwargs: object) -> str:
        raise ActionError("lookup failed")

    monkeypatch.setattr(release_common_module, "run", fake_run)
    assert first_merged_pr_url("alice", "conda/conda", {}) is None


def test_render_contributors() -> None:
    body = render_contributors(
        [
            ("Bob", None),
            ("alice", "https://github.com/conda/conda/pull/42"),
            ("dependabot[bot]", None),
        ]
    )

    assert body == (
        "* @alice made their first commit in "
        "https://github.com/conda/conda/pull/42\n"
        "* @Bob\n"
        "* @dependabot[bot]"
    )


def test_render_changelog_entry_with_contributors() -> None:
    entry = render_changelog_entry(
        "26.7.0",
        "2026-06-05",
        {"Bug fixes": ["* Fix bug. (#123)"]},
        "* @alice\n* @Bob",
    )

    assert entry == (
        "## 26.7.0 (2026-06-05)\n\n"
        "### Bug fixes\n\n"
        "* Fix bug. (#123)\n\n"
        "### Contributors\n\n"
        "* @alice\n"
        "* @Bob\n\n\n"
    )


def test_merge_changelog_entry_replaces_contributors() -> None:
    release = (
        "## 26.7.0 (2026-06-05)\n\n"
        "### Enhancements\n\n"
        "* Existing enhancement. (#100)\n\n"
        "### Contributors\n\n"
        "* @alice\n\n\n"
    )
    entry = "## 26.7.0 (2026-08-12)\n\n### Contributors\n\n* @alice\n* @Bob\n\n\n"

    assert merge_changelog_entry(release, entry) == (
        "## 26.7.0 (2026-06-05)\n\n"
        "### Enhancements\n\n"
        "* Existing enhancement. (#100)\n\n"
        "### Contributors\n\n"
        "* @alice\n"
        "* @Bob\n\n\n"
    )


def test_merge_changelog_entry_appends_contributors() -> None:
    release = (
        "## 26.7.0 (2026-06-05)\n\n"
        "### Enhancements\n\n"
        "* Existing enhancement. (#100)\n\n\n"
    )
    entry = "## 26.7.0 (2026-08-12)\n\n### Contributors\n\n* @alice\n\n\n"

    assert merge_changelog_entry(release, entry) == (
        "## 26.7.0 (2026-06-05)\n\n"
        "### Enhancements\n\n"
        "* Existing enhancement. (#100)\n\n"
        "### Contributors\n\n"
        "* @alice\n\n\n"
    )


def test_collect_contributors_without_previous_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> str:
        commands.append(command)
        if command[:3] == ["git", "tag", "--merged"]:
            return ""
        if command[:2] == ["git", "log"]:
            return "sha1\0alice@example.com\0sha2\0bob@example.com\0"
        if command[:2] == ["gh", "api"]:
            sha = command[-1].rsplit("/", 1)[-1]
            login = {"sha1": "alice", "sha2": "Bob"}[sha]
            return json.dumps({"author": {"login": login}})
        if command[:3] == ["gh", "search", "prs"]:
            login = command[command.index("--author") + 1]
            return json.dumps([{"url": f"https://github.com/conda/conda/pull/{login}"}])
        return ""

    patch_run(monkeypatch, fake_run)

    assert collect_contributors("conda/conda", {}, base_branch="26.7.x") == (
        "* @alice made their first commit in "
        "https://github.com/conda/conda/pull/alice\n"
        "* @Bob made their first commit in "
        "https://github.com/conda/conda/pull/Bob"
    )
    assert not any(
        any("commits?author=" in argument for argument in command)
        for command in commands
    )


def test_collect_contributors_scopes_previous_tag_to_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> str:
        commands.append(command)
        if command[:3] == ["git", "tag", "--merged"]:
            return "26.7.0\n"
        if command[:2] == ["git", "log"] and "-z" in command:
            return "sha1\0alice@example.com\0"
        if command[:2] == ["git", "log"]:
            return "2026-05-01T00:00:00+00:00\n"
        if command[:2] == ["gh", "api"] and "commits?author=" in command[-1]:
            return '[{"sha": "old"}]'
        if command[:2] == ["gh", "api"]:
            return json.dumps({"author": {"login": "alice"}})
        return ""

    patch_run(monkeypatch, fake_run)

    assert (
        collect_contributors(
            "conda/conda",
            {},
            base_branch="26.7.x",
            tag_prefix="26.7.",
        )
        == "* @alice"
    )
    assert commands[0] == [
        "git",
        "tag",
        "--merged",
        "HEAD",
        "--list",
        "26.7.*",
        "--list",
        "v26.7.*",
    ]


def test_collect_contributors_dedupes_logins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(command: list[str], **kwargs: object) -> str:
        if command[:3] == ["git", "tag", "--merged"]:
            return "26.6.1\n"
        if command[:2] == ["git", "log"] and "-z" in command:
            return "sha1\0alice@example.com\0sha2\0alice@work.example.com\0"
        if command[:2] == ["git", "log"]:
            return "2026-05-01T00:00:00+00:00\n"
        if command[:2] == ["gh", "api"] and "commits?author=" in command[-1]:
            return '[{"sha": "old"}]'
        if command[:2] == ["gh", "api"]:
            return json.dumps({"author": {"login": "alice"}})
        return ""

    patch_run(monkeypatch, fake_run)

    assert collect_contributors("conda/conda", {}, base_branch="26.7.x") == "* @alice"


def test_collect_contributors_skips_unresolvable_authors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_run(command: list[str], **kwargs: object) -> str:
        if command[:3] == ["git", "tag", "--merged"]:
            return "26.6.1\n"
        if command[:2] == ["git", "log"] and "-z" in command:
            return "sha1\0alice@example.com\0sha2\0ghost@example.com\0"
        if command[:2] == ["git", "log"]:
            return "2026-05-01T00:00:00+00:00\n"
        if command[:2] == ["gh", "api"] and "commits?author=" in command[-1]:
            return '[{"sha": "old"}]'
        if command[:2] == ["gh", "api"]:
            if command[-1].endswith("/sha2"):
                return json.dumps({"author": None})
            return json.dumps({"author": {"login": "alice"}})
        return ""

    patch_run(monkeypatch, fake_run)

    assert collect_contributors("conda/conda", {}, base_branch="26.7.x") == "* @alice"
    assert (
        "::warning::No GitHub login associated with commit" in capsys.readouterr().err
    )


def test_prepare_release_adds_contributors_section(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    write_workflow_run_event(tmp_path, monkeypatch, sha="a" * 40)
    write_release_files(tmp_path)
    gh_envs: list[dict[str, str] | None] = []

    def fake_run(
        command: list[str],
        *,
        capture: bool = False,
        env: dict[str, str] | None = None,
    ) -> str:
        if command[:2] == ["gh", "api"] or command[:3] == ["gh", "search", "prs"]:
            gh_envs.append(env)
        if command[:3] == ["git", "tag", "--list"]:
            return ""
        if command[:3] == ["git", "tag", "--merged"]:
            return "26.6.1\n"
        if command[:2] == ["git", "log"] and "-z" in command:
            return "sha1\0alice@example.com\0sha2\0bob@example.com\0"
        if command[:2] == ["git", "log"]:
            return "2026-05-01T00:00:00+00:00\n"
        if command[:3] == ["git", "status", "--porcelain"]:
            return " M CHANGELOG.md\n D news/123-fix\n"
        if command[:2] == ["git", "ls-remote"]:
            return f"{'a' * 40}\trefs/heads/26.7.x\n"
        if command[:2] == ["gh", "api"] and "commits?author=" in command[-1]:
            return "[]" if "author=alice" in command[-1] else '[{"sha": "old"}]'
        if command[:2] == ["gh", "api"]:
            sha = command[-1].rsplit("/", 1)[-1]
            login = {"sha1": "alice", "sha2": "Bob"}[sha]
            return json.dumps({"author": {"login": login}})
        if command[:3] == ["gh", "search", "prs"]:
            return json.dumps([{"url": "https://github.com/conda/conda/pull/42"}])
        return ""

    patch_run(monkeypatch, fake_run)
    monkeypatch.setattr(
        prepare_release_module,
        "create_or_update_pr",
        lambda **kwargs: "https://github.com/conda/conda/pull/123",
    )

    prepare_release(prepare_args())

    changelog = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    assert (
        "### Bug fixes\n\n"
        "* Fix the thing. (#123)\n\n"
        "### Contributors\n\n"
        "* @alice made their first commit in "
        "https://github.com/conda/conda/pull/42\n"
        "* @Bob\n"
    ) in changelog
    assert gh_envs
    assert all(env is not None and env["GH_TOKEN"] == "test-token" for env in gh_envs)
