# User in Team

This NodeJS package is designed to be both a GitHub Action and a CLI tool. The latter primarily being available for debugging and testing purposes.

## GitHub Action Usage

In your GitHub repository include this action in your workflows:

```yaml
- id: in_team
  uses: conda/actions/user-in-team@v1
  with:
    # [required]
    # the team org
    org: conda

    # [required]
    # the team name
    team: conda-core

    # [optional]
    # the user to search for
    # if not provided return list of user in team
    user: conda-bot

# if user was provided we get a boolean
- if: steps.in_team.outputs.contains == 'true'
  ...

# if no user provided we get a list of users
- if: contains(steps.in_team.outputs.contains.*, 'conda-bot')
  ...
```

## CLI Usage

From the terminal we can invoke this package as a NodeJS executable:

### One-time Setup

```bash
# clone repository
git clone git@github.com:conda/actions.git <local directory>
cd <local directory>/user-in-team

# create development/testing environment
conda create -n conda-actions nodejs
conda activate conda-actions
npm install
```

### Standard CLI Usage

```bash
# can invoke the TypeScript code (src) or the compiled JS code (dist)
npm run <src|dist> -- --help

# list users in orgs/conda/teams/conda-core
npm run <src|dist> -- conda conda-core [USER_LOGIN]
```

### Miming GitHub Action Usage

```bash
# can invoke the TypeScript code (src) or the compiled JS code (dist)

# list users in orgs/conda/teams/conda-core
INPUT_ORG=conda INPUT_TEAM=conda-core [INPUT_USER=USER_LOGIN] npm run <src|dist>
```
