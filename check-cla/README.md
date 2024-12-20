# Check CLA (Contributor License Agreement)

A custom GitHub action to be used in the conda GitHub organization for checking the
conda contributor license agreement.

## Action Inputs

| Name | Description | Default |
| ---- | ----------- | ------- |
| `label` | Label to apply to contributor's PR once the CLA is signed. | cla-signed |
| `repository` | Repository in which to create PR adding CLA signature. | conda/cla |
| `path` | Path to the CLA signees file within the provided `repository`. | .cla-signers |
| `magic-command` | Magic word to trigger the action via a comment. | `@conda-bot check` |
| `author` | Git-format author to use for the CLA commits. | @conda-bot |
| `token` | GitHub token to comment on PRs, change PR labels, and modify the commit status in the current repository.<br>Fine-grained PAT: `pull_request: write; statuses: write` | `${{ github.token }}` |
| `pr-token` | GitHub token to create pull request in the `repository`.<br>Fine-grained PAT: `pull_request: write` | `${{ inputs.token }}` |
| `fork-token` | GitHub token to create and push to a `repository` fork.<br>Fine-grained PAT: `administration: write; contents: write` | `${{ inputs.pr-token }}` |

## Sample Workflows

```yaml
name: Check CLA

on:
  issue_comment:
    types: [created]
  pull_request_target:

jobs:
  check:
    if: >-
      (
        github.event.comment.body == '@conda-bot check'
        && github.event.issue.pull_request
        || github.event_name == 'pull_request_target'
      )
    steps:
      - uses: conda/actions/check-cla
        with:
          token: ...
          pr-token: ...
          fork-token: ...
```
