"""Add a contributor to the list of signees in the CLA."""

from __future__ import annotations

import json
from argparse import ArgumentParser
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argparse import Namespace
    from collections.abc import Sequence


def parse_args(argv: Sequence[str] | None = None) -> Namespace:
    # parse CLI for inputs
    parser = ArgumentParser()
    parser.add_argument("path", type=Path, help="Local path to the CLA file.")
    parser.add_argument(
        "--id",
        type=int,
        required=True,
        help="Contributor to add to the CLA.",
    )
    parser.add_argument(
        "--login",
        type=str,
        required=True,
        help="Contributor's GitHub login.",
    )
    return parser.parse_args(argv)


def read_cla(path: Path) -> dict[int, str]:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}


def write_cla(path: Path, signees: dict[int, str]) -> None:
    path.write_text(json.dumps(signees, indent=2, sort_keys=True) + "\n")


def main() -> None:
    args = parse_args()
    write_cla(args.path, {**read_cla(args.path), args.id: args.login})


if __name__ == "__main__":
    main()
