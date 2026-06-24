"""Prepare .authors.yml updates for rever using git and the GitHub CLI."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from argparse import ArgumentParser, Namespace
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ruamel.yaml import YAML

if TYPE_CHECKING:
    from collections.abc import Callable

AuthorEntry = dict[str, Any]
AuthorIndex = dict[str, AuthorEntry]


class ActionError(Exception):
    pass


@dataclass(frozen=True)
class CommitAuthor:
    hash: str
    email: str
    name: str
    subject: str


@dataclass
class AuthorAnalysis:
    alternate_email_updates: list[tuple[dict[str, Any], CommitAuthor]]
    new_authors: list[dict[str, Any]]
    missing_github_keys: list[tuple[str, str]]
    email_to_hash: dict[str, str]
    since_label: str


def parse_args(argv: list[str] | None = None) -> Namespace:
    parser = ArgumentParser(description="Prepare .authors.yml updates for rever.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sub_check = subparsers.add_parser("check")
    add_shared_args(sub_check)

    sub_prepare = subparsers.add_parser("prepare")
    add_shared_args(sub_prepare)
    add_prepare_args(sub_prepare)

    return parser.parse_args(argv)


def add_shared_args(parser: ArgumentParser) -> None:
    parser.add_argument("--authors-path", default=".authors.yml")
    parser.add_argument(
        "--since",
        choices=["tag", "all"],
        default="tag",
        help="Scan commits since the latest tag or all history.",
    )
    parser.add_argument(
        "--git-remote",
        default="origin",
        help="Git remote alias used to resolve owner/repo for gh api.",
    )


def add_prepare_args(parser: ArgumentParser) -> None:
    parser.add_argument("--base-branch", default="main")
    parser.add_argument("--branch-prefix", default="prepare-authors-")
    parser.add_argument("--git-author-name", default="Conda Bot")
    parser.add_argument(
        "--git-author-email",
        default="18747875+conda-bot@users.noreply.github.com",
    )
    parser.add_argument(
        "--repository",
        default=os.environ.get("GITHUB_REPOSITORY", ""),
    )
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        if args.command == "check":
            check_authors(args)
        else:
            prepare_authors(args)
    except ActionError as err:
        print(f"::error::{err}", file=sys.stderr)
        return 1
    return 0


def check_authors(args: Namespace) -> None:
    authors_path = Path(args.authors_path)
    metadata, _ = load_metadata(authors_path)
    repo_full = get_repo_full(args.git_remote)
    analysis = analyze_authors(
        metadata,
        since=args.since,
        repo_full=repo_full,
        get_github_login_fn=get_github_login,
    )

    summary_lines: list[str] = []
    if analysis.missing_github_keys:
        for _, name in analysis.missing_github_keys:
            print(
                f"::warning::Author {name!r} is missing a github key "
                f"in `.authors.yml`.",
                file=sys.stderr,
            )
        summary_lines.append(
            f"Found {len(analysis.missing_github_keys)} author(s) missing github keys:"
        )
        for _, name in analysis.missing_github_keys:
            summary_lines.append(f"- {name}")

    if not analysis.alternate_email_updates and not analysis.new_authors:
        if summary_lines:
            write_summary("\n".join(summary_lines))
        else:
            write_summary("All authors are present in `.authors.yml`.")
        write_output("changed", "false")
        return

    messages: list[str] = []
    if analysis.alternate_email_updates:
        messages.append(
            f"Found {len(analysis.alternate_email_updates)} existing contributor(s) "
            "needing alternate_emails or aliases updates:"
        )
        for entry, commit in analysis.alternate_email_updates:
            messages.append(
                f"- {entry['name']}: add {commit.email!r} to alternate_emails"
            )
    if analysis.new_authors:
        messages.append(f"Found {len(analysis.new_authors)} new contributor(s):")
        for commit in analysis.new_authors:
            messages.append(f"- {commit['name']} <{commit['email']}>")

    write_summary("\n".join(summary_lines + messages))
    write_output("changed", "true")
    raise ActionError("\n".join(messages))


def prepare_authors(args: Namespace) -> None:
    if not args.token:
        raise ActionError("No GitHub token was provided.")

    authors_path = Path(args.authors_path)
    metadata, yaml_engine = load_metadata(authors_path)
    repo_full = get_repo_full(args.git_remote)
    analysis = analyze_authors(
        metadata,
        since=args.since,
        repo_full=repo_full,
        get_github_login_fn=get_github_login,
    )

    if (
        not analysis.alternate_email_updates
        and not analysis.new_authors
        and not analysis.missing_github_keys
    ):
        write_summary("`.authors.yml` is already complete.")
        write_output("changed", "false")
        print("No author metadata changes to commit.")
        return

    changed = apply_updates(
        metadata,
        analysis,
        repo_full=repo_full,
        get_github_login_fn=get_github_login,
    )
    if not changed:
        write_summary("`.authors.yml` is already complete.")
        write_output("changed", "false")
        print("No author metadata changes to commit.")
        return

    save_metadata(metadata, yaml_engine, authors_path)
    ensure_allowed_paths(get_changed_paths(), authors_path=authors_path)

    branch = f"{args.branch_prefix}{args.base_branch}"
    run(["git", "checkout", "-B", branch])
    run(["git", "config", "user.name", args.git_author_name])
    run(["git", "config", "user.email", args.git_author_email])

    git_env = os.environ | {"GH_TOKEN": args.token}
    run(["git", "add", str(authors_path)])
    run(["git", "commit", "-m", "Update .authors.yml"])
    run(["gh", "auth", "setup-git"], env=git_env)
    run(
        ["git", "push", "--force-with-lease", args.git_remote, branch],
        env=git_env,
    )

    url = create_or_update_pr(
        repository=args.repository,
        branch=branch,
        base_branch=args.base_branch,
        since_label=analysis.since_label,
        token=args.token,
    )

    write_output("changed", "true")
    write_output("branch", branch)
    write_output("pull-request-url", url)
    write_summary(f"Updated `.authors.yml` and opened PR: {url}")
    print(f"Prepared author metadata updates: {url}")


def analyze_authors(
    metadata: list[dict[str, Any]],
    *,
    since: str,
    repo_full: str,
    get_github_login_fn: Callable[[str, str], str | None],
) -> AuthorAnalysis:
    _, by_names, by_github, last_github_index, known_emails = build_author_indexes(
        metadata
    )

    commits, since_label = get_commits_since(since)
    email_to_hash = {commit.email: commit.hash for commit in commits}
    alternate_email_updates, new_authors = classify_commits(
        commits,
        known_emails,
        by_names,
        by_github,
        repo_full,
        get_github_login_fn,
    )

    missing_github_keys: list[tuple[str, str]] = []
    if last_github_index is not None:
        for i in range(last_github_index + 1, len(metadata)):
            entry = metadata[i]
            if "github" not in entry:
                missing_github_keys.append((entry["email"], entry["name"]))

    return AuthorAnalysis(
        alternate_email_updates=alternate_email_updates,
        new_authors=new_authors,
        missing_github_keys=missing_github_keys,
        email_to_hash=email_to_hash,
        since_label=since_label,
    )


def apply_updates(
    metadata: list[dict[str, Any]],
    analysis: AuthorAnalysis,
    *,
    repo_full: str,
    get_github_login_fn: Callable[[str, str], str | None],
) -> bool:
    changed = False

    for entry, commit in analysis.alternate_email_updates:
        if update_existing_entry(entry, commit.email, commit.name):
            changed = True

    for commit in analysis.new_authors:
        new_entry: dict[str, Any] = {
            "name": commit["name"],
            "email": commit["email"],
        }
        if commit.get("github"):
            new_entry["github"] = commit["github"]
        metadata.append(new_entry)
        changed = True

    for email, _name in analysis.missing_github_keys:
        commit_hash = analysis.email_to_hash.get(email)
        github_login = None
        if commit_hash and repo_full:
            github_login = get_github_login_fn(repo_full, commit_hash)
        if github_login:
            for entry in metadata:
                if entry["email"] == email:
                    entry["github"] = github_login
                    changed = True
                    break

    return changed


def load_metadata(
    filename: Path | str = ".authors.yml",
) -> tuple[list[dict[str, Any]], YAML]:
    path = Path(filename)
    yaml_engine = YAML()
    yaml_engine.preserve_quotes = True
    if path.is_file():
        with path.open(encoding="utf-8") as handle:
            metadata = yaml_engine.load(handle)
    else:
        metadata = []
    if metadata is None:
        metadata = []
    return metadata, yaml_engine


def save_metadata(
    metadata: list[dict[str, Any]],
    yaml_engine: YAML,
    filename: Path | str,
) -> None:
    path = Path(filename)
    with path.open("w", encoding="utf-8") as handle:
        yaml_engine.dump(metadata, handle)


def build_author_indexes(
    metadata: list[AuthorEntry],
) -> tuple[AuthorIndex, AuthorIndex, AuthorIndex, int | None, set[str]]:
    by_emails: dict[str, dict[str, Any]] = {}
    by_names: dict[str, dict[str, Any]] = {}
    by_github: dict[str, dict[str, Any]] = {}
    last_github_index: int | None = None

    for i, entry in enumerate(metadata):
        by_emails[entry["email"]] = entry
        for alt in entry.get("alternate_emails", []):
            by_emails[alt] = entry
        by_names[entry["name"]] = entry
        for alias in entry.get("aliases", []):
            by_names[alias] = entry
        if "github" in entry:
            by_github[entry["github"]] = entry
            last_github_index = i

    return by_emails, by_names, by_github, last_github_index, set(by_emails.keys())


def find_existing_entry(
    name: str,
    github_login: str | None,
    by_names: dict[str, dict[str, Any]],
    by_github: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    if name in by_names:
        return by_names[name]
    if github_login and github_login in by_github:
        return by_github[github_login]
    return None


def update_existing_entry(entry: dict[str, Any], email: str, name: str) -> bool:
    changed = False
    if email != entry["email"]:
        alt_emails = entry.setdefault("alternate_emails", [])
        if email not in alt_emails:
            alt_emails.append(email)
            changed = True
    if name != entry["name"]:
        aliases = entry.setdefault("aliases", [])
        if name not in aliases:
            aliases.append(name)
            changed = True
    return changed


def classify_commits(
    commits: list[CommitAuthor],
    known_emails: set[str],
    by_names: dict[str, dict[str, Any]],
    by_github: dict[str, dict[str, Any]],
    repo_full: str,
    get_github_login_fn: Callable[[str, str], str | None],
) -> tuple[list[tuple[dict[str, Any], CommitAuthor]], list[dict[str, Any]]]:
    seen: set[str] = set()
    alternate_email_updates: list[tuple[dict[str, Any], CommitAuthor]] = []
    new_authors: list[dict[str, Any]] = []

    for commit in commits:
        if commit.email in known_emails or commit.email in seen:
            continue
        seen.add(commit.email)

        github_login = None
        if repo_full:
            github_login = get_github_login_fn(repo_full, commit.hash)

        entry = find_existing_entry(
            commit.name,
            github_login,
            by_names,
            by_github,
        )
        if entry is not None:
            alternate_email_updates.append((entry, commit))
            known_emails.add(commit.email)
        else:
            commit_data: dict[str, Any] = {
                "hash": commit.hash,
                "email": commit.email,
                "name": commit.name,
                "subject": commit.subject,
            }
            if github_login:
                commit_data["github"] = github_login
            new_authors.append(commit_data)

    return alternate_email_updates, new_authors


def get_latest_tag() -> str:
    output = run(["git", "tag", "--sort=-version:refname"], capture=True)
    return output.splitlines()[0] if output else ""


def get_commits_since(since: str) -> tuple[list[CommitAuthor], str]:
    if since == "all":
        commits_range = "HEAD"
        since_label = "all commits"
    elif since == "tag":
        tag = get_latest_tag()
        if not tag:
            print("No tags found; skipping author scan.")
            return [], "no tags"
        print(f"Checking commits since tag: {tag}")
        commits_range = f"{tag}..HEAD"
        since_label = f"tag {tag}"
    else:
        commits_range = f"{since}..HEAD"
        since_label = since

    output = run(
        ["git", "log", "--format=%H%n%ae%n%an%n%s%n---", commits_range],
        capture=True,
    )
    commits: list[CommitAuthor] = []
    if not output:
        return commits, since_label

    for chunk in output.split("---\n"):
        lines = chunk.strip().split("\n")
        if len(lines) >= 4:
            commits.append(
                CommitAuthor(
                    hash=lines[0],
                    email=lines[1],
                    name=lines[2],
                    subject=lines[3],
                )
            )
    return commits, since_label


def get_github_login(repo: str, commit_hash: str) -> str | None:
    try:
        data = run_json(
            ["gh", "api", f"repos/{repo}/commits/{commit_hash}"],
        )
    except ActionError:
        return None
    author = data.get("author") or {}
    login = author.get("login")
    return str(login) if login else None


def get_repo_full(remote_alias: str) -> str:
    result = subprocess.run(
        ["git", "remote", "get-url", remote_alias],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    url = result.stdout.strip()
    match = re.search(r"[:/]([^/]+)/([^/]+?)(?:\.git)?$", url)
    if match:
        return f"{match.group(1)}/{match.group(2)}"
    return ""


def get_changed_paths() -> list[Path]:
    status = run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        capture=True,
    )
    return [Path(line[3:]) for line in status.splitlines() if line]


def ensure_allowed_paths(paths: list[Path], *, authors_path: Path) -> None:
    unexpected = [path for path in paths if path != authors_path]
    if unexpected:
        raise ActionError(
            "prepare-authors produced unexpected file changes: "
            + ", ".join(str(path) for path in unexpected)
        )


def create_or_update_pr(
    *,
    repository: str,
    branch: str,
    base_branch: str,
    since_label: str,
    token: str,
) -> str:
    if not repository:
        raise ActionError("No GitHub repository was provided.")
    if not token:
        raise ActionError("No GitHub token was provided.")

    env = os.environ | {"GH_TOKEN": token}
    title = "Update .authors.yml"
    body = (
        "Update author metadata in `.authors.yml`.\n\n"
        f"Scanned commits since {since_label}."
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
    return result.stdout.strip() if capture else ""


def run_json(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    output = run(command, capture=True, env=env)
    try:
        return json.loads(output)
    except json.JSONDecodeError as err:
        raise ActionError(f"Failed to parse JSON from: {' '.join(command)}") from err


def write_output(name: str, value: str) -> None:
    if output := os.environ.get("GITHUB_OUTPUT"):
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def write_summary(message: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write(message)
            handle.write("\n")


if __name__ == "__main__":
    sys.exit(main())
