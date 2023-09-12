# Combine Durations

A composite GitHub Action to combine the duration files from recent pytest runs.

## GitHub Action Usage

In your GitHub repository include this action in your workflows:

```yaml
- uses: conda/actions/combine-durations
  with:
    # [optional]
    # the git branch to search
    branch: main

    # [optional]
    # the glob pattern to search for duration files
    pattern: '*-all'

    # [optional]
    # the GitHub token with artifact downloading permissions
    token: ${{ github.token }}

    # [optional]
    # the workflow to search
    workflow: tests.yml
```
