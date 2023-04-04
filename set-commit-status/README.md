# Set Commit Status

A custom GitHub action to be used in the conda GitHub organization to set a commit
status.

## GitHub Action Usage

In your GitHub repository include the action in your workflows:

```yaml
name: Set Commit Status

on: pull_request_target

jobs:
  pending:
    # need write access for statuses to succeed
    permissions:
      statuses: write

    steps:
      - uses: conda/actions/set-commit-status
        with:
          # [required]
          # A token with the ability to modify the commit status
          # (`statuses: write`)
          # (default: secrets.GITHUB_TOKEN)
          token:

          # [required]
          # The name of the commit status
          context:
          # [required]
          # The commit status to set; either success, error, failure, or pending
          state:

          # A short text explaining the commit status
          # (default: '')
          description:
          # URL/URI linking to further details
          target_url:
```
