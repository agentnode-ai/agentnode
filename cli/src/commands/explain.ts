/**
 * agentnode explain <slug> — detailed capability/permission explanation.
 * Spec §20.3 Phase 1.1
 */

import { Command } from "commander";
import chalk from "chalk";
import { getPackage } from "../api.js";

export const explainCommand = new Command("explain")
  .description("Explain what a package does, its capabilities, permissions, and use cases")
  .argument("<slug>", "Package slug")
  .option("--json", "Output JSON")
  .action(async (slug: string, opts) => {
    try {
      const pkg = await getPackage(slug);

      if (opts.json) {
        console.log(JSON.stringify(pkg));
        return;
      }

      console.log(chalk.bold(`\n${pkg.name}`));
      console.log(chalk.dim(`${pkg.slug} v${pkg.latest_version?.version_number || "?"}\n`));
      console.log(`${pkg.summary}\n`);

      if (pkg.description) {
        console.log(chalk.dim("Description:"));
        console.log(`  ${pkg.description.trim()}\n`);
      }

      // Capabilities
      const caps = pkg.blocks?.capabilities || [];
      if (caps.length > 0) {
        console.log(chalk.dim("Capabilities:"));
        for (const cap of caps) {
          console.log(`  ${chalk.cyan(cap.name)} (${cap.capability_id})`);
          if (cap.description) {
            console.log(`    ${cap.description}`);
          }
          if (cap.input_schema?.properties) {
            const params = Object.entries(cap.input_schema.properties).map(
              ([k, v]: [string, any]) => `${k}: ${v.type || "any"}`
            );
            console.log(chalk.dim(`    Parameters: ${params.join(", ")}`));
          }
        }
        console.log();
      }

      // Use cases
      const recommended = pkg.blocks?.recommended_for || [];
      if (recommended.length > 0) {
        console.log(chalk.dim("Recommended for:"));
        for (const rec of recommended) {
          console.log(`  • ${rec.agent_type} (missing: ${rec.missing_capability})`);
        }
        console.log();
      }

      // Compatibility
      const compat = pkg.blocks?.compatibility || {};
      if (compat.frameworks) {
        console.log(chalk.dim("Compatibility:"));
        console.log(`  Frameworks: ${compat.frameworks.join(", ")}`);
        if (compat.python) console.log(`  Python:     ${compat.python}`);
        console.log();
      }

      // Permissions
      const perms = pkg.blocks?.permissions || {};
      console.log(chalk.dim("Permissions:"));
      console.log(`  Network:        ${formatPerm(perms.network_level)}`);
      console.log(`  Filesystem:     ${formatPerm(perms.filesystem_level)}`);
      console.log(`  Code execution: ${formatPerm(perms.code_execution_level)}`);
      console.log(`  Data access:    ${formatPerm(perms.data_access_level)}`);
      console.log(`  User approval:  ${perms.user_approval_level || "never"}`);
      console.log();

      // Trust
      const trust = pkg.blocks?.trust || {};
      console.log(chalk.dim("Trust:"));
      console.log(`  Publisher: ${trust.publisher_trust_level || "unverified"}`);
      console.log(`  Signature: ${trust.signature_present ? "yes" : "no"}`);
      console.log(`  Provenance: ${trust.provenance_present ? "yes" : "no"}`);
      console.log(`  Security findings: ${trust.security_findings_count ?? 0}`);
      console.log();

      // Install
      const install = pkg.blocks?.install || {};
      console.log(chalk.dim("Install:"));
      console.log(`  ${chalk.cyan(install.cli_command || `agentnode install ${slug}`)}`);
      if (install.entrypoint) {
        console.log(`  Entrypoint: ${install.entrypoint}`);
      }
      console.log();
    } catch (err: any) {
      console.error(chalk.red(`✗ ${err.message}`));
      process.exit(1);
    }
  });

function formatPerm(level: string): string {
  if (!level || level === "none") return chalk.green(level || "none");
  if (level === "unrestricted" || level === "shell" || level === "any")
    return chalk.red(level);
  return chalk.yellow(level);
}
