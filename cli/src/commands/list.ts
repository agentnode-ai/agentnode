/**
 * agentnode list — show installed packages from lockfile.
 * Spec §13.5
 */

import { Command } from "commander";
import chalk from "chalk";
import { readLockfile } from "../lockfile.js";

export const listCommand = new Command("list")
  .description("List installed AgentNode packages")
  .option("--json", "Output JSON")
  .action((opts) => {
    const lf = readLockfile();
    const packages = Object.entries(lf.packages);

    if (opts.json) {
      console.log(JSON.stringify(lf));
      return;
    }

    if (packages.length === 0) {
      console.log(chalk.dim("No packages installed."));
      console.log(chalk.dim("Run `agentnode install <slug>` to install a package."));
      return;
    }

    console.log(chalk.bold(`Installed packages (${packages.length}):\n`));

    for (const [slug, entry] of packages) {
      console.log(
        `  ${chalk.cyan(slug)} ${chalk.dim("@")}${entry.version}  ` +
        chalk.dim(`[${entry.package_type}] → ${entry.entrypoint}`)
      );
      if (entry.capability_ids.length > 0) {
        console.log(chalk.dim(`    capabilities: ${entry.capability_ids.join(", ")}`));
      }
    }

    console.log(chalk.dim(`\nLockfile: agentnode.lock (updated ${lf.updated_at})`));
  });
