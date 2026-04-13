/**
 * agentnode login — prompt for API key and verify.
 * Spec §13.5
 */

import { Command } from "commander";
import chalk from "chalk";
import { createInterface } from "node:readline";
import { saveConfig, loadConfig } from "../config.js";

/**
 * Prompt for a secret without echoing characters to the terminal.
 *
 * P1-C2: Previously the login flow echoed the API key as the user typed it,
 * leaving the full key visible in the terminal scrollback and any active
 * screen-recording/shoulder-surfing window. We mute echo by overriding the
 * internal `_writeToOutput` hook on the readline interface, which is the
 * standard Node.js pattern for hidden-input prompts.
 */
async function promptSecret(prompt: string): Promise<string> {
  return new Promise((resolveFn) => {
    const rl = createInterface({ input: process.stdin, output: process.stdout });
    const rlAny = rl as unknown as { _writeToOutput: (s: string) => void };
    // Print the prompt, then mute output so the typed key is not echoed.
    process.stdout.write(prompt);
    rlAny._writeToOutput = (s: string) => {
      // Still forward newlines (so the user sees a line break after Enter)
      // but drop the echoed characters.
      if (s === "\n" || s === "\r\n" || s === "\r") {
        process.stdout.write(s);
      }
    };
    rl.question("", (answer: string) => {
      rl.close();
      resolveFn(answer.trim());
    });
  });
}

function redactKey(key: string): string {
  if (key.length <= 8) return "***";
  return `${key.slice(0, 4)}…${key.slice(-4)}`;
}

export const loginCommand = new Command("login")
  .description("Authenticate with the AgentNode registry")
  .option("--api-key <key>", "API key (or set AGENTNODE_API_KEY env var)")
  .action(async (opts) => {
    let apiKey = opts.apiKey;

    if (!apiKey) {
      apiKey = await promptSecret("Enter your API key: ");
    }

    if (!apiKey) {
      console.error(chalk.red("No API key provided."));
      process.exit(1);
    }

    // Verify the key by calling /v1/auth/me
    const config = loadConfig();
    const baseUrl = process.env.AGENTNODE_API_URL || config.api_url || "https://api.agentnode.net";

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
      console.log(
        chalk.green(
          `✓ Logged in as ${user.username} (${redactKey(apiKey)})`,
        ),
      );
    } catch (err: any) {
      console.error(chalk.red(`Login failed: ${err.message}`));
      process.exit(1);
    }
  });
