# Issue in Project

This NodeJS package is designed to be both a GitHub Action and a CLI tool. The latter primarily being available for debugging and testing purposes.

## GitHub Action Usage

In your GitHub repository include this action in your workflows:

```yaml
- id: in_project
  uses: conda/actions/issue-in-project
  with:
    # [required]
    # the project owner (org, user, or repo)
    org: conda
    # user: conda-bot
    # repo: conda/conda

    # [required]
    # the project number
    project: 5

    # [optional]
    # the issue to search for
    # if not provided return list of issues for the project
    issue: ${{ github.event.issue.id }}

    # [required]
    # the github token with read:org access
    github_token: ${{ secrets.PROJECT_TOKEN }}

# if issue was provided we get a boolean
- if: steps.in_project.outputs.contains == 'true'
  ...

# if no issue provided we get a list of issues
- if: contains(steps.in_project.outputs.contains.*.id, github.event.issue.id)
  ...
```

## CLI Usage

From the terminal we can invoke this package as a NodeJS executable:

### One-time Setup

```bash
# clone repository
git clone git@github.com:conda/actions.git <local directory>
cd <local directory>/issue-in-project

# create development/testing environment
conda create -n conda-actions nodejs
conda activate conda-actions
npm install
```

### Standard CLI Usage

```bash
# can invoke the TypeScript code (src) or the compiled JS code (dist)
npm run <src|dist> -- --help

# list issues in orgs/conda/projects/5
npm run <src|dist> -- --org conda 5 [ISSUE_ID]

# list issues in user/conda-bot/projects/5
npm run <src|dist> -- --user conda 5 [ISSUE_ID]

# list issues in conda/conda/projects/5
npm run <src|dist> -- --repo conda/conda 5 [ISSUE_ID]
```

### Miming GitHub Action Usage

```bash
# can invoke the TypeScript code (src) or the compiled JS code (dist)

# list issues in orgs/conda/projects/5
INPUT_ORG=conda INPUT_PROJECT=5 [INPUT_ISSUE=ISSUE_ID] npm run <src|dist>

# list issues in user/conda-bot/projects/5
INPUT_USER=conda-bot INPUT_PROJECT=5 [INPUT_ISSUE=ISSUE_ID] npm run <src|dist>

# list issues in conda/conda/projects/5
INPUT_REPO=conda/conda INPUT_PROJECT=5 [INPUT_ISSUE=ISSUE_ID] npm run <src|dist>
```
