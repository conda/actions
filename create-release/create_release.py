from __future__ import annotations

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
        "--artifacts-directory",
        default="",
        help="Directory holding the files to attach to the release.",
    )
    return parser.parse_args(argv)


def create_release(args: Namespace) -> None:
    if not os.environ.get("GITHUB_REPOSITORY"):
        raise ActionError("No GitHub repository was provided.")
    if not os.environ.get("GH_TOKEN"):
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

    artifacts = resolve_artifacts(args.artifacts_directory)
    state = release_state(version)
    if state == "published":
        print(f"Release {version} is already published. Nothing to do.")
    else:
        if state is None and tag_exists(version):
            raise ActionError(
                f"Tag {version} already exists without a release. "
                "Delete the tag before creating the release."
            )
        if state is None:
            create_draft(version, commit, notes, artifacts)
        else:
            update_draft(version, notes, artifacts)
        publish_release(version)

    write_output("version", version)
    write_output("commit", commit)
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    release_url = f"{server}/{os.environ['GITHUB_REPOSITORY']}/releases/tag/{version}"
    write_output("release-url", release_url)


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
    payload_path = Path(event_path)
    if not payload_path.is_file():
        return {}
    return json.loads(payload_path.read_text(encoding="utf-8"))


def resolve_artifacts(directory: str) -> list[Path]:
    if not directory:
        return []
    artifacts_dir = Path(directory)
    if not artifacts_dir.is_dir():
        raise ActionError(f"Artifacts directory does not exist: {directory}")
    return sorted(child for child in artifacts_dir.iterdir() if child.is_file())


def release_state(tag: str) -> str | None:
    release = get_json_or_none(["gh", "release", "view", tag, "--json", "isDraft"])
    if release is None:
        return None
    return "draft" if release.get("isDraft") else "published"


def tag_exists(tag: str) -> bool:
    ref = get_json_or_none(["gh", "api", f"git/ref/tags/{tag}"])
    return ref is not None


def get_json_or_none(command: list[str]) -> Any:
    try:
        return run_json(command)
    except ActionError as err:
        # gh words a missing release as "release not found"
        # and a missing ref as HTTP 404.
        if not any(missing in str(err) for missing in ("HTTP 404", "not found")):
            raise
        return None


def create_draft(
    version: str,
    commit: str,
    notes: str,
    artifacts: list[Path],
) -> None:
    command = [
        "gh",
        "release",
        "create",
        version,
        "--draft",
        "--target",
        commit,
        "--title",
        version,
        f"--notes={notes}",
        *map(str, artifacts),
    ]
    run(command)


def update_draft(
    version: str,
    notes: str,
    artifacts: list[Path],
) -> None:
    run(
        [
            "gh",
            "release",
            "edit",
            version,
            "--title",
            version,
            f"--notes={notes}",
        ],
    )
    if artifacts:
        run(
            [
                "gh",
                "release",
                "upload",
                version,
                "--clobber",
                *map(str, artifacts),
            ],
        )


def publish_release(version: str) -> None:
    run(["gh", "release", "edit", version, "--draft=false"])


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
