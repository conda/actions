import { info } from '@actions/core'
import * as types from '@src/types'
import { getToken, formatOwner } from '@src/utils'
import { getOctokit } from "@actions/github"

const octokit = getOctokit(getToken())

/**
 * Asynchronous generator returning all issues of a given project.
 *
 * @param owner The organization name (e.g. conda), user login (e.g. conda-bot), or repository path (e.g. conda/conda).
 * @param project The project number (e.g. 5).
 * @return List of issues associated with the org/user/repo project.
 */
export async function* getProjectIssues(
  owner: types.Owner,
  project: number,
): AsyncGenerator<types.Item> {
  // define the query's types
  interface QueryType {
    project?: {
      columns: {
        nodes: {
          cards: {
            edges: {
              node: {
                content: {
                  databaseId: number
                  number: number
                  title: string
                }
              }
            }[]
          }
        }[]
        pageInfo: {
          endCursor: string
          hasNextPage: boolean
        }
      }
    }
  }

  // get GraphQL query parts
  const qowner = formatOwner(owner);
  info(`project: ${project}`)

  // execute query with pagination
  let hasNext = true
  let cursor = ""
  while (hasNext) {
    const resp: {
      organization?: QueryType
      user?: QueryType
      repository?: QueryType
    } = await octokit.graphql(
      `{
        ${qowner} {
          project(number: ${project}) {
            columns(first: 100, ${cursor}) {
              nodes {
                cards {
                  edges {
                    node {
                      content {
                        ... on Issue {
                          databaseId
                          number
                          title
                        }
                      }
                    }
                  }
                }
              }
              pageInfo {
                endCursor
                hasNextPage
              }
            }
          }
        }
      }`
    )

    // if no nodes then stop processing/querying
    const rcolumns = (resp?.organization || resp?.user || resp?.repository)?.project?.columns
    if (!rcolumns?.nodes) break

    // store cursor for next iteration
    hasNext = rcolumns.pageInfo.hasNextPage
    cursor = 'after: "' + rcolumns.pageInfo.endCursor + '"'

    // flatten structure and yield
    for (const node of rcolumns.nodes) {
      for (const edge of node.cards.edges) {
        // if no content, skip to next node
        if (!edge.node.content) continue

        yield {
          id: edge.node.content.databaseId,
          number: edge.node.content.number,
          title: edge.node.content.title,
        }
      }
    }
  }
}

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
  const qowner = formatOwner({ org: org });
  info(`team: ${team}`)

  // execute query with pagination
  let hasNext = true
  let cursor = ""
  while (hasNext) {
    const resp: QueryType = await octokit.graphql(
      `{
        ${qowner} {
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
      }`
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
