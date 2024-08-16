# Read JSON

A composite GitHub Action to read a json file from a local or remote file.

## GitHub Action Usage

In your GitHub repository include this action in your workflows:

```yaml
- id: read_json
  uses: conda/actions/read-json
  with:
    # [required]
    # the relative path (or URL) to the YAML file to read
    path: path/to/json.json
    path: https://raw.githubusercontent.com/owner/repo/ref/path/to/json.json

    # [optional]
    # the keys to the value to extract
    key: foo.bar.2.baz

# if key provided get the value itself
- run: echo ${{ steps.read_json.outputs.value }}

# if no key provided get the entire YAML
- run: echo ${{ fromJSON(steps.read_json.outputs.value)['key'] }}
```
