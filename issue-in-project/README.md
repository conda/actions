# Issue in Project

This NodeJS package is designed to be both a GitHub Action and a CLI tool. The latter primarily being available for debugging and testing purposes.

## GitHub Action Usage

In your GitHub repository include this action in your workflows:

```yaml
- id: in_project
  uses: conda/actions/issue-in-project@v1
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
# can invoke the TypeScript code (dev) or the compiled JS code (app)
npm run [dev|app] -- --help

# list issues in orgs/conda/projects/5
npm run [dev|app] -- --org conda 5 [ISSUE_ID]

# list issues in user/conda-bot/projects/5
npm run [dev|app] -- --user conda 5 [ISSUE_ID]

# list issues in conda/conda/projects/5
npm run [dev|app] -- --repo conda/conda 5 [ISSUE_ID]
```

### Miming GitHub Action Usage

```bash
# can invoke the TypeScript code (dev) or the compiled JS code (app)

# list issues in orgs/conda/projects/5
INPUT_ORG=conda INPUT_PROJECT=5 [INPUT_ISSUE=ISSUE_ID] npm run [dev|app]

# list issues in user/conda-bot/projects/5
INPUT_USER=conda-bot INPUT_PROJECT=5 [INPUT_ISSUE=ISSUE_ID] npm run [dev|app]

# list issues in conda/conda/projects/5
INPUT_REPO=conda/conda INPUT_PROJECT=5 [INPUT_ISSUE=ISSUE_ID] npm run [dev|app]
```
