import { Command } from 'commander'
import { Owner, issueInProject } from '@src/api'
import { setFailed } from '@actions/core'

/**
 * Command line interface.
 * Expects all inputs to be provided on the command line (see --help).
 */
export async function cli(
  argv?: string[],
  exceptionError?: boolean,
): Promise<void> {
  const program = new Command()
    .name('issue-in-project')
    .showSuggestionAfterError()
    .showHelpAfterError()

  if (exceptionError)
    program.exitOverride(err => {
      throw err
    })

  program
    .description(
      'detect if an issue is found within a given project, if no issue defined simply list all issues',
    )
    .option('-o, --org <org>', 'the organization to search (e.g. conda)')
    .option('-u, --user <user>', 'the user to search (e.g. conda-bot)')
    .option(
      '-r, --repo, --repository <repo>',
      'the repository to search (e.g. conda/conda)',
    )
    .argument('<project>', 'the project number (e.g. 5)', parseInt)
    .argument('[issue]', 'the issue id (e.g. XXXXX)', parseInt)
    .action(async (project: number, issue: number, owner: Owner) => {
      console.log(await issueInProject(owner, project, issue))
    })

  if (argv) await program.parseAsync(argv, { from: 'user' })
  else await program.parseAsync()
}

if (require.main === module) {
  cli().catch(err => {
    if (err instanceof Error) setFailed(err)
    else throw err
  })
}
