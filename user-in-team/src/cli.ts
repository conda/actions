import { Command } from 'commander'
import { userInTeam } from '@src/api'
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
    .name('user-in-team')
    .showSuggestionAfterError()
    .showHelpAfterError()

  if (exceptionError)
    program.exitOverride(err => {
      throw err
    })

  program
    .description(
      'detect if a user is a member of a given team, if no user defined simply list all members',
    )
    .argument('<org>', 'the organization to search (e.g. conda)')
    .argument('<team>', 'the team to search (e.g. conda-core)')
    .argument('[user]', 'the user to search for (e.g. conda-bot)')
    .action(async (org: string, team: string, user: string) => {
      console.log(await userInTeam(org, team, user))
    })

  if (argv) program.parseAsync(argv, { from: 'user' })
  else program.parseAsync()
}

if (require.main === module) {
  try {
    cli()
  } catch (err) {
    if (err instanceof Error) setFailed(err.message)
    else throw err
  }
}
