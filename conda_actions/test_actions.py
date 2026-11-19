from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml

if TYPE_CHECKING:
    from typing import Literal

    Action = Literal["bencher", "combine-durations", "template-files"]

REPOSITORY = Path(__file__).resolve().parents[1]

# stand-in for `gh api --paginate ... --jq <filter>`; it applies the step's real
# filter so the artifact selection is exercised without touching the network
FAKE_GH = """#!/usr/bin/env bash
if [[ -n "${FAKE_GH_FAIL:-}" ]]; then
  echo "gh: could not authenticate" >&2
  exit 1
fi
filter=""
previous=""
for argument in "$@"; do
  [[ "$previous" == "--jq" ]] && filter="$argument"
  previous="$argument"
done
jq --raw-output "$filter" "$FAKE_GH_ARTIFACTS"
"""


@pytest.mark.parametrize(
    "script",
    [
        "check-news/check_news.py",
        "create-release/create_release.py",
        "prepare-authors/prepare_authors.py",
        "prepare-release/prepare_release.py",
    ],
)
def test_action_script_imports_from_action_pythonpath(
    tmp_path: Path,
    script: str,
) -> None:
    repository = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(repository / script), "--help"],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(repository)},
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )

    assert "usage:" in result.stdout


def read_steps(action: Action, id: str | None = None) -> list[dict] | dict:
    path = REPOSITORY / action / "action.yml"
    steps = yaml.safe_load(path.read_text())["runs"]["steps"]
    if id is None:
        return steps
    return next(step for step in steps if step.get("id") == id)


def run_check_step(
    action: Action,
    tmp_path: Path,
    **env: str,
) -> tuple[dict[str, str], str, str]:
    """Run the action's `check` step; return outputs, summary, and stdout."""
    step = read_steps(action, "check")
    assert step["shell"].split()[0] == "bash", (
        f"{action} check step does not run under bash: {step['shell']!r}"
    )
    script = tmp_path / "check.sh"
    script.write_text(step["run"])

    outputs = tmp_path / "outputs"
    summary = tmp_path / "summary"
    outputs.touch()
    summary.touch()

    result = subprocess.run(
        ["bash", str(script)],
        cwd=tmp_path,
        env={
            **os.environ,
            "GITHUB_OUTPUT": str(outputs),
            "GITHUB_STEP_SUMMARY": str(summary),
            **env,
        },
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    parsed = dict(
        line.split("=", 1) for line in outputs.read_text().splitlines() if "=" in line
    )
    return parsed, summary.read_text(), result.stdout


@pytest.fixture
def fake_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin = tmp_path / "bin"
    bin.mkdir()
    (bin / "gh").write_text(FAKE_GH)
    (bin / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", str(bin), prepend=os.pathsep)


@pytest.mark.parametrize(
    ("artifacts", "failure", "skip"),
    [
        ([{"name": "benchmark-results-v3", "expired": False}], False, False),
        ([{"name": "benchmark-results-v3", "expired": True}], False, True),
        ([{"name": "test-results", "expired": False}], False, True),
        ([], False, True),
        ([], True, False),
    ],
)
def test_bencher_check_step(
    tmp_path: Path,
    fake_gh: None,
    artifacts: list[dict],
    failure: bool,
    skip: bool,
) -> None:
    listing = tmp_path / "artifacts.json"
    listing.write_text(json.dumps({"artifacts": artifacts}))
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"workflow_run": {"id": 123}}))

    outputs, summary, stdout = run_check_step(
        "bencher",
        tmp_path,
        GITHUB_EVENT_PATH=str(event),
        GITHUB_REPOSITORY="conda/project",
        INPUT_ARTIFACT_NAME="benchmark-results-v3",
        FAKE_GH_ARTIFACTS=str(listing),
        FAKE_GH_FAIL="1" if failure else "",
    )

    assert outputs["skip"] == str(skip).lower()
    if skip:
        assert "no current benchmark artifact" in summary
    else:
        assert summary == ""
    if failure:
        assert "proceeding with the Bencher report" in stdout


def test_bencher_check_step_without_run_id(tmp_path: Path, fake_gh: None) -> None:
    listing = tmp_path / "artifacts.json"
    listing.write_text(json.dumps({"artifacts": []}))
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"repository": {"full_name": "conda/project"}}))

    outputs, summary, stdout = run_check_step(
        "bencher",
        tmp_path,
        GITHUB_EVENT_PATH=str(event),
        GITHUB_REPOSITORY="conda/project",
        INPUT_ARTIFACT_NAME="benchmark-results-v3",
        FAKE_GH_ARTIFACTS=str(listing),
    )

    assert outputs["skip"] == "false"
    assert summary == ""
    assert "proceeding with the Bencher report" in stdout


def test_template_files_check_step_without_config(tmp_path: Path) -> None:
    outputs, summary, stdout = run_check_step(
        "template-files",
        tmp_path,
        INPUT_CONFIG=str(tmp_path / "config.yml"),
    )

    assert outputs["skip"] == "true"
    assert summary == ""
    assert "No template config" in stdout


def test_template_files_check_step_with_config(tmp_path: Path) -> None:
    config = tmp_path / "config.yml"
    config.write_text("conda/governance:\n  - CODE_OF_CONDUCT.md\n")

    outputs, _, _ = run_check_step(
        "template-files",
        tmp_path,
        INPUT_CONFIG=str(config),
    )

    assert outputs["skip"] == "false"


@pytest.mark.parametrize("create_directory", [True, False])
def test_combine_durations_check_step_without_artifacts(
    tmp_path: Path,
    create_directory: bool,
) -> None:
    if create_directory:
        (tmp_path / "artifacts").mkdir()

    outputs, _, _ = run_check_step(
        "combine-durations",
        tmp_path,
        RUNNER_TEMP=str(tmp_path),
    )

    assert outputs["skip"] == "true"


def test_combine_durations_check_step_with_artifacts(tmp_path: Path) -> None:
    artifact = tmp_path / "artifacts" / "123" / "Linux"
    artifact.mkdir(parents=True)
    (artifact / "Linux.json").write_text("{}\n")

    outputs, _, _ = run_check_step(
        "combine-durations",
        tmp_path,
        RUNNER_TEMP=str(tmp_path),
    )

    assert outputs["skip"] == "false"


@pytest.mark.parametrize("action", ["bencher", "combine-durations", "template-files"])
def test_check_step_gates_all_following_steps(action: Action) -> None:
    steps = read_steps(action)
    index = next(i for i, step in enumerate(steps) if step.get("id") == "check")

    ungated = [
        step["name"]
        for step in steps[index + 1 :]
        if step.get("if") != "steps.check.outputs.skip != 'true'"
    ]

    assert not ungated, f"steps not gated on the check step: {ungated}"
