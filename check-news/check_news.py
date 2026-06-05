from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from argparse import ArgumentParser, Namespace
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from news_common import is_news_fragment, parse_sectioned_news

PR_RE = re.compile(r"(?<!\d)#?(?P<number>\d+)(?!\d)")


@dataclass(frozen=True)
class ChangedFile:
    status: str
    path: Path


class ActionError(Exception):
    pass


def parse_bool(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    match value.strip().casefold():
        case "1" | "true" | "yes" | "on":
            return True
        case "0" | "false" | "no" | "off":
            return False
        case _:
            raise ActionError(f"Invalid boolean value: {value!r}")


def parse_args(argv: list[str] | None = None) -> Namespace:
    parser = ArgumentParser(description="Validate conda news fragments.")
    parser.add_argument("--news-directory", default="news")
    parser.add_argument("--skip-label", default="no-news")
    parser.add_argument("--require-pr-number", default="true")
    parser.add_argument(
        "--fragment-format",
        default="sectioned",
        choices=["sectioned", "auto"],
    )
    parser.add_argument(
        "--exempt-authors",
        default="pre-commit-ci[bot],dependabot[bot],conda-bot,github-actions[bot]",
        help="Comma-separated GitHub logins that do not need a news fragment.",
    )
    parser.add_argument(
        "--changed-file",
        action="append",
        default=[],
        help="Changed file path for tests; when omitted, git diff is used.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        check_news(args)
    except ActionError as err:
        print(f"::error::{err}", file=sys.stderr)
        return 1
    return 0


def check_news(args: Namespace) -> None:
    payload = load_event_payload()
    labels = pull_request_labels(payload)
    author = pull_request_author(payload)
    pr_number = pull_request_number(payload)

    if args.skip_label and args.skip_label in labels:
        write_summary(f"News check skipped because `{args.skip_label}` is present.")
        return

    exempt_authors = {
        author.strip() for author in args.exempt_authors.split(",") if author.strip()
    }
    if author and author in exempt_authors:
        write_summary(f"News check skipped for exempt author `{author}`.")
        return

    changed_files = (
        [ChangedFile(status="A", path=Path(path)) for path in args.changed_file]
        if args.changed_file
        else get_changed_files(payload)
    )
    news_directory = Path(args.news_directory)
    news_files = [
        changed.path
        for changed in changed_files
        if not changed.status.startswith("D")
        and is_news_fragment(changed.path, news_directory)
    ]

    if not news_files:
        raise ActionError(
            "This PR needs a news fragment or the "
            f"`{args.skip_label}` label. Add a file under `{news_directory}/`."
        )

    require_pr_number = parse_bool(args.require_pr_number)
    errors: list[str] = []
    checked = 0
    for path in news_files:
        if not path.exists():
            errors.append(f"{path}: changed news fragment is missing from the checkout")
            continue

        text = path.read_text(encoding="utf-8")
        fragment = parse_sectioned_news(path, text)
        errors.extend(fragment.errors)
        checked += 1

        if require_pr_number:
            if pr_number is None:
                errors.append(
                    "Could not determine the pull request number for validation."
                )
            elif not fragment_mentions_pr(path, text, pr_number):
                errors.append(
                    f"{path}: expected the filename or contents to mention "
                    f"PR #{pr_number}"
                )

    if errors:
        raise ActionError("\n".join(errors))

    write_summary(f"Validated {checked} news fragment(s).")


def load_event_payload() -> dict[str, Any]:
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path:
        return {}
    event_path = Path(path)
    if not event_path.is_file():
        return {}
    return json.loads(event_path.read_text(encoding="utf-8"))


def pull_request_labels(payload: dict[str, Any]) -> set[str]:
    pull_request = payload.get("pull_request") or {}
    issue = payload.get("issue") or {}
    return {
        label.get("name", "")
        for label in pull_request.get("labels", issue.get("labels", []))
        if isinstance(label, dict)
    }


def pull_request_author(payload: dict[str, Any]) -> str | None:
    pull_request = payload.get("pull_request") or {}
    user = pull_request.get("user") or {}
    return user.get("login")


def pull_request_number(payload: dict[str, Any]) -> int | None:
    pull_request = payload.get("pull_request") or {}
    number = pull_request.get("number") or payload.get("number")
    return int(number) if number is not None else None


def get_changed_files(payload: dict[str, Any]) -> list[ChangedFile]:
    pull_request = payload.get("pull_request") or {}
    base = pull_request.get("base") or {}
    head = pull_request.get("head") or {}
    attempts = [
        (base.get("sha"), head.get("sha")),
        (f"origin/{base.get('ref')}", "HEAD") if base.get("ref") else (None, None),
        ("HEAD^", "HEAD"),
    ]

    for before, after in attempts:
        if not before or not after:
            continue
        try:
            return diff_name_status(before, after)
        except subprocess.CalledProcessError:
            continue

    raise ActionError(
        "Could not determine changed files. Use actions/checkout with fetch-depth: 0."
    )


def diff_name_status(before: str, after: str) -> list[ChangedFile]:
    result = subprocess.run(
        ["git", "diff", "--name-status", before, after],
        check=True,
        text=True,
        capture_output=True,
    )
    changed: list[ChangedFile] = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0]
        path = parts[-1]
        changed.append(ChangedFile(status=status, path=Path(path)))
    return changed


def fragment_mentions_pr(path: str | Path, text: str, pr_number: int | str) -> bool:
    pr_number = str(pr_number)
    for value in (Path(path).name, text):
        for match in PR_RE.finditer(value):
            if match.group("number") == pr_number:
                return True
    return False


def write_summary(text: str) -> None:
    print(text)
    if output := os.environ.get("GITHUB_OUTPUT"):
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(f"summary={text}\n")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a", encoding="utf-8") as handle:
            handle.write(f"{text}\n")


if __name__ == "__main__":
    sys.exit(main())
