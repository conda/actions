from __future__ import annotations

import re
from pathlib import Path
from argparse import ArgumentParser, ArgumentTypeError


def get_input(value: str) -> Path:
    path = Path(value)
    if not path.exists():
        raise ArgumentTypeError(f"{value!r} does not exist")
    return path


def get_output(value: str) -> Path:
    path = Path(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def get_version(value: str) -> str:
    if not value:
        raise ArgumentTypeError("must be a non-empty string")
    return value


parser = ArgumentParser()
parser.add_argument("--input", required=True, type=get_input)
parser.add_argument("--output", required=True, type=get_output)
parser.add_argument("--version", required=True, type=get_version)
params = parser.parse_args()

text = params.input.read_text()
pattern = re.compile(
    rf"""
    \n+
    (
        \#\#\s+  # markdown header
        {re.escape(params.version)}\s+  # version number
        \(\d\d\d\d-\d\d-\d\d\)  # release date
    )\n+
    (
        .+?  # release notes
    )\n+
    (
        \#\#\s+  # markdown header
        \d+\.\d+\.\d+\s+  # version number
        \(\d\d\d\d-\d\d-\d\d\)  # release date
    )\n+
    """,
    flags=re.VERBOSE | re.DOTALL,
)
notes = match.group(2) if (match := pattern.search(text)) else ""
params.output.write_text(notes)
