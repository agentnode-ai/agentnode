/**
 * agentnode validate <dir> — validate an ANP manifest.
 * Spec §13.5
 */

import { Command } from "commander";
import chalk from "chalk";
import { readFileSync, existsSync } from "node:fs";
import { join, extname } from "node:path";
import { parse as parseYAML } from "yaml";

function loadManifest(dir: string): any {
  const yamlPath = join(dir, "agentnode.yaml");
  const ymlPath = join(dir, "agentnode.yml");
  const jsonPath = join(dir, "agentnode.json");

  if (existsSync(yamlPath)) {
    const raw = readFileSync(yamlPath, "utf-8");
    const parsed = parseYAML(raw);
    if (!parsed || typeof parsed !== "object") {
      throw new Error(`Invalid YAML in ${yamlPath}`);
    }
    return parsed;
  }

  if (existsSync(ymlPath)) {
    const raw = readFileSync(ymlPath, "utf-8");
    const parsed = parseYAML(raw);
    if (!parsed || typeof parsed !== "object") {
      throw new Error(`Invalid YAML in ${ymlPath}`);
    }
    return parsed;
  }

  if (existsSync(jsonPath)) {
    return JSON.parse(readFileSync(jsonPath, "utf-8"));
  }

  throw new Error(
    `No manifest found in ${dir}. Expected agentnode.yaml or agentnode.json`
  );
}

export const validateCommand = new Command("validate")
  .description("Validate an ANP package manifest")
  .argument("[dir]", "Package directory", ".")
  .option("--json", "Output JSON")
  .option(
    "--timeout <seconds>",
    "Network timeout in seconds for the validate API call",
    "30",
  )
  .option(
    "--no-network",
    "Perform only local manifest parsing; skip the server validate API call",
  )
  .action(async (dir: string, opts) => {
    try {
      const manifest = loadManifest(dir);

      // P1-C10: --no-network lets users validate in air-gapped / offline
      // environments. We only do the local YAML/JSON parse (loadManifest
      // above already caught structural errors) and a minimal shape check.
      // Commander negates --no-network into opts.network === false.
      if (opts.network === false) {
        const errors: string[] = [];
        if (!manifest.package_id && !manifest.slug) errors.push("package_id is required");
        if (!manifest.version) errors.push("version is required");
        if (!manifest.package_type) errors.push("package_type is required");
        if (opts.json) {
          console.log(JSON.stringify({ valid: errors.length === 0, errors, offline: true }));
        } else if (errors.length === 0) {
          console.log(chalk.green("✓ Manifest parses and has required fields (offline check)"));
          console.log(chalk.dim("  Skipped server validation — re-run without --no-network for full check."));
        } else {
          console.log(chalk.red("✗ Manifest validation failed (offline check):"));
          for (const err of errors) console.log(chalk.red(`  - ${err}`));
          process.exit(1);
        }
        return;
      }

      // P1-C9: Previously the validate command had no timeout and could
      // hang indefinitely on a wedged server. Default 30s, opt-out via
      // --timeout.
      const timeoutSeconds = Number(opts.timeout);
      if (!Number.isFinite(timeoutSeconds) || timeoutSeconds <= 0) {
        throw new Error(
          `--timeout must be a positive number (got '${opts.timeout}')`,
        );
      }

      const { getApiUrl, getApiKey } = await import("../config.js");
      const baseUrl = getApiUrl();
      const resp = await fetch(`${baseUrl}/v1/packages/validate`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(getApiKey() ? { "X-API-Key": getApiKey()! } : {}),
        },
        body: JSON.stringify({ manifest }),
        signal: AbortSignal.timeout(timeoutSeconds * 1000),
      });
      const data = await resp.json();

      if (opts.json) {
        console.log(JSON.stringify(data));
      } else {
        if (data.valid) {
          console.log(chalk.green("✓ Manifest is valid"));
        } else {
          console.log(chalk.red("✗ Manifest validation failed:"));
          for (const err of data.errors || []) {
            console.log(chalk.red(`  - ${err}`));
          }
        }
        for (const warn of data.warnings || []) {
          console.log(chalk.yellow(`  ⚠ ${warn}`));
        }
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
