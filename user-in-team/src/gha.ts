import { getInput, info, setOutput, setFailed } from '@actions/core'
import { userInTeam } from '@src/api'

/**
 * GitHub Action interface.
 * Expects all inputs to be defined as environment variables defined as INPUT_*.
 */
export async function gha(): Promise<void> {
  const org = getInput('org', { required: true })
  const team = getInput('team', { required: true })
  const user = getInput('user') || null

  const result = await userInTeam(org, team, user)
  if (!!result == result) {
    const icon = result ? '✅' : '❌'
    const verb = result ? 'is' : 'is not'
    info(
      `${icon} user (login: ${user}) ${verb} a member of the team (org: ${org}, team: ${team})`,
    )
  } else {
    const number = Object.keys(result).length
    info(`#️⃣  ${number} members in the team (org: ${org}, team: ${team})`)
  }
  setOutput('contains', result)
}

if (require.main === module) {
  try {
    gha()
  } catch (err) {
    if (err instanceof Error) setFailed(err.message)
    else throw err
  }
}
