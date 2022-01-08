import { setFailed } from '@actions/core'
import { cli } from '@src/cli'
import { gha } from '@src/gha'
export { cli, gha }
export * as api from '@src/api'
export * as utils from '@src/utils'

if (require.main === module) {
  try {
    // make a guess as to whether running as CLI tool or GitHub Action
    process.argv.length > 2 ? cli() : gha()
  } catch (err) {
    if (err instanceof Error) setFailed(err)
    else throw err
  }
}
