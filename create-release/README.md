# Create Release

Publish an immutable GitHub release for a merged release PR. The action takes
the version from the release PR branch and the notes from `CHANGELOG.md` at
the merge commit. It attaches the complete supplied asset set, then publishes
and verifies the release. GitHub creates the version tag if it does not exist.
Enable release immutability for the repository or organization before use.

```yaml
name: Release

on:
  pull_request:
    types:
      - closed
    branches:
      - main

concurrency:
  group: release-${{ github.event.pull_request.head.ref }}
  cancel-in-progress: false

permissions: {}

jobs:
  release:
    if: >-
      github.event.pull_request.merged
      && startsWith(github.event.pull_request.head.ref, 'release-notes-')
      && github.event.pull_request.head.repo.full_name == github.repository
    runs-on: ubuntu-latest
    steps:
      - name: Checkout Source
        uses: actions/checkout@<sha>
        with:
          ref: ${{ github.event.pull_request.merge_commit_sha }}
          persist-credentials: false

      - name: Build Artifacts
        run: <repository-specific build into dist/>

      - name: Create Release
        uses: conda/actions/create-release@<sha>
        with:
          token: ${{ secrets.RELEASE_TOKEN }}
          artifacts-directory: dist
```

The job must check out `github.event.pull_request.merge_commit_sha` because
the notes come from the checked-out `CHANGELOG.md`. Produce the complete
artifact directory and finish any other required work before calling this
action. It publishes the GitHub release before returning. A separate release
job can use `needs` to wait for repository-specific jobs.

`secrets.RELEASE_TOKEN` needs `contents: write`. Use a GitHub App installation
token or personal access token when publication must trigger another workflow.
You can pass `${{ github.token }}` when the job grants `contents: write` and
you do not need those downstream events.

The action re-checks these conditions before publishing:

- event is `pull_request`
- the pull request was merged
- the pull request head repository is the current repository
- the pull request base branch is the repository default branch or a
  `MAJOR.MINOR.x` release branch
- the pull request head branch starts with `branch-prefix`, followed by a
  bare `MAJOR.MINOR.PATCH` version
- the event payload includes the merge commit SHA
- the checked-out changelog has a `## <version> (<date>)` entry for a new or
  draft release

`branch-prefix` does not change the supported target branch names. Update the
workflow's `startsWith()` check when changing that prefix, and include any
release branches in the workflow's `on.pull_request.branches` filter.

## Action Inputs

| Name                  | Description                                                        | Default               |
| --------------------- | ------------------------------------------------------------------ | --------------------- |
| `changelog-path`      | Changelog file to read the release notes from.                     | `CHANGELOG.md`        |
| `branch-prefix`       | Prefix of the release PR branch that names the version to release. | `release-notes-`      |
| `artifacts-directory` | Directory holding the files to attach to the release.              |                       |
| `token`               | GitHub token with `contents: write`. Required.                     |                       |

## Action Outputs

| Name          | Description                                                |
| ------------- | ---------------------------------------------------------- |
| `version`     | Released version, taken from the merged release PR branch. |
| `commit`      | Merge commit SHA the release is created from.              |
| `release-url` | URL of the release.                                        |

## Re-runs

Enable release immutability in the repository or organization settings
before publishing. It applies only to future releases. Use a GitHub CLI
version supporting `isImmutable`, `gh release verify`, and
`gh release verify-asset`. GitHub locks the published tag and assets and
generates the release attestation automatically.

The supplied directory is the complete expected asset set, with files directly
in that directory. Restore those same files when retrying. The action compares
existing assets by SHA-256 digest, preserves matching files, and uploads only
missing files. Extra assets, different bytes, unfinished uploads, or missing
SHA-256 digests stop the action for manual inspection. It never replaces or
deletes an existing asset.

An existing tag must resolve to the merge commit. A matching tag is preserved.
A draft must target that same commit before the action updates it. Conflicting
tags and draft targets stop the action.

Failed uploads leave a draft. Before publication, the action checks the full
asset set again. It then verifies the published immutable release and each
supplied file before writing success outputs. A published retry performs the
same verification without changing the release or rereading its changelog.

A verification failure after publication cannot undo publication. Inspect the
release and retry verification with the original files. A published release
with the wrong commit or assets requires a new version.
