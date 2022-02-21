import { getInput, info, setOutput, setFailed } from '@actions/core'
import { Owner, issueInProject } from '@src/api'

/**
 * GitHub Action interface.
 * Expects all inputs to be defined as environment variables defined as INPUT_*.
 */
export async function gha(): Promise<void> {
  const owner: Owner = {
    org: getInput('org'),
    user: getInput('user'),
    repo: getInput('repo'),
  }

  const project = parseInt(getInput('project', { required: true }))

  const rissue = getInput('issue')
  const issue = rissue ? parseInt(rissue) : null

  const result = await issueInProject(owner, project, issue)
  if (!!result === result) {
    const icon = result ? '✅' : '❌'
    const verb = result ? 'exists' : 'does not exist'
    info(`${icon} issue (id: ${issue}) ${verb} in project (number: ${project})`)
  } else {
    const number = Object.keys(result).length
    info(`#️⃣  ${number} issues in project (number: ${project})`)
  }
  setOutput('contains', result)
}

if (require.main === module) {
  gha().catch(err => {
    if (err instanceof Error) setFailed(err)
    else throw err
  })
}
