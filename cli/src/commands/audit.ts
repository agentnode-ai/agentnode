/**
 * agentnode audit <slug> — show trust and security info.
 * Spec §13.5
 */

import { Command } from "commander";
import chalk from "chalk";
import { getPackage } from "../api.js";

export const auditCommand = new Command("audit")
  .description("Show trust and security information for a package")
  .argument("<slug>", "Package slug")
  .option("--json", "Output JSON")
  .action(async (slug: string, opts) => {
    try {
      const pkg = await getPackage(slug);
      const trust = pkg.blocks?.trust || {};
      const permissions = pkg.blocks?.permissions || {};

      if (opts.json) {
        console.log(JSON.stringify({ trust, permissions }));
        return;
      }

      console.log(chalk.bold(`\nSecurity audit: ${slug}\n`));

      // Trust info
      console.log(chalk.dim("Trust:"));
      console.log(`  Publisher trust:  ${colorTrust(trust.publisher_trust_level)}`);
      console.log(`  Signature:        ${trust.signature_present ? chalk.green("present") : chalk.dim("none")}`);
      console.log(`  Provenance:       ${trust.provenance_present ? chalk.green("verified") : chalk.dim("none")}`);
      console.log(`  Security issues:  ${trust.security_findings_count > 0 ? chalk.red(trust.security_findings_count) : chalk.green("0")}`);

      // Permissions
      console.log(chalk.dim("\nPermissions:"));
      console.log(`  Network:          ${colorPerm(permissions.network_level)}`);
      console.log(`  Filesystem:       ${colorPerm(permissions.filesystem_level)}`);
      console.log(`  Code execution:   ${colorPerm(permissions.code_execution_level)}`);
      console.log(`  Data access:      ${colorPerm(permissions.data_access_level)}`);
      console.log(`  User approval:    ${permissions.user_approval_level || "N/A"}`);
    } catch (err: any) {
      console.error(chalk.red(`✗ ${err.message}`));
      process.exit(1);
    }
  });

function colorTrust(level: string): string {
  switch (level) {
    case "curated": return chalk.blue(level);
    case "trusted": return chalk.green(level);
    case "verified": return chalk.yellow(level);
    default: return chalk.dim(level || "unknown");
  }
}

function colorPerm(level: string): string {
  if (!level || level === "none") return chalk.green(level || "none");
  if (level === "unrestricted" || level === "shell" || level === "any")
    return chalk.red(level);
  return chalk.yellow(level);
}
