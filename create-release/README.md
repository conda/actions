# Create Release

This action creates a release on GitHub:
1. Archiving the current repository state as a tarball (`.tar.gz`). While GitHub also does this for releases those archives are unfortunately not stable and cannot be relied on.
2. Computing the checksum for the archived tarball.
3. Extracting the release notes from the changelog.

## Action Inputs

| Name | Description | Default |
| ---- | ----------- | ------- |
| `version` | Version to release. | **Required** |
| `branch` | Target branch to use for the release. | `${{ github.even.repository.default_branch` |
| `archive-name` | Name of the git archive to create. | `${{ github.event.repository.name }}-${{ inputs.version }}` |
| `output-directory` | Directory for the release artifacts. | `release` |
| `release-notes` | Name of the release notes to create. | `RELEASE_NOTES.md` |
| `token` | GitHub token to create the release.<br>Fine-grained PAT: `contents: write` | `${{ github.token }}` |

## Sample Workflows

### Basic Workflow

```yaml
name: Create Release

on:
  workflow_dispatch:
    inputs:
      version:
        description: The version to release.
        required: true

permissions:
  contents: write

jobs:
  create:
    runs-on: ubuntu-latest
    steps:
      - name: Create Release
        uses: conda/actions/create-release
        with:
          version: ${{ inputs.version }}
          branch: main
```

### Dynamic Branch Workflow

```yaml
name: Create Release

on:
  workflow_dispatch:
    inputs:
      version:
        description: The version to release.
        required: true

permissions:
  contents: write

jobs:
  create:
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

      - name: Create Release
        uses: conda/actions/create-release
        with:
          version: ${{ inputs.version }}
          branch: ${{ env.BRANCH }}
```
