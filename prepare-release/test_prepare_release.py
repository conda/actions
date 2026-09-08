from __future__ import annotations

import json
import os
import shutil
import subprocess
from argparse import Namespace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

import prepare_release as prepare_release_module
from conda_actions import commands as commands_module
from conda_actions import release as release_module
from prepare_release import (
    MAX_LOGIN_LOOKUPS_PER_EMAIL,
    MAX_UNRESOLVED_LOGIN_LOOKUPS,
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

if TYPE_CHECKING:
    from pytest_httpserver import HTTPServer


def patch_run(monkeypatch: pytest.MonkeyPatch, fake_run: object) -> None:
    monkeypatch.setattr(prepare_release_module, "run", fake_run)
    monkeypatch.setattr(release_module, "run", fake_run)
    monkeypatch.setattr(commands_module, "run", fake_run)


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
    require_auth: bool = False,
) -> tuple[list[tuple[list[str], dict[str, str] | None]], list[dict[str, object]]]:
    calls: list[tuple[list[str], dict[str, str] | None]] = []
    pull_requests: list[dict[str, object]] = []
    authenticated = False

    def fake_run(
        command: list[str],
        *,
        capture: bool = False,
        env: dict[str, str] | None = None,
    ) -> str:
        nonlocal authenticated
        calls.append((command, env))
        if command == ["gh", "auth", "setup-git"]:
            if auth_error:
                raise ActionError("GitHub authentication failed.")
            authenticated = True
        if command[:3] == ["git", "tag", "--list"]:
            return ""
        if command[:3] == ["git", "status", "--porcelain"]:
            return " M CHANGELOG.md\n D news/123-fix\n"
        if command[:2] == ["git", "ls-remote"]:
            if require_auth and not authenticated:
                raise ActionError("Git credentials are not configured.")
            if lookup_error:
                raise ActionError("Remote branch lookup failed.")
            return f"{remote_sha}\trefs/heads/26.7.x\n"
        return ""

    def fake_create_or_update_pr(**kwargs: object) -> str:
        pull_requests.append(kwargs)
        return "https://github.com/conda/conda/pull/123"

    monkeypatch.setattr(prepare_release_module, "run", fake_run)
    monkeypatch.setattr(release_module, "run", fake_run)
    monkeypatch.setattr(commands_module, "run", fake_run)
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


def test_main_verifies_context_and_writes_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_workflow_run_event(tmp_path, monkeypatch)
    output = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    assert prepare_release_module.main(["verify-context"]) == 0

    assert output.read_text(encoding="utf-8") == (
        "head-branch=26.7.x\nhead-sha=abc123\n"
    )
    assert "Verified release context for 26.7.x." in capsys.readouterr().out


def test_main_rejects_other_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_workflow_run_event(tmp_path, monkeypatch)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    output = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    assert prepare_release_module.main(["verify-context"]) == 1

    assert "must run from the workflow_run event" in capsys.readouterr().err
    assert not output.exists()


def test_main_rejects_unknown_subcommand(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        prepare_release_module,
        "prepare_release",
        lambda *args: pytest.fail("An invalid command must not prepare a release."),
    )

    with pytest.raises(SystemExit) as exc:
        prepare_release_module.main(["unknown"])

    assert exc.value.code == 2


@pytest.mark.parametrize("event_path", [None, "missing.json"])
def test_main_rejects_unavailable_event_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    event_path: str | None,
) -> None:
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_run")
    monkeypatch.setenv("GITHUB_REPOSITORY", "conda/conda")
    if event_path is None:
        monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    else:
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(tmp_path / event_path))
    output = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    assert prepare_release_module.main(["verify-context"]) == 1

    assert "did not conclude successfully" in capsys.readouterr().err
    assert not output.exists()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("conclusion", "failure", "did not conclude successfully"),
        ("event", "pull_request", "must come from a push"),
        ("head_repository", "someone/conda", "must come from this repository"),
        ("branch", "main", "does not match"),
        ("branch", "", "did not include a head branch and SHA"),
        ("sha", "", "did not include a head branch and SHA"),
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

    assert prepare_release_module.main(["prepare"]) == 0

    assert (
        "No news fragments found under 'news'. Nothing to do."
        in capsys.readouterr().out
    )


def test_prepare_release_skips_publication_without_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    write_workflow_run_event(tmp_path, monkeypatch, sha="a" * 40)
    write_release_files(tmp_path)
    calls, pull_requests = mock_prepare_commands(monkeypatch)
    monkeypatch.setattr(prepare_release_module, "get_changed_paths", lambda: [])

    prepare_release(prepare_args())

    assert "No release note changes to commit." in capsys.readouterr().out
    assert not any(
        command[:2] in (["git", "add"], ["git", "commit"], ["git", "push"])
        for command, _ in calls
    )
    assert not pull_requests


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


@pytest.mark.parametrize("field", ["token", "repository"])
def test_prepare_release_requires_github_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    write_workflow_run_event(tmp_path, monkeypatch, sha="a" * 40)
    write_release_files(tmp_path)
    calls, pull_requests = mock_prepare_commands(monkeypatch)
    args = prepare_args()
    setattr(args, field, "")
    changelog = tmp_path / "CHANGELOG.md"
    fragment = tmp_path / "news/123-fix"
    original_changelog = changelog.read_bytes()
    original_fragment = fragment.read_bytes()

    with pytest.raises(ActionError, match=f"No GitHub {field} was provided"):
        prepare_release(args)

    assert changelog.read_bytes() == original_changelog
    assert fragment.read_bytes() == original_fragment
    assert not any(command[0] == "gh" for command, _ in calls)
    assert not any(command[:2] == ["git", "push"] for command, _ in calls)
    assert not pull_requests


@pytest.mark.parametrize("response", ["", f"{'a' * 40}\trefs/heads/26.8.x\n"])
def test_prepare_release_rejects_unexpected_remote_head_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    response: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    write_workflow_run_event(tmp_path, monkeypatch, sha="a" * 40)
    write_release_files(tmp_path)
    calls, pull_requests = mock_prepare_commands(monkeypatch)
    run = prepare_release_module.run

    def fake_run(command: list[str], **kwargs: object) -> str:
        result = run(command, **kwargs)
        return response if command[:2] == ["git", "ls-remote"] else result

    monkeypatch.setattr(prepare_release_module, "run", fake_run)
    changelog = tmp_path / "CHANGELOG.md"
    fragment = tmp_path / "news/123-fix"
    original_changelog = changelog.read_bytes()
    original_fragment = fragment.read_bytes()

    with pytest.raises(ActionError, match="Could not determine remote head"):
        prepare_release(prepare_args())

    assert changelog.read_bytes() == original_changelog
    assert fragment.read_bytes() == original_fragment
    assert not any(command[:2] == ["git", "push"] for command, _ in calls)
    assert not pull_requests


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
    assert commands[-1] == [
        "git",
        "ls-remote",
        "--exit-code",
        "--heads",
        "origin",
        "refs/heads/26.7.x",
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
    assert commands[-2:] == [
        ["git", "ls-remote", "--exit-code", "--heads", "origin", "refs/heads/26.7.x"],
        [
            "git",
            "push",
            "--force-with-lease",
            "origin",
            "release-notes-26.7.0",
        ],
    ]
    assert sum(command[:2] == ["git", "ls-remote"] for command in commands) == 2
    lookup = next(
        (command, env) for command, env in calls if command[:2] == ["git", "ls-remote"]
    )
    assert lookup[0] == [
        "git",
        "ls-remote",
        "--exit-code",
        "--heads",
        "origin",
        "refs/heads/26.7.x",
    ]
    assert lookup[1] is not None
    assert lookup[1]["GH_TOKEN"] == "test-token"
    assert pull_requests == [
        {
            "repository": "conda/conda",
            "branch": "release-notes-26.7.0",
            "base_branch": "26.7.x",
            "version": "26.7.0",
            "token": "test-token",
        }
    ]


@pytest.mark.parametrize("lookup_error", [False, True])
def test_prepare_release_checks_head_again_after_contributor_collection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lookup_error: bool,
) -> None:
    monkeypatch.chdir(tmp_path)
    write_workflow_run_event(tmp_path, monkeypatch, sha="a" * 40)
    write_release_files(tmp_path)
    calls, pull_requests = mock_prepare_commands(monkeypatch)
    run = prepare_release_module.run
    collected = False

    def collect(*args: object, **kwargs: object) -> str:
        nonlocal collected
        collected = True
        return "* @alice"

    def fake_run(command: list[str], **kwargs: object) -> str:
        if command[:2] == ["git", "ls-remote"] and collected:
            if lookup_error:
                raise ActionError("Remote branch lookup failed.")
            return f"{'b' * 40}\trefs/heads/26.7.x\n"
        return run(command, **kwargs)

    monkeypatch.setattr(prepare_release_module, "collect_contributors", collect)
    monkeypatch.setattr(prepare_release_module, "run", fake_run)
    output = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    if lookup_error:
        with pytest.raises(ActionError, match="Remote branch lookup failed"):
            prepare_release(prepare_args())
    else:
        prepare_release(prepare_args())

    assert collected
    assert not any(command[:2] == ["git", "push"] for command, _ in calls)
    assert not pull_requests
    assert not output.exists()


def test_prepare_release_authenticates_before_reading_remote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    write_workflow_run_event(tmp_path, monkeypatch, sha="a" * 40)
    write_release_files(tmp_path)
    calls, pull_requests = mock_prepare_commands(monkeypatch, require_auth=True)

    prepare_release(prepare_args())

    auth_env = next(
        env for command, env in calls if command == ["gh", "auth", "setup-git"]
    )
    assert auth_env is not None
    assert auth_env["GH_TOKEN"] == "test-token"
    assert pull_requests


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
    if auth_error:
        assert not any(command[:2] == ["git", "ls-remote"] for command in commands)
    if not lookup_error:
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
    subprocess.run(["git", "tag", "26.7.2rc1"], check=True)
    subprocess.run(["git", "tag", "26.8.0"], check=True)

    assert infer_next_version("26.7.x") == "26.7.2"


def test_infer_next_version_rejects_non_release_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        prepare_release_module,
        "run",
        lambda *args, **kwargs: pytest.fail("No Git commands should run."),
    )

    with pytest.raises(ActionError, match="Cannot infer release version from branch"):
        infer_next_version("main")


def test_news_fragment_paths_without_directory(tmp_path: Path) -> None:
    assert prepare_release_module.news_fragment_paths(tmp_path / "missing") == []


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


def test_update_changelog_requires_existing_file(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"

    with pytest.raises(ActionError, match="Changelog file does not exist"):
        update_changelog(changelog, "## 26.7.0 (2026-06-05)\n", "26.7.0")

    assert not changelog.exists()


def test_update_changelog_prepends_without_developments_marker(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    previous = "## 26.6.0 (2026-05-01)\n\n* Previous release notes.\n"
    changelog.write_text("\n\n" + previous, encoding="utf-8")
    entry = "## 26.7.0 (2026-06-05)\n\n* New release notes.\n\n"

    update_changelog(changelog, entry, "26.7.0")

    assert changelog.read_text(encoding="utf-8") == entry + previous


def test_merge_changelog_entry_appends_new_section() -> None:
    release = (
        "## 26.7.0 (2026-06-05)\n\n### Enhancements\n\n* Existing enhancement.\n\n\n"
    )
    entry = (
        "## 26.7.0 (2026-06-06)\n\n"
        "### Bug fixes\n\n* New fix.\n\n"
        "### Extra\n\nUnrecognized incoming section.\n"
    )

    assert merge_changelog_entry(release, entry) == (
        "## 26.7.0 (2026-06-05)\n\n"
        "### Enhancements\n\n* Existing enhancement.\n\n"
        "### Bug fixes\n\n* New fix.\n\n\n"
    )


def test_get_changed_paths_ignores_blank_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        prepare_release_module,
        "run",
        lambda *args, **kwargs: " M CHANGELOG.md\n\n D news/123-fix\n",
    )

    assert prepare_release_module.get_changed_paths() == [
        Path("CHANGELOG.md"),
        Path("news/123-fix"),
    ]


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
            return json.dumps({"author": None})
        return json.dumps({"author": {"login": f"user-{sha}"}})

    monkeypatch.setattr(commands_module, "run", fake_run)
    commits = [
        ContributorCommit(hash="sha1", email="a@example.com"),
        ContributorCommit(hash="sha2", email="b@example.com"),
        ContributorCommit(hash="sha3", email="a@example.com"),
    ]

    assert resolve_logins(commits, "conda/conda", {}) == {
        "user-sha1": "user-sha1",
    }
    assert len(calls) == 2
    assert (
        "::warning::No GitHub login associated with commit" in capsys.readouterr().err
    )


@pytest.mark.parametrize("repeat_count", [1, MAX_LOGIN_LOOKUPS_PER_EMAIL])
def test_resolve_logins_tries_distinct_hashes_per_email(
    monkeypatch: pytest.MonkeyPatch,
    repeat_count: int,
) -> None:
    calls: list[str] = []

    def fake_run(command: list[str], **kwargs: object) -> str:
        sha = command[-1].rsplit("/", 1)[-1]
        calls.append(sha)
        if sha == "sha1":
            return json.dumps({"author": None})
        return json.dumps({"author": {"login": "alice"}})

    monkeypatch.setattr(commands_module, "run", fake_run)
    commits = [
        ContributorCommit(hash="sha1", email="a@example.com"),
    ] * repeat_count + [
        ContributorCommit(hash="sha2", email="a@example.com"),
    ]

    assert resolve_logins(commits, "conda/conda", {}) == {"alice": "alice"}
    assert calls == ["sha1", "sha2"]


@pytest.mark.parametrize(
    "count", [MAX_UNRESOLVED_LOGIN_LOOKUPS, MAX_UNRESOLVED_LOGIN_LOOKUPS + 5]
)
def test_resolve_logins_requires_complete_lookup_within_limit(
    monkeypatch: pytest.MonkeyPatch,
    count: int,
) -> None:
    calls = 0

    def fake_run(*args: object, **kwargs: object) -> str:
        nonlocal calls
        calls += 1
        return json.dumps({"author": None})

    monkeypatch.setattr(commands_module, "run", fake_run)
    commits = [
        ContributorCommit(hash=f"sha{index}", email=f"user{index}@example.com")
        for index in range(count)
    ]

    if count > MAX_UNRESOLVED_LOGIN_LOOKUPS:
        with pytest.raises(
            ActionError, match="Cannot prepare complete contributor list"
        ):
            resolve_logins(commits, "conda/conda", {})
    else:
        assert resolve_logins(commits, "conda/conda", {}) == {}
    assert calls == MAX_UNRESOLVED_LOGIN_LOOKUPS


def test_resolve_logins_caps_hashes_per_email(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = 0

    def fake_run(*args: object, **kwargs: object) -> str:
        nonlocal calls
        calls += 1
        return json.dumps({"author": None})

    monkeypatch.setattr(commands_module, "run", fake_run)
    commits = [
        ContributorCommit(hash=f"sha{index}", email="a@example.com")
        for index in range(MAX_LOGIN_LOOKUPS_PER_EMAIL + 3)
    ]

    assert resolve_logins(commits, "conda/conda", {}) == {}
    assert calls == MAX_LOGIN_LOOKUPS_PER_EMAIL
    assert (
        capsys.readouterr().err.count("No GitHub login associated with commit") == calls
    )


def test_resolve_logins_successes_do_not_count_toward_cap(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = 0

    def fake_run(command: list[str], **kwargs: object) -> str:
        nonlocal calls
        calls += 1
        sha = command[-1].rsplit("/", 1)[-1]
        return json.dumps({"author": {"login": f"user-{sha}"}})

    monkeypatch.setattr(commands_module, "run", fake_run)
    commits = [
        ContributorCommit(hash=f"sha{index}", email=f"user{index}@example.com")
        for index in range(MAX_UNRESOLVED_LOGIN_LOOKUPS + 5)
    ]

    result = resolve_logins(commits, "conda/conda", {})
    assert result == {
        f"user-sha{index}": f"user-sha{index}"
        for index in range(MAX_UNRESOLVED_LOGIN_LOOKUPS + 5)
    }
    assert calls == MAX_UNRESOLVED_LOGIN_LOOKUPS + 5
    assert not capsys.readouterr().err


def test_is_first_timer_without_previous_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_run(
        monkeypatch,
        lambda *args, **kwargs: pytest.fail("No commands should run."),
    )

    assert is_first_timer("alice", "", "conda/conda", {}, "26.7.x")


@pytest.mark.skipif(shutil.which("gh") is None, reason="GitHub CLI is required")
def test_is_first_timer_sends_get_query(
    monkeypatch: pytest.MonkeyPatch,
    httpserver: HTTPServer,
) -> None:
    httpserver.expect_oneshot_request(
        "/repos/conda/conda/commits",
        method="GET",
        query_string={
            "author": "alice",
            "until": "2026-05-01T00:00:00+00:00",
            "per_page": "1",
            "sha": "26.7.x",
        },
    ).respond_with_json([])
    run = commands_module.run

    def local_run(command: list[str], **kwargs: object) -> str:
        command = command.copy()
        command[2] = httpserver.url_for(f"/{command[2]}")
        return run(command, **kwargs)

    monkeypatch.setattr(commands_module, "run", local_run)
    result = is_first_timer(
        "alice",
        "2026-05-01T00:00:00+00:00",
        "conda/conda",
        os.environ | {"GH_TOKEN": "test-token"},
        "26.7.x",
    )
    assert result
    httpserver.check()


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

    monkeypatch.setattr(commands_module, "run", fake_run)

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
    assert commands[0] == [
        "gh",
        "api",
        "repos/conda/conda/commits",
        "--method",
        "GET",
        "-f",
        "author=alice",
        "-f",
        "until=2026-05-01T00:00:00+00:00",
        "-F",
        "per_page=1",
        "-f",
        "sha=26.7.x",
    ]


def test_is_first_timer_encodes_until_offset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> str:
        commands.append(command)
        return "[]"

    monkeypatch.setattr(commands_module, "run", fake_run)

    assert is_first_timer(
        "alice",
        "2026-05-01T00:00:00+00:00",
        "conda/conda",
        {},
        "26.7.x",
    )
    # The +00:00 offset must survive as a discrete -f value so gh URL-encodes
    # it; an unencoded + in a query string decodes to a space.
    assert "until=2026-05-01T00:00:00+00:00" in commands[0]
    assert not any("?" in argument for argument in commands[0])


def test_is_first_timer_propagates_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args: object, **kwargs: object) -> str:
        raise ActionError("lookup failed")

    monkeypatch.setattr(commands_module, "run", fake_run)

    with pytest.raises(ActionError, match="lookup failed"):
        is_first_timer(
            "alice",
            "2026-05-01T00:00:00+00:00",
            "conda/conda",
            {},
            "26.7.x",
        )


def test_first_merged_pr_url(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> str:
        commands.append(command)
        return json.dumps(
            [
                [
                    {
                        "html_url": "https://github.com/conda/conda/pull/42",
                        "pull_request": {"merged_at": "2025-09-14T19:13:48Z"},
                    }
                ]
            ]
        )

    monkeypatch.setattr(commands_module, "run", fake_run)

    assert (
        first_merged_pr_url("alice", "conda/conda", {})
        == "https://github.com/conda/conda/pull/42"
    )
    assert commands == [
        [
            "gh",
            "api",
            "repos/conda/conda/issues",
            "--method",
            "GET",
            "--paginate",
            "--slurp",
            "-f",
            "creator=alice",
            "-f",
            "state=closed",
            "-F",
            "per_page=100",
        ]
    ]


@pytest.mark.parametrize(
    "pages",
    [
        [[]],
        [
            [
                {"html_url": "https://github.com/conda/conda/issues/1"},
                {
                    "html_url": "https://github.com/conda/conda/pull/2",
                    "pull_request": {"merged_at": None},
                },
            ]
        ],
    ],
)
def test_first_merged_pr_url_without_merged_prs(
    monkeypatch: pytest.MonkeyPatch,
    pages: list[list[dict]],
) -> None:
    monkeypatch.setattr(
        commands_module, "run", lambda *args, **kwargs: json.dumps(pages)
    )
    assert first_merged_pr_url("alice", "conda/conda", {}) is None


def test_first_merged_pr_url_propagates_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*args: object, **kwargs: object) -> str:
        raise ActionError("lookup failed")

    monkeypatch.setattr(commands_module, "run", fake_run)
    with pytest.raises(ActionError, match="lookup failed"):
        first_merged_pr_url("alice", "conda/conda", {})


@pytest.mark.parametrize("count", [2, 101, 1001])
def test_first_merged_pr_url_uses_merge_order(
    monkeypatch: pytest.MonkeyPatch,
    count: int,
) -> None:
    prs = [
        {
            "html_url": f"https://github.com/conda/conda/pull/{index}",
            "pull_request": {"merged_at": "2025-09-15T09:47:28Z"},
        }
        for index in range(count)
    ]
    # The last PR by creation order was the first to merge.
    prs[-1]["pull_request"]["merged_at"] = "2025-09-14T19:13:48Z"

    def fake_run(command: list[str], **kwargs: object) -> str:
        return json.dumps([prs[start : start + 100] for start in range(0, count, 100)])

    monkeypatch.setattr(commands_module, "run", fake_run)

    assert first_merged_pr_url("alice", "conda/conda", {}) == prs[-1]["html_url"]


@pytest.mark.skipif(shutil.which("gh") is None, reason="GitHub CLI is required")
@pytest.mark.parametrize("second_page_status", [200, 502])
def test_first_merged_pr_url_paginates(
    monkeypatch: pytest.MonkeyPatch,
    httpserver: HTTPServer,
    second_page_status: int,
) -> None:
    endpoint = "/repos/conda/conda/issues"
    query = {"creator": "alice", "state": "closed", "per_page": "100"}
    next_page = httpserver.url_for(endpoint) + (
        "?creator=alice&state=closed&per_page=100&page=2"
    )
    httpserver.expect_oneshot_request(
        endpoint, method="GET", query_string=query
    ).respond_with_json(
        [
            {"html_url": "https://github.com/conda/conda/issues/1"},
            {
                "html_url": "https://github.com/conda/conda/pull/2",
                "pull_request": {"merged_at": None},
            },
            {
                "html_url": "https://github.com/conda/conda/pull/3",
                "pull_request": {"merged_at": "2025-09-15T09:47:28Z"},
            },
        ],
        headers={"Link": f'<{next_page}>; rel="next"'},
    )
    httpserver.expect_oneshot_request(
        endpoint, method="GET", query_string=query | {"page": "2"}
    ).respond_with_json(
        [
            {
                "html_url": "https://github.com/conda/conda/pull/43",
                "pull_request": {"merged_at": "2025-09-14T19:13:48Z"},
            },
            {
                "html_url": "https://github.com/conda/conda/pull/42",
                "pull_request": {"merged_at": "2025-09-14T19:13:48Z"},
            },
        ]
        if second_page_status == 200
        else {"message": "GitHub unavailable"},
        status=second_page_status,
    )
    run = commands_module.run

    def local_run(command: list[str], **kwargs: object) -> str:
        command = command.copy()
        command[2] = httpserver.url_for(f"/{command[2]}")
        return run(command, **kwargs)

    monkeypatch.setattr(commands_module, "run", local_run)
    env = os.environ | {"GH_TOKEN": "test-token"}
    if second_page_status == 200:
        assert (
            first_merged_pr_url("alice", "conda/conda", env)
            == "https://github.com/conda/conda/pull/42"
        )
    else:
        with pytest.raises(ActionError, match="HTTP 502"):
            first_merged_pr_url("alice", "conda/conda", env)
    httpserver.check()


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


def test_merge_changelog_entry_updates_contributors() -> None:
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


def test_merge_changelog_entry_replaces_contributors() -> None:
    prefix = "## 26.7.0 (2026-06-05)\n\n### Contributors\n\n"
    suffix = "\n\n### Extra\n\nKeep this text.\n\n\n"
    release = (
        prefix
        + "* @alice made their first commit in https://example.com/1\n* @bob"
        + suffix
    )
    incoming = "* @alice\n* @carol"
    entry = prefix + incoming + "\n\n\n"

    assert merge_changelog_entry(release, entry) == prefix + incoming + suffix


@pytest.mark.parametrize("failed_lookup", ["login", "history", "first-pr", "limit"])
def test_prepare_release_fails_before_writing_on_contributor_lookup_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failed_lookup: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    write_workflow_run_event(tmp_path, monkeypatch, sha="a" * 40)
    write_release_files(tmp_path)
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "## 26.7.0 (2026-06-05)\n\n### Contributors\n\n"
        "* @alice made their first commit in https://example.com/1\n* @bob\n",
        encoding="utf-8",
    )
    fragment = tmp_path / "news/123-fix"
    original_changelog = changelog.read_bytes()
    original_fragment = fragment.read_bytes()
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    calls, pull_requests = mock_prepare_commands(monkeypatch)
    monkeypatch.setattr(prepare_release_module, "get_latest_tag", lambda **_: "26.6.1")
    monkeypatch.setattr(prepare_release_module, "get_tag_commit_date", lambda _: "date")
    unresolved = (
        [
            ContributorCommit(f"unknown-{index}", f"unknown-{index}@example.com")
            for index in range(MAX_UNRESOLVED_LOGIN_LOOKUPS)
        ]
        if failed_lookup == "limit"
        else []
    )
    monkeypatch.setattr(
        prepare_release_module,
        "get_contributor_commits",
        lambda _: [
            ContributorCommit("sha1", "alice@example.com"),
            *unresolved,
            ContributorCommit("sha2", "bob@example.com"),
        ],
    )

    def fake_api(command: list[str], **kwargs: object) -> str:
        calls.append((command, kwargs.get("env")))
        if command[2].endswith("/commits/sha1"):
            return json.dumps({"author": {"login": "alice"}})
        if "/commits/unknown-" in command[2]:
            return json.dumps({"author": None})
        if command[2].endswith("/commits/sha2"):
            if failed_lookup == "login":
                raise ActionError("HTTP 502")
            return json.dumps({"author": {"login": "bob"}})
        if command[2].endswith("/commits"):
            if "author=bob" in command:
                if failed_lookup == "history":
                    raise ActionError("HTTP 502")
                return "[]"
            return '[{"sha": "old"}]'
        raise ActionError("HTTP 502")

    monkeypatch.setattr(commands_module, "run", fake_api)

    assert (
        prepare_release_module.main(
            [
                "prepare",
                "--repository",
                "conda/conda",
                "--token",
                "test-token",
            ]
        )
        == 1
    )

    message = (
        "Cannot prepare complete contributor list"
        if failed_lookup == "limit"
        else "HTTP 502"
    )
    assert f"::error::{message}" in capsys.readouterr().err
    if failed_lookup == "limit":
        assert not any(
            command[2].endswith("/commits/sha2")
            for command, _ in calls
            if command[:2] == ["gh", "api"]
        )
    assert changelog.read_bytes() == original_changelog
    assert fragment.read_bytes() == original_fragment
    assert not output.exists()
    assert not pull_requests
    assert not any(
        command[:2] in (["git", "add"], ["git", "commit"], ["git", "push"])
        for command, _ in calls
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
        if command[:2] == ["gh", "api"] and command[2].endswith("/issues"):
            login = next(
                arg.removeprefix("creator=")
                for arg in command
                if arg.startswith("creator=")
            )
            return json.dumps(
                [
                    [
                        {
                            "html_url": f"https://github.com/conda/conda/pull/{login}",
                            "pull_request": {"merged_at": "2025-09-14T19:13:48Z"},
                        }
                    ]
                ]
            )
        if command[:2] == ["gh", "api"]:
            sha = command[-1].rsplit("/", 1)[-1]
            login = {"sha1": "alice", "sha2": "Bob"}[sha]
            return json.dumps({"author": {"login": login}})
        return ""

    patch_run(monkeypatch, fake_run)

    assert collect_contributors("conda/conda", {}, base_branch="26.7.x") == (
        "* @alice made their first commit in "
        "https://github.com/conda/conda/pull/alice\n"
        "* @Bob made their first commit in "
        "https://github.com/conda/conda/pull/Bob"
    )
    assert not any(
        any(argument.startswith("author=") for argument in command)
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
        if command[:2] == ["gh", "api"] and command[2].endswith("/commits"):
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


def test_collect_contributors_falls_back_to_previous_series_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> str:
        commands.append(command)
        if command[:3] == ["git", "tag", "--merged"]:
            return "" if "--list" in command else "26.6.1\n"
        if command[:2] == ["git", "log"] and "-z" in command:
            return "sha1\0alice@example.com\0"
        if command[:2] == ["git", "log"]:
            return "2026-05-01T00:00:00+00:00\n"
        if command[:2] == ["gh", "api"] and command[2].endswith("/commits"):
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
    log_command = next(
        command
        for command in commands
        if command[:2] == ["git", "log"] and "-z" in command
    )
    assert log_command[-1] == "26.6.1..HEAD"
    assert any(
        any(argument.startswith("author=") for argument in command)
        for command in commands
    )


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
        if command[:2] == ["gh", "api"] and command[2].endswith("/commits"):
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
        if command[:2] == ["gh", "api"] and command[2].endswith("/commits"):
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


def test_collect_contributors_without_resolved_logins(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_run(command: list[str], **kwargs: object) -> str:
        if command[:3] == ["git", "tag", "--merged"]:
            return "26.6.1\n"
        if command[:2] == ["git", "log"] and "-z" in command:
            return "sha1\0ghost@example.com\0"
        if command == ["gh", "api", "repos/conda/conda/commits/sha1"]:
            return json.dumps({"author": None})
        pytest.fail(f"Unexpected command: {command}")

    patch_run(monkeypatch, fake_run)

    assert collect_contributors("conda/conda", {}, base_branch="26.7.x") == ""
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
        if command[:2] == ["gh", "api"] or command[:3] == ["gh", "pr", "list"]:
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
        if command[:2] == ["gh", "api"] and command[2].endswith("/commits"):
            return "[]" if "author=alice" in command else '[{"sha": "old"}]'
        if command[:2] == ["gh", "api"] and command[2].endswith("/issues"):
            return json.dumps(
                [
                    [
                        {
                            "html_url": "https://github.com/conda/conda/pull/42",
                            "pull_request": {"merged_at": "2025-09-14T19:13:48Z"},
                        }
                    ]
                ]
            )
        if command[:2] == ["gh", "api"]:
            sha = command[-1].rsplit("/", 1)[-1]
            login = {"sha1": "alice", "sha2": "Bob"}[sha]
            return json.dumps({"author": {"login": login}})
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


@pytest.mark.parametrize("existing", [False, True], ids=["create", "update"])
def test_create_or_update_pr_targets_release_branch(
    monkeypatch: pytest.MonkeyPatch,
    existing: bool,
) -> None:
    repository = "conda/conda-build"
    branch = "release-notes-26.7.1"
    base_branch = "26.7.x"
    url = f"https://github.com/{repository}/pull/42"
    calls: list[list[str]] = []
    monkeypatch.setenv("GH_TOKEN", "unrelated-token")

    def fake_run(
        command: list[str],
        *,
        capture: bool = False,
        env: dict[str, str] | None = None,
    ) -> str:
        calls.append(command)
        assert env is not None and env["GH_TOKEN"] == "release-token"
        assert command[command.index("--repo") + 1] == repository
        assert command[command.index("--base") + 1] == base_branch
        if command[:3] == ["gh", "pr", "list"]:
            assert capture
            assert command[command.index("--head") + 1] == branch
            assert command[command.index("--state") + 1] == "open"
            return json.dumps([{"number": 42, "url": url}] if existing else [])
        if command[:3] == ["gh", "pr", "create"]:
            assert capture
            assert command[command.index("--head") + 1] == branch
            return f"{url}\n"
        if command[:4] == ["gh", "pr", "edit", "42"]:
            return ""
        pytest.fail(f"Unexpected command: {command}")

    monkeypatch.setattr(prepare_release_module, "run", fake_run)

    assert (
        prepare_release_module.create_or_update_pr(
            repository=repository,
            branch=branch,
            base_branch=base_branch,
            version="26.7.1",
            token="release-token",
        )
        == url
    )
    assert [command[2] for command in calls] == [
        "list",
        "edit" if existing else "create",
    ]
    mutation = calls[-1]
    assert mutation[mutation.index("--title") + 1] == "Prepare release notes for 26.7.1"
    assert mutation[mutation.index("--body") + 1] == (
        "Prepare release notes for `26.7.1`.\n\n"
        "This PR updates `CHANGELOG.md` from the news fragments and "
        "removes the consumed snippets."
    )
    assert os.environ["GH_TOKEN"] == "unrelated-token"


@pytest.mark.parametrize(
    ("repository", "token", "message"),
    [
        ("", "release-token", "No GitHub repository"),
        ("conda/conda", "", "No GitHub token"),
    ],
)
def test_create_or_update_pr_requires_repository_and_token(
    monkeypatch: pytest.MonkeyPatch,
    repository: str,
    token: str,
    message: str,
) -> None:
    monkeypatch.setattr(
        prepare_release_module,
        "run",
        lambda *args, **kwargs: pytest.fail("No GitHub commands should run."),
    )

    with pytest.raises(ActionError, match=message):
        prepare_release_module.create_or_update_pr(
            repository=repository,
            branch="release-notes-26.7.1",
            base_branch="26.7.x",
            version="26.7.1",
            token=token,
        )


@pytest.mark.parametrize("existing", ["", "existing=keep\n"])
def test_write_output_appends_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    existing: str,
) -> None:
    output = tmp_path / "github-output"
    if existing:
        output.write_text(existing, encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    prepare_release_module.write_output("version", "26.7.1")
    prepare_release_module.write_output(
        "pull-request-url", "https://github.com/conda/conda/pull/42"
    )

    assert output.read_text(encoding="utf-8") == (
        existing
        + "version=26.7.1\n"
        + "pull-request-url=https://github.com/conda/conda/pull/42\n"
    )


def test_write_output_without_github_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    prepare_release_module.write_output("version", "26.7.1")

    assert not list(tmp_path.iterdir())
    assert not capsys.readouterr().out
