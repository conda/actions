# Read JSON

A composite GitHub Action to read a local file or remote URL.

## GitHub Action Usage

In your GitHub repository include this action in your workflows:

```yaml
- id: read_json
  uses: conda/actions/read-file
  with:
    # [required]
    # the local path or remote URL to the file to read
    path: path/to/json.json
    # path: https://raw.githubusercontent.com/owner/repo/ref/path/to/json.json

    # [optional]
    # the parser to use for the file
    parser: json

- id: read_yaml
  uses: conda/actions/read-file
  with:
    path: path/to/yaml.yaml
    # path: https://raw.githubusercontent.com/owner/repo/ref/path/to/yaml.yaml

    parser: yaml

  - id: read_text
  uses: conda/actions/read-file
  with:
    path: path/to/text.text
    # path: https://raw.githubusercontent.com/owner/repo/ref/path/to/text.text

    parser: null

- run: echo "${{ fromJSON(steps.read_file.outputs.content)['key'] }}"
- run: echo "${{ fromJSON(steps.read_file.outputs.content)['key'] }}"
- run: echo "${{ steps.read_file.outputs.content }}"
```
