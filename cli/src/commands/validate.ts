/**
 * agentnode validate <dir> — validate an ANP manifest.
 * Spec §13.5
 */

import { Command } from "commander";
import chalk from "chalk";
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";

export const validateCommand = new Command("validate")
  .description("Validate an ANP package manifest")
  .argument("[dir]", "Package directory", ".")
  .option("--json", "Output JSON")
  .action(async (dir: string, opts) => {
    try {
      // Look for agentnode.yaml (try JSON first since YAML needs parsing)
      const yamlPath = join(dir, "agentnode.yaml");
      const jsonPath = join(dir, "agentnode.json");

      let manifest: any;

      if (existsSync(jsonPath)) {
        manifest = JSON.parse(readFileSync(jsonPath, "utf-8"));
      } else if (existsSync(yamlPath)) {
        // Parse YAML — basic key:value parsing for MVP
        const raw = readFileSync(yamlPath, "utf-8");
        // For now, send to server for validation
        console.log(chalk.yellow("YAML parsing: sending to server for validation..."));
        // We'd need a YAML parser here — for MVP, try server-side validation
        const { getApiUrl, getApiKey } = await import("../config.js");
        const baseUrl = getApiUrl();
        const resp = await fetch(`${baseUrl}/v1/packages/validate`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...(getApiKey() ? { "X-API-Key": getApiKey()! } : {}),
          },
          body: JSON.stringify({ manifest_raw: raw }),
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
        return;
      } else {
        throw new Error(
          `No manifest found in ${dir}. Expected agentnode.yaml or agentnode.json`
        );
      }

      // Send to server for validation
      const { getApiUrl, getApiKey } = await import("../config.js");
      const baseUrl = getApiUrl();
      const resp = await fetch(`${baseUrl}/v1/packages/validate`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(getApiKey() ? { "X-API-Key": getApiKey()! } : {}),
        },
        body: JSON.stringify({ manifest }),
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
