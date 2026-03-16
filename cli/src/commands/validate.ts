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
  .action(async (dir: string, opts) => {
    try {
      const manifest = loadManifest(dir);

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
