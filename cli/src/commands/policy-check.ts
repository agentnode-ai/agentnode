/**
 * agentnode policy-check — check if a package passes policy constraints.
 * Spec §13.5
 */

import { Command } from "commander";
import chalk from "chalk";
import { checkPolicy } from "../api.js";

export const policyCheckCommand = new Command("policy-check")
  .description("Check if a package passes policy constraints")
  .option("--package <slug>", "Package slug (required)")
  .option("--min-trust <level>", "Minimum trust level (unverified, verified, trusted, curated)")
  .option("--no-shell", "Block packages requiring shell execution")
  .option("--no-network", "Block packages requiring unrestricted network")
  .option("--json", "Output JSON")
  .action(async (opts) => {
    try {
      if (!opts.package) {
        throw new Error("Provide --package <slug>");
      }

      const policy: any = {};
      if (opts.minTrust) policy.min_trust = opts.minTrust;
      if (opts.shell === false) policy.allow_shell = false;
      if (opts.network === false) policy.allow_network = false;

      const result = await checkPolicy(opts.package, policy);

      if (opts.json) {
        console.log(JSON.stringify(result));
        return;
      }

      const resultColor = result.result === "allowed"
        ? chalk.green
        : result.result === "requires_approval"
        ? chalk.yellow
        : chalk.red;

      console.log(chalk.bold(`\nPolicy check: ${opts.package}\n`));
      console.log(`  Result:     ${resultColor(result.result)}`);
      console.log(`  Trust:      ${result.package_trust_level}`);

      if (result.reasons && result.reasons.length > 0) {
        console.log(chalk.dim("\n  Reasons:"));
        for (const reason of result.reasons) {
          console.log(`    → ${reason}`);
        }
      }

      console.log(chalk.dim("\n  Permissions:"));
      const p = result.package_permissions;
      console.log(`    Network:        ${colorPerm(p.network_level)}`);
      console.log(`    Filesystem:     ${colorPerm(p.filesystem_level)}`);
      console.log(`    Code execution: ${colorPerm(p.code_execution_level)}`);
      console.log(`    Data access:    ${colorPerm(p.data_access_level)}`);
      console.log(`    User approval:  ${p.user_approval_level}`);
    } catch (err: any) {
      if (opts.json) {
        console.log(JSON.stringify({ error: err.message }));
      } else {
        console.error(chalk.red(`✗ ${err.message}`));
      }
      process.exit(1);
    }
  });

function colorPerm(level: string): string {
  if (!level || level === "none") return chalk.green(level || "none");
  if (level === "unrestricted" || level === "shell" || level === "any")
    return chalk.red(level);
  return chalk.yellow(level);
}
