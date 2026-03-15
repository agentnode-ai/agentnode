/**
 * agentnode login — prompt for API key and verify.
 * Spec §13.5
 */

import { Command } from "commander";
import chalk from "chalk";
import { createInterface } from "node:readline";
import { saveConfig, loadConfig } from "../config.js";

export const loginCommand = new Command("login")
  .description("Authenticate with the AgentNode registry")
  .option("--api-key <key>", "API key (or set AGENTNODE_API_KEY env var)")
  .action(async (opts) => {
    let apiKey = opts.apiKey;

    if (!apiKey) {
      const rl = createInterface({ input: process.stdin, output: process.stdout });
      apiKey = await new Promise<string>((resolve) => {
        rl.question("Enter your API key: ", (answer) => {
          rl.close();
          resolve(answer.trim());
        });
      });
    }

    if (!apiKey) {
      console.error(chalk.red("No API key provided."));
      process.exit(1);
    }

    // Verify the key by calling /v1/auth/me
    const config = loadConfig();
    const baseUrl = process.env.AGENTNODE_API_URL || config.api_url || "https://api.agentnode.net/v1";

    try {
      const resp = await fetch(`${baseUrl}/v1/auth/me`, {
        headers: { "X-API-Key": apiKey },
        signal: AbortSignal.timeout(10_000),
      });

      if (!resp.ok) {
        console.error(chalk.red("Authentication failed. Check your API key."));
        process.exit(1);
      }

      const user = await resp.json();
      saveConfig({ ...config, api_key: apiKey, username: user.username });
      console.log(chalk.green(`✓ Logged in as ${user.username}`));
    } catch (err: any) {
      console.error(chalk.red(`Login failed: ${err.message}`));
      process.exit(1);
    }
  });
