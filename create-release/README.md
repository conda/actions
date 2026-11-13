# Create Release

Publish a GitHub release for a merged release PR. The action takes the version
from the release PR branch and the release notes from `CHANGELOG.md` at the
merge commit. It attaches any artifacts, then publishes the release, which
creates the version tag.

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
    permissions:
      contents: write
    steps:
      - name: Checkout Source
        uses: actions/checkout@<sha>
        with:
          ref: ${{ github.event.pull_request.merge_commit_sha }}
          persist-credentials: false

      - name: Build Artifacts
        run: <repository-specific build into dist/>

      - name: Create Release
        uses: conda/actions/create-release@main
        with:
          token: ${{ secrets.RELEASE_TOKEN }}
          artifacts: dist/*
```

The job must check out `github.event.pull_request.merge_commit_sha`. The notes
come from the checked-out `CHANGELOG.md`, and the tip of the default branch may
have moved past the release commit.

`secrets.RELEASE_TOKEN` must be a personal access token. Releases published
with `github.token` do not start workflows that subscribe to the `release`
event, such as the release upload or merge-back workflows.

The action re-checks these conditions before publishing:

- event is `pull_request`
- the pull request was merged
- the pull request head repository is the current repository
- the pull request base branch is the repository default branch
- the pull request branch matches the configured prefix and names a
  `MAJOR.MINOR.PATCH` version
- the event payload includes the merge commit SHA
- the checked-out changelog has a `## <version> (<date>)` entry

## Action Inputs

| Name             | Description                                                        | Default               |
| ---------------- | ------------------------------------------------------------------ | --------------------- |
| `changelog-path` | Changelog file to read the release notes from.                     | `CHANGELOG.md`        |
| `branch-prefix`  | Prefix of the release PR branch that names the version to release. | `release-notes-`      |
| `artifacts`      | Newline-separated glob patterns of files to attach to the release. |                       |
| `token`          | Token with `contents: write`.                                      | `${{ github.token }}` |

## Action Outputs

| Name          | Description                                                |
| ------------- | ---------------------------------------------------------- |
| `version`     | Released version, taken from the merged release PR branch. |
| `commit`      | Merge commit SHA the release is created from.              |
| `release-url` | URL of the release.                                        |

## Re-runs

A published release stops the action with a notice and is never modified, so
re-running the workflow after publication is safe. A draft release is updated
in place: its notes are refreshed and its assets are re-uploaded with
`--clobber`. If a tag exists without a release, the action fails before
publishing, since publishing cannot move an existing tag.

Assets are uploaded while the release is a draft. A failed upload leaves a
fixable draft instead of a published release with missing assets, which
immutable releases would lock. Publishing happens only after every upload
succeeds.
