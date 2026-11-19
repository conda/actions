from __future__ import annotations

import fnmatch
import json
import os
import re
import sys
from argparse import ArgumentParser, Namespace
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from conda_actions.changelog import (
    CONTRIBUTORS_SECTION,
    CURRENT_DEVELOPMENTS,
    find_release_entry,
    merge_changelog_entry,
)
from conda_actions.commands import ActionError, run, run_json
from conda_actions.news import SECTION_ORDER, is_news_fragment, parse_sectioned_news
from conda_actions.release import (
    get_github_login,
    get_latest_tag,
    parse_nul_records,
)

VERSION_BRANCH_RE = re.compile(r"^(?P<major_minor>\d+\.\d+)\.x$")
TAG_RE = re.compile(r"^v?(?P<version>\d+\.\d+\.(?P<micro>\d+))$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
MAX_LOGIN_LOOKUPS_PER_EMAIL = 5
MAX_UNRESOLVED_LOGIN_LOOKUPS = 20


@dataclass(frozen=True)
class ContributorCommit:
    hash: str
    email: str


def parse_args(argv: list[str] | None = None) -> Namespace:
    parser = ArgumentParser(description="Prepare conda release notes.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    verify = subparsers.add_parser("verify-context")
    add_context_args(verify)

    prepare = subparsers.add_parser("prepare")
    add_context_args(prepare)
    prepare.add_argument("--news-directory", default="news")
    prepare.add_argument("--changelog-path", default="CHANGELOG.md")
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
    parser.add_argument(
        "--version",
        default="",
        help="Explicit release version (MAJOR.MINOR.PATCH) for workflow_dispatch runs.",
    )


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        if args.command == "verify-context":
            context = verify_context(args.release_branch_pattern, args.version)
            write_output("head-branch", context["head_branch"])
            write_output("head-sha", context["head_sha"])
            print(f"Verified release context for {context['head_branch']}.")
        else:
            prepare_release(args)
    except ActionError as err:
        print(f"::error::{err}", file=sys.stderr)
        return 1
    return 0


def prepare_release(args: Namespace) -> None:
    context = verify_context(args.release_branch_pattern, args.version)
    base_branch = context["head_branch"]

    news_directory = Path(args.news_directory)
    if not news_directory.is_dir():
        raise ActionError(f"News directory does not exist: {news_directory}")

    fragment_paths = news_fragment_paths(news_directory)
    if not fragment_paths:
        print(f"No news fragments found under {str(news_directory)!r}. Nothing to do.")
        return
    fragments = collect_fragments(fragment_paths)

    version = args.version or infer_next_version(base_branch)
    release_date = datetime.now(UTC).date().isoformat()
    release_branch = f"{args.branch_prefix}{version}"

    run(["git", "checkout", "-B", release_branch])
    run(["git", "config", "user.name", args.git_author_name])
    run(["git", "config", "user.email", args.git_author_email])
    if not args.token:
        raise ActionError("No GitHub token was provided.")
    if not args.repository:
        raise ActionError("No GitHub repository was provided.")
    git_env = os.environ | {"GH_TOKEN": args.token}
    run(["gh", "auth", "setup-git"], env=git_env)

    if not is_current_release_head(base_branch, context["head_sha"], git_env):
        return

    contributors = collect_contributors(
        args.repository,
        env=git_env,
        tag_prefix=f"{version.rpartition('.')[0]}.",
    )
    entry = render_changelog_entry(version, release_date, fragments, contributors)
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

    if not is_current_release_head(base_branch, context["head_sha"], git_env):
        return

    run(["git", "push", "--force-with-lease", "origin", release_branch], env=git_env)

    url = create_or_update_pr(
        repository=args.repository,
        branch=release_branch,
        base_branch=base_branch,
        version=version,
        token=args.token,
    )

    write_output("version", version)
    write_output("branch", release_branch)
    write_output("pull-request-url", url)
    print(f"Prepared release notes for {version}: {url}")


def is_current_release_head(
    base_branch: str, head_sha: str, env: dict[str, str]
) -> bool:
    remote_ref = f"refs/heads/{base_branch}"
    remote = run(
        ["git", "ls-remote", "--exit-code", "--heads", "origin", remote_ref],
        capture=True,
        env=env,
    ).split()
    if len(remote) != 2 or remote[1] != remote_ref:
        raise ActionError(f"Could not determine remote head for {base_branch!r}.")
    if remote[0] != head_sha:
        print(
            f"Skipping stale workflow run for {base_branch}: "
            f"{head_sha} is no longer the branch tip."
        )
        return False
    return True


def verify_context(release_branch_pattern: str, version: str = "") -> dict[str, str]:
    event_name = os.environ.get("GITHUB_EVENT_NAME")
    payload = load_event_payload()
    repository = os.environ.get("GITHUB_REPOSITORY")

    if version:
        if event_name != "workflow_dispatch":
            raise ActionError(
                "An explicit version is only supported for workflow_dispatch runs."
            )
        if not VERSION_RE.match(version):
            raise ActionError(
                f"Invalid version {version!r}, expected MAJOR.MINOR.PATCH "
                "without a leading 'v'."
            )
        if os.environ.get("GITHUB_REF_TYPE") != "branch":
            raise ActionError("The dispatched run must come from a branch, not a tag.")
        head_branch = os.environ.get("GITHUB_REF_NAME")
        head_sha = os.environ.get("GITHUB_SHA")
        if not head_branch or not head_sha:
            raise ActionError("The dispatched run did not include a branch and SHA.")
        default_branch = (payload.get("repository") or {}).get("default_branch")
        if not default_branch:
            raise ActionError(
                "The dispatched run did not include the repository default branch."
            )
        if head_branch != default_branch:
            raise ActionError(
                f"The dispatched branch {head_branch!r} is not the repository "
                f"default branch {default_branch!r}."
            )
        return {"head_branch": head_branch, "head_sha": head_sha}

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


def get_contributor_commits(prev_tag: str) -> list[ContributorCommit]:
    commits_range = f"{prev_tag}..HEAD" if prev_tag else "HEAD"
    output = run(
        ["git", "log", "-z", "--format=%H%x00%ae", commits_range],
        capture=True,
    )
    return [
        ContributorCommit(hash=fields[0], email=fields[1])
        for fields in parse_nul_records(output, 2)
    ]


def resolve_logins(
    commits: list[ContributorCommit],
    repository: str,
    env: dict[str, str],
) -> dict[str, str]:
    hashes_by_email: dict[str, list[str]] = {}
    for commit in commits:
        hashes = hashes_by_email.setdefault(commit.email, [])
        if commit.hash not in hashes:
            hashes.append(commit.hash)
    unique: dict[str, str] = {}
    unresolved = 0
    for hashes in hashes_by_email.values():
        for commit_hash in hashes[:MAX_LOGIN_LOOKUPS_PER_EMAIL]:
            if unresolved >= MAX_UNRESOLVED_LOGIN_LOOKUPS:
                raise ActionError(
                    "Cannot prepare complete contributor list after "
                    f"{MAX_UNRESOLVED_LOGIN_LOOKUPS} unresolved GitHub login lookups."
                )
            if login := get_github_login(repository, commit_hash, env=env):
                unique.setdefault(login.casefold(), login)
                break
            unresolved += 1
    return unique


def is_first_timer(
    login: str,
    prev_tag: str,
    repository: str,
    env: dict[str, str],
) -> bool:
    if not prev_tag:
        return True
    commits = run_json(
        [
            "gh",
            "api",
            f"repos/{repository}/commits",
            "--method",
            "GET",
            "-f",
            f"author={login}",
            "-F",
            "per_page=1",
            "-f",
            f"sha={prev_tag}",
        ],
        env=env,
    )
    return not commits


def first_merged_pr_url(
    login: str,
    repository: str,
    env: dict[str, str],
) -> str | None:
    pages = run_json(
        [
            "gh",
            "api",
            f"repos/{repository}/issues",
            "--method",
            "GET",
            "--paginate",
            "--slurp",
            "-f",
            f"creator={login}",
            "-f",
            "state=closed",
            "-F",
            "per_page=100",
        ],
        env=env,
    )
    first = min(
        (
            issue
            for page in pages
            for issue in page
            if issue.get("pull_request", {}).get("merged_at")
        ),
        key=lambda pr: (pr["pull_request"]["merged_at"], pr["html_url"]),
        default=None,
    )
    return str(first["html_url"]) if first else None


def collect_contributors(
    repository: str,
    env: dict[str, str],
    *,
    tag_prefix: str = "",
) -> str:
    prev_tag = get_latest_tag(prefix=tag_prefix)
    if not prev_tag and tag_prefix:
        # First release of a series: fall back to the previous series' final tag.
        prev_tag = get_latest_tag()
    commits = get_contributor_commits(prev_tag)
    if not commits:
        return ""

    unique = resolve_logins(commits, repository, env)
    if not unique:
        return ""

    entries: list[tuple[str, str | None]] = []
    for login in unique.values():
        pr_url = None
        if is_first_timer(login, prev_tag, repository, env):
            pr_url = first_merged_pr_url(login, repository, env)
        entries.append((login, pr_url))
    return render_contributors(entries)


def render_contributors(entries: list[tuple[str, str | None]]) -> str:
    lines = []
    for login, pr_url in sorted(entries, key=lambda entry: entry[0].casefold()):
        if pr_url:
            lines.append(f"* @{login} made their first contribution in <{pr_url}>")
        else:
            lines.append(f"* @{login}")
    return "\n".join(lines)


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


def news_fragment_paths(news_directory: str | Path) -> list[Path]:
    news_directory = Path(news_directory)
    if not news_directory.is_dir():
        return []
    return sorted(
        path
        for path in news_directory.iterdir()
        if path.is_file() and is_news_fragment(path, news_directory)
    )


def render_changelog_entry(
    version: str,
    release_date: str,
    fragments: dict[str, list[str]],
    contributors: str = "",
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

    if contributors:
        lines.extend([f"### {CONTRIBUTORS_SECTION}", ""])
        lines.extend(contributors.splitlines())
        lines.append("")

    return "\n".join(lines).rstrip() + "\n\n\n"


def update_changelog(path: Path, entry: str, version: str) -> None:
    if not path.is_file():
        raise ActionError(f"Changelog file does not exist: {path}")

    text = path.read_text(encoding="utf-8")
    existing = find_release_entry(text, version)
    if existing:
        updated_release = merge_changelog_entry(existing.raw, entry)
        updated = text[: existing.start] + updated_release + text[existing.end :]
    elif CURRENT_DEVELOPMENTS in text:
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
    unexpected = [path for path in paths if path not in allowed_paths]
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
        "This PR updates `CHANGELOG.md` from the news fragments and "
        "removes the consumed snippets."
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


def write_output(name: str, value: str) -> None:
    if output := os.environ.get("GITHUB_OUTPUT"):
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


if __name__ == "__main__":
    sys.exit(main())
