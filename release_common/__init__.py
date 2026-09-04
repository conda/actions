from __future__ import annotations

import json
import re
import subprocess
import sys
from typing import Any

__all__ = [
    "RELEASE_TAG_PATTERN",
    "ActionError",
    "get_github_login",
    "get_latest_tag",
    "normalize_release_tag",
    "parse_nul_records",
    "run",
    "run_json",
    "select_latest_release_tag",
]

RELEASE_TAG_PATTERN = re.compile(r"^v?(\d+\.\d+\.\d+)$")


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


def normalize_release_tag(tag: str) -> tuple[int, ...] | None:
    """Return numeric version parts for a final release tag, else None."""
    match = RELEASE_TAG_PATTERN.fullmatch(tag)
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def select_latest_release_tag(tags: list[str]) -> str:
    """Pick the highest final release tag by normalized numeric version."""
    scored = [
        (version, tag)
        for tag in tags
        if (version := normalize_release_tag(tag)) is not None
    ]
    if not scored:
        return ""
    return max(scored)[1]


def get_latest_tag(prefix: str = "") -> str:
    command = ["git", "tag", "--merged", "HEAD"]
    if prefix:
        command += ["--list", f"{prefix}*", "--list", f"v{prefix}*"]
    tags = run(command, capture=True).splitlines()
    return select_latest_release_tag(tags)


def parse_nul_records(output: str, width: int) -> list[tuple[str, ...]]:
    if not output:
        return []
    fields = output.split("\0")
    if fields[-1] == "":
        fields.pop()
    if len(fields) % width:
        raise ActionError("Failed to parse NUL-delimited git log output.")
    return [
        tuple(fields[offset : offset + width])
        for offset in range(0, len(fields), width)
    ]


def get_github_login(
    repo: str,
    commit_hash: str,
    *,
    env: dict[str, str],
) -> str | None:
    try:
        data = run_json(
            ["gh", "api", f"repos/{repo}/commits/{commit_hash}"],
            env=env,
        )
    except ActionError as err:
        print(
            f"::warning::Failed to resolve GitHub login for "
            f"{repo}@{commit_hash}: {err}",
            file=sys.stderr,
        )
        return None
    author = data.get("author") or {}
    login = author.get("login")
    if not login:
        print(
            f"::warning::No GitHub login associated with commit {repo}@{commit_hash}.",
            file=sys.stderr,
        )
        return None
    return str(login)
