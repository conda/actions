# Template Files

A composite GitHub Action to template (or copy) files from other repositories and
commits them to the specified PR.

## Action Inputs

| Name | Description | Default |
|------|-------------|---------|
| `config` | Configuration path defining what files to template/copy. | `.github/template-files/config.yml` |
| `stubs` | Path to where stub files are located in the current repository. | `.github/template-files/templates/` |
| `token` | GitHub token to comment, label, and modify the commit status in the current repository.<br>Fine-grained PAT: `pull_request: write`; `statuses: write` | `${{ github.token }}` |

## Action Outputs

| Name | Description |
|------|-------------|
| `summary` | Summary of the files that were templated/copied. |

## Sample Workflows

In your GitHub repository include this action in your workflows:

```yaml
name: Template Files

on:
  workflow_dispatch:

jobs:
  template:
    steps:
      - uses: conda/actions/template-files
        with:
          token: ...
```

## Sample Config (e.g., `.github/templates/config.yml`)

```yaml
user/repo:
  # copy to same path
  - path/to/file
  - src: path/to/file

  # copy to different path
  - src: path/to/other
    dst: path/to/another

  # templating
  - src: path/to/template
    with:
      name: value

  # removing
  - dst: path/to/remove
    remove: true
```
