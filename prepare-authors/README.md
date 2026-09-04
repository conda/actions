# Prepare Authors

A composite GitHub Action to keep `.authors.yml` current for rever by scanning
recent commits, updating author metadata, and opening or updating a pull request.
Existing emails with a new commit author name get an `aliases` update; new emails
for an existing contributor are added to `alternate_emails`. A name match is not
used when the commit’s resolved GitHub login conflicts with the entry’s `github`
key (the commit is treated as a different person, or matched by that login).
Duplicate emails fail. Duplicate names or GitHub logins require the other
identifier to select one author entry.
Unresolved missing `github:` keys fail the step after lookup
attempts (no commit or PR). That includes newly discovered contributors whose
commit author could not be mapped to a GitHub login. Rever still updates
`.mailmap` and `AUTHORS.md` at release time.

## Action Inputs

| Name | Description | Default |
|------|-------------|---------|
| `authors-path` | Path to the rever authors metadata file. | `.authors.yml` |
| `since` | Commit range to scan. Use `tag` for commits since the highest final release tag (`X.Y.Z` or `vX.Y.Z`; pre-releases ignored), or `all`. Fails if no such tag exists. | `tag` |
| `base-branch` | Base branch for the generated authors PR. | `main` |
| `branch-prefix` | Non-empty prefix for the generated authors branch. | `prepare-authors-` |
| `git-remote` | Git remote alias used to resolve owner/repo for gh api. | `origin` |
| `git-author-name` | Git author name for the generated commit. | `Conda Bot` |
| `git-author-email` | Git author email for the generated commit. | `18747875+conda-bot@users.noreply.github.com` |
| `token` | Token for checkout and push/PR (`contents:write`, `pull-requests:write`). Author login lookups use `GITHUB_TOKEN` / `github.token`. | `${{ github.token }}` |

## Action Outputs

| Name | Description |
|------|-------------|
| `changed` | Whether `.authors.yml` needed updates. |
| `branch` | Generated authors branch. |
| `pull-request-url` | Generated or updated authors PR URL. |

## Sample Workflows

In your GitHub repository include this action in your workflows:

```yaml
name: Prepare authors

on:
  schedule:
    - cron: '0 3 * * 1'  # Monday 03:00 UTC
  workflow_dispatch:

permissions:
  contents: read

jobs:
  prepare:
    if: '!github.event.repository.fork'
    runs-on: ubuntu-latest
    steps:
      - uses: conda/actions/prepare-authors@main
        with:
          since: tag
          base-branch: main
          token: ${{ secrets.SYNC_TOKEN }}
```
