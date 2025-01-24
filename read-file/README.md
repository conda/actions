# Read File

A composite GitHub Action to read a local file or remote URL with an optional JSON/YAML parser.

## Action Inputs

| Name | Description | Default |
|------|-------------|---------|
| `path` | Local path or remote URL to the file to read. | **required** |
| `parser` | Parser to use for the file. Choose json, yaml, or null (to leave it as plain text). | **optional** |
| `default` | File contents to use if the file is not found. | **optional** |

## Action Outputs

| Name | Description |
|------|-------------|
| `content` | File contents as a JSON object (if a parser is specified) or the raw text. |

## Sample Workflows

```yaml
name: Read File

on:
  pull_request:

jobs:
  read:
    steps:
      - id: read_json
        uses: conda/actions/read-file
        with:
          path: https://raw.githubusercontent.com/owner/repo/ref/path/to/json.json
          default: '{}'
          parser: json

      - id: read_yaml
        uses: conda/actions/read-file
        with:
          path: https://raw.githubusercontent.com/owner/repo/ref/path/to/yaml.yaml
          default: '{}'
          parser: yaml

      - id: read_text
        uses: conda/actions/read-file
        with:
          path: path/to/text.text

      - run: echo "${{ fromJSON(steps.read_json.outputs.content)['key'] }}"
      - run: echo "${{ fromJSON(steps.read_yaml.outputs.content)['key'] }}"
      - run: echo "${{ steps.read_file.outputs.content }}"
```
