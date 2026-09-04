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

from news_common import SECTION_ORDER, is_news_fragment, parse_sectioned_news
from release_common import (
    ActionError,
    get_github_login,
    get_latest_tag,
    parse_nul_records,
    run,
    run_json,
)

VERSION_BRANCH_RE = re.compile(r"^(?P<major_minor>\d+\.\d+)\.x$")
TAG_RE = re.compile(r"^v?(?P<version>\d+\.\d+\.(?P<micro>\d+))$")
CURRENT_DEVELOPMENTS = "[//]: # (current developments)"
SECTION_HEADING_RE = re.compile(r"^###\s+(?P<title>.+?)\s*$", re.MULTILINE)
CONTRIBUTORS_SECTION = "Contributors"
MAX_LOGIN_LOOKUPS_PER_EMAIL = 5
MAX_FAILED_LOGIN_LOOKUPS = 20


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

    news_directory = Path(args.news_directory)
    if not news_directory.is_dir():
        raise ActionError(f"News directory does not exist: {news_directory}")

    fragment_paths = news_fragment_paths(news_directory)
    if not fragment_paths:
        print(f"No news fragments found under {str(news_directory)!r}. Nothing to do.")
        return
    fragments = collect_fragments(fragment_paths)

    version = infer_next_version(base_branch)
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

    remote_ref = f"refs/heads/{base_branch}"
    remote = run(
        [
            "git",
            "ls-remote",
            "--exit-code",
            "--heads",
            "origin",
            remote_ref,
        ],
        capture=True,
        env=git_env,
    ).split()
    if len(remote) != 2 or remote[1] != remote_ref:
        raise ActionError(f"Could not determine remote head for {base_branch!r}.")
    remote_head = remote[0]
    if remote_head != context["head_sha"]:
        print(
            f"Skipping stale workflow run for {base_branch}: "
            f"{context['head_sha']} is no longer the branch tip."
        )
        return

    contributors = collect_contributors(
        args.repository,
        env=git_env,
        base_branch=base_branch,
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
    run(["gh", "auth", "setup-git"], env=git_env)

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
    failures = 0
    for hashes in hashes_by_email.values():
        for commit_hash in hashes[:MAX_LOGIN_LOOKUPS_PER_EMAIL]:
            if failures >= MAX_FAILED_LOGIN_LOOKUPS:
                print(
                    f"::warning::Skipping remaining GitHub login lookups "
                    f"after {MAX_FAILED_LOGIN_LOOKUPS} unresolved lookups.",
                    file=sys.stderr,
                )
                return unique
            if login := get_github_login(repository, commit_hash, env=env):
                unique.setdefault(login.casefold(), login)
                break
            failures += 1
    return unique


def get_tag_commit_date(tag: str) -> str:
    return run(["git", "log", "-1", "--format=%cI", tag], capture=True).strip()


def is_first_timer(
    login: str,
    prev_tag_date: str,
    repository: str,
    env: dict[str, str],
    base_branch: str,
) -> bool:
    if not prev_tag_date:
        return True
    # Known limitation: the history check filters by committer date, so a
    # rebased or cherry-picked older commit suppresses the first-timer
    # annotation even when the author is new to the release branch.
    try:
        commits = run_json(
            [
                "gh",
                "api",
                f"repos/{repository}/commits",
                "-f",
                f"author={login}",
                "-f",
                f"until={prev_tag_date}",
                "-F",
                "per_page=1",
                "-f",
                f"sha={base_branch}",
            ],
            env=env,
        )
    except ActionError as err:
        print(
            f"::warning::Failed to check commit history for {login}: {err}",
            file=sys.stderr,
        )
        return False
    return not commits


def first_merged_pr_url(
    login: str,
    repository: str,
    env: dict[str, str],
) -> str | None:
    try:
        prs = run_json(
            [
                "gh",
                "search",
                "prs",
                "--repo",
                repository,
                "--author",
                login,
                "--merged",
                "--sort",
                "created",
                "--order",
                "asc",
                "--limit",
                "1",
                "--json",
                "url",
            ],
            env=env,
        )
    except ActionError as err:
        print(
            f"::warning::Failed to look up first merged PR for {login}: {err}",
            file=sys.stderr,
        )
        return None
    if not prs:
        return None
    url = prs[0].get("url")
    return str(url) if url else None


def collect_contributors(
    repository: str,
    env: dict[str, str],
    *,
    base_branch: str,
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

    prev_tag_date = get_tag_commit_date(prev_tag) if prev_tag else ""
    entries: list[tuple[str, str | None]] = []
    for login in unique.values():
        pr_url = None
        if is_first_timer(login, prev_tag_date, repository, env, base_branch):
            pr_url = first_merged_pr_url(login, repository, env)
        entries.append((login, pr_url))
    return render_contributors(entries)


def render_contributors(entries: list[tuple[str, str | None]]) -> str:
    lines = []
    for login, pr_url in sorted(entries, key=lambda entry: entry[0].casefold()):
        if pr_url:
            lines.append(f"* @{login} made their first commit in {pr_url}")
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
    version_match = re.search(
        rf"^##\s+{re.escape(version)}\s+\([^\n]*\)\s*$",
        text,
        flags=re.MULTILINE,
    )
    if version_match:
        following = re.search(r"^##\s+", text[version_match.end() :], re.MULTILINE)
        release_end = (
            version_match.end() + following.start() if following else len(text)
        )
        release = text[version_match.start() : release_end]
        updated_release = merge_changelog_entry(release, entry)
        updated = text[: version_match.start()] + updated_release + text[release_end:]
    elif CURRENT_DEVELOPMENTS in text:
        marker_end = text.index(CURRENT_DEVELOPMENTS) + len(CURRENT_DEVELOPMENTS)
        prefix = text[:marker_end].rstrip() + "\n\n"
        suffix = text[marker_end:].lstrip("\n")
        updated = prefix + entry + suffix
    else:
        updated = entry + text.lstrip("\n")

    path.write_text(updated, encoding="utf-8")


def merge_changelog_entry(release: str, entry: str) -> str:
    entry_headings = list(SECTION_HEADING_RE.finditer(entry))
    incoming = {
        match.group("title"): entry[
            match.end() : (
                entry_headings[index + 1].start()
                if index + 1 < len(entry_headings)
                else len(entry)
            )
        ].strip()
        for index, match in enumerate(entry_headings)
        if match.group("title") in (*SECTION_ORDER, CONTRIBUTORS_SECTION)
    }

    for section in SECTION_ORDER:
        body = incoming.get(section)
        if not body:
            continue

        headings = list(SECTION_HEADING_RE.finditer(release))
        existing = next(
            (match for match in headings if match.group("title") == section),
            None,
        )
        if existing:
            index = headings.index(existing)
            section_end = (
                headings[index + 1].start()
                if index + 1 < len(headings)
                else len(release)
            )
            insert_at = len(release[:section_end].rstrip())
            separator = "\n" if release[existing.end() : insert_at].strip() else "\n\n"
            release = release[:insert_at] + separator + body + release[insert_at:]
            continue

        section_index = SECTION_ORDER.index(section)
        insert_before = next(
            (
                match
                for match in headings
                if match.group("title") not in SECTION_ORDER
                or SECTION_ORDER.index(match.group("title")) > section_index
            ),
            None,
        )
        block = f"### {section}\n\n{body}"
        if insert_before:
            release = (
                release[: insert_before.start()]
                + block
                + "\n\n"
                + release[insert_before.start() :]
            )
        else:
            insert_at = len(release.rstrip())
            release = release[:insert_at] + "\n\n" + block + release[insert_at:]

    contributors = incoming.get(CONTRIBUTORS_SECTION)
    if contributors:
        release = merge_contributors_section(release, contributors)

    return release


def merge_contributors_section(release: str, body: str) -> str:
    headings = list(SECTION_HEADING_RE.finditer(release))
    existing = next(
        (match for match in headings if match.group("title") == CONTRIBUTORS_SECTION),
        None,
    )
    if existing:
        index = headings.index(existing)
        section_end = (
            headings[index + 1].start() if index + 1 < len(headings) else len(release)
        )
        heading_end = len(release[: existing.end()].rstrip())
        trailing = release[len(release[:section_end].rstrip()) : section_end]
        return release[:heading_end] + "\n\n" + body + trailing + release[section_end:]

    insert_at = len(release.rstrip())
    block = f"### {CONTRIBUTORS_SECTION}\n\n{body}"
    return release[:insert_at] + "\n\n" + block + release[insert_at:]


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
