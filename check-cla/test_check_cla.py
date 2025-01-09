from __future__ import annotations

import filecmp
from argparse import Namespace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from check_cla import parse_args, read_cla, write_cla

if TYPE_CHECKING:
    from typing import Final

DATA: Final = Path(__file__).parent / "data"

EMPTY: Final[dict[int, str]] = {}
SINGLE: Final = {"123": "login"}
MULTIPLE: Final = {"111": "foo", "123": "login", "222": "bar"}


def test_parse_args() -> None:
    with pytest.raises(SystemExit):
        parse_args([])
    with pytest.raises(SystemExit):
        parse_args(["file"])
    with pytest.raises(SystemExit):
        parse_args(["--id=123"])
    with pytest.raises(SystemExit):
        parse_args(["--login=login"])
    with pytest.raises(SystemExit):
        parse_args(["file", "--id=123"])
    with pytest.raises(SystemExit):
        parse_args(["file", "--login=login"])
    with pytest.raises(SystemExit):
        parse_args(["--id=123", "--login=login"])
    assert parse_args(["file", "--id=123", "--login=login"]) == Namespace(
        path=Path("file"),
        id=123,
        login="login",
    )


@pytest.mark.parametrize(
    "path,signees",
    [
        ("missing", EMPTY),
        ("empty.json", EMPTY),
        ("single.json", SINGLE),
        ("multiple.json", MULTIPLE),
    ],
)
def test_read_cla(path: str, signees: dict[int, str]) -> None:
    assert read_cla(DATA / path) == signees


@pytest.mark.parametrize(
    "path,signees",
    [
        ("empty.json", EMPTY),
        ("single.json", SINGLE),
        ("multiple.json", MULTIPLE),
    ],
)
def test_write_cla(tmp_path: Path, path: str, signees: dict[int, str]) -> None:
    write_cla(tmp := tmp_path / path, signees)
    assert filecmp.cmp(tmp, DATA / path)
