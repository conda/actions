# Prepare Release

This action prepares a release on GitHub:
1. Running `towncrier` to update the changelog.

## GitHub Action Usage

```yaml
name: Prepare Release

on:
  workflow_dispatch:
    inputs:
      version:
        description: Release version
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
          # [required]
          # the version to be released
          version: ${{ inputs.version }}

          # [optional]
          # GitHub token to fork repository and create changelog PR
          # (`contents: write` & `pull-request: write` for fine-grained PAT; `repo` for classic PAT)
          # token: ${{ github.token }}
```

### Sample Workflow Preparing Release using Dynamic Branch

```yaml
name: Prepare Release

on:
  workflow_dispatch:
    inputs:
      version:
        description: Release version
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
