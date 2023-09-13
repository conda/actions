# Template Files

A composite GitHub Action to template (or copy) files from other repositories and
commits them to the specified PR.

## GitHub Action Usage

In your GitHub repository include this action in your workflows:

```yaml
- uses: conda/actions/template-files
  with:
    # [optional]
    # the path to the configuration file
    config: .github/templates/config.yml

    # [optional]
    # the path to the template stubs
    stubs: .github/templates/

    # [optional]
    # the GitHub token with API access
    token: ${{ github.token }}
```

Define what files to template in a configuration file, e.g., `.github/templates/config.yml`:

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
```
