#!/usr/bin/env python
"""
Generate a bunch of allure reports.

Assumes github pages branch is checked out to $PWD/gh-pages.
"""

import json
import os
from pathlib import Path
import subprocess
import re

ALLURE = os.environ.get("ALLURE_PATH", "allure")

# pass in env: section; not automatic from github actions
REPOSITORY = os.environ.get("INPUT_REPOSITORY")
# glob pattern used to download some artifacts
PATTERN = os.environ.get("INPUT_ARTIFACT_PATTERN")
# regex matching the group part of an artifact
GROUP_REGEX = os.environ.get("INPUT_ARTIFACT_GROUP", "allure-(\d.*)")


def list_runs(repository):
    runs = subprocess.run(
        [
            "gh",
            "run",
            "-R",
            repository,
            "ls",
            "--json",
            "status,conclusion,databaseId,workflowName,headBranch",
        ],
        check=True,
        capture_output=True,
    )
    return json.loads(runs.stdout)


def download_run(repository, run_id, pattern=PATTERN):
    """
    Download artifacts for a single run.
    """
    workdir = Path(run_id)
    try:
        workdir.mkdir()
    except OSError:
        print(f"Skip existing {workdir}")
        return workdir
    result = subprocess.run(
        ["gh", "run", "download", "-p", pattern, "-R", repository, run_id],
        cwd=workdir,
    )
    if result.returncode == 0:
        return workdir
    else:
        # will also fail if workdir is not empty
        workdir.rmdir()
        print("stdout", result.stdout)
        print("stderr", result.stderr)


def report_run(
    repository: str,
    run: dict,
    outdir="gh-pages",
    pattern: re.Pattern = re.compile(r"(.*)"),
):
    workdir = Path(str(run["databaseId"]))
    groups = {}
    for path in workdir.iterdir():
        if match := pattern.match(path.name):
            groupname = match.groups()[0]
            groups[groupname] = groups.get(groupname, []) + [path]

    for group in groups:
        report_dir = Path(workdir, f"allure-report-{group}")
        report_dir.mkdir(exist_ok=True)
        # extract related results on top of each other
        for artifact_dir in groups[group]:
            command = [
                "find",
                str(artifact_dir),
                "-name",
                "allure-results.tar.gz",
                "-exec",
                "tar",
                "-C",
                str(report_dir),
                "-xf",
                "{}",
                "--strip-components=1",
                ";",  # no shell escaping
            ]

            subprocess.run(
                command,
            )

        output_dir = Path(outdir, run["headBranch"].replace("/", "-"), group)

        try:
            os.rename(output_dir / "history", report_dir / "history")
        except OSError:
            print(f"Could not move history from {output_dir} to {report_dir}")

        # allure generate --output <report dir> input-dir
        command = [
            ALLURE,
            "generate",
            "--clean",
            "--output",
            str(output_dir),
            report_dir,
        ]
        subprocess.run(command)


if __name__ == "__main__":
    repository = REPOSITORY
    assert repository, "no repository!"
    # group multiple assets together
    pattern = re.compile(GROUP_REGEX)
    for run in reversed(list_runs(repository)):
        # TODO skip processed runs
        if run["status"] != "completed":
            continue
        print(f"Download {run}")
        rundir = download_run(repository, str(run["databaseId"]))
        if not rundir:
            continue
        print(f"Report {run}")
        report_run(repository, run, pattern=pattern)
