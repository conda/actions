from __future__ import annotations

import json
from argparse import ArgumentTypeError
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from combine_durations import (
    aggregate_new_durations,
    aggregate_old_durations,
    read_durations,
    validate_dir,
)

if TYPE_CHECKING:
    from combine_durations import COMBINED_TYPE, STATS_MAP

DURATIONS_DIR = Path(__file__).parent / "data" / "durations"
ARTIFACTS_DIR = Path(__file__).parent / "data" / "artifacts"


def test_validate_dir(tmp_path: Path) -> None:
    # directory
    assert validate_dir(tmp_path, writable=False) == tmp_path
    assert validate_dir(tmp_path, writable=True) == tmp_path

    # inaccessible directory
    stat = tmp_path.stat()
    try:
        # make file unreadable
        tmp_path.chmod(0o000)
        assert validate_dir(tmp_path, writable=False) == tmp_path
        with pytest.raises(ArgumentTypeError, match=r"not a valid directory"):
            assert validate_dir(tmp_path, writable=True)
    finally:
        # cleanup so tmp_path can be removed
        tmp_path.chmod(stat.st_mode)

    # missing
    assert validate_dir(path := tmp_path / "missing1", writable=False) == path
    assert validate_dir(path := tmp_path / "missing2", writable=True) == path

    # file
    (path := tmp_path / "file").touch()
    with pytest.raises(ArgumentTypeError, match=r"not a valid directory"):
        assert validate_dir(path, writable=False)
    with pytest.raises(ArgumentTypeError, match=r"not a valid directory"):
        assert validate_dir(path, writable=True) == path

    # permissions
    # TODO: not easy to test using either chmod or chown


@pytest.mark.parametrize(
    "path",
    [pytest.param(path, id=path.name) for path in DURATIONS_DIR.glob("*.json")],
)
def test_read_durations(path: Path) -> None:
    stats: STATS_MAP = {}
    os, data = read_durations(path, stats)
    assert os == path.stem
    assert data == json.loads(path.read_text())
    assert os in stats
    assert len(stats) == 1
    assert stats[os].number_of_tests == len(data)
    assert stats[os].total_run_time == sum(data.values())
    assert stats[os].average_run_time == sum(data.values()) / len(data)


def test_aggregate_new_durations() -> None:
    combined, stats = aggregate_new_durations(ARTIFACTS_DIR)
    assert len(combined) == len(stats) == 2
    for os in ("OS1", "OS2"):
        assert len(combined[os]) == 5
        assert stats[os].number_of_tests == 5
        assert stats[os].total_run_time > 0
        assert stats[os].average_run_time > 0


@pytest.mark.parametrize(
    "combined,num_combined,num_tests",
    [
        pytest.param({}, 0, 6, id="no durations"),
        pytest.param(
            {
                path.stem: {
                    test: [duration]
                    for test, duration in json.loads(path.read_text()).items()
                }
                for path in DURATIONS_DIR.glob("*.json")
            },
            6,
            6,
            id="unchanged durations",
        ),
        pytest.param(
            aggregate_new_durations(ARTIFACTS_DIR)[0],
            5,
            6,
            id="updated durations",
        ),
    ],
)
def test_aggregate_old_durations(
    combined: COMBINED_TYPE,
    num_combined: int,
    num_tests: int,
) -> None:
    combined, stats = aggregate_old_durations(DURATIONS_DIR, combined, unlink=False)
    assert len(combined) == (2 if num_combined else 0)
    assert len(stats) == 2
    for os in ("OS1", "OS2"):
        assert len(combined.get(os, ())) == num_combined
        assert stats[os].number_of_tests == num_tests
