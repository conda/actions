# Check News

Validate that pull requests include a conda news fragment.

```yaml
name: News fragment

on:
  pull_request:
    types: [opened, synchronize, reopened, labeled, unlabeled, ready_for_review]

permissions:
  contents: read
  pull-requests: read

jobs:
  news:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
        with:
          fetch-depth: 0
          persist-credentials: false
      - uses: conda/actions/check-news@main
        with:
          skip-label: no-news
          require-pr-number: true
          fragment-format: sectioned
          news-directory: news
```

The action accepts current conda sectioned snippets under `news/`, including
extensionless files and `.md` files. It ignores `news/TEMPLATE`,
`news/TEMPLATE.md`, and hidden files.

Supported headings are:

- `Enhancements`
- `Bug fixes`
- `Deprecations`
- `Docs`
- `Other`

Pull requests without a news fragment can use the `no-news` label.
