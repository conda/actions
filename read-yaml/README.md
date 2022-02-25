# Read YAML

A composite GitHub Action to read a yaml file from the current repository.

## GitHub Action Usage

In your GitHub repository include this action in your workflows:

```yaml
- id: read_yaml
  uses: conda/actions/read-yaml
  with:
    # [required]
    # the relative path (or URL) to the YAML file to read
    path: path/to/yaml.yml
    path: https://raw.githubusercontent.com/owner/repo/ref/path/to/yaml.yml

    # [optional]
    # the keys to the valye to extract
    key: foo.bar.2.baz

# if key provided get the value itself
- run: echo ${{ steps.read_yaml.outputs.value }}

# if no key provided get the entire YAML
- run: echo ${{ fromJSON(steps.read_yaml.outputs.value)['key'] }}
```
