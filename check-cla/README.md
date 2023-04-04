# Check CLA (Contributor License Agreement)

A custom GitHub action to be used in the conda GitHub organization for checking the
conda contributor license agreement.

## GitHub Action Usage

In your GitHub repository include the action in your workflows:

```yaml
name: Check CLA

on:
  issue_comment:
    types:
      - created
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
          # [required]
          # A token with ability to comment, label, and modify the commit status
          # (`pull_request: write` and `statuses: write`)
          # (default: secrets.GITHUB_TOKEN)
          token:
          # [required]
          # Label to apply to contributor's PR once CLA is singed
          label:

          # Upstream repository in which to create PR
          # (default: conda/infrastructure)
          cla_repo:
          # Path to the CLA signees file within the provided `cla_repo`
          # (default: .clabot)
          cla_path:

          # Fork of cla_repo in which to create branch
          # (default: conda-bot/infrastructure)
          cla_fork:
          # [required]
          # Token for opening singee PR in the provided `cla_repo`
          cla_token:
```
