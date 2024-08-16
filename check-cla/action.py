"""Add a contributor to the list of signees in the CLA."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from argparse import ArgumentParser

if TYPE_CHECKING:
    from argparse import Namespace


def parse_args() -> Namespace:
    # parse CLI for inputs
    parser = ArgumentParser()
    parser.add_argument("cla_path", type=Path, help="Local path to the CLA file.")
    parser.add_argument("contributor", type=str, help="Contributor to add to the CLA.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    path = args.cla_path
    signees = json.loads(path.read_text())
    signees["contributors"].append(args.contributor)
    signees["contributors"].sort(key=str.lower)
    path.write_text(json.dumps(signees, indent=2) + "\n")


if __name__ == "__main__":
    main()
