from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

import create_release as create_release_module
from conda_actions.commands import ActionError
from create_release import (
    create_release,
    release_notes,
    resolve_artifacts,
    verify_context,
)

CHANGELOG = (
    "[//]: # (current developments)\n\n"
    "## 1.2.3 (2026-11-02)\n\n"
    "### Enhancements\n\n"
    "* Outstanding feature. (#12)\n\n"
    "### Contributors\n\n"
    "* @alice\n\n\n"
    "## 1.2.2 (2026-10-01)\n\n"
    "### Bug fixes\n\n"
    "* Bugs, bugs, bugs. (#11)\n"
)
NOTES = (
    "### Enhancements\n\n* Outstanding feature. (#12)\n\n### Contributors\n\n* @alice"
)


def write_merged_pr_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    event: str = "pull_request",
    merged: bool = True,
    repository: str = "conda/example",
    head_repository: str = "conda/example",
    branch: str = "release-notes-1.2.3",
    base: str = "main",
    default_branch: str = "main",
    commit: str = "a" * 40,
) -> None:
    payload = {
        "repository": {"default_branch": default_branch},
        "pull_request": {
            "merged": merged,
            "merge_commit_sha": commit,
            "head": {"ref": branch, "repo": {"full_name": head_repository}},
            "base": {"ref": base},
        },
    }
    path = tmp_path / "event.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(path))
    monkeypatch.setenv("GITHUB_EVENT_NAME", event)
    monkeypatch.setenv("GITHUB_REPOSITORY", repository)


def release_args(tmp_path: Path, **overrides: object) -> Namespace:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG, encoding="utf-8")
    args = Namespace(
        changelog_path=str(changelog),
        branch_prefix="release-notes-",
        artifacts="",
        repository="conda/example",
        token="test-token",
    )
    for name, value in overrides.items():
        setattr(args, name, value)
    return args


def mock_gh(
    monkeypatch: pytest.MonkeyPatch,
    *,
    state: str | None = None,
    tag: bool = False,
) -> tuple[list[list[str]], list[str]]:
    calls: list[list[str]] = []
    notes: list[str] = []

    def fake_run_json(
        command: list[str],
        *,
        env: dict[str, str] | None = None,
    ) -> object:
        if "/releases/tags/" in command[2]:
            if state is None:
                raise ActionError("Command failed: gh api\n\ngh: Not Found (HTTP 404)")
            return {"isDraft": state == "draft"}
        if "/git/ref/tags/" in command[2]:
            if not tag:
                raise ActionError("Command failed: gh api\n\ngh: Not Found (HTTP 404)")
            return {"ref": f"refs/tags/{command[2].rsplit('/', 1)[-1]}"}
        raise AssertionError(f"Unexpected command: {command}")

    def fake_run(
        command: list[str],
        *,
        capture: bool = False,
        env: dict[str, str] | None = None,
    ) -> str:
        calls.append(command)
        notes.extend(
            arg.removeprefix("--notes=")
            for arg in command
            if arg.startswith("--notes=")
        )
        return ""

    monkeypatch.setattr(create_release_module, "run_json", fake_run_json)
    monkeypatch.setattr(create_release_module, "run", fake_run)
    return calls, notes


def test_verify_context_accepts_merged_release_pull_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_merged_pr_event(tmp_path, monkeypatch)

    assert verify_context("release-notes-") == {
        "version": "1.2.3",
        "commit": "a" * 40,
    }


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("event", "push", "must run from the pull_request event"),
        ("merged", False, "was not merged"),
        ("head_repository", "someone/example", "must come from this repository"),
        ("base", "1.2.x", "is not the repository default branch"),
        ("default_branch", "", "did not include the repository default branch"),
        ("branch", "main", "does not start with 'release-notes-'"),
        ("branch", "release-notes-1.2", "Invalid release version"),
        ("branch", "release-notes-v1.2.3", "Invalid release version"),
        ("commit", "", "did not include a merge commit SHA"),
    ],
)
def test_verify_context_rejects_untrusted_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
    message: str,
) -> None:
    write_merged_pr_event(tmp_path, monkeypatch, **{field: value})

    with pytest.raises(ActionError, match=message):
        verify_context("release-notes-")


def test_main_reports_action_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_merged_pr_event(tmp_path, monkeypatch)
    args = release_args(tmp_path)
    mock_gh(monkeypatch)
    changelog = Path(args.changelog_path)
    changelog.write_text("## 1.2.2 (2026-10-01)\n\n* Old.\n", encoding="utf-8")

    assert (
        create_release_module.main(
            [
                "--changelog-path",
                str(changelog),
                "--repository",
                "conda/example",
                "--token",
                "test-token",
            ]
        )
        == 1
    )

    assert "::error::No release notes for 1.2.3" in capsys.readouterr().err


@pytest.mark.parametrize("field", ["repository", "token"])
def test_create_release_requires_github_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    write_merged_pr_event(tmp_path, monkeypatch)
    args = release_args(tmp_path, **{field: ""})
    calls, _ = mock_gh(monkeypatch)

    with pytest.raises(ActionError, match=f"No GitHub {field} was provided"):
        create_release(args)

    assert not calls


def test_create_release_creates_draft_with_artifacts_and_publishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    write_merged_pr_event(tmp_path, monkeypatch)
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    dist = tmp_path / "dist"
    dist.mkdir()
    wheel = dist / "example-1.2.3-py3-none-any.whl"
    sdist = dist / "example-1.2.3.tar.gz"
    wheel.write_text("wheel", encoding="utf-8")
    sdist.write_text("sdist", encoding="utf-8")
    output = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    calls, notes = mock_gh(monkeypatch)

    create_release(release_args(tmp_path, artifacts="dist/*"))

    assert calls[0][:5] == ["gh", "release", "create", "1.2.3", "--draft"]
    assert calls[0][-2:] == [
        "dist/example-1.2.3-py3-none-any.whl",
        "dist/example-1.2.3.tar.gz",
    ]
    assert calls[0][5:11] == [
        "--repo",
        "conda/example",
        "--target",
        "a" * 40,
        "--title",
        "1.2.3",
    ]
    assert notes == [NOTES]
    assert calls[1] == [
        "gh",
        "release",
        "edit",
        "1.2.3",
        "--repo",
        "conda/example",
        "--draft=false",
    ]
    assert output.read_text(encoding="utf-8") == (
        "version=1.2.3\n"
        f"commit={'a' * 40}\n"
        "release-url=https://github.com/conda/example/releases/tag/1.2.3\n"
    )


def test_create_release_publishes_without_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    write_merged_pr_event(tmp_path, monkeypatch)
    calls, notes = mock_gh(monkeypatch)

    create_release(release_args(tmp_path))

    assert calls[0][:5] == ["gh", "release", "create", "1.2.3", "--draft"]
    assert calls[0][-1].startswith("--notes=")
    assert len(calls[0]) == 12
    assert notes
    assert calls[-1] == [
        "gh",
        "release",
        "edit",
        "1.2.3",
        "--repo",
        "conda/example",
        "--draft=false",
    ]


def test_create_release_updates_existing_draft(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    write_merged_pr_event(tmp_path, monkeypatch)
    artifact = tmp_path / "example-1.2.3.tar.gz"
    artifact.write_text("sdist", encoding="utf-8")
    calls, notes = mock_gh(monkeypatch, state="draft")

    create_release(release_args(tmp_path, artifacts="*.tar.gz"))

    assert calls[0][:3] == ["gh", "release", "edit"]
    assert "--draft" not in calls[0]
    assert notes == [NOTES]
    assert calls[1] == [
        "gh",
        "release",
        "upload",
        "1.2.3",
        "--repo",
        "conda/example",
        "--clobber",
        "example-1.2.3.tar.gz",
    ]
    assert calls[2] == [
        "gh",
        "release",
        "edit",
        "1.2.3",
        "--repo",
        "conda/example",
        "--draft=false",
    ]


def test_create_release_noops_when_already_published(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    write_merged_pr_event(tmp_path, monkeypatch)
    output = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    calls, _ = mock_gh(monkeypatch, state="published")

    create_release(release_args(tmp_path))

    assert not calls
    assert (
        "Release 1.2.3 is already published. Nothing to do." in capsys.readouterr().out
    )
    assert "version=1.2.3\n" in output.read_text(encoding="utf-8")


def test_create_release_rejects_existing_tag_without_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    write_merged_pr_event(tmp_path, monkeypatch)
    calls, _ = mock_gh(monkeypatch, tag=True)

    with pytest.raises(ActionError, match="Tag 1.2.3 already exists"):
        create_release(release_args(tmp_path))

    assert not calls


def test_create_release_rejects_missing_changelog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    write_merged_pr_event(tmp_path, monkeypatch)
    args = release_args(tmp_path, changelog_path=str(tmp_path / "missing.md"))
    mock_gh(monkeypatch)

    with pytest.raises(ActionError, match="Changelog file does not exist"):
        create_release(args)


def test_create_release_rejects_empty_release_notes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    write_merged_pr_event(tmp_path, monkeypatch)
    args = release_args(tmp_path)
    Path(args.changelog_path).write_text(
        "## 1.2.3 (2026-11-02)\n\n## 1.2.2 (2026-10-01)\n", encoding="utf-8"
    )
    mock_gh(monkeypatch)

    with pytest.raises(ActionError, match="Release notes for 1.2.3 are empty"):
        create_release(args)


def test_resolve_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    dist = tmp_path / "dist"
    nested = dist / "nested"
    nested.mkdir(parents=True)
    first = Path("dist/a.whl")
    second = Path("dist/nested/b.tar.gz")
    (tmp_path / first).write_text("a", encoding="utf-8")
    (tmp_path / second).write_text("b", encoding="utf-8")
    (dist / "empty").mkdir()

    assert resolve_artifacts("") == []
    assert resolve_artifacts("\n  \n") == []
    assert resolve_artifacts("dist/*") == [first]
    assert resolve_artifacts("dist/*\ndist/**/*.tar.gz") == [first, second]
    assert resolve_artifacts("missing/*") == []


def test_main_publishes_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    write_merged_pr_event(tmp_path, monkeypatch)
    args = release_args(tmp_path)
    calls, _ = mock_gh(monkeypatch)

    assert (
        create_release_module.main(
            [
                "--changelog-path",
                args.changelog_path,
                "--repository",
                "conda/example",
                "--token",
                "test-token",
            ]
        )
        == 0
    )

    assert calls[-1][-1] == "--draft=false"


@pytest.mark.parametrize("event_path", [None, "missing.json"])
def test_verify_context_rejects_unavailable_event_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    event_path: str | None,
) -> None:
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_REPOSITORY", "conda/example")
    if event_path is None:
        monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    else:
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(tmp_path / event_path))

    with pytest.raises(ActionError, match="was not merged"):
        verify_context("release-notes-")


def test_create_release_propagates_api_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    write_merged_pr_event(tmp_path, monkeypatch)
    calls, _ = mock_gh(monkeypatch)

    def fake_run_json(
        command: list[str],
        *,
        env: dict[str, str] | None = None,
    ) -> object:
        raise ActionError("Command failed: gh api\n\ngh: Server Error (HTTP 502)")

    monkeypatch.setattr(create_release_module, "run_json", fake_run_json)

    with pytest.raises(ActionError, match="HTTP 502"):
        create_release(release_args(tmp_path))

    assert not calls


def test_create_release_updates_draft_without_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    write_merged_pr_event(tmp_path, monkeypatch)
    calls, _ = mock_gh(monkeypatch, state="draft")

    create_release(release_args(tmp_path))

    assert [command[2] for command in calls] == ["edit", "edit"]


def test_release_notes_does_not_match_a_longer_version() -> None:
    assert release_notes("## 1.2.30 (2026-09-01)\n\n* Notes.\n", "1.2.3") is None


def test_release_notes_handles_crlf() -> None:
    assert (
        release_notes("## 1.2.3 (2026-09-01)\r\n\r\n* Notes.\r\n", "1.2.3")
        == "* Notes."
    )
