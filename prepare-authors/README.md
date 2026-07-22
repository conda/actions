# Prepare Authors

A composite GitHub Action to keep `.authors.yml` current for rever by scanning
recent commits, updating author metadata, and opening or updating a pull request.
In `check` mode it validates only and fails when new contributors or
alternate-email updates are needed. Rever still updates `.mailmap` and
`AUTHORS.md` at release time.

## Action Inputs

| Name | Description | Default |
|------|-------------|---------|
| `authors-path` | Path to the rever authors metadata file. | `.authors.yml` |
| `since` | Commit range to scan. Use `tag` for commits since the latest tag, or `all`. | `tag` |
| `base-branch` | Base branch for the generated authors PR. | `main` |
| `branch-prefix` | Prefix for the generated authors branch. | `prepare-authors-` |
| `git-remote` | Git remote alias used to resolve owner/repo for gh api. | `origin` |
| `mode` | `prepare` updates `.authors.yml` and opens a PR; `check` validates only. | `prepare` |
| `git-author-name` | Git author name for the generated commit. | `Conda Bot` |
| `git-author-email` | Git author email for the generated commit. | `18747875+conda-bot@users.noreply.github.com` |
| `token` | GitHub token with `contents:write` and `pull-requests:write` for prepare mode. | `${{ github.token }}` |

## Action Outputs

| Name | Description |
|------|-------------|
| `changed` | Whether `.authors.yml` needed updates. |
| `branch` | Generated authors branch. |
| `pull-request-url` | Generated or updated authors PR URL in prepare mode. |

## Sample Workflows

In your GitHub repository include this action in your workflows:

### Prepare mode (weekly)

```yaml
name: Prepare authors

on:
  schedule:
    - cron: '0 3 * * 1'  # Monday 03:00 UTC
  workflow_dispatch:

permissions:
  contents: write
  pull-requests: write

jobs:
  prepare:
    if: '!github.event.repository.fork'
    runs-on: ubuntu-latest
    steps:
      - uses: conda/actions/prepare-authors@main
        with:
          since: tag
          base-branch: main
```

### Check mode

Use `mode: check` to validate `.authors.yml` without writing files or opening a
PR; missing `github:` keys emit warnings but do not fail the step.

```yaml
- uses: conda/actions/prepare-authors@main
  with:
    mode: check
    since: tag
```
