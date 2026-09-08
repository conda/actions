from __future__ import annotations

import sys

import pytest

from conda_actions import commands as commands_module
from conda_actions.commands import ActionError, run, run_json


def test_run_captures_and_trims_trailing_newlines() -> None:
    assert run([sys.executable, "-c", "print('hi')"], capture=True) == "hi"


def test_run_raises_action_error_on_failure() -> None:
    with pytest.raises(ActionError, match="Command failed"):
        run([sys.executable, "-c", "import sys; sys.exit(1)"])


def test_run_json_rejects_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        commands_module,
        "run",
        lambda *args, **kwargs: "not json",
    )

    with pytest.raises(ActionError, match="Failed to parse JSON"):
        run_json(["gh", "api", "repos/conda/example"])
