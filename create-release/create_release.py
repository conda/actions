from __future__ import annotations

import glob
import json
import os
import re
import sys
from argparse import ArgumentParser, Namespace
from pathlib import Path
from typing import Any

from conda_actions.commands import ActionError, run, run_json

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def parse_args(argv: list[str] | None = None) -> Namespace:
    parser = ArgumentParser(
        description="Publish a GitHub release for a merged release PR."
    )
    parser.add_argument("--changelog-path", default="CHANGELOG.md")
    parser.add_argument("--branch-prefix", default="release-notes-")
    parser.add_argument(
        "--artifacts",
        default="",
        help="Newline-separated glob patterns of files to attach to the release.",
    )
    parser.add_argument(
        "--repository",
        default=os.environ.get("GITHUB_REPOSITORY", ""),
    )
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
    return parser.parse_args(argv)


def create_release(args: Namespace) -> None:
    if not args.repository:
        raise ActionError("No GitHub repository was provided.")
    if not args.token:
        raise ActionError("No GitHub token was provided.")

    context = verify_context(args.branch_prefix)
    version = context["version"]
    commit = context["commit"]

    changelog = Path(args.changelog_path)
    if not changelog.is_file():
        raise ActionError(f"Changelog file does not exist: {changelog}")
    notes = release_notes(changelog.read_text(encoding="utf-8"), version)
    if notes is None:
        raise ActionError(f"No release notes for {version} in {changelog}.")
    if not notes:
        raise ActionError(f"Release notes for {version} are empty.")

    artifacts = resolve_artifacts(args.artifacts)
    env = os.environ | {"GH_TOKEN": args.token}
    state = release_state(args.repository, version, env)
    if state == "published":
        print(f"Release {version} is already published. Nothing to do.")
    else:
        if state is None and tag_exists(args.repository, version, env):
            raise ActionError(
                f"Tag {version} already exists without a release. "
                "Delete the tag before creating the release."
            )
        if state is None:
            create_draft(args.repository, version, commit, notes, artifacts, env)
        else:
            update_draft(args.repository, version, notes, artifacts, env)
        publish_release(args.repository, version, env)

    write_output("version", version)
    write_output("commit", commit)
    write_output("release-url", release_url(args.repository, version))


def verify_context(branch_prefix: str) -> dict[str, str]:
    if os.environ.get("GITHUB_EVENT_NAME") != "pull_request":
        raise ActionError("create-release must run from the pull_request event.")

    payload = load_event_payload()
    pull_request = payload.get("pull_request") or {}
    if pull_request.get("merged") is not True:
        raise ActionError("The pull request was not merged.")

    head_repository = (pull_request.get("head") or {}).get("repo") or {}
    if head_repository.get("full_name") != os.environ.get("GITHUB_REPOSITORY"):
        raise ActionError("The merged pull request must come from this repository.")

    default_branch = (payload.get("repository") or {}).get("default_branch")
    if not default_branch:
        raise ActionError(
            "The event payload did not include the repository default branch."
        )
    base_branch = (pull_request.get("base") or {}).get("ref")
    if base_branch != default_branch:
        raise ActionError(
            f"The pull request base branch {base_branch!r} is not the repository "
            f"default branch {default_branch!r}."
        )

    head_branch = (pull_request.get("head") or {}).get("ref") or ""
    if not head_branch.startswith(branch_prefix):
        raise ActionError(
            f"The pull request branch {head_branch!r} does not start with "
            f"{branch_prefix!r}."
        )
    version = head_branch.removeprefix(branch_prefix)
    if not VERSION_RE.match(version):
        raise ActionError(
            f"Invalid release version {version!r} in branch {head_branch!r}, "
            "expected MAJOR.MINOR.PATCH without a leading 'v'."
        )

    commit = pull_request.get("merge_commit_sha")
    if not commit:
        raise ActionError("The merged pull request did not include a merge commit SHA.")
    return {"version": version, "commit": commit}


def release_notes(changelog: str, version: str) -> str | None:
    heading = re.search(
        rf"^##\s+{re.escape(version)}\s+\([^)\n]*\)\s*$",
        changelog,
        flags=re.MULTILINE,
    )
    if not heading:
        return None

    following = re.search(r"^##\s+", changelog[heading.end() :], re.MULTILINE)
    end = heading.end() + following.start() if following else len(changelog)
    return changelog[heading.end() : end].strip()


def load_event_payload() -> dict[str, Any]:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return {}
    path = Path(event_path)
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_artifacts(artifacts: str) -> list[Path]:
    patterns = [line.strip() for line in artifacts.splitlines() if line.strip()]
    matched = {
        Path(match)
        for pattern in patterns
        for match in glob.glob(pattern, recursive=True)
    }
    return sorted(path for path in matched if path.is_file())


def release_state(repository: str, tag: str, env: dict[str, str]) -> str | None:
    release = get_json_or_none(
        ["gh", "api", f"repos/{repository}/releases/tags/{tag}"],
        env,
    )
    if release is None:
        return None
    return "draft" if release.get("isDraft") else "published"


def tag_exists(repository: str, tag: str, env: dict[str, str]) -> bool:
    ref = get_json_or_none(["gh", "api", f"repos/{repository}/git/ref/tags/{tag}"], env)
    return ref is not None


def get_json_or_none(command: list[str], env: dict[str, str]) -> Any:
    try:
        return run_json(command, env=env)
    except ActionError as err:
        if "HTTP 404" not in str(err):
            raise
        return None


def create_draft(
    repository: str,
    version: str,
    commit: str,
    notes: str,
    artifacts: list[Path],
    env: dict[str, str],
) -> None:
    command = [
        "gh",
        "release",
        "create",
        version,
        "--draft",
        "--repo",
        repository,
        "--target",
        commit,
        "--title",
        version,
        f"--notes={notes}",
        *map(str, artifacts),
    ]
    run(command, env=env)


def update_draft(
    repository: str,
    version: str,
    notes: str,
    artifacts: list[Path],
    env: dict[str, str],
) -> None:
    run(
        [
            "gh",
            "release",
            "edit",
            version,
            "--repo",
            repository,
            "--title",
            version,
            f"--notes={notes}",
        ],
        env=env,
    )
    if artifacts:
        run(
            [
                "gh",
                "release",
                "upload",
                version,
                "--repo",
                repository,
                "--clobber",
                *map(str, artifacts),
            ],
            env=env,
        )


def publish_release(repository: str, version: str, env: dict[str, str]) -> None:
    run(
        ["gh", "release", "edit", version, "--repo", repository, "--draft=false"],
        env=env,
    )


def release_url(repository: str, version: str) -> str:
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    return f"{server}/{repository}/releases/tag/{version}"


def write_output(name: str, value: str) -> None:
    if output := os.environ.get("GITHUB_OUTPUT"):
        with Path(output).open("a", encoding="utf-8") as gho:
            gho.write(f"{name}={value}\n")


def main(argv: list[str] | None = None) -> int:
    try:
        create_release(parse_args(argv))
    except ActionError as err:
        print(f"::error::{err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
