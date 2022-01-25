# Read YAML

A composite GitHub Action to read a yaml file from the current repository.

## GitHub Action Usage

In your GitHub repository include this action in your workflows:

```yaml
- id: read_yaml
  uses: conda/actions/read-yaml
  with:
    # [required]
    # the relative path to the YAML file to read
    path: path/to/yaml.yml

    # [optional]
    # the keys/indices scope to extract
    scopes: |
      value1: foo.bar.2.baz
      value2: foo.bar.5.qux

- run: echo ${{ steps.read_yaml.outputs.value1 }}
- run: echo ${{ steps.read_yaml.outputs.value2 }}
```
