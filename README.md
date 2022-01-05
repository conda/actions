# Conda GitHub Actions

A GitHub Action encapsulating a collection of GraphQL queries used across the conda org for issue and project automation.

## Usage

This NodeJS package is designed to be both a GitHub Action and a CLI tool. The latter primarily being available for debugging and testing purposes.

### GitHub Action

In your GitHub repository include this action in your workflows:

```yaml
```

### CLI

From the terminal we can invoke this package as a NodeJS executable:

#### One-time Setup

```bash
# clone repository
git clone git@github.com:conda/actions.git <local directory>
cd <local directory>

# create development/testing environment
conda create -n conda-actions nodejs
npm install
```

#### Standard CLI Usage

```bash
# can invoke the TypeScript code (dev) or the compiled JS code (app)
npm run [dev|app] -- --help

# list issues in orgs/conda/projects/5
npm run [dev|app] -- issue-in-project --org conda 5

# list issues in user/conda-bot/projects/5
npm run [dev|app] -- issue-in-project --user conda 5

# list issues in conda/conda/projects/5
npm run [dev|app] -- issue-in-project --repo conda/conda 5
```

#### Miming GitHub Action Usage

```bash
# can invoke the TypeScript code (dev) or the compiled JS code (app)
npm run [dev|app]

# list issues in orgs/conda/projects/5
INPUT_SUBCOMMAND=issue-in-project \
    INPUT_ORG=conda \
    INPUT_PROJECT=5 \
    npm run [dev|app]

# list issues in user/conda-bot/projects/5
INPUT_SUBCOMMAND=issue-in-project \
    INPUT_USER=conda-bot \
    INPUT_PROJECT=5 \
    npm run [dev|app]

# list issues in conda/conda/projects/5
INPUT_SUBCOMMAND=issue-in-project \
    INPUT_REPO=conda/conda \
    INPUT_PROJECT=5 \
    npm run [dev|app]
```
