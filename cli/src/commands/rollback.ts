/**
 * agentnode rollback <slug>@<version> — install a specific older version.
 * Spec §13.5
 */

import { Command } from "commander";
import chalk from "chalk";
import { getInstallMetadata, trackInstall } from "../api.js";
import { installPackage } from "../installer.js";

export const rollbackCommand = new Command("rollback")
  .description("Roll back a package to a specific version")
  .argument("<target>", "Package slug@version (e.g. pdf-reader-pack@0.9.0)")
  .option("--verbose", "Show detailed output")
  .option("--json", "Output JSON")
  .action(async (target: string, opts) => {
    try {
      const atIndex = target.lastIndexOf("@");
      if (atIndex <= 0) {
        throw new Error("Format: <slug>@<version> (e.g. pdf-reader-pack@0.9.0)");
      }

      const slug = target.substring(0, atIndex);
      const version = target.substring(atIndex + 1);

      if (!opts.json) {
        console.log(`Rolling back ${slug} to ${version}...`);
      }

      const meta = await getInstallMetadata(slug, version);

      if (!meta.artifact?.url) {
        throw new Error(`No artifact available for ${slug}@${version}`);
      }

      // Track as rollback — does NOT increment download_count
      try { await trackInstall(slug, version, "rollback"); } catch { /* non-fatal */ }

      const result = await installPackage(
        {
          artifact_url: meta.artifact.url,
          artifact_hash: meta.artifact.hash_sha256 || "",
          entrypoint: meta.entrypoint || "",
          post_install_code: "",
          package_type: meta.package_type || "toolpack",
          capability_ids: (meta.capabilities || []).map((c: any) => c.capability_id),
          deprecated: false,
        },
        slug,
        version,
        "rollback",
        opts.verbose,
      );

      if (opts.json) {
        console.log(JSON.stringify({ slug, version: result.version, rolled_back: true }));
      } else {
        console.log(chalk.green(`✓ Rolled back ${slug} to ${result.version}`));
      }
    } catch (err: any) {
      if (opts.json) {
        console.log(JSON.stringify({ error: err.message }));
      } else {
        console.error(chalk.red(`✗ ${err.message}`));
      }
      process.exit(1);
    }
  });
