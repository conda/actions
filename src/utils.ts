import * as types from '@src/types'
import { getInput, info } from '@actions/core'
import { readFileSync } from 'fs'
import { homedir } from 'os'
import { join } from 'path'

/**
 * Check whether the item is defined in the generator.
 *
 * @param generator Asynchronous generator to search for item.
 * @param item Key-value pair or value to match in generator.
 * @return The first element matching item.
 */
export async function contains<T>(
  generator: AsyncGenerator<T>,
  item: Partial<T>,
): Promise<T | undefined> {
  let func: (e: T) => boolean

  if (item instanceof Object) {
    if (Object.keys(item).length != 1)
      throw new Error('Invalid item, expected object with one non-null/undefined key-value')

    const kv = Object.entries(item).find((_, v) => v !== null && v !== undefined)
    if (!kv)
      throw new Error('Invalid item, expected object with one non-null/undefined key-value')
    const [key, value] = kv

    // special conditional to extract key and compare with value
    func = (e: T): boolean => (e as { [key: string]: any })[key] == value
  } else {
    // standard conditional to compare element and item
    func = (e: T) => e == item
  }

  // iterate over generator and return first match
  for await (const e of generator)
    if (func(e)) return e
}

/**
 * Flatten a generator into an array of the same type.
 *
 * @param generator Asynchronous generator to flatten into an Array.
 * @return The array of elements from the generator.
 */
export async function flatten<T>(generator: AsyncGenerator<T>): Promise<Array<T>> {
  const arr: Array<T> = []
  for await (const e of generator) arr.push(e)
  return arr
}

/**
 * Fetch the API token needed to authenticate GitHub object.
 *
 * @return The authentication token for getOctokit.
 */
export function getToken(): string {
  try {
    return getInput('github_token', { required: true })
  } catch (e1) {
    try {
      return readFileSync(join(homedir(), '.github_token'), 'utf8').trim()
    } catch (e2) {
      // ENOENT: file non-existent
      if (e2 instanceof Error && e2.name == 'ENOENT') throw e1
      else throw e2
    }
  }
}

/**
 * Helper function to convert owner object into the correct GraphQL query.
 *
 * @param owner The organization name (e.g. conda), user login (e.g. conda-bot), or repository path (e.g. conda/conda).
 * @return The owner object formatted as a GraphQL query string.
 */
export function formatOwner(owner: types.Owner): string {
  if (owner.org) {
    info(`org: ${owner.org}`)
    return `organization(login: "${owner.org}")`
  } else if (owner.user) {
    info(`user: ${owner.user}`)
    return `user(login: "${owner.user}")`
  } else if (owner.repo) {
    const [o, n] = owner.repo.split('/')
    info(`repo: ${o}/${n}`)
    return `repository(owner: "${o}", name: "${n}")`
  } else {
    throw new Error('Input required and not supplied: org, user, or repo')
  }
}
