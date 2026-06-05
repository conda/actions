from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from prepare_release import (
    ActionError,
    collect_fragments,
    ensure_allowed_paths,
    infer_next_version,
    render_changelog_entry,
    update_changelog,
    verify_context,
)


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


def test_update_changelog_refuses_existing_version(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("## 26.7.0 (2026-06-05)\n", encoding="utf-8")

    with pytest.raises(ActionError, match="already contains"):
        update_changelog(changelog, "## 26.7.0 (2026-06-05)\n", "26.7.0")


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
