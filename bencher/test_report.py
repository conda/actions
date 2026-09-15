from __future__ import annotations

import copy
import io
import json
import subprocess
import zipfile
from types import SimpleNamespace

import pytest
import report

HEAD = "a" * 40
BASE = "b" * 40


@pytest.fixture
def producer(tmp_path, monkeypatch):
    run = {
        "id": 123,
        "run_attempt": 2,
        "head_sha": HEAD,
        "head_branch": "feature",
        "head_repository": {"full_name": "fork/project"},
        "html_url": "https://github.com/conda/project/actions/runs/123",
        "event": "pull_request",
    }
    head = {
        "commit_info": {"id": HEAD},
        "machine_info": {
            "system": "Linux",
            "machine": "x86_64",
            "python_version": "3.12.13",
            "cpu": {"brand_raw": "AMD EPYC 9V45", "hz_actual_friendly": "2 GHz"},
        },
        "benchmarks": [
            {"fullname": "test_build"},
            {"fullname": "test_convert_bencher_ignore"},
        ],
    }
    base = copy.deepcopy(head)
    base["commit_info"]["id"] = BASE
    pr = {
        "number": 42,
        "head": {"sha": HEAD, "repo": {"full_name": "fork/project"}},
        # Deliberately advanced: the event-time base must still be used.
        "base": {"sha": "c" * 40, "ref": "main"},
    }
    event_pr = copy.deepcopy(pr)
    event_pr["base"]["sha"] = BASE
    data = {
        "event.json": {"number": 42, "pull_request": event_pr},
        "benchmark_results.json": head,
        "baseline_results.json": base,
        "runner_metadata.json": {"runner": "ubuntu-24.04", "harness_sha": HEAD},
    }
    for name, contents in data.items():
        (tmp_path / name).write_text(json.dumps(contents))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary"))
    monkeypatch.setenv("GH_TOKEN", "github-test-token")
    monkeypatch.setenv("INPUT_API_KEY", "bencher-test-key")
    monkeypatch.delenv("INPUT_API_TOKEN", raising=False)
    checks, commands = [], []

    def github(endpoint, payload=None):
        if payload:
            checks.append(payload)
            return {}
        return pr

    def execute(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(report, "github", github)
    monkeypatch.setattr(report.subprocess, "run", execute)
    return SimpleNamespace(
        directory=tmp_path,
        run=run,
        head=head,
        base=base,
        pr=pr,
        data=data,
        checks=checks,
        commands=commands,
    )


def run_report(producer, policy="percentage"):
    return report.report(
        producer.directory, producer.run, "conda/project", "project", policy, "builds"
    )


def test_paired_upload_uses_event_base_without_rewriting_results(producer):
    assert run_report(producer) == 0
    baseline, head = producer.commands
    assert baseline[baseline.index("--hash") + 1] == BASE
    branch = baseline[baseline.index("--branch") + 1]
    assert branch == "pr-42-base-123-2"
    assert head[head.index("--start-point") + 1] == branch
    assert head[head.index("--start-point-hash") + 1] == BASE
    assert head[head.index("--threshold-test") + 1] == "percentage"
    assert head[head.index("--threshold-upper-boundary") + 1] == "0.25"
    assert "--err" in head
    assert "--github-actions" not in baseline
    assert "--start-point-clone-thresholds" not in head
    assert (
        report.read_json(producer.directory, "benchmark_results.json") == producer.head
    )
    assert (
        "1 benchmarks explicitly suppress alerts"
        in (producer.directory / "summary").read_text()
    )


@pytest.mark.parametrize("field", ["sha", "repo"])
def test_stale_or_unrelated_head_never_uploads(producer, field):
    producer.pr["head"][field] = (
        "c" * 40 if field == "sha" else {"full_name": "other/project"}
    )
    assert run_report(producer) == 0
    assert not producer.commands
    assert not producer.checks


@pytest.mark.parametrize("field", ["number", "head", "base_sha", "base_ref"])
def test_invalid_event_is_rejected_before_upload(producer, field):
    event = producer.data["event.json"]
    if field == "number":
        event["number"] = True
    elif field == "head":
        event["pull_request"]["head"]["sha"] = "c" * 40
    else:
        event["pull_request"]["base"]["sha" if field == "base_sha" else "ref"] = (
            "invalid"
        )
    (producer.directory / "event.json").write_text(json.dumps(event))
    with pytest.raises(ValueError):
        run_report(producer)
    assert not producer.commands


@pytest.mark.parametrize("missing", ["baseline_results.json", "runner_metadata.json"])
def test_missing_comparison_data_is_neutral(producer, missing):
    (producer.directory / missing).unlink()
    assert run_report(producer) == 0
    assert not producer.commands
    assert producer.checks[0]["conclusion"] == "neutral"
    assert "No performance comparison" in producer.checks[0]["output"]["summary"]


@pytest.mark.parametrize("change", ["names", "python_version", "cpu"])
def test_incomparable_pair_is_neutral(producer, change):
    if change == "names":
        producer.base["benchmarks"].pop()
    elif change == "cpu":
        producer.base["machine_info"]["cpu"]["brand_raw"] = "Other CPU"
    else:
        producer.base["machine_info"][change] = "3.12.12"
    (producer.directory / "baseline_results.json").write_text(json.dumps(producer.base))
    assert run_report(producer) == 0
    assert not producer.commands
    assert producer.checks[0]["conclusion"] == "neutral"


@pytest.mark.parametrize(
    "filename,field",
    [
        ("benchmark_results.json", "commit_info"),
        ("baseline_results.json", "commit_info"),
        ("runner_metadata.json", "harness_sha"),
        ("runner_metadata.json", "runner"),
    ],
)
def test_wrong_revisions_or_runner_are_rejected(producer, filename, field):
    data = producer.data[filename]
    data[field] = {"id": "d" * 40} if field == "commit_info" else "wrong"
    (producer.directory / filename).write_text(json.dumps(data))
    with pytest.raises(ValueError):
        run_report(producer)
    assert not producer.commands


def test_testbed_uses_producer_and_ignores_variable_frequency(producer):
    metadata = producer.data["runner_metadata.json"]
    bed = report.testbed(producer.head, metadata, HEAD)
    assert bed == "ubuntu-24.04-x86_64-py3.12-AMD-EPYC-9V45"
    producer.base["machine_info"]["cpu"]["hz_actual_friendly"] = "3 GHz"
    assert report.comparison_reason(producer.head, producer.base, BASE) is None
    producer.head["machine_info"]["cpu"]["brand_raw"] = "Very long CPU " * 20
    first = report.testbed(producer.head, metadata, HEAD)
    assert len(first) == 64
    producer.head["machine_info"]["cpu"]["brand_raw"] += "2"
    assert report.testbed(producer.head, metadata, HEAD) != first


def test_informational_upload_does_not_gate_or_inherit_thresholds(producer):
    assert run_report(producer, "informational") == 0
    for command in producer.commands:
        assert "--threshold-test" not in command
        assert "--github-actions" not in command
        assert "--err" not in command
    assert "--thresholds-reset" in producer.commands[1]
    assert "--start-point-reset" in producer.commands[1]
    assert producer.checks[0]["conclusion"] == "neutral"
    assert "No regression gate" in producer.checks[0]["output"]["summary"]


def test_historical_upload_keeps_history_and_t_test(producer):
    producer.run.update(event="push", head_branch="main")
    assert run_report(producer, "informational") == 0
    [command] = producer.commands
    assert "--start-point-reset" not in command
    assert command[command.index("--branch") + 1] == "main"
    assert command[command.index("--threshold-test") + 1] == "t_test"
    assert command[command.index("--threshold-min-sample-size") + 1] == "10"
    assert "--err" in command
    assert not producer.checks


def test_failed_baseline_does_not_upload_head_or_claim_success(producer, monkeypatch):
    calls = []

    def fail(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(report.subprocess, "run", fail)
    assert run_report(producer, "informational") == 1
    assert len(calls) == 1
    assert producer.checks[0]["output"]["title"] == "Measurement upload failed"
    assert producer.checks[0]["conclusion"] == "neutral"


def test_legacy_token(producer, monkeypatch):
    monkeypatch.delenv("INPUT_API_KEY")
    monkeypatch.setenv("INPUT_API_TOKEN", "legacy-test-token")
    assert run_report(producer) == 0
    assert "--token" in producer.commands[0]
    assert "--key" not in producer.commands[0]


def test_bundle_never_extracts_untrusted_paths(tmp_path):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as bundle:
        bundle.writestr("../escaped", "unsafe")
        bundle.writestr("report.py", "unsafe")
        bundle.writestr("event.json", "{}")
    report.read_bundle(stream.getvalue(), tmp_path)
    assert sorted(path.name for path in tmp_path.iterdir()) == ["event.json"]


def test_duplicate_bundle_file_is_rejected(tmp_path):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as bundle:
        bundle.writestr("event.json", "{}")
        with pytest.warns(UserWarning):
            bundle.writestr("event.json", "{}")
    with pytest.raises(ValueError, match="Duplicate"):
        report.read_bundle(stream.getvalue(), tmp_path)


def test_download_finds_artifact_after_first_page(tmp_path, monkeypatch):
    pages = iter(
        [
            {"artifacts": [{"name": "other"}] * 100},
            {
                "artifacts": [
                    {"name": "benchmark-results-v3", "expired": False, "id": 999}
                ]
            },
        ]
    )
    monkeypatch.setattr(report, "github", lambda endpoint: next(pages))
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as bundle:
        bundle.writestr("event.json", "{}")

    def download(command):
        assert command[-1] == "repos/conda/project/actions/artifacts/999/zip"
        return stream.getvalue()

    monkeypatch.setattr(subprocess, "check_output", download)
    assert report.download(
        "conda/project", {"id": 123}, "benchmark-results-v3", tmp_path
    )
    assert (tmp_path / "event.json").read_text() == "{}"
