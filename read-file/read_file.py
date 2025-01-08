"""Read a local file or remote URL."""

from __future__ import annotations

import json
import os
from argparse import ArgumentParser
from pathlib import Path
from typing import TYPE_CHECKING

import requests
import yaml
from requests.exceptions import HTTPError, MissingSchema

if TYPE_CHECKING:
    from argparse import Namespace
    from collections.abc import Sequence
    from typing import Literal


def parse_args(argv: Sequence[str] | None = None) -> Namespace:
    # parse CLI for inputs
    parser = ArgumentParser()
    parser.add_argument(
        "file",
        type=str,
        help="Local path or remote URL to the file to read.",
    )
    parser.add_argument(
        "--parser",
        choices=["json", "yaml"],
        help=(
            "Parser to use for the file. "
            "If not specified, the file content is returned as is."
        ),
    )
    parser.add_argument(
        "--default",
        type=str,
        help=(
            "Default value to use if the file is not found. "
            "If not specified, an error is raised."
        ),
    )
    return parser.parse_args(argv)


def read_file(file: str | os.PathLike[str] | Path, default: str | None) -> str:
    try:
        response = requests.get(file)
        response.raise_for_status()
    except (HTTPError, MissingSchema):
        # HTTPError: if the response status code is not ok
        # MissingSchema: if the URL is not valid
        try:
            return Path(file).read_text()
        except FileNotFoundError:
            if default is None:
                raise
            return default
    else:
        return response.text


def parse_content(content: str, parser: Literal["json", "yaml"]) -> str:
    # if a parser is defined we parse the content and dump it as JSON
    if parser == "json":
        content = json.loads(content)
        return json.dumps(content)
    elif parser == "yaml":
        content = yaml.safe_load(content)
        return json.dumps(content)
    else:
        raise ValueError("Parser not supported.")


def get_output(content: str) -> str:
    return f"content<<GITHUB_OUTPUT_content\n{content}\nGITHUB_OUTPUT_content\n"


def dump_output(content: str) -> None:
    if output := os.getenv("GITHUB_OUTPUT"):
        with Path(output).open("a") as fh:
            fh.write(get_output(content))


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)

    content = read_file(args.file, args.default)
    if args.parser:
        content = parse_content(content, args.parser)
    dump_output(content)


if __name__ == "__main__":
    main()
