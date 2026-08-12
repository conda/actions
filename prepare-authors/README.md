# Prepare Authors

A composite GitHub Action to keep `.authors.yml` current for rever by scanning
recent commits, updating author metadata, and opening or updating a pull request.
Existing emails with a new commit author name get an `aliases` update; new emails
for an existing contributor are added to `alternate_emails`. A name match is not
used when the commit’s resolved GitHub login conflicts with the entry’s `github`
key (the commit is treated as a different person, or matched by that login).
In `check` mode it validates only and fails when new contributors or
alternate-email/alias updates are needed; missing `github:` keys warn but do not fail.
In `prepare` mode unresolved missing `github:` keys fail the step after lookup
attempts (no commit or PR). That includes newly discovered contributors whose
commit author could not be mapped to a GitHub login. Rever still updates
`.mailmap` and `AUTHORS.md` at release time.

## Action Inputs

| Name | Description | Default |
|------|-------------|---------|
| `authors-path` | Path to the rever authors metadata file. | `.authors.yml` |
| `since` | Commit range to scan. Use `tag` for commits since the highest final release tag (`X.Y.Z` or `vX.Y.Z`; pre-releases ignored), or `all`. Fails if no such tag exists. | `tag` |
| `base-branch` | Base branch for the generated authors PR. | `main` |
| `branch-prefix` | Prefix for the generated authors branch. | `prepare-authors-` |
| `git-remote` | Git remote alias used to resolve owner/repo for gh api. | `origin` |
| `mode` | `prepare` updates `.authors.yml` and opens a PR; `check` validates only. | `prepare` |
| `git-author-name` | Git author name for the generated commit. | `Conda Bot` |
| `git-author-email` | Git author email for the generated commit. | `18747875+conda-bot@users.noreply.github.com` |
| `token` | Token for checkout and prepare-mode push/PR (`contents:write`, `pull-requests:write`). Author login lookups use `GITHUB_TOKEN` / `github.token`. | `${{ github.token }}` |

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

### Check mode

Use `mode: check` to validate `.authors.yml` without writing files or opening a
PR. Check mode checks out the triggering revision (not `base-branch`). New
contributors and alternate-email or alias updates fail the step. Missing `github:` keys
emit warnings but do not fail (unlike prepare mode, which fails when those keys
stay unresolved after lookup, including for newly discovered contributors).
Lookups use the job `GITHUB_TOKEN`
(`contents: read` is enough).

```yaml
- uses: conda/actions/prepare-authors@main
  with:
    mode: check
    since: tag
```
