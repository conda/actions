"""Combine test durations from all recent runs."""

from __future__ import annotations

import json
import os
import sys
from argparse import ArgumentParser, ArgumentTypeError, Namespace
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from statistics import fmean
from typing import TYPE_CHECKING

from rich import box
from rich.console import Console
from rich.table import Table

if TYPE_CHECKING:
    from collections.abc import Iterable

    COMBINED_TYPE = dict[str, dict[str, list[float]]]

CONSOLE = Console(color_system="standard", soft_wrap=True, record=True)
print = CONSOLE.print


def validate_dir(value: str | os.PathLike[str] | Path, writable: bool = False) -> Path:
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


@dataclass
class DurationStats:
    number_of_tests: int = 0
    total_run_time: float = 0.0

    @property
    def average_run_time(self) -> float:
        return self.total_run_time / self.number_of_tests

    def __iter__(self) -> Iterable[int, float]:
        yield self.number_of_tests
        yield self.total_run_time
        yield self.average_run_time


STATS_MAP = dict[str, DurationStats]


def read_durations(
    path: Path,
    stats: STATS_MAP,
) -> tuple[str, dict[str, float]]:
    os_name = path.stem
    data = json.loads(path.read_text())

    # update durations stats
    os_stats = stats.setdefault(os_name, DurationStats())
    os_stats.number_of_tests += len(data)
    os_stats.total_run_time += sum(data.values())

    return os_name, data


def aggregate_new_durations(artifacts_dir: Path) -> tuple[COMBINED_TYPE, STATS_MAP]:
    combined: COMBINED_TYPE = {}

    new_stats: dict[str, DurationStats] = {}
    for path in artifacts_dir.glob("**/*.json"):
        # read new durations
        os_name, new_data = read_durations(path, new_stats)

        # insert new durations
        os_combined = combined.setdefault(os_name, {})
        for key, value in new_data.items():
            os_combined.setdefault(key, []).append(value)

    return combined, new_stats


def aggregate_old_durations(
    durations_dir: Path,
    combined: COMBINED_TYPE,
    unlink: bool = True,
) -> tuple[COMBINED_TYPE, STATS_MAP]:
    combined = combined or {}

    old_stats: dict[str, DurationStats] = {}
    for path in durations_dir.glob("*.json"):
        # read old durations
        os_name, old_data = read_durations(path, old_stats)

        try:
            os_combined = combined[os_name]
        except KeyError:
            # KeyError: OS not present in new durations
            if unlink:
                print(f"⚠️ {os_name} not present in new durations, removing")
                path.unlink()
            else:
                print(f"⚠️ {os_name} not present in new durations, skipping")
            continue

        # warn about tests that are no longer present
        for name in set(old_data) - set(combined[os_name]):
            print(f"⚠️ {os_name}::{name} not present in new durations, removing")

        # only copy over keys that are still present in new durations
        for key in set(old_data) & set(combined[os_name]):
            os_combined[key].append(old_data[key])

    return combined, old_stats


def get_step_summary(html: str) -> str:
    return f"### Durations Audit\n{html}"


def get_output(html: str) -> str:
    return (
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


def dump_summary(console: Console = CONSOLE) -> None:
    # dump summary to GitHub Actions summary
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    output = os.getenv("GITHUB_OUTPUT")
    if summary or output:
        html = console.export_text()
    if summary:
        Path(summary).write_text(get_step_summary(html))
    if output:
        with Path(output).open("a") as fh:
            fh.write(get_output(html))


def main() -> None:
    args = parse_args()

    combined, new_stats = aggregate_new_durations(args.artifacts_dir)
    combined, old_stats = aggregate_old_durations(args.durations_dir, combined)

    # display stats
    table = Table(box=box.MARKDOWN)
    table.add_column("OS")
    table.add_column("Number of tests")
    table.add_column("Total run time")
    table.add_column("Average run time")
    for os_name in sorted({*new_stats, *old_stats}):
        ncount, ntotal, naverage = new_stats.get(os_name, DurationStats())
        ocount, ototal, oaverage = old_stats.get(os_name, DurationStats())

        dcount = ncount - ocount
        dtotal = ntotal - ototal
        daverage = naverage - oaverage

        table.add_row(
            os_name,
            f"{ncount} ({dcount:+}) {'🟢' if dcount >= 0 else '🔴'}",
            f"{ntotal:.2f} ({dtotal:+.2f}) {'🔴' if dtotal >= 0 else '🟢'}",
            f"{naverage:.2f} ({daverage:+.2f}) {'🔴' if daverage >= 0 else '🟢'}",
        )
    print(table)

    # write out averages
    for os_name, os_combined in combined.items():
        (args.durations_dir / f"{os_name}.json").write_text(
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
