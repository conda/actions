"""Add a contributor to the list of signees in the CLA."""

from __future__ import annotations

import json
from argparse import ArgumentParser
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argparse import Namespace


def parse_args() -> Namespace:
    # parse CLI for inputs
    parser = ArgumentParser()
    parser.add_argument("cla_path", type=Path, help="Local path to the CLA file.")
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
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    path = args.cla_path
    try:
        signees = json.loads(path.read_text())
    except FileNotFoundError:
        signees = {}

    signees[args.id] = args.login
    path.write_text(json.dumps(signees, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
