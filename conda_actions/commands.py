from __future__ import annotations

import json
import subprocess
from typing import Any

__all__ = ["ActionError", "run", "run_json"]


class ActionError(Exception):
    pass


def run(
    command: list[str],
    *,
    capture: bool = False,
    env: dict[str, str] | None = None,
) -> str:
    try:
        result = subprocess.run(
            command,
            check=True,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            env=env,
        )
    except subprocess.CalledProcessError as err:
        detail = err.stderr.strip() if err.stderr else str(err)
        raise ActionError(f"Command failed: {' '.join(command)}\n{detail}") from err
    # Preserve leading spaces (e.g. git porcelain " M path"); only trim newlines.
    return result.stdout.rstrip("\n") if capture else ""


def run_json(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
) -> Any:
    output = run(command, capture=True, env=env)
    try:
        return json.loads(output)
    except json.JSONDecodeError as err:
        raise ActionError(f"Failed to parse JSON from: {' '.join(command)}") from err
