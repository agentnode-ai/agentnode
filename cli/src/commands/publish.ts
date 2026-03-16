import { Command } from "commander";
import { readFileSync, existsSync } from "node:fs";
import { join, extname } from "node:path";
import { parse as parseYAML } from "yaml";
import { publishPackage } from "../api.js";

function loadManifest(pathOrDir: string): string {
  let filePath = pathOrDir;

  // If directory, look for agentnode.yaml or agentnode.json
  try {
    const stat = readFileSync(filePath);
    // If it reads, it's a file — handled below
  } catch {
    // Not a file or doesn't exist as-is — check as directory
  }

  if (
    !existsSync(filePath) ||
    (!filePath.endsWith(".yaml") &&
      !filePath.endsWith(".yml") &&
      !filePath.endsWith(".json"))
  ) {
    // Treat as directory
    const yamlPath = join(pathOrDir, "agentnode.yaml");
    const ymlPath = join(pathOrDir, "agentnode.yml");
    const jsonPath = join(pathOrDir, "agentnode.json");

    if (existsSync(yamlPath)) filePath = yamlPath;
    else if (existsSync(ymlPath)) filePath = ymlPath;
    else if (existsSync(jsonPath)) filePath = jsonPath;
    else
      throw new Error(
        `No manifest found in ${pathOrDir}. Expected agentnode.yaml or agentnode.json`
      );
  }

  const raw = readFileSync(filePath, "utf-8");
  const ext = extname(filePath).toLowerCase();

  if (ext === ".yaml" || ext === ".yml") {
    const parsed = parseYAML(raw);
    if (!parsed || typeof parsed !== "object") {
      throw new Error(`Invalid YAML in ${filePath}`);
    }
    return JSON.stringify(parsed);
  }

  // JSON
  JSON.parse(raw); // validate
  return raw;
}

export const publishCommand = new Command("publish")
  .description("Publish a package to the AgentNode registry")
  .argument(
    "<path>",
    "Path to agentnode.yaml manifest file or package directory"
  )
  .requiredOption("--token <token>", "Authentication token")
  .action(async (pathArg: string, opts) => {
    try {
      const manifest = loadManifest(pathArg);

      console.log("Publishing package...");
      const result = await publishPackage(manifest, opts.token);

      console.log(`\n  Published: ${result.slug}@${result.version}`);
      console.log(`  Type:      ${result.package_type}`);
      console.log(`  ${result.message}\n`);
    } catch (err: any) {
      console.error(`Error: ${err.message}`);
      process.exit(1);
    }
  });
