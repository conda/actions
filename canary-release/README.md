# Canary release

This is a custom GitHub action to be used in the conda GitHub organization
for doing development/canary releases to anaconda.org or a Github repository releases.

## Channel backends

Two channel backends are provided:

- A channel at anaconda.org, to be configured with `anaconda-org-channel`, `anaconda-org-label` and `anaconda-org-token`. The final channel can be accessed via `https://conda.anaconda.org/{anaconda-org-channel}/label/{anaconda-org-label}`.
- A channel backed by Github Releases, to be configured by `github-releases-repository`, `github-releases-channel-name`, `github-releases-token`.
  - Each subdir will be backed by a release tag in the target repository.
  - The final channel can be accessed via `https://github.com/{github-releases-repository}/releases/download/{github-releases-channel-name}`.
  - Github limits each release to 1000 artifacts, 2GB max each. That said, you should only upload only a few artifacts per channel for indexing performance. For example, use a timestamped `github-releases-channel-name` value like `canary-{package-name}-{YYYY-MM-DD}`

## Windows ARM64

Use `subdir: win-arm64` and `base-architecture: x64` on a `windows-11-arm`
runner. The action runs x64 Miniconda and `conda-build` under emulation and
sets `target_platform` to `win-arm64`. Packages target ARM64, and recipe
tests can run ARM64 executables natively on the runner. A native Miniconda
installer is not required.

The recipe must support `win-arm64`, with dependencies available for that
platform from the selected channels. The action does not pin the recipe's
Python version or select conda-forge automatically. For example, the ARM64
smoke recipe requires Python 3.14, and its workflow passes
`conda-build-arguments: --override-channels -c conda-forge`.

## GitHub Action Usage

The `upload` input defaults to `'true'` on every platform. Set it to `'false'`
to build and test without publishing packages. When uploads are disabled,
no upload backend configuration or credentials are required.

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
            subdir: osx-arm64
          - runner: windows-latest
            subdir: win-64
          - runner: windows-11-arm
            subdir: win-arm64
            base-architecture: x64
            conda-build-arguments: --override-channels -c conda-forge

    runs-on: ${{ matrix.runner }}

    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          ref: ${{ github.ref }}
          clean: true
          fetch-depth: 0

      - name: Create and upload canary build
        uses: conda/actions/canary-release@main # Pin to a reviewed commit in production.
        with:
          # [required]
          # the package name to be built and released
          package-name: conda

          # [required]
          # the output subdirectory and target platform, e.g. linux-64
          subdir: ${{ matrix.subdir }}

          # [optional]
          # installer architecture (empty uses setup-miniconda's default)
          base-architecture: ${{ matrix.base-architecture || '' }}
          # recipe directory (default: recipe)
          conda-build-path: recipe
          # extra conda-build arguments (default: empty)
          conda-build-arguments: ${{ matrix.conda-build-arguments || '' }}
          # publish packages after building and testing (default: 'true')
          upload: 'true'

          # [at least one of these two groups is required when upload is 'true']
          # anaconda.org configuration:
          #   the anaconda.org channel
          anaconda-org-channel: conda-canary
          #   the anaconda.org label to apply
          anaconda-org-label: dev
          #   the anaconda.org token to upload to the channel
          anaconda-org-token: ${{ secrets.CANARY_ANACONDA_ORG_TOKEN }}
          # Github Releases configuration:
          #   target repository
          github-releases-repository: my-org/my-repo
          #   chosen name for the channel
          github-releases-channel-name: conda-canary-on-github
          #   github token if the repo is a different one
          github-releases-token: ${{ secrets.GITHUB_RELEASES_TOKEN }}

          # [optional]
          # the GitHub Personal Access Token to comment with
          # comment-token: ${{ secrets.CANARY_ACTION_COMMENT_TOKEN }}
          # [optional]
          # the comment heading (default: Canary release status)
          # comment-headline: Canary release status
```
