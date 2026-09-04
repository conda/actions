# Prepare Release

Generate release notes from conda news fragments and open or update a release
PR. The action is intended to run from a trusted `workflow_run` event after the
test workflow succeeds on a protected release branch.

```yaml
name: Prepare release notes

on:
  workflow_run:
    workflows: [Tests]
    types: [completed]
    branches:
      - '[0-9]*.[0-9]*.x'

concurrency:
  group: ${{ github.workflow }}-${{ github.event.workflow_run.head_branch }}
  queue: max
  cancel-in-progress: false

permissions:
  contents: read

jobs:
  prepare:
    if: >-
      github.event.workflow_run.conclusion == 'success'
      && github.event.workflow_run.event == 'push'
      && github.event.workflow_run.head_repository.full_name == github.repository
    runs-on: ubuntu-latest
    steps:
      - uses: conda/actions/prepare-release@main
        with:
          token: ${{ secrets.BOT_TOKEN }}
          news-directory: news
          changelog-path: CHANGELOG.md
```

The action checks the same security conditions internally before checkout:

- event is `workflow_run`
- triggering workflow concluded successfully
- triggering workflow came from a `push`
- triggering repository is the current repository
- triggering branch matches the configured release branch pattern

An existing news directory with no eligible fragments is a successful no-op.
A missing news directory or a malformed fragment fails the action.

## Action Inputs

| Name | Description | Default |
|------|-------------|---------|
| `news-directory` | Directory containing news fragments. | `news` |
| `changelog-path` | Changelog file to update. | `CHANGELOG.md` |
| `release-branch-pattern` | Comma- or newline-separated release branch glob patterns. | `[0-9]*.[0-9]*.x` |
| `branch-prefix` | Prefix for the generated release-notes branch. | `release-notes-` |
| `git-author-name` | Git author name for the generated commit. | `Conda Bot` |
| `git-author-email` | Git author email for the generated commit. | `18747875+conda-bot@users.noreply.github.com` |
| `token` | GitHub token with `contents: write` and `pull-requests: write`. | `${{ github.token }}` |

## Action Outputs

| Name | Description |
|------|-------------|
| `version` | Release version inferred from the release branch. |
| `branch` | Generated release-notes branch. |
| `pull-request-url` | Generated or updated release PR URL. |

Each generated entry ends with a `Contributors` section listing the GitHub
logins of commit authors since the previous release tag, sorted
alphabetically. First-time contributors are annotated with a link to their
earliest merged PR. The section is omitted when no author resolves to a
GitHub login:

```
### Contributors

* @alice made their first commit in https://github.com/conda/conda/pull/123
* @bob
* @dependabot[bot]
```

Before pushing, the action verifies that the triggering SHA is still the tip
of the release branch. A stale workflow run exits successfully without pushing
or creating or updating a pull request. Authentication or branch lookup
failures fail the action.
