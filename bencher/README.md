# Bencher reporting

Report pytest-benchmark artifacts from a completed workflow to Bencher. Benchmark
execution stays in the repository's unprivileged test workflow. This action runs
in a separate `workflow_run` workflow with upload credentials, including for fork
PRs. It reads data files only and never checks out or executes producer code.

```yaml
name: Track Benchmarks
on:
  workflow_run:
    workflows: [Tests]
    types: [completed]
permissions: {}
jobs:
  report:
    if: >-
      !github.event.repository.fork
      && contains(fromJSON('["success", "failure"]'), github.event.workflow_run.conclusion)
      && (github.event.workflow_run.event == 'pull_request'
          || (github.event.workflow_run.event == 'push'
              && github.event.workflow_run.head_branch == 'main'))
    runs-on: ubuntu-24.04
    permissions:
      actions: read
      checks: write
      pull-requests: write
    steps:
      - uses: conda/actions/bencher@main # Pin to a reviewed commit in production.
        with:
          project: my-project
          api-key: ${{ secrets.BENCHER_API_KEY }}
          pr-policy: percentage
```

The reporter must exist on the default branch before `workflow_run` can use it.
Use concurrency grouped by producer repository and PR number or branch to cancel
outdated reporters. The action also skips PR heads that no longer match GitHub.
Python 3.10+ and GitHub CLI must be installed, as on GitHub's Ubuntu 24.04 runners.
The action installs Bencher 0.6.12.

## Producer artifact

Upload one flat artifact named `benchmark-results-v3`, containing:

- `benchmark_results.json`, pytest-benchmark output at the actual head commit
- `baseline_results.json`, output at the event's actual base commit for PRs
- `event.json`, copied from `$GITHUB_EVENT_PATH` before leaving the producer
- `runner_metadata.json`, with `runner: "ubuntu-24.04"` and `harness_sha` equal to
  the head commit, plus optional image metadata for diagnostics

Run the base and head sequentially on the same runner, using the head's benchmark
tests and resolved dependencies for both. Keep their actual commit IDs in the
JSON. Record the runner image version, dependency inventory and optional
`bencher noise` output alongside these files for diagnosis. These diagnostics do
not prove that timings are free from load or cache effects.

The action validates commit IDs, the recorded PR event against GitHub, harness
revision, runner label, CPU model, architecture and full Python version. Both
runs must contain the same nonempty set of benchmark fullnames. A missing base
run or incomparable pair produces a neutral check and no upload. Testbed names
use the producer's Ubuntu label, architecture, Python minor version and CPU model.
The reporting runner does not determine the testbed. Image patch versions remain
diagnostics so routine image releases do not discard historical comparisons.

## Alert policies

`pr-policy: percentage` compares each PR with a separate baseline branch for that
producer run. It alerts on latency increases over 25%. This avoids mixing PR
baseline measurements into main history. Reruns use the producer run ID and
attempt to keep their baselines separate.

`pr-policy: informational` uploads paired measurements without PR thresholds or
a passing performance check. It creates an explicitly neutral check. Upload
failures still fail the reporting job.

For individual noisy benchmarks, append `_bencher_ignore` to the fixture's
`benchmark.fullname` before measuring. Bencher retains their measurements but
suppresses alerts. The action uploads the original JSON without rewriting names
or selecting a partial subset of results.

Pushes use historical latency thresholds with a 99% t-test and 10 to 64 previous
measurements under either PR policy. `ci-id` optionally identifies the Bencher
GitHub check. `api-token` supports existing user tokens instead of `api-key`.
`github-token` defaults to `github.token`. `artifact-name` can override the v3
name when a producer deliberately changes its artifact layout.

See Bencher's [GitHub Actions guide](https://bencher.dev/docs/how-to/github-actions/),
[relative benchmarking guide](https://bencher.dev/docs/how-to/track-benchmarks/#relative-continuous-benchmarking)
and [alert suppression](https://bencher.dev/docs/explanation/thresholds/#suppressing-alerts).
