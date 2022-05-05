# Check CLA (Contributor License Agreement)

This is a custom GitHub action to be used in the conda GitHub organization
for checking the conda contributor license agreement.

## GitHub Action Usage

In your GitHub repository include the action in your workflows:

```yaml
name: Contributor license agreement (CLA)

on:
  issue_comment:
    types:
      - created
  pull_request_target:
    types:
      - reopened
      - opened
      - closed
      - synchronize

jobs:
  check:
    if: >-
      !github.event.repository.fork
      && (
        github.event.comment.body == '@conda-bot check'
        || github.event_name == 'pull_request_target'
      )
    runs-on: ubuntu-latest
    steps:
      - name: Check CLA
        uses: conda/actions/check-cla@check-cla
        with:
          # [required]
          # label to add when actor has signed the CLA
          label: cla-signed
          # [required]
          # the GitHub Personal Access Token to comment and label with
          token: ${{ secrets.CLA_ACTION_TOKEN }}
```
