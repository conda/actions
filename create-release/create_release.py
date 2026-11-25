from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from argparse import ArgumentParser, Namespace
from pathlib import Path
from typing import Any
from urllib.parse import quote

from conda_actions.changelog import find_release_entry
from conda_actions.commands import ActionError, run, run_json

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
RELEASE_BRANCH_RE = re.compile(r"^\d+\.\d+\.x$")


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

    artifacts = resolve_artifacts(args.artifacts_directory)
    digests = {artifact.name: file_digest(artifact) for artifact in artifacts}
    release = get_release(version)
    if release is not None and not release["isDraft"]:
        verify_published(release, version, commit, artifacts, digests)
        print(f"Release {version} is already published and matches these files.")
    else:
        verify_source(version, commit, release)
        changelog = Path(args.changelog_path)
        if not changelog.is_file():
            raise ActionError(f"Changelog file does not exist: {changelog}")
        notes = release_notes_from_changelog(
            changelog.read_text(encoding="utf-8"), version
        )
        if notes is None:
            raise ActionError(f"No release notes for {version} in {changelog}.")
        if not notes:
            raise ActionError(f"Release notes for {version} are empty.")

        if release is None:
            create_draft(version, commit, notes, artifacts)
        else:
            missing = missing_artifacts(release, digests)
            update_draft(
                version,
                commit,
                notes,
                [artifact for artifact in artifacts if artifact.name in missing],
            )
        release = get_release(version)
        if release is None or not release["isDraft"]:
            raise ActionError(
                "The release changed before publication. Retry the action."
            )
        verify_source(version, commit, release)
        if missing_artifacts(release, digests):
            raise ActionError("The draft is missing release assets.")
        publish_release(version)
        release = get_release(version)
        if release is None:
            raise ActionError("The published release could not be read.")
        verify_published(release, version, commit, artifacts, digests)

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

    base_branch = (pull_request.get("base") or {}).get("ref", "")
    if not RELEASE_BRANCH_RE.match(base_branch):
        default_branch = (payload.get("repository") or {}).get("default_branch")
        if not default_branch:
            raise ActionError(
                "The event payload did not include the repository default branch."
            )
        if base_branch != default_branch:
            raise ActionError(
                f"The pull request base branch {base_branch!r} is not the repository "
                f"default branch {default_branch!r} or a release branch."
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


def release_notes_from_changelog(changelog: str, version: str) -> str | None:
    entry = find_release_entry(changelog, version)
    return entry.body if entry else None


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
    artifacts = sorted(child for child in artifacts_dir.iterdir() if child.is_file())
    if not artifacts:
        raise ActionError(f"Artifacts directory contains no files: {directory}")
    return artifacts


def get_release(tag: str) -> dict[str, Any] | None:
    return get_json_or_none(
        [
            "gh",
            "release",
            "view",
            tag,
            "--repo",
            os.environ["GITHUB_REPOSITORY"],
            "--json",
            "databaseId,isDraft,isImmutable,targetCommitish",
        ]
    )


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def missing_artifacts(release: dict[str, Any], digests: dict[str, str]) -> set[str]:
    endpoint = (
        f"repos/{os.environ['GITHUB_REPOSITORY']}/releases/"
        f"{release['databaseId']}/assets?per_page=100"
    )
    pages = run_json(["gh", "api", "--paginate", "--slurp", endpoint])
    assets = [asset for page in pages for asset in page]
    names = {asset["name"] for asset in assets}
    unexpected = names - digests.keys()
    if unexpected:
        raise ActionError(
            f"The release has unexpected assets: {', '.join(sorted(unexpected))}"
        )
    for asset in assets:
        name = asset["name"]
        if asset["state"] != "uploaded":
            raise ActionError(f"Release asset {name!r} has not finished uploading.")
        digest = asset.get("digest")
        if not digest or not digest.startswith("sha256:"):
            raise ActionError(
                f"Release asset {name!r} has no SHA-256 digest and cannot be verified."
            )
        if digest != digests[name]:
            raise ActionError(f"Release asset {name!r} differs from the local file.")
    return digests.keys() - names


def verify_source(version: str, commit: str, release: dict[str, Any] | None) -> None:
    tagged_commit = tag_commit(version)
    if tagged_commit is not None and tagged_commit != commit:
        raise ActionError(f"Tag {version} points to a different commit.")
    if release is None:
        return
    if not release["isDraft"]:
        if tagged_commit is None:
            raise ActionError(f"Published release {version} has no matching tag.")
        return
    target = release["targetCommitish"]
    if target != commit:
        endpoint = (
            f"repos/{os.environ['GITHUB_REPOSITORY']}/commits/{quote(target, safe='')}"
        )
        if run_json(["gh", "api", endpoint])["sha"] != commit:
            raise ActionError(f"Draft release {version} targets a different commit.")


def verify_published(
    release: dict[str, Any],
    version: str,
    commit: str,
    artifacts: list[Path],
    digests: dict[str, str],
) -> None:
    if release["isDraft"] or not release["isImmutable"]:
        raise ActionError("The release is not published with immutability enabled.")
    verify_source(version, commit, release)
    if missing_artifacts(release, digests):
        raise ActionError("The published release is missing release assets.")
    repository = os.environ["GITHUB_REPOSITORY"]
    run(["gh", "release", "verify", version, "--repo", repository])
    for artifact in artifacts:
        run(
            [
                "gh",
                "release",
                "verify-asset",
                version,
                str(artifact),
                "--repo",
                repository,
            ]
        )


def tag_commit(tag: str) -> str | None:
    repository = os.environ["GITHUB_REPOSITORY"]
    endpoint = f"repos/{repository}/git/ref/tags/{quote(tag, safe='')}"
    if get_json_or_none(["gh", "api", endpoint]) is None:
        return None
    reference = quote(f"refs/tags/{tag}", safe="")
    return run_json(["gh", "api", f"repos/{repository}/commits/{reference}"])["sha"]


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
        "--repo",
        os.environ["GITHUB_REPOSITORY"],
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
    commit: str,
    notes: str,
    artifacts: list[Path],
) -> None:
    repository = os.environ["GITHUB_REPOSITORY"]
    run(
        [
            "gh",
            "release",
            "edit",
            version,
            "--repo",
            repository,
            "--target",
            commit,
            "--title",
            version,
            f"--notes={notes}",
        ]
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
                *map(str, artifacts),
            ]
        )


def publish_release(version: str) -> None:
    run(
        [
            "gh",
            "release",
            "edit",
            version,
            "--repo",
            os.environ["GITHUB_REPOSITORY"],
            "--draft=false",
        ]
    )


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
