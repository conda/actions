# Create Fork

GitHub action to create a fork of the current repository. The workflow is intended
to work with a fine-grained Personal Access Token (PAT) from another account
(e.g., a bot account).

## Action Inputs

| Name | Description | Default |
| ---- | ----------- | ------- |
| `timeout` | Seconds to wait after creating forked repository. | 60 |
| `token` | GitHub token to create fork of current repository.<br>Fine-grained PAT: `administration: write` | `${{ github.token }}` |

## Sample Workflows

### Basic Workflow

```yaml
name: Create Fork

on:
  workflow_dispatch:

permissions:
  administration: write

jobs:
  prepare:
    runs-on: ubuntu-latest
    steps:
      - name: Create Fork
        uses: conda/actions/create-fork
```
