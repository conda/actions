"""Read local file or remote URL."""

from __future__ import annotations

import yaml
import os
import json
import requests
from typing import TYPE_CHECKING
from argparse import ArgumentParser
from pathlib import Path

if TYPE_CHECKING:
    from argparse import Namespace


def parse_args() -> Namespace:
    # parse CLI for inputs
    parser = ArgumentParser()
    parser.add_argument(
        "file",
        type=str,
        help="Local path or remote URL to the file to read.",
    )
    parser.add_argument(
        "parser",
        choices=["json", "yaml"],
        nargs="?",
        help="Parser to use for the file.",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    try:
        response = requests.get(args.file)
        response.raise_for_status()
    except requests.exceptions.HTTPError:
        content = Path(args.file).read_text()
    else:
        content = response.text

    # if a parser is defined we parse the content and dump it as JSON
    if args.parser == "json":
        content = json.loads(content)
        content = json.dumps(content)
    elif args.parser == "yaml":
        content = yaml.safe_load(content)
        content = json.dumps(content)

    if output := os.getenv("GITHUB_OUTPUT"):
        with Path(output).open("a") as fh:
            fh.write(
                f"content<<GITHUB_OUTPUT_content\n"
                f"{content}\n"
                f"GITHUB_OUTPUT_content\n"
            )


if __name__ == "__main__":
    main()
