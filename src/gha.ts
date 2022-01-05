/**
 *
 *
 * @module
 */
import { getInput, info, setOutput, setFailed } from '@actions/core'
import * as types from '@src/types'
import * as api from "@src/api"

async function _issueInProject() {
  const owner: types.Owner = {
    org: getInput('org'),
    user: getInput('user'),
    repo: getInput('repo'),
  }

  const project = parseInt(getInput('project', { required: true }))

  const rissue = getInput('issue')
  const issue = rissue ? parseInt(rissue) : null

  const result = await api.issueInProject(owner, project, issue)
  if (!!result === result) {
    const icon = result ? '✅' : '❌'
    const verb = result ? 'exists' : 'does not exist'
    info(`${icon} issue (id: ${issue}) ${verb} in project (number: ${project})`)
    setOutput('contains', result)
  } else {
    const number = Object.keys(result).length
    info(`#️⃣  ${number} issues in project (number: ${project})`)
    setOutput('issues', result)
  }
}

async function _userInTeam() {
  const org = getInput('org', { required: true })
  const team = getInput('team', { required: true })
  const user = getInput("user") || null

  const result = await api.userInTeam(org, team, user)
  if (!!result == result) {
    const icon = result ? '✅' : '❌'
    const verb = result ? 'is' : 'is not'
    info(`${icon} user (login: ${user}) ${verb} a member of the team (name: ${team})`)
    setOutput('contains', result)
  } else {
    const number = Object.keys(result).length
    info(`#️⃣  ${number} members in the team (name: ${team})`)
    setOutput('issues', result)
  }
}

async function _gha(): Promise<void> {
  const command = getInput("command", { required: true })

  switch (command) {
    case "issue-in-project":
    case "issueInProject":
      await _issueInProject()
      break
    case "members":
    case "userInTeam":
      await _userInTeam()
      break
    default:
      throw new Error(`Unknown input command=${command}`)
  }
}

/**
 * GitHub Action interface.
 * Expects all inputs to be defined as environment variables defined as INPUT_*.
 */
export function gha(): void {
  _gha().catch(err => { throw err })
}

if (require.main === module) {
  try {
    gha()
  } catch (err) {
    if (err instanceof Error) setFailed(err.message)
    else throw err
  }
}
