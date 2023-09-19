"""Combine test durations from all recent runs."""
from __future__ import annotations

import json
from argparse import ArgumentParser, ArgumentTypeError, Namespace
from pathlib import Path
from functools import partial
from statistics import fmean

from rich.console import Console

print = Console(color_system="standard", soft_wrap=True).print


def validate_dir(value: str, writable: bool = False) -> Path:
    try:
        path = Path(value).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        if writable:
            ignore = (path / ".ignore")
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
    parser.add_argument("--artifacts-dir", type=partial(validate_dir, writable=True), required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # aggregate new durations
    combined: dict[str, dict[str, list[float]]] = {}
    for path in args.artifacts_dir.glob("**/*.json"):
        os_combined = combined.setdefault((OS := path.stem), {})

        new_data = json.loads(path.read_text())
        for key, value in new_data.items():
            os_combined.setdefault(key, []).append(value)

    # aggregate old durations
    for path in args.durations_dir.glob("*.json"):
        try:
            os_combined = combined[(OS := path.stem)]
        except KeyError:
            # KeyError: OS not present in new durations
            print(f"⚠️ {OS} not present in new durations, removing")
            path.unlink()
            continue

        old_data = json.loads(path.read_text())
        if missing := set(old_data) - set(combined[OS]):
            for name in missing:
                print(f"⚠️ {OS}::{name} not present in new durations, removing")

        # only copy over keys that are still present in new durations
        for key in set(old_data) & set(combined[OS]):
            os_combined[key].append(old_data[key])

    # drop durations no longer present in new durations and write out averages
    for OS, os_combined in combined.items():
        (args.durations_dir / f"{OS}.json").write_text(
            json.dumps(
                {key: fmean(values) for key, values in os_combined.items()},
                indent=4,
                sort_keys=True,
            )
            + "\n"  # include trailing newline
        )


if __name__ == '__main__':
    main()
