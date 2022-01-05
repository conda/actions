import * as types from '@src/types'
import { contains, flatten } from '@src/utils'
import { getProjectIssues, getMembers } from '@src/queries'

/**
 * @param owner The organization name (e.g. conda), user login (e.g. conda-bot),
 * or repository path (e.g. conda/conda) within which to find the project.
 * @param project The project number (e.g. 5).
 * @param issue The issue id (e.g. ????).
 * @return If issue provided return issue if it exists in project or list all issues
 * in project.
 */
export async function issueInProject(
  owner: types.Owner,
  project: number,
  issue: number | null | undefined = undefined,
): Promise<boolean | types.Item[]> {
  const issues = getProjectIssues(owner, project)
  if (!issue) return await flatten(issues)
  return !!await contains(issues, { id: issue })
}

/**
 * @param org The organization name (e.g. conda).
 * @param team The team name (e.g. conda-core).
 * @param user The user login (e.g. conda-bot).
 * @return If user provided return user if they are a member of the team or list team
 * members.
 */
export async function userInTeam(
  org: string,
  team: string,
  user: string | null | undefined = undefined,
): Promise<boolean | string[]> {
  const members = getMembers(org, team)
  if (!user) return await flatten(members)
  return !!await contains(members, user)
}
