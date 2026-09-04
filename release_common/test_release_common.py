from __future__ import annotations

import json
import sys

import pytest

import release_common as release_common_module
from release_common import (
    ActionError,
    get_github_login,
    get_latest_tag,
    normalize_release_tag,
    parse_nul_records,
    run,
    run_json,
    select_latest_release_tag,
)


def test_normalize_release_tag() -> None:
    assert normalize_release_tag("26.7.0") == (26, 7, 0)
    assert normalize_release_tag("v3.20.4") == (3, 20, 4)
    assert normalize_release_tag("1.10.0") == (1, 10, 0)
    assert normalize_release_tag("26.8.0rc1") is None
    assert normalize_release_tag("4.14.0b2") is None
    assert normalize_release_tag("pre-commit-hooks-v1") is None
    assert normalize_release_tag("2026") is None
    assert normalize_release_tag("26.8") is None
    assert normalize_release_tag("26.8.0.1") is None


def test_select_latest_release_tag() -> None:
    assert (
        select_latest_release_tag(["v3.20.4", "26.7.0", "25.1.0", "26.7.0rc1"])
        == "26.7.0"
    )
    assert select_latest_release_tag(["1.9.0", "1.10.0"]) == "1.10.0"
    assert select_latest_release_tag(["v1.2.3", "1.2.3"]) in {"v1.2.3", "1.2.3"}
    assert select_latest_release_tag([]) == ""
    assert select_latest_release_tag(["26.8.0rc1", "hooks-v1"]) == ""


def test_get_latest_tag_merged_into_head(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> str:
        commands.append(command)
        return "v26.6.0\n26.6.1\n26.7.0a1\nrandom\n"

    monkeypatch.setattr(release_common_module, "run", fake_run)

    assert get_latest_tag() == "26.6.1"
    assert commands == [["git", "tag", "--merged", "HEAD"]]


def test_get_latest_tag_with_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> str:
        commands.append(command)
        return "26.7.0\nv26.7.1\n26.7.2rc1\n"

    monkeypatch.setattr(release_common_module, "run", fake_run)

    assert get_latest_tag(prefix="26.7.") == "v26.7.1"
    assert commands == [
        [
            "git",
            "tag",
            "--merged",
            "HEAD",
            "--list",
            "26.7.*",
            "--list",
            "v26.7.*",
        ]
    ]


def test_get_latest_tag_without_release_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        release_common_module,
        "run",
        lambda *args, **kwargs: "26.7.0a1\n",
    )

    assert get_latest_tag() == ""


def test_parse_nul_records() -> None:
    assert parse_nul_records("", 2) == []
    assert parse_nul_records("sha1\0a@example.com\0sha2\0b@example.com\0", 2) == [
        ("sha1", "a@example.com"),
        ("sha2", "b@example.com"),
    ]
    assert parse_nul_records("sha1\0a@example.com", 2) == [("sha1", "a@example.com")]


def test_parse_nul_records_malformed() -> None:
    with pytest.raises(ActionError, match="NUL-delimited"):
        parse_nul_records("sha1\0a@example.com\0sha2", 2)


def test_get_github_login(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(
        command: list[str],
        *,
        capture: bool = False,
        env: dict[str, str] | None = None,
    ) -> str:
        captured["command"] = command
        captured["env"] = env
        return json.dumps({"author": {"login": "alice"}})

    monkeypatch.setattr(release_common_module, "run", fake_run)

    assert get_github_login("conda/example", "abc123", env={"GH_TOKEN": "t"}) == "alice"
    assert captured["command"] == ["gh", "api", "repos/conda/example/commits/abc123"]
    assert captured["env"] == {"GH_TOKEN": "t"}


def test_get_github_login_warns_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_run(*args: object, **kwargs: object) -> str:
        raise ActionError("lookup failed")

    monkeypatch.setattr(release_common_module, "run", fake_run)

    assert get_github_login("conda/example", "abc123", env={}) is None
    assert "::warning::Failed to resolve GitHub login" in capsys.readouterr().err


@pytest.mark.parametrize(
    "payload",
    [{}, {"author": None}, {"author": {"login": None}}],
)
def test_get_github_login_warns_without_login(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    payload: dict[str, object],
) -> None:
    monkeypatch.setattr(
        release_common_module,
        "run",
        lambda *args, **kwargs: json.dumps(payload),
    )

    assert get_github_login("conda/example", "abc123", env={}) is None
    assert (
        "::warning::No GitHub login associated with commit" in capsys.readouterr().err
    )


def test_run_captures_and_trims_trailing_newlines() -> None:
    assert run([sys.executable, "-c", "print('hi')"], capture=True) == "hi"


def test_run_raises_action_error_on_failure() -> None:
    with pytest.raises(ActionError, match="Command failed"):
        run([sys.executable, "-c", "import sys; sys.exit(1)"])


def test_run_json_rejects_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        release_common_module,
        "run",
        lambda *args, **kwargs: "not json",
    )

    with pytest.raises(ActionError, match="Failed to parse JSON"):
        run_json(["gh", "api", "repos/conda/example"])
