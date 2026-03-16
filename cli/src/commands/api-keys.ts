/**
 * agentnode api-keys — manage API keys.
 * Subcommands: create, list, set, remove
 */

import { Command } from "commander";
import chalk from "chalk";
import { createInterface } from "node:readline";
import { loadConfig, saveConfig } from "../config.js";
import { createApiKey } from "../api.js";

function prompt(question: string): Promise<string> {
  const rl = createInterface({
    input: process.stdin,
    output: process.stdout,
  });
  return new Promise((resolve) => {
    rl.question(question, (answer) => {
      rl.close();
      resolve(answer.trim());
    });
  });
}

const createCommand = new Command("create")
  .description("Create a new API key (requires Bearer token from login)")
  .argument("<label>", "Label for the new API key")
  .option("--token <token>", "Bearer token (or uses stored token)")
  .option("--json", "Output JSON")
  .action(async (label: string, opts) => {
    try {
      let token = opts.token;

      if (!token) {
        const config = loadConfig();
        token = config.api_key;
      }

      if (!token) {
        token = await prompt("Enter your Bearer token: ");
      }

      if (!token) {
        console.error(chalk.red("No token provided. Log in first or pass --token."));
        process.exit(1);
      }

      const result = await createApiKey(label, token);

      if (opts.json) {
        console.log(JSON.stringify(result));
      } else {
        console.log(chalk.green(`✓ API key created`));
        console.log(`  Label:  ${result.label}`);
        console.log(`  Key:    ${result.api_key}`);
        console.log(chalk.yellow(`  Save this key — it will not be shown again.`));
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

const listCommand = new Command("list")
  .description("Show currently configured API key")
  .option("--json", "Output JSON")
  .action((opts) => {
    const config = loadConfig();
    const envKey = process.env.AGENTNODE_API_KEY;
    const configKey = config.api_key;

    if (opts.json) {
      console.log(JSON.stringify({
        source: envKey ? "environment" : configKey ? "config" : "none",
        key_preview: envKey
          ? maskKey(envKey)
          : configKey
            ? maskKey(configKey)
            : null,
        username: config.username || null,
      }));
      return;
    }

    if (envKey) {
      console.log(`  Source:   ${chalk.cyan("AGENTNODE_API_KEY environment variable")}`);
      console.log(`  Key:     ${maskKey(envKey)}`);
    } else if (configKey) {
      console.log(`  Source:   ${chalk.cyan("~/.agentnode/config.json")}`);
      console.log(`  Key:     ${maskKey(configKey)}`);
      if (config.username) {
        console.log(`  User:    ${config.username}`);
      }
    } else {
      console.log(chalk.dim("No API key configured. Run `agentnode login` or `agentnode api-keys set <key>`."));
    }
  });

const setCommand = new Command("set")
  .description("Store an API key in local config")
  .argument("<key>", "The API key to store")
  .action((key: string) => {
    if (!key) {
      console.error(chalk.red("No key provided."));
      process.exit(1);
    }

    const config = loadConfig();
    saveConfig({ ...config, api_key: key });
    console.log(chalk.green(`✓ API key saved to config.`));
  });

const removeCommand = new Command("remove")
  .description("Remove stored API key from config")
  .action(() => {
    const config = loadConfig();

    if (!config.api_key) {
      console.log(chalk.dim("No API key stored in config."));
      return;
    }

    const { api_key, username, ...rest } = config;
    saveConfig(rest as any);
    console.log(chalk.green(`✓ API key removed from config.`));
  });

export const apiKeysCommand = new Command("api-keys")
  .description("Manage API keys")
  .addCommand(createCommand)
  .addCommand(listCommand)
  .addCommand(setCommand)
  .addCommand(removeCommand);

function maskKey(key: string): string {
  if (key.length <= 8) {
    return "****" + key.slice(-4);
  }
  return key.slice(0, 4) + "****" + key.slice(-4);
}
