import { info } from '@actions/core'
import { cli } from "@src/cli"
import { gha } from "@src/gha"
export { cli, gha }
export * as api from "@src/api"
export * as queries from "@src/queries"
export * as types from "@src/types"
export * as utils from "@src/utils"

if (require.main === module) {
  // make a guess as to whether running as CLI tool or GitHub Action
  if (process.argv.length > 2) {
    info("Invoking as CLI tool")
    cli()
  } else {
    info("Invoking as GitHub Action")
    gha()
  }
}
