import { getToken, contains, flatten } from '@src/utils'
import { debug } from '@actions/core'
import { getOctokit } from '@actions/github'

export interface Item {
  id: number
  number: number
  title: string
}

export interface Owner {
  org?: string | null
  user?: string | null
  repo?: string | null
}

function formatOwner(owner: Owner): string {
  if (owner.org) {
    debug(`org: ${owner.org}`)
    return `organization(login: "${owner.org}")`
  } else if (owner.user) {
    debug(`user: ${owner.user}`)
    return `user(login: "${owner.user}")`
  } else if (owner.repo) {
    const [o, n] = owner.repo.split('/')
    debug(`repo: ${o}/${n}`)
    return `repository(owner: "${o}", name: "${n}")`
  } else {
    throw new Error('Input required and not supplied: org, user, or repo')
  }
}

const octokit = getOctokit(getToken())

/**
 * Asynchronous generator returning all issues of a given project.
 *
 * @param owner The organization name (e.g. conda), user login (e.g. conda-bot), or repository path (e.g. conda/conda).
 * @param project The project number (e.g. 5).
 * @return List of issues associated with the org/user/repo project.
 */
export async function* getProjectIssues(
  owner: Owner,
  project: number,
): AsyncGenerator<Item> {
  // define the query type
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
  const qowner = formatOwner(owner)
  debug(`project: ${project}`)

  // execute query with pagination
  let hasNext = true
  let cursor = ''
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
      }`,
    )

    // if no nodes then stop processing/querying
    const rcolumns = (resp?.organization || resp?.user || resp?.repository)
      ?.project?.columns
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
 * @param owner The organization name (e.g. conda), user login (e.g. conda-bot),
 * or repository path (e.g. conda/conda) within which to find the project.
 * @param project The project number (e.g. 5).
 * @param issue The issue id (e.g. ????).
 * @return If issue provided return issue if it exists in project or list all issues
 * in project.
 */
export async function issueInProject(
  owner: Owner,
  project: number,
  issue: number | null | undefined = undefined,
): Promise<boolean | Item[]> {
  const issues = getProjectIssues(owner, project)
  if (!issue) return await flatten(issues)

  debug(`issue: ${issue}`)
  return !!(await contains(issues, { id: issue }))
}
