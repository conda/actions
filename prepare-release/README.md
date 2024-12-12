# Prepare Release

This action prepares a release on GitHub:
1. Running `towncrier` to update the changelog.

## Action Inputs

| Name | Description | Default |
| ---- | ----------- | ------- |
| `version` | Version to release. | **Required** |
| `branch` | Target branch to use for the release. | `${{ github.even.repository.default_branch` |
| `changelog-author` | Git-format author to use for the changelog commits. | @conda-bot |
| `fork-token` | GitHub token to create and push to the fork. If not provided, no fork will be used.<br>Fine-grained PAT: `administration: write` | `${{ github.token }}` |
| `pr-token` | GitHub token to create the pull request.<br>Fine-grained PAT: `pull-request: write` | `${{ github.token }}` |

## Sample Workflows

### Basic Workflow

```yaml
name: Prepare Release

on:
  workflow_dispatch:
    inputs:
      version:
        description: The version to release.
        required: true

permissions:
  contents: write
  pull-requests: write

jobs:
  prepare:
    runs-on: ubuntu-latest
    steps:
      - name: Prepare Release
        uses: conda/actions/prepare-release
        with:
          version: ${{ inputs.version }}
```

### Dynamic Branch Workflow

```yaml
name: Prepare Release

on:
  workflow_dispatch:
    inputs:
      version:
        description: The version to release.
        required: true

permissions:
  contents: write
  pull-requests: write

jobs:
  prepare:
    runs-on: ubuntu-latest
    steps:
      - name: Get Branch
        shell: python
        run: |
          from os import environ
          from pathlib import Path

          # derive the branch from the version by dropping the `PATCH` and using `.x`
          branch = "${{ inputs.version }}".rsplit(".", 1)[0]
          Path(environ["GITHUB_ENV"]).write_text(f"BRANCH={branch}.x")

      - name: Prepare Release
        uses: conda/actions/prepare-release
        with:
          version: ${{ inputs.version }}
          branch: ${{ env.BRANCH }}
```
