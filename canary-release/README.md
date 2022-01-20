# Canary release

This is a custom GitHub action to be used in the conda GitHub organization
for doing development/canary releases to anaconda.org.

## GitHub Action Usage

In your GitHub repository include the action in your workflows,
e.g. for doing canary release for when changes are merged into the main
branch:

```yaml
name: Canary builds

on:
  workflow_run:
    workflows:
      - CI tests
    branches:
      - main
    types:
      - completed

jobs:
  build:
    if: github.event.workflow_run.conclusion == 'success'
    strategy:
      matrix:
        include:
          - runner: ubuntu-latest
            subdir: linux-64
          - runner: macos-latest
            subdir: osx-64
          - runner: windows-latest
            subdir: win-64

    runs-on: ${{ matrix.runner }}

    steps:
      - uses: actions/checkout@v2
        with:
          ref: ${{ github.ref }}
          clean: true
          fetch-depth: 0

      - name: Create and upload canary build
        uses: conda/actions/canary-release
        with:
          # [required]
          # the pull request ID
          pr: ${{ github.event.number }}

          # [required]
          # the package name to be build and released
          package-name: conda

          # [required]
          # the subdiretory, e.g. linux-64
          subdir: ${{ matrix.subdir }}

          # [required]
          # the anaconda.org channel
          anaconda-org-channel: conda-canary
          # [required]
          # the anaconda.org label to apply
          anaconda-org-label: dev
          # [required]
          # the anaconda.org token to upload to the channel
          anaconda-org-token: ${{ secrets.CANARY_ANACONDA_ORG_TOKEN }}

          # [optional]
          # the GitHub Personal Access Token to comment with
          # comment-token: ${{ secrets.CANARY_ACTION_COMMENT_TOKEN }}
          # [optional]
          # the GitHub user account that comment with
          # comment-author: conda-bot
```
