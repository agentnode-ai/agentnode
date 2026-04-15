/**
 * agentnode install <slug> — full install flow.
 * Spec §13.4
 */

import { Command } from "commander";
import chalk from "chalk";
import { createInterface } from "node:readline";
import { getInstallMetadata, trackInstall } from "../api.js";
import { installPackage } from "../installer.js";
import { hasLocalCredential, PROVIDERS, runAuthFlow } from "./auth.js";

export const installCommand = new Command("install")
  .description("Install a package from the AgentNode registry")
  .argument("<slug>", "Package slug")
  .option("-v, --version <version>", "Specific version to install")
  // P1-C5: --pkg-version is an unambiguous alias for --version so scripts
  // that call `agentnode install <slug> --pkg-version 1.2.3` never collide
  // visually with the global `agentnode --version` flag. Both forms work.
  .option("--pkg-version <version>", "Specific version to install (alias for --version)")
  .option("--verbose", "Show detailed output")
  .option("--json", "Output JSON")
  .option(
    "--allow-unhashed",
    "Proceed even when the server did not return an artifact hash " +
      "(unsafe — only use for local testing or development packages)",
  )
  .action(async (slug: string, opts) => {
    try {
      // P1-C5: accept either --version or --pkg-version (alias).
      const pinnedVersion: string | undefined = opts.version || opts.pkgVersion;
      // 1. Get install metadata
      if (!opts.json) {
        console.log(`Resolving ${slug}...`);
      }
      const meta = await getInstallMetadata(slug, pinnedVersion);

      // Check if artifact is available (before credential prompt — no point
      // asking for tokens if the package has no downloadable artifact)
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

      // Credential awareness: check if this package requires provider credentials
      const connectorProvider: string | undefined = meta.connector?.provider;
      if (connectorProvider && !opts.json) {
        const providerLower = connectorProvider.toLowerCase();
        const hasEnvCred = !!process.env[`AGENTNODE_CRED_${providerLower.toUpperCase().replace(/-/g, "_")}`];
        const hasLocalCred = hasLocalCredential(providerLower);

        if (!hasEnvCred && !hasLocalCred) {
          const providerDisplay = PROVIDERS[providerLower]?.displayName ?? connectorProvider;
          const rl = createInterface({ input: process.stdin, output: process.stdout });
          const setupNow = await new Promise<boolean>((resolve) => {
            rl.question(
              chalk.yellow(`This package requires ${providerDisplay} credentials. Set up now? [y/N] `),
              (answer) => {
                rl.close();
                resolve(answer.trim().toLowerCase() === "y");
              },
            );
          });

          if (setupNow) {
            const success = await runAuthFlow(providerLower);
            if (!success) {
              console.log(chalk.yellow(`\nContinuing install without ${providerDisplay} credentials.`));
            }
          } else {
            console.log(
              chalk.yellow(
                `\nInstalling without credentials. Run ${chalk.cyan(`agentnode auth ${providerLower}`)} before using this package.`,
              ),
            );
          }
        }
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

      // P0-08: Fail closed when the server did not return an artifact
      // hash. Previously the CLI silently bypassed hash verification by
      // defaulting the expected hash to an empty string — a silent
      // downgrade of the documented integrity check. Users can opt into
      // the old behavior for local/dev packages with --allow-unhashed.
      if (!meta.artifact.hash_sha256) {
        if (!opts.allowUnhashed) {
          throw new Error(
            "Server did not return an artifact hash for " +
              `${slug}@${meta.version}; refusing to install without ` +
              "integrity verification. Pass --allow-unhashed to override " +
              "(unsafe — only for local/dev testing).",
          );
        }
        if (!opts.json) {
          console.warn(
            chalk.yellow(
              "⚠ Installing without artifact hash verification (--allow-unhashed).",
            ),
          );
        }
      }

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
