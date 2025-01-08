from __future__ import annotations

from argparse import ArgumentTypeError
from typing import TYPE_CHECKING

import pytest

from combine_durations import validate_dir

if TYPE_CHECKING:
    from pathlib import Path


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
    assert validate_dir(path := tmp_path / "missing", writable=False) == path
    assert validate_dir(path := tmp_path / "missing", writable=True) == path

    # file
    (path := tmp_path / "file").touch()
    with pytest.raises(ArgumentTypeError, match=r"not a valid directory"):
        assert validate_dir(path, writable=False)
    with pytest.raises(ArgumentTypeError, match=r"not a valid directory"):
        assert validate_dir(path, writable=True) == path

    # permissions
    # TODO: not easy to test using either chmod or chown
