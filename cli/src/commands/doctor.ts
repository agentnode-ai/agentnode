/**
 * agentnode doctor — analyze installed packages, check compatibility,
 * suggest missing upgrades. Spec §20.3 Phase 1.1
 */

import { Command } from "commander";
import chalk from "chalk";
import { readLockfile } from "../lockfile.js";
import { getPackage, checkUpdates, recommend } from "../api.js";

export const doctorCommand = new Command("doctor")
  .description("Analyze your agent setup and suggest improvements")
  .option("--json", "Output JSON")
  .action(async (opts) => {
    try {
      const lockfile = readLockfile();
      const slugs = Object.keys(lockfile.packages);

      if (slugs.length === 0) {
        console.log(chalk.dim("\nNo packages installed. Run `agentnode install <slug>` to get started.\n"));
        return;
      }

      console.log(chalk.bold(`\nAgentNode Doctor\n`));
      console.log(chalk.dim(`Checking ${slugs.length} installed package(s)...\n`));

      const issues: { type: string; slug: string; message: string }[] = [];
      const installedCapabilities: string[] = [];

      // Fetch all package details in parallel instead of sequentially
      const pkgResults = await Promise.allSettled(
        slugs.map((slug) => getPackage(slug).then((pkg) => ({ slug, pkg })))
      );

      for (const result of pkgResults) {
        if (result.status === "rejected") continue;
        const { slug, pkg } = result.value;

        // Collect capabilities
        const caps = pkg.blocks?.capabilities || [];
        for (const cap of caps) {
          if (cap.capability_id && !installedCapabilities.includes(cap.capability_id)) {
            installedCapabilities.push(cap.capability_id);
          }
        }

        if (pkg.is_deprecated) {
          issues.push({ type: "warning", slug, message: "Package is deprecated" });
        }

        const trust = pkg.blocks?.trust || {};
        if (trust.security_findings_count > 0) {
          issues.push({
            type: "error",
            slug,
            message: `${trust.security_findings_count} open security finding(s)`,
          });
        }

        const perms = pkg.blocks?.permissions || {};
        if (perms.code_execution_level === "shell") {
          issues.push({ type: "warning", slug, message: "Has shell execution permission" });
        }
        if (perms.network_level === "unrestricted") {
          issues.push({ type: "warning", slug, message: "Has unrestricted network access" });
        }
      }

      // Mark failed fetches
      for (let i = 0; i < pkgResults.length; i++) {
        if (pkgResults[i].status === "rejected") {
          issues.push({ type: "error", slug: slugs[i], message: "Could not fetch package details (may be removed)" });
        }
      }

      // Check for updates (runs in parallel with package fetches if possible,
      // but we need installed caps first for recommendations)
      try {
        const packagesToCheck = slugs.map((s) => ({
          slug: s,
          version: lockfile.packages[s].version,
        }));
        const updateResult = await checkUpdates(packagesToCheck);
        for (const u of updateResult.updates || []) {
          if (u.has_update) {
            issues.push({
              type: "info",
              slug: u.slug,
              message: `Update available: ${u.current_version} → ${u.latest_version}`,
            });
          }
        }
      } catch {
        // non-critical
      }

      if (opts.json) {
        console.log(JSON.stringify({ installed: slugs.length, capabilities: installedCapabilities, issues }));
        return;
      }

      // Print results
      console.log(chalk.dim("Installed capabilities:"));
      if (installedCapabilities.length > 0) {
        console.log(`  ${installedCapabilities.join(", ")}\n`);
      } else {
        console.log(chalk.dim("  (none detected)\n"));
      }

      if (issues.length === 0) {
        console.log(chalk.green("✓ No issues found. Your setup looks good.\n"));
      } else {
        console.log(`Found ${issues.length} issue(s):\n`);
        for (const issue of issues) {
          const icon =
            issue.type === "error" ? chalk.red("✗") :
            issue.type === "warning" ? chalk.yellow("⚠") :
            chalk.blue("ℹ");
          console.log(`  ${icon} ${chalk.bold(issue.slug)}: ${issue.message}`);
        }
        console.log();
      }
    } catch (err: any) {
      console.error(chalk.red(`✗ ${err.message}`));
      process.exit(1);
    }
  });
