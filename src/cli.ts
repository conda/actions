/**
 *  Usage:
 *
 * @module
 */
import * as types from '@src/types'
import { VERSION } from '@src/version'
import { Command } from 'commander'
import { issueInProject, userInTeam } from "@src/api"
import { setFailed } from '@actions/core'

async function _cli(): Promise<void> {
  const program = new Command()
    .version(VERSION)
    .showSuggestionAfterError()
    .showHelpAfterError()

  program
    .command("issue-in-project")
    .description("detect if an issue is found within a given project, if no issue defined simply list all issues")
    .option('-o, --org <org>', 'the organization to search (e.g. conda)')
    .option('-u, --user <user>', 'the user to search (e.g. conda-bot)')
    .option('-r, --repo, --repository <repo>', 'the repository to search (e.g. conda/conda)')
    .argument('<project>', 'the project number (e.g. 5)', parseInt)
    .argument('[issue]', 'the issue id (e.g. XXXXX)', parseInt)
    .action(
      async (project: number, issue: number, owner: types.Owner) => {
        console.log(await issueInProject(owner, project, issue))
      }
    )

  program
    .command("members")
    .description("detect if a user is a member of a given team, if no user defined simply list all members")
    .argument("<org>", "the organization to search (e.g. conda)")
    .argument("<team>", "the team to search (e.g. conda-core)")
    .argument("[user]", "the user to search for (e.g. conda-bot)")
    .action(
      async (org: string, team: string, user: string) => {
        console.log(await userInTeam(org, team, user))
      }
    )

  program.parseAsync()
}

/**
 * Command line interface.
 * Expects all inputs to be provided on the command line (see --help).
 */
export function cli(): void {
  _cli().catch(err => { throw err })
}

if (require.main === module) {
  try {
    cli()
  } catch (err) {
    if (err instanceof Error) setFailed(err.message)
    else throw err
  }
}
