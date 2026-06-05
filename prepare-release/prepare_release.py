from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
from argparse import ArgumentParser, Namespace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from news_common import (
    SECTION_ORDER,
    iter_news_fragments,
    parse_sectioned_news,
)

VERSION_BRANCH_RE = re.compile(r"^(?P<major_minor>\d+\.\d+)\.x$")
TAG_RE = re.compile(r"^v?(?P<version>\d+\.\d+\.(?P<micro>\d+))$")
CURRENT_DEVELOPMENTS = "[//]: # (current developments)"


class ActionError(Exception):
    pass


def parse_args(argv: list[str] | None = None) -> Namespace:
    parser = ArgumentParser(description="Prepare conda release notes.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    verify = subparsers.add_parser("verify-context")
    add_context_args(verify)

    prepare = subparsers.add_parser("prepare")
    add_context_args(prepare)
    prepare.add_argument("--news-directory", default="news")
    prepare.add_argument("--changelog-path", default="CHANGELOG.md")
    prepare.add_argument("--issue-reference", default="conda/infrastructure#556")
    prepare.add_argument("--branch-prefix", default="release-notes-")
    prepare.add_argument("--git-author-name", default="Conda Bot")
    prepare.add_argument(
        "--git-author-email",
        default="18747875+conda-bot@users.noreply.github.com",
    )
    prepare.add_argument(
        "--repository",
        default=os.environ.get("GITHUB_REPOSITORY", ""),
    )
    prepare.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))

    return parser.parse_args(argv)


def add_context_args(parser: ArgumentParser) -> None:
    parser.add_argument("--release-branch-pattern", default="[0-9]*.[0-9]*.x")


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        if args.command == "verify-context":
            context = verify_context(args.release_branch_pattern)
            write_output("head-branch", context["head_branch"])
            write_output("head-sha", context["head_sha"])
            print(f"Verified release context for {context['head_branch']}.")
        elif args.command == "prepare":
            prepare_release(args)
    except ActionError as err:
        print(f"::error::{err}", file=sys.stderr)
        return 1
    return 0


def prepare_release(args: Namespace) -> None:
    context = verify_context(args.release_branch_pattern)
    base_branch = context["head_branch"]
    version = infer_next_version(base_branch)
    release_date = datetime.now(UTC).date().isoformat()
    release_branch = f"{args.branch_prefix}{version}"

    run(["git", "checkout", "-B", release_branch])
    run(["git", "config", "user.name", args.git_author_name])
    run(["git", "config", "user.email", args.git_author_email])

    fragment_paths = iter_news_fragments(args.news_directory)
    fragments = collect_fragments(fragment_paths)
    if not fragments:
        raise ActionError(f"No news fragments found under {args.news_directory!r}.")

    entry = render_changelog_entry(version, release_date, fragments)
    update_changelog(Path(args.changelog_path), entry, version)

    for path in fragment_paths:
        path.unlink()

    changed_paths = get_changed_paths()
    ensure_allowed_paths(
        changed_paths,
        changelog_path=Path(args.changelog_path),
        news_paths=fragment_paths,
    )
    if not changed_paths:
        print("No release note changes to commit.")
        return

    run(["git", "add", args.changelog_path, *map(str, fragment_paths)])
    run(["git", "commit", "-m", f"Prepare release notes for {version}"])
    run(["git", "push", "--force-with-lease", "origin", release_branch])

    url = create_or_update_pr(
        repository=args.repository,
        branch=release_branch,
        base_branch=base_branch,
        version=version,
        issue_reference=args.issue_reference,
        token=args.token,
    )

    write_output("version", version)
    write_output("branch", release_branch)
    write_output("pull-request-url", url)
    print(f"Prepared release notes for {version}: {url}")


def verify_context(release_branch_pattern: str) -> dict[str, str]:
    event_name = os.environ.get("GITHUB_EVENT_NAME")
    payload = load_event_payload()
    repository = os.environ.get("GITHUB_REPOSITORY")

    if event_name != "workflow_run":
        raise ActionError("prepare-release must run from the workflow_run event.")

    workflow_run = payload.get("workflow_run") or {}
    if workflow_run.get("conclusion") != "success":
        raise ActionError("The triggering workflow_run did not conclude successfully.")
    if workflow_run.get("event") != "push":
        raise ActionError("The triggering workflow_run must come from a push event.")

    head_repository = workflow_run.get("head_repository") or {}
    if head_repository.get("full_name") != repository:
        raise ActionError("The triggering workflow_run must come from this repository.")

    head_branch = workflow_run.get("head_branch")
    head_sha = workflow_run.get("head_sha")
    if not head_branch or not head_sha:
        raise ActionError(
            "The triggering workflow_run did not include a head branch and SHA."
        )

    patterns = split_patterns(release_branch_pattern)
    if not any(fnmatch.fnmatchcase(head_branch, pattern) for pattern in patterns):
        raise ActionError(
            f"The triggering branch {head_branch!r} does not match "
            f"{', '.join(patterns)!r}."
        )

    return {"head_branch": head_branch, "head_sha": head_sha}


def load_event_payload() -> dict[str, Any]:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return {}
    path = Path(event_path)
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def split_patterns(value: str) -> list[str]:
    patterns = [
        pattern.strip()
        for chunk in value.splitlines()
        for pattern in chunk.split(",")
        if pattern.strip()
    ]
    return patterns or ["[0-9]*.[0-9]*.x"]


def infer_next_version(branch: str) -> str:
    match = VERSION_BRANCH_RE.match(branch)
    if not match:
        raise ActionError(f"Cannot infer release version from branch {branch!r}.")

    major_minor = match.group("major_minor")
    tags = run(["git", "tag", "--list", f"{major_minor}.*"], capture=True).splitlines()
    tags.extend(
        run(["git", "tag", "--list", f"v{major_minor}.*"], capture=True).splitlines()
    )

    micros = []
    for tag in tags:
        tag_match = TAG_RE.match(tag)
        if tag_match and tag_match.group("version").startswith(f"{major_minor}."):
            micros.append(int(tag_match.group("micro")))

    next_micro = max(micros) + 1 if micros else 0
    return f"{major_minor}.{next_micro}"


def collect_fragments(paths: list[Path]) -> dict[str, list[str]]:
    fragments: dict[str, list[str]] = {section: [] for section in SECTION_ORDER}
    errors: list[str] = []

    for path in paths:
        fragment = parse_sectioned_news(path, path.read_text(encoding="utf-8"))
        errors.extend(fragment.errors)
        for section, items in fragment.sections.items():
            fragments[section].extend(items)

    if errors:
        raise ActionError("\n".join(errors))

    return {section: items for section, items in fragments.items() if items}


def render_changelog_entry(
    version: str,
    release_date: str,
    fragments: dict[str, list[str]],
) -> str:
    lines = [f"## {version} ({release_date})", ""]

    for section in SECTION_ORDER:
        items = fragments.get(section)
        if not items:
            continue

        lines.extend([f"### {section}", ""])
        for item in items:
            lines.extend(item.splitlines())
        lines.append("")

    return "\n".join(lines).rstrip() + "\n\n\n"


def update_changelog(path: Path, entry: str, version: str) -> None:
    if not path.is_file():
        raise ActionError(f"Changelog file does not exist: {path}")

    text = path.read_text(encoding="utf-8")
    if re.search(rf"^##\s+{re.escape(version)}\s+\(", text, flags=re.MULTILINE):
        raise ActionError(f"Changelog already contains an entry for {version}.")

    if CURRENT_DEVELOPMENTS in text:
        marker_end = text.index(CURRENT_DEVELOPMENTS) + len(CURRENT_DEVELOPMENTS)
        prefix = text[:marker_end].rstrip() + "\n\n"
        suffix = text[marker_end:].lstrip("\n")
        updated = prefix + entry + suffix
    else:
        updated = entry + text.lstrip("\n")

    path.write_text(updated, encoding="utf-8")


def get_changed_paths() -> list[Path]:
    status = run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        capture=True,
    )
    paths: list[Path] = []
    for line in status.splitlines():
        if not line:
            continue
        paths.append(Path(line[3:]))
    return paths


def ensure_allowed_paths(
    paths: list[Path],
    *,
    changelog_path: Path,
    news_paths: list[Path],
) -> None:
    allowed_paths = {changelog_path, *news_paths}
    unexpected = [
        path
        for path in paths
        if path not in allowed_paths
    ]
    if unexpected:
        raise ActionError(
            "prepare-release produced unexpected file changes: "
            + ", ".join(str(path) for path in unexpected)
        )


def create_or_update_pr(
    *,
    repository: str,
    branch: str,
    base_branch: str,
    version: str,
    issue_reference: str,
    token: str,
) -> str:
    if not repository:
        raise ActionError("No GitHub repository was provided.")
    if not token:
        raise ActionError("No GitHub token was provided.")

    env = os.environ | {"GH_TOKEN": token}
    title = f"Prepare release notes for {version}"
    body = (
        f"Prepare release notes for `{version}`.\n\n"
        f"Refs {issue_reference}\n\n"
        "This PR was generated by `conda/actions/prepare-release`."
    )
    existing = run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repository,
            "--head",
            branch,
            "--base",
            base_branch,
            "--state",
            "open",
            "--json",
            "number,url",
        ],
        capture=True,
        env=env,
    )
    prs = json.loads(existing)

    if prs:
        number = str(prs[0]["number"])
        run(
            [
                "gh",
                "pr",
                "edit",
                number,
                "--repo",
                repository,
                "--title",
                title,
                "--body",
                body,
                "--base",
                base_branch,
            ],
            env=env,
        )
        return str(prs[0]["url"])

    return run(
        [
            "gh",
            "pr",
            "create",
            "--repo",
            repository,
            "--base",
            base_branch,
            "--head",
            branch,
            "--title",
            title,
            "--body",
            body,
        ],
        capture=True,
        env=env,
    ).strip()


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
    return result.stdout if capture else ""


def write_output(name: str, value: str) -> None:
    if output := os.environ.get("GITHUB_OUTPUT"):
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


if __name__ == "__main__":
    sys.exit(main())
