# Lint Action

A composite GitHub Action that runs [prek](https://github.com/j178/prek) (a fast pre-commit hook runner) with optional autofix and PR comments.

## Files

- `action.yml` - Composite action with all logic
- `workflow.yml.tmpl` - Workflow template for syncing to repos

## Features

- Installs and runs prek with your existing `.pre-commit-config.yaml`
- Captures command output and git diff for PR comments
- Creates/updates sticky PR comments showing lint issues and suggested fixes
- Updates comment to show success when issues are resolved
- Optionally commits and pushes fixes (autofix mode)
- Reacts to trigger comments with 👀 → 🎉/😕

## Usage

### Basic Usage (lint check only)

```yaml
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: conda/actions/lint@main
```

The action automatically fails if lint issues are found (unless `autofix: true`).

### With Autofix via Comment Trigger

```yaml
on:
  pull_request:
  issue_comment:
    types: [created]

jobs:
  lint:
    if: >-
      github.event_name == 'pull_request'
      || (
        github.event_name == 'issue_comment'
        && github.event.issue.pull_request
        && github.event.comment.body == '@conda-bot prek autofix'
      )
    runs-on: ubuntu-latest
    permissions:
      contents: write
      pull-requests: write
    steps:
      - uses: conda/actions/lint@main
        with:
          autofix: ${{ github.event_name == 'issue_comment' }}
          comment-id: ${{ github.event.comment.id }}
          pr-number: ${{ github.event.issue.number }}
```

## Inputs

| Input | Description | Required | Default |
|-------|-------------|----------|---------|
| `token` | GitHub token for PR comments and pushing | No | `${{ github.token }}` |
| `autofix` | Whether to commit and push fixes | No | `'false'` |
| `comment-id` | Comment ID to react to (for issue_comment triggers) | No | `''` |
| `pr-number` | PR number (defaults to current PR, override for issue_comment triggers) | No | `${{ github.event.pull_request.number }}` |
| `git-user-name` | Git user name for autofix commits | No | `conda-bot` |
| `git-user-email` | Git user email for autofix commits | No | `18747875+conda-bot@users.noreply.github.com` |
| `python-version` | Python version for running prek hooks | No | `'3.12'` |
| `checkout` | Whether to checkout the repository (set to false if already checked out) | No | `'true'` |
| `working-directory` | Directory to run prek in (defaults to repo root) | No | `'.'` |
| `config` | Path to pre-commit config file (defaults to auto-discovery) | No | `''` |
| `comment-anchor` | Unique anchor for sticky comment (customize to avoid conflicts with parallel workflows) | No | `'lint-comment'` |
| `comment-header` | Optional header text to prepend to comments (e.g., to mark test comments) | No | `''` |
| `comment-on-success` | Create success comment even without prior lint failure (useful for testing) | No | `'false'` |

## Outputs

| Output | Description |
|--------|-------------|
| `outcome` | `success` if no lint issues, `failure` if issues found |
| `output` | The prek command output |
| `diff` | The git diff of suggested fixes (only if outcome is failure) |

## Disabling PR Comments

To run lint without creating PR comments, omit the `pr-number` input:

```yaml
- uses: conda/actions/lint@main
  with:
    pr-number: ''  # No PR comments
```

This is useful for:
- Running lint in contexts without a PR (e.g., scheduled runs)
- CI test scenarios where you don't want test comments cluttering PRs
- Conditional commenting based on file changes (see tests.yml for an example)

## Behavior by Event Type

### `push`

- Runs prek on the pushed branch
- Fails if lint issues found (no PR comment - no PR context)

### `pull_request`

- Runs prek on the PR
- On failure: creates/updates PR comment with issues and diff
- On success (after previous failure): updates comment to "✅ Lint issues fixed"
- Detects fork PRs and shows a note that autofix won't work

### `issue_comment` (with `autofix: true`)

- Reacts to trigger comment with 👀
- Checks out PR branch via `gh pr checkout`
- Runs prek
- On success (fixes pushed): reacts 🎉, updates comment to "✅ Lint issues fixed"
- On push failure (fork): reacts 😕, updates comment with warning
- On no issues: reacts 🎉

## PR Comments

The action creates a sticky comment (identified by `<!-- lint-comment -->`) that:

- Shows prek output and git diff on failure
- Shows a note for fork PRs (autofix cannot push to forks)
- Shows a warning if autofix was attempted but push failed
- Updates to "✅ Lint issues fixed" when resolved
- Includes link to workflow run for details

## Limitations

- **Fork PRs**: The default `GITHUB_TOKEN` cannot push to forks. Autofix will fail on fork PRs with a clear message explaining how to fix locally. A GitHub App with broader permissions could enable this in the future.
