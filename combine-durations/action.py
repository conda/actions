"""Combine test durations from all recent runs."""

from __future__ import annotations

import sys
import json
from argparse import ArgumentParser, ArgumentTypeError, Namespace
from functools import partial
from pathlib import Path
import os
from statistics import fmean
from typing import NamedTuple

from rich.console import Console
from rich import box
from rich.table import Table

console = Console(color_system="standard", soft_wrap=True, record=True)
print = console.print


def validate_dir(value: str, writable: bool = False) -> Path:
    try:
        path = Path(value).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        if writable:
            ignore = path / ".ignore"
            ignore.touch()
            ignore.unlink()
        return path
    except (FileExistsError, PermissionError) as err:
        # FileExistsError: value is a file, not a directory
        # PermissionError: value is not writable
        raise ArgumentTypeError(f"{value} is not a valid directory: {err}")


def parse_args() -> Namespace:
    # parse CLI for inputs
    parser = ArgumentParser()
    parser.add_argument("--durations-dir", type=validate_dir, required=True)
    parser.add_argument(
        "--artifacts-dir",
        type=partial(validate_dir, writable=True),
        required=True,
    )
    return parser.parse_args()

class DurationStats(NamedTuple):
    number_of_tests: int
    total_run_time: float
    average_run_time: float


def read_durations(path: Path, stats: dict[str, DurationStats]) -> tuple[str, dict[str, float]]:
    OS = path.stem
    data = json.loads(path.read_text())

    # new durations stats
    stats[OS] = DurationStats(
        number_of_tests=len(data),
        total_run_time=sum(data.values()),
        average_run_time=fmean(data.values()),
    )

    return OS, data


def dump_summary():
    # dump summary to GitHub Actions summary
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    output = os.getenv("GITHUB_OUTPUT")
    if summary or output:
        html = console.export_text()
    if summary:
        Path(summary).write_text(f"### Durations Audit\n{html}")
    if output:
        with Path(output).open("a") as fh:
            fh.write(
                # https://docs.github.com/en/actions/writing-workflows/choosing-what-your-workflow-does/workflow-commands-for-github-actions#setting-an-output-parameter
                # https://docs.github.com/en/actions/writing-workflows/choosing-what-your-workflow-does/workflow-commands-for-github-actions#multiline-strings
                f"summary<<GITHUB_OUTPUT_summary\n"
                f"<details>\n"
                f"<summary>Durations Audit</summary>\n"
                f"\n"
                f"{html}\n"
                f"\n"
                f"</details>\n"
                f"GITHUB_OUTPUT_summary\n"
            )


def main() -> None:
    args = parse_args()

    combined: dict[str, dict[str, list[float]]] = {}

    # aggregate new durations
    new_stats: dict[str, DurationStats] = {}
    for path in args.artifacts_dir.glob("**/*.json"):
        # read new durations
        OS, new_data = read_durations(path, new_stats)

        # insert new durations
        os_combined = combined.setdefault(OS, {})
        for key, value in new_data.items():
            os_combined.setdefault(key, []).append(value)

    # aggregate old durations
    old_stats: dict[str, DurationStats] = {}
    for path in args.durations_dir.glob("*.json"):
        # read old durations
        OS, old_data = read_durations(path, old_stats)

        try:
            os_combined = combined[OS]
        except KeyError:
            # KeyError: OS not present in new durations
            print(f"⚠️ {OS} not present in new durations, removing")
            path.unlink()
            continue

        # warn about tests that are no longer present
        for name in set(old_data) - set(combined[OS]):
            print(f"⚠️ {OS}::{name} not present in new durations, removing")

        # only copy over keys that are still present in new durations
        for key in set(old_data) & set(combined[OS]):
            os_combined[key].append(old_data[key])

    # display stats
    table = Table(box=box.MARKDOWN)
    table.add_column("OS")
    table.add_column("Number of tests")
    table.add_column("Total run time")
    table.add_column("Average run time")
    for OS in sorted({*new_stats, *old_stats}):
        ncount, ntotal, naverage = new_stats.get(OS, (0, 0.0, 0.0))
        ocount, ototal, oaverage = old_stats.get(OS, (0, 0.0, 0.0))

        dcount = ncount - ocount
        dtotal = ntotal - ototal
        daverage = naverage - oaverage

        table.add_row(
            OS,
            f"{ncount} ({dcount:+}) {'🟢' if dcount >= 0 else '🔴'}",
            f"{ntotal:.2f} ({dtotal:+.2f}) {'🔴' if dtotal >= 0 else '🟢'}",
            f"{naverage:.2f} ({daverage:+.2f}) {'🔴' if daverage >= 0 else '🟢'}",
        )
    print(table)

    # write out averages
    for OS, os_combined in combined.items():
        (args.durations_dir / f"{OS}.json").write_text(
            json.dumps(
                {key: fmean(values) for key, values in os_combined.items()},
                indent=4,
                sort_keys=True,
            )
            + "\n"  # include trailing newline
        )

    dump_summary()
    sys.exit(0)


if __name__ == "__main__":
    main()
