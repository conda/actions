from __future__ import annotations

import json
import shutil
from argparse import ArgumentTypeError
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

import combine_durations
from combine_durations import (
    DurationStats,
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


def test_duration_stats_empty() -> None:
    stats = DurationStats()
    assert stats.number_of_tests == 0
    assert stats.total_run_time == 0.0
    assert stats.average_run_time == 0.0
    assert list(stats) == [0, 0.0, 0.0]


def test_stats_table_with_missing_new_durations(tmp_path: Path) -> None:
    """No downloaded artifacts, but existing duration files (conda-build failure)."""
    durations_dir = tmp_path / "durations"
    durations_dir.mkdir()
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()

    for path in DURATIONS_DIR.glob("*.json"):
        (durations_dir / path.name).write_text(path.read_text())

    combined, new_stats = aggregate_new_durations(artifacts_dir)
    _, old_stats = aggregate_old_durations(durations_dir, combined, unlink=False)

    assert not new_stats
    assert len(old_stats) == 2

    # main() unpacks stats this way for the summary table
    for os_name in sorted({*new_stats, *old_stats}):
        ncount, ntotal, naverage = new_stats.get(os_name, DurationStats())
        ocount, ototal, oaverage = old_stats.get(os_name, DurationStats())
        assert ncount == 0
        assert ntotal == 0.0
        assert naverage == 0.0
        assert ocount == 6
        assert ototal > 0
        assert oaverage > 0


def test_aggregate_new_durations() -> None:
    combined, stats = aggregate_new_durations(ARTIFACTS_DIR)
    assert len(combined) == len(stats) == 2
    for os in ("OS1", "OS2"):
        assert len(combined[os]) == 5
        assert stats[os].number_of_tests == 5
        assert stats[os].total_run_time > 0
        assert stats[os].average_run_time > 0


def test_aggregate_new_durations_deduplicates(tmp_path: Path) -> None:
    # same artifact contents uploaded from multiple jobs/runs
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    (artifacts_dir / "OS1_run1").mkdir()
    (artifacts_dir / "OS1_run2").mkdir()
    (artifacts_dir / "OS1_run3").mkdir()
    for run in ("OS1_run1", "OS1_run2", "OS1_run3"):
        shutil.copy(ARTIFACTS_DIR / "OS1_run1" / "OS1.json", artifacts_dir / run)

    combined, stats = aggregate_new_durations(artifacts_dir)

    # duplicated uploads are only read once
    assert all(len(durations) == 1 for durations in combined["OS1"].values())
    assert len(combined["OS1"]) == stats["OS1"].number_of_tests == 3


def test_aggregate_old_durations_warn_limit(
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    durations_dir = tmp_path / "durations"
    durations_dir.mkdir()
    (durations_dir / "OS1.json").write_text(
        json.dumps({f"test{i}": 1.0 for i in range(10)})
    )
    monkeypatch.setattr(combine_durations, "WARN_LIMIT", 5)

    aggregate_old_durations(durations_dir, {"OS1": {}}, unlink=False)

    captured = capsys.readouterr()
    assert "OS1::test0 not present in new durations, removing" in captured.out
    assert "OS1::test5 not present in new durations, removing" not in captured.out
    assert "OS1: … and" in captured.out and "more" in captured.out


@pytest.mark.parametrize(
    "combined,num_combined",
    [
        pytest.param({}, 0, id="no durations"),
        pytest.param(
            {
                path.stem: {
                    test: [duration]
                    for test, duration in json.loads(path.read_text()).items()
                }
                for path in DURATIONS_DIR.glob("*.json")
            },
            6,
            id="unchanged durations",
        ),
        pytest.param(
            aggregate_new_durations(ARTIFACTS_DIR)[0],
            5,
            id="updated durations",
        ),
    ],
)
def test_aggregate_old_durations(combined: COMBINED_TYPE, num_combined: int) -> None:
    combined, old_stats = aggregate_old_durations(DURATIONS_DIR, combined, unlink=False)
    assert len(combined) == (2 if num_combined else 0)
    assert len(old_stats) == 2
    for os in ("OS1", "OS2"):
        assert len(combined.get(os, ())) == num_combined
        assert old_stats[os].number_of_tests == 6
