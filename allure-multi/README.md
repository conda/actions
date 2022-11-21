allure-multi
============

Generate multiple allure reports from a third repository, using github actions' builtin Java runtime instead of containers.

Allows hosting of allure reports in a github pages separate from the tested project.

How to use
----------

1. Configure a repository to upload `allure-results.tar.gz` as an artifact,
   containing a tarball of the `allure-results` folder. This is a bunch of json that allure
   uses to create its report. The name should be unique per test run, and the path should always be `allure-results.tar.gz`.

```yaml
- name: Run test
  run: |
    python -m pip install allure-pytest
    pytest --alluredir=allure-results
    tar -zcf allure-results.tar.gz allure-results
- name: Upload allure-results
  uses: actions/upload-artifact@v2
  with:
    name: allure-${{ matrix.python-version }}_${{ matrix.os }}
    path: allure-results.tar.gz
```

2. Clone https://github.com/dholth/allure-multi-target, as a template. Make sure it has a `gh-pages` branch. Configure github pages to use the `GitHub Actions` source, instead of `Deploy from a branch`

3. Edit `.github/workflows/build-report.yml` to point to the repository configured in (1). The action will download all artifacts named `allure-*` (or what's specified in the pattern).
Artifacts are grouped into reports based on regular expression matching, or everything after `allure-` by default.

```yaml
- name: Call allure-multi action
  uses: dholth/allure-multi@main
  with:
    repository: your/public-repository
    pattern: allure-*
    group: allure-(.*)
```

> For example, if your tests are split into `allure-osx-1` and `allure-osx-2`, and `allure-linux-1` and `allure-linux-2`, the pattern `allure-(.*)-.*` creates an `osx` report combining `allure-osx-1` and `allure-osx-2`, and a `linux` report combining `allure-linux-1` and `allure-linux-2`. This is especially useful for parallel test runs, where parts of the test suite run on different machines.
