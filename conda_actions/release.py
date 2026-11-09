from __future__ import annotations

import re
import sys
from dataclasses import dataclass

from .commands import ActionError, run, run_json

__all__ = [
    "RELEASE_TAG_PATTERN",
    "GitHubAuthor",
    "get_github_author",
    "get_github_login",
    "get_latest_tag",
    "normalize_release_tag",
    "parse_nul_records",
    "select_latest_release_tag",
]

RELEASE_TAG_PATTERN = re.compile(r"^v?(\d+\.\d+\.\d+)$")


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


@dataclass(frozen=True)
class GitHubAuthor:
    login: str
    author_id: str | None


def get_github_author(
    repo: str,
    commit_hash: str,
    *,
    env: dict[str, str],
) -> GitHubAuthor | None:
    data = run_json(
        ["gh", "api", f"repos/{repo}/commits/{commit_hash}"],
        env=env,
    )
    author = data.get("author") or {}
    login = author.get("login")
    if not login:
        print(
            f"::warning::No GitHub login associated with commit {repo}@{commit_hash}.",
            file=sys.stderr,
        )
        return None
    node_id = author.get("node_id")
    return GitHubAuthor(login=str(login), author_id=str(node_id) if node_id else None)


def get_github_login(
    repo: str,
    commit_hash: str,
    *,
    env: dict[str, str],
) -> str | None:
    author = get_github_author(repo, commit_hash, env=env)
    return author.login if author else None
