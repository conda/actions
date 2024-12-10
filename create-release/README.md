# Create Release

This action creates a release on GitHub:
1. Archiving the current repository state as a tarball (`.tar.gz`). While GitHub also does this for releases those archives are unfortunately not stable and cannot be relied on.
2. Computing the checksum for the archived tarball.
3. Extracting the release notes from the changelog.

## GitHub Action Usage

Requirements:
- `contents: write` permission to the repository
- (optional) Python >=3.7

```yaml
name: Create Release

on:
  workflow_dispatch:
    inputs:
      version:
        description: Release version
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
          # [required]
          # the version to be released
          version: ${{ inputs.version }}

          # [required]
          # the target branch for the release
          branch: main

          # [optional]
          # name of the tarball archive
          # archive-name: ${{ github.event.repository.name }}-${{ github.ref_name }}

          # [optional]
          # directory for the release artifacts
          # output-directory: release

          # [optional]
          # path to the release notes
          # release-notes: RELEASE_NOTES.md

          # [optional]
          # GitHub token to author release
          # (`contents: write` for fine-grained PAT; `repo` for classic PAT)
          # token: ${{ github.token }}
```

### Sample Workflow Creating Release using Dynamic Branch

```yaml
name: Create Release

on:
  workflow_dispatch:
    inputs:
      version:
        description: Release version
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
