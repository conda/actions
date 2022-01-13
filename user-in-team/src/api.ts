import { getToken, contains, flatten } from '@src/utils'
import { debug } from '@actions/core'
import { getOctokit } from '@actions/github'

const octokit = getOctokit(getToken())

/**
 * Asynchronous generator returning all members of a given team.
 *
 * @param org The organization name (e.g. conda).
 * @param team The team name (e.g. conda-core).
 * @return List of members in team.
 */
export async function* getMembers(
  org: string,
  team: string,
): AsyncGenerator<string> {
  // define the query's types
  interface QueryType {
    organization: {
      team: {
        members: {
          nodes: {
            login: string
          }[]
          pageInfo: {
            endCursor: string
            hasNextPage: boolean
          }
        }
      }
    }
  }

  // get GraphQL query parts
  debug(`org: ${org}`)
  debug(`team: ${team}`)

  // execute query with pagination
  let hasNext = true
  let cursor = ''
  while (hasNext) {
    const resp: QueryType = await octokit.graphql(
      `{
        organization(login: "${org}") {
          team(slug: "${team}") {
            members(first: 100, ${cursor}) {
              nodes {
                login
              }
              pageInfo {
                endCursor
                hasNextPage
              }
            }
          }
        }
      }`,
    )

    // if no nodes then stop processing/querying
    const rmembers = resp?.organization?.team?.members
    if (!rmembers?.nodes) break

    // store cursor for next iteration
    hasNext = rmembers.pageInfo.hasNextPage
    cursor = 'after: "' + rmembers.pageInfo.endCursor + '"'

    // flatten structure and yield
    for (const node of rmembers.nodes) {
      yield node.login
    }
  }
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

  debug(`user: ${user}`)
  return !!(await contains(members, user))
}
