"""Combine test durations from all recent runs."""
from __future__ import annotations

from argparse import ArgumentParser, ArgumentTypeError
import json
from pathlib import Path
from statistics import fmean


def validate(value: str) -> Path:
    try:
        path = Path(value).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path
    except FileExistsError as err:
        # FileExistsError: value is a file, not a directory
        raise ArgumentTypeError(f"{value} is not a valid directory: {err}")


# parse CLI for inputs
parser = ArgumentParser()
parser.add_argument("--durations-dir", type=validate, required=True)
parser.add_argument("--artifacts-dir", type=validate, required=True)
args = parser.parse_args()

combined: dict[str, dict[str, list[float]]] = {}

# aggregate all new durations
for path in args.artifacts_dir.glob("**/*.json"):
    os = path.stem
    combined_os = combined.setdefault(os, {})

    data = json.loads(path.read_text())
    for key, value in data.items():
        combined_os.setdefault(key, []).append(value)

# aggregate new and old durations while discarding durations that no longer exist
for path in args.durations_dir.glob("**/*.json"):
    os = path.stem
    combined_os = combined.setdefault(os, {})

    data = json.loads(path.read_text())
    for key in set(combined_os).intersection(args.durations_dir.glob("**/*.json")):
        combined_os.setdefault(key, []).append(data[key])

# write out averaged durations
for os, combined_os in combined.items():
    (args.durations_dir / f"{os}.json").write_text(
        json.dumps(
            {key: fmean(values) for key, values in combined_os.items()},
            indent=4,
            sort_keys=True,
        )
        + "\n"  # include trailing newline
    )
