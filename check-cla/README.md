# Check CLA (Contributor License Agreement)

This is a custom GitHub action to be used in the conda GitHub organization
for checking the conda contributor license agreement.

## GitHub Action Usage

In your GitHub repository include the action in your workflows:

```yaml
name: Check CLA

on:
  issue_comment:
    types:
      - created
  pull_request:
    types:
      - reopened
      - opened
      - closed
      - synchronize

jobs:
  check-cla:
    if: >-
      !github.event.repository.fork
      && (
        github.event.comment.body == '@conda-bot check'
        || github.event_name == 'pull_request'
      )
    runs-on: ubuntu-latest
    steps:
      - name: Check CLA
        uses: conda/actions/check-cla@check-cla
        with:
          # [required]
          # the GitHub Personal Access Token to comment with
          comment-token: ${{ secrets.CLA_COMMENT_TOKEN }}
```
