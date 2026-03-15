import { Command } from "commander";
import { readFileSync } from "node:fs";
import { publishPackage } from "../api.js";

export const publishCommand = new Command("publish")
  .description("Publish a package to the AgentNode registry")
  .argument("<manifest>", "Path to agentnode.yaml manifest file")
  .requiredOption("--token <token>", "Authentication token")
  .action(async (manifestPath: string, opts) => {
    try {
      const manifestContent = readFileSync(manifestPath, "utf-8");

      // Parse YAML to JSON (for now, expect JSON manifest)
      let manifest: string;
      try {
        JSON.parse(manifestContent);
        manifest = manifestContent;
      } catch {
        console.error("Manifest must be valid JSON. YAML support coming soon.");
        process.exit(1);
      }

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
