# Prepare Release

Generate release notes from conda news fragments and open or update a release
PR. The action is intended to run on pushes to release branches, independent of
the test workflow's result, or from a `workflow_dispatch` run with an explicit
`version` input for on-demand release projects.

```yaml
name: Prepare release notes

on:
  push:
    branches:
      - '[0-9]*.[0-9]*.x'
  workflow_dispatch:
    inputs:
      version:
        description: Release version to prepare (MAJOR.MINOR.PATCH).
        required: true
        type: string

concurrency:
  group: ${{ github.workflow }}-${{ github.ref_name }}
  queue: max
  cancel-in-progress: false

permissions:
  contents: read

jobs:
  prepare:
    runs-on: ubuntu-latest
    steps:
      - uses: conda/actions/prepare-release@main
        with:
          token: ${{ secrets.BOT_TOKEN }}
          news-directory: news
          changelog-path: CHANGELOG.md
          version: ${{ inputs.version }}
```

Release notes are prepared whenever commits land on a release branch. Test
failures do not block the release notes PR; they remain visible through normal
CI reporting.

Because the action's write token is exercised on every release-branch push,
confirm that branch protection (or a repository ruleset) covers the release
branch pattern so only trusted commits can reach those branches.

The action checks the following conditions internally before checkout:

- event is `push` (or `workflow_dispatch` when a `version` is given)
- for `push`, the push is to a branch (not a tag)
- for `push`, the pushed branch matches the configured release branch pattern
- for `workflow_dispatch`, the `version` input matches `MAJOR.MINOR.PATCH`
- for `workflow_dispatch`, the run was dispatched from a branch (not a tag)
- for `workflow_dispatch`, the dispatched branch is the repository default
  branch

A dispatched run must be started from the repository default branch (usually
`main`), which becomes the base branch for the release PR, and uses the
explicit `version` instead of deriving one from a release branch.

An existing news directory with no eligible fragments is a successful no-op.
A missing news directory or a malformed fragment fails the action.

## Action Inputs

| Name | Description | Default |
|------|-------------|---------|
| `news-directory` | Directory containing news fragments. | `news` |
| `changelog-path` | Changelog file to update. | `CHANGELOG.md` |
| `release-branch-pattern` | Comma- or newline-separated release branch glob patterns. | `[0-9]*.[0-9]*.x` |
| `version` | Release version (`MAJOR.MINOR.PATCH`) for `workflow_dispatch` runs; ignored for `push` runs. | |
| `branch-prefix` | Prefix for the generated release-notes branch. | `release-notes-` |
| `git-author-name` | Git author name for the generated commit. | `Conda Bot` |
| `git-author-email` | Git author email for the generated commit. | `18747875+conda-bot@users.noreply.github.com` |
| `token` | GitHub token with `contents: write` and `pull-requests: write`. | `${{ github.token }}` |

The token also needs `issues: read` when used with a private repository, because
first-contribution lookups use the repository issues API.

## Action Outputs

| Name | Description |
|------|-------------|
| `version` | Release version inferred from the release branch or given via the `version` input. |
| `branch` | Generated release-notes branch. |
| `pull-request-url` | Generated or updated release PR URL. |

Each generated entry ends with a `Contributors` section listing the GitHub
logins of commit authors since the previous release tag, sorted
alphabetically. Contributors with no commits in the previous release tag's
history are marked as first-time contributors with a link to their earliest
merged PR. The section is omitted when no author resolves to a
GitHub login. A failed contributor lookup or reaching the lookup limit with
authors still unchecked stops the action before it changes the changelog or
removes news fragments. Successful lookups regenerate the contributors section
when updating an existing release entry:

```
### Contributors

* @alice made their first contribution in <https://github.com/conda/conda/pull/123>
* @bob
* @dependabot[bot]
```

Before pushing, the action verifies that the triggering SHA is still the tip
of the release branch. A stale run exits successfully without pushing
or creating or updating a pull request. Authentication or branch lookup
failures fail the action.

Each run regenerates the release-notes branch from the current base commit and
force-pushes it, so re-running the action for the same version discards any
commits added to the PR branch after the previous run (e.g., contributors
changes); re-run those steps afterwards.
