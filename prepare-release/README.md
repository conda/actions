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

Before pushing, the action verifies that the triggering SHA is still the tip of
the release branch. A stale workflow run exits successfully without pushing or
creating or updating a pull request. Authentication or branch lookup failures
fail the action.

The concurrency group serializes runs for each release branch. `queue: max`
keeps every pending run, while `cancel-in-progress: false` prevents a later run
from cancelling one that is already preparing a release.
