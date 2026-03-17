/**
 * agentnode install <slug> — full install flow.
 * Spec §13.4
 */

import { Command } from "commander";
import chalk from "chalk";
import { getInstallMetadata, trackInstall } from "../api.js";
import { installPackage } from "../installer.js";

export const installCommand = new Command("install")
  .description("Install a package from the AgentNode registry")
  .argument("<slug>", "Package slug")
  .option("-v, --version <version>", "Specific version to install")
  .option("--verbose", "Show detailed output")
  .option("--json", "Output JSON")
  .action(async (slug: string, opts) => {
    try {
      // 1. Get install metadata
      if (!opts.json) {
        console.log(`Resolving ${slug}...`);
      }
      const meta = await getInstallMetadata(slug, opts.version);

      // Check if artifact is available
      if (!meta.artifact?.url) {
        // Fallback: no artifact, just show metadata
        if (opts.json) {
          console.log(JSON.stringify({ slug: meta.slug, version: meta.version, status: "no_artifact" }));
        } else {
          console.log(chalk.yellow(`\nNo artifact available for ${slug}@${meta.version}.`));
          console.log(`This package is registered but has no downloadable artifact yet.`);
          if (meta.entrypoint) {
            console.log(`\nEntrypoint: ${chalk.cyan(meta.entrypoint)}`);
          }
        }
        return;
      }

      // 2. Track the install with backend
      let installResponse: any = {};
      try {
        installResponse = await trackInstall(slug, meta.version, "install");
      } catch {
        // Non-fatal — we can still install from the metadata
      }

      // 3. Run the full install flow
      // Build tools array from capabilities with entrypoints (v0.2)
      const tools = (meta.capabilities || [])
        .filter((c: any) => c.entrypoint)
        .map((c: any) => ({
          name: c.name,
          entrypoint: c.entrypoint,
          capability_id: c.capability_id,
        }));

      const result = await installPackage(
        {
          artifact_url: meta.artifact.url,
          artifact_hash: meta.artifact.hash_sha256 || "",
          entrypoint: meta.entrypoint || "",
          post_install_code: `from ${(meta.entrypoint || "").split(".")[0]} import tool`,
          package_type: meta.package_type || "toolpack",
          capability_ids: (meta.capabilities || []).map((c: any) => c.capability_id),
          tools,
          deprecated: false,
        },
        slug,
        meta.version,
        "install",
        opts.verbose,
      );

      // 4. Output result
      if (opts.json) {
        console.log(JSON.stringify({
          slug: result.slug,
          version: result.version,
          entrypoint: result.entrypoint,
          was_upgrade: result.wasUpgrade,
          previous_version: result.previousVersion,
        }));
      } else {
        if (result.wasUpgrade) {
          console.log(chalk.green(`\n✓ ${slug} upgraded ${result.previousVersion} → ${result.version}`));
        } else {
          console.log(chalk.green(`\n✓ Installed ${slug}@${result.version}`));
        }

        console.log(chalk.dim(`\nFiles updated:`));
        console.log(`  agentnode.lock`);

        if (result.entrypoint) {
          const moduleName = result.entrypoint.split(".")[0];
          console.log(chalk.dim(`\nNext step:`));
          console.log(chalk.cyan(`  from ${moduleName} import tool`));
        }
      }
    } catch (err: any) {
      if (opts.json) {
        console.log(JSON.stringify({ error: err.message }));
      } else {
        console.error(chalk.red(`\n✗ ${err.message}`));
      }
      process.exit(1);
    }
  });
