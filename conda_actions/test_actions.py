from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "script",
    [
        "check-news/check_news.py",
        "prepare-authors/prepare_authors.py",
        "prepare-release/prepare_release.py",
    ],
)
def test_action_script_imports_from_action_pythonpath(
    tmp_path: Path,
    script: str,
) -> None:
    repository = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(repository / script), "--help"],
        cwd=tmp_path,
        env=os.environ | {"PYTHONPATH": str(repository)},
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )

    assert "usage:" in result.stdout
