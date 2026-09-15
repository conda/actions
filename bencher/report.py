"""Upload data from a workflow_run artifact without executing producer code."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

FILES = {
    "benchmark_results.json",
    "baseline_results.json",
    "runner_metadata.json",
    "event.json",
}


def github(endpoint, payload=None):
    command = ["gh", "api", endpoint]
    if payload is not None:
        command += ["--method", "POST", "--input", "-"]
    return json.loads(
        subprocess.check_output(
            command, input=json.dumps(payload).encode() if payload else None
        )
    )


def download(repository, run, artifact_name, directory):
    # Paginate because a test matrix may produce many unrelated artifacts.
    for page in range(1, 100):
        artifacts = github(
            f"repos/{repository}/actions/runs/{run['id']}/artifacts"
            f"?per_page=100&page={page}"
        )["artifacts"]
        for artifact in artifacts:
            if artifact["name"] != artifact_name or artifact["expired"]:
                continue
            archive = subprocess.check_output(
                [
                    "gh",
                    "api",
                    f"repos/{repository}/actions/artifacts/{artifact['id']}/zip",
                ]
            )
            read_bundle(archive, directory)
            return True
        if len(artifacts) < 100:
            return False
    raise ValueError("Too many artifact pages")


def read_bundle(archive, directory):
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        # Read only fixed filenames. Never extract paths or execute artifact code.
        selected = [entry for entry in bundle.infolist() if entry.filename in FILES]
        if len({entry.filename for entry in selected}) != len(selected):
            raise ValueError("Duplicate benchmark artifact files")
        if sum(entry.file_size for entry in selected) > 32 * 1024 * 1024:
            raise ValueError("Benchmark artifact is too large")
        for entry in selected:
            (directory / entry.filename).write_bytes(bundle.read(entry))


def read_json(directory, filename):
    return json.loads((directory / filename).read_text(encoding="utf-8"))


def pull_request(directory, run, repository):
    event = read_json(directory, "event.json")
    number = event["number"]
    if type(number) is not int or not 0 < number < 2**53:
        raise ValueError("Invalid pull request number")
    pr = github(f"repos/{repository}/pulls/{number}")
    if (
        pr["head"]["sha"] != run["head_sha"]
        or pr["head"]["repo"]["full_name"] != run["head_repository"]["full_name"]
    ):
        return None
    recorded = event["pull_request"]
    if (
        recorded["head"]["sha"] != pr["head"]["sha"]
        or not re.fullmatch(r"[a-f0-9]{40}", recorded["base"]["sha"])
        or recorded["base"]["ref"] != pr["base"]["ref"]
    ):
        raise ValueError("Benchmark event does not match the pull request")
    # The base may have advanced since this producer started.
    return number, recorded["base"]["sha"]


def testbed(head, metadata, sha):
    if head["commit_info"]["id"] != sha or metadata["harness_sha"] != sha:
        raise ValueError("Benchmark revision or harness does not match the workflow")
    if metadata["runner"] != "ubuntu-24.04":
        raise ValueError("Unexpected benchmark runner image")
    machine = head["machine_info"]
    python = ".".join(machine["python_version"].split(".")[:2])
    name = re.sub(
        r"[^\w.-]+",
        "-",
        f"{metadata['runner']}-{machine['machine']}-py{python}-"
        f"{machine['cpu']['brand_raw']}",
        flags=re.ASCII,
    )
    if len(name) > 64:
        name = f"{name[:51]}-{hashlib.sha256(name.encode()).hexdigest()[:12]}"
    return name


def comparison_reason(head, base, base_sha):
    if base["commit_info"]["id"] != base_sha:
        raise ValueError("Base revision does not match the producer event")
    machine, baseline_machine = head["machine_info"], base["machine_info"]
    if any(
        machine[key] != baseline_machine[key]
        for key in ("system", "machine", "python_version")
    ) or (machine["cpu"]["brand_raw"] != baseline_machine["cpu"]["brand_raw"]):
        return "Base and head runner metadata differ."
    names = {item["fullname"] for item in head["benchmarks"]}
    if not names or names != {item["fullname"] for item in base["benchmarks"]}:
        return "Base and head benchmark names differ or are empty."
    return None


def summarize(message):
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
        print(message, file=summary)


def neutral(repository, run, title, message, informational=False):
    github(
        f"repos/{repository}/check-runs",
        {
            "name": "Benchmark measurements (informational)"
            if informational
            else "Benchmark comparison",
            "head_sha": run["head_sha"],
            "status": "completed",
            "conclusion": "neutral",
            "details_url": run["html_url"],
            "output": {"title": title, "summary": message},
        },
    )


def threshold(test, minimum, maximum, upper):
    return [
        "--threshold-measure",
        "latency",
        "--threshold-test",
        test,
        "--threshold-min-sample-size",
        str(minimum),
        "--threshold-max-sample-size",
        str(maximum),
        "--threshold-upper-boundary",
        str(upper),
    ]


def upload(directory, run, project, policy, ci_id, bed, pr):
    key, token = os.environ.get("INPUT_API_KEY"), os.environ.get("INPUT_API_TOKEN")
    if bool(key) == bool(token):
        raise ValueError("Configure exactly one Bencher API key or API token")
    common = [
        "bencher",
        "run",
        "--project",
        project,
        "--key" if key else "--token",
        key or token,
        "--testbed",
        bed,
        "--adapter",
        "python_pytest",
    ]
    options = ["--thresholds-reset"]
    if pr:
        number, base_sha = pr
        branch = f"pr-{number}"
        baseline = f"{branch}-base-{run['id']}-{run['run_attempt']}"
        result = subprocess.run(
            common
            + [
                "--branch",
                baseline,
                "--hash",
                base_sha,
                "--file",
                str(directory / "baseline_results.json"),
            ],
            check=False,
        )
        if result.returncode:
            return result.returncode
        options += [
            "--start-point",
            baseline,
            "--start-point-hash",
            base_sha,
            "--start-point-reset",
        ]
        if policy == "percentage":
            options += threshold("percentage", 1, 1, 0.25)
            options += ["--ci-only-on-alert", "--ci-number", str(number)]
    else:
        branch = run["head_branch"]
        options += threshold("t_test", 10, 64, 0.99)
    if not pr or policy == "percentage":
        options += ["--err", "--github-actions", os.environ["GH_TOKEN"]]
        if ci_id:
            options += ["--ci-id", ci_id]
    return subprocess.run(
        common
        + [
            "--branch",
            branch,
            "--hash",
            run["head_sha"],
            *options,
            "--file",
            str(directory / "benchmark_results.json"),
        ],
        check=False,
    ).returncode


def report(directory, run, repository, project, policy, ci_id):
    is_pr = run["event"] == "pull_request"
    pr = pull_request(directory, run, repository) if is_pr else None
    if is_pr and not pr:
        summarize("Skipped measurements for an outdated pull request head.")
        return 0
    head = read_json(directory, "benchmark_results.json")
    reason = None
    if not (directory / "runner_metadata.json").exists():
        reason = "The producer did not record its runner and benchmark harness."
    else:
        bed = testbed(
            head, read_json(directory, "runner_metadata.json"), run["head_sha"]
        )
    if not reason and pr:
        reason = (
            comparison_reason(
                head, read_json(directory, "baseline_results.json"), pr[1]
            )
            if (directory / "baseline_results.json").exists()
            else "The base run did not produce benchmark results."
        )
    if reason:
        message = (
            f"{reason} No performance comparison was made. See the producer artifact."
        )
        summarize(message)
        if pr:
            neutral(repository, run, "PR comparison is unavailable", message)
        return 0
    result = upload(directory, run, project, policy, ci_id, bed, pr)
    # Bencher overwrites the step summary, so explain the policy after uploading.
    if pr:
        message = (
            f"Measured {len(head['benchmarks'])} benchmarks at the base and PR "
            "revisions "
            "on the same runner, using the PR head's harness and dependencies. "
        )
        if policy == "informational":
            message += (
                "These measurements are informational. No regression gate was applied."
            )
        else:
            message += "The alert tolerance is a 25% latency increase."
        message += " Runner load and cache state can still vary."
    else:
        message = (
            "Historical alerts use a 99% t-test with 10 to 64 previous measurements."
        )
    ignored = sum(
        item["fullname"].endswith("_bencher_ignore") for item in head["benchmarks"]
    )
    if ignored:
        message += (
            f" {ignored} benchmarks explicitly suppress alerts but retain measurements."
        )
    if result:
        message += " Bencher did not complete successfully. See the reporting log."
    message += (
        f" [Bencher reports](https://bencher.dev/console/projects/{project}/reports)."
    )
    summarize(message)
    if pr and policy == "informational":
        title = (
            "Measurement upload failed" if result else "Informational PR measurements"
        )
        neutral(repository, run, title, message, informational=True)
    return result


def main():
    if os.environ["GITHUB_EVENT_NAME"] != "workflow_run":
        raise ValueError("This action requires a workflow_run event")
    event = json.loads(
        Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8")
    )
    run = event["workflow_run"]
    if run["event"] not in ("pull_request", "push"):
        raise ValueError("Unsupported benchmark producer event")
    project, policy = os.environ["INPUT_PROJECT"], os.environ["INPUT_PR_POLICY"]
    if policy not in ("percentage", "informational"):
        raise ValueError("Unknown PR policy")
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", project):
        raise ValueError("Invalid Bencher project slug")
    repository = os.environ["GITHUB_REPOSITORY"]
    with tempfile.TemporaryDirectory(
        prefix="bencher-", dir=os.environ["RUNNER_TEMP"]
    ) as temp:
        directory = Path(temp)
        if not download(repository, run, os.environ["INPUT_ARTIFACT_NAME"], directory):
            summarize(
                "The producer has no current benchmark artifact. Nothing was uploaded."
            )
            return 0
        return report(
            directory, run, repository, project, policy, os.environ["INPUT_CI_ID"]
        )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        ValueError,
        KeyError,
        OSError,
        subprocess.SubprocessError,
        zipfile.BadZipFile,
    ):
        # Subprocess exception strings can contain credential-bearing arguments.
        print(
            "::error::Benchmark reporting failed. "
            "Check the producer data and reporting log."
        )
        raise SystemExit(1) from None
