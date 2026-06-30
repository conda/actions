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

permissions:
  contents: write
  pull-requests: write

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
          news-directory: news
          changelog-path: CHANGELOG.md
```

The action checks the same security conditions internally before checkout:

- event is `workflow_run`
- triggering workflow concluded successfully
- triggering workflow came from a `push`
- triggering repository is the current repository
- triggering branch matches the configured release branch pattern
