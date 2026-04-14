/**
 * agentnode credentials — manage stored credentials.
 * Subcommands: list, test, delete
 */

import { Command } from "commander";
import chalk from "chalk";
import { getApiUrl, getApiKey } from "../config.js";

function requireAuth(): string {
  const key = getApiKey();
  if (!key) {
    console.error(
      chalk.red("Authentication required. Run `agentnode login` or `agentnode api-keys set <key>` first."),
    );
    process.exit(1);
  }
  return key;
}

function getHeaders(apiKey: string): Record<string, string> {
  return {
    "Content-Type": "application/json",
    "X-API-Key": apiKey,
  };
}

async function apiRequest(method: string, path: string, apiKey: string, body?: unknown): Promise<any> {
  const url = `${getApiUrl()}${path}`;
  const options: RequestInit = {
    method,
    headers: getHeaders(apiKey),
    signal: AbortSignal.timeout(30_000),
  };
  if (body) {
    options.body = JSON.stringify(body);
  }

  const resp = await fetch(url, options);

  if (resp.status === 204) return null;

  const ctype = (resp.headers?.get?.("content-type") || "").toLowerCase();
  let data: any = {};
  if (ctype.includes("json") || ctype === "") {
    try {
      data = await resp.json();
    } catch {
      data = {};
    }
  }

  if (!resp.ok) {
    if (resp.status === 401) {
      throw new Error("Authentication failed (401). Check your API key.");
    }
    const err = data?.error || {};
    throw new Error(`[${err.code || resp.status}] ${err.message || "Request failed"}`);
  }

  return data;
}

const listSubcommand = new Command("list")
  .description("List stored credentials")
  .option("--json", "Output JSON")
  .action(async (opts) => {
    const apiKey = requireAuth();
    try {
      const data = await apiRequest("GET", "/v1/credentials/", apiKey);
      const creds = data.credentials || [];

      if (opts.json) {
        console.log(JSON.stringify(data, null, 2));
        return;
      }

      if (creds.length === 0) {
        console.log(chalk.dim("No credentials stored."));
        return;
      }

      console.log(chalk.bold(`Credentials (${creds.length}):\n`));
      for (const c of creds) {
        const status =
          c.status === "active"
            ? chalk.green("active")
            : chalk.red(c.status);
        const domains = (c.allowed_domains || []).join(", ") || chalk.dim("none");
        console.log(`  ${chalk.cyan(c.id.slice(0, 8))}  ${c.connector_provider.padEnd(10)} ${status}  domains=[${domains}]`);
        if (c.scopes?.length) {
          console.log(`           scopes: ${c.scopes.join(", ")}`);
        }
      }
    } catch (err: any) {
      console.error(chalk.red(`Error: ${err.message}`));
      process.exit(1);
    }
  });

const testSubcommand = new Command("test")
  .description("Test a credential's connectivity")
  .argument("<id>", "Credential ID (full or prefix)")
  .option("--json", "Output JSON")
  .action(async (id: string, opts) => {
    const apiKey = requireAuth();
    try {
      const data = await apiRequest("POST", `/v1/credentials/${id}/test`, apiKey);

      if (opts.json) {
        console.log(JSON.stringify(data, null, 2));
        return;
      }

      if (data.reachable) {
        console.log(chalk.green(`Reachable`));
        if (data.latency_ms != null) {
          console.log(`  Latency: ${data.latency_ms}ms`);
        }
        if (data.status_code != null) {
          console.log(`  Status:  ${data.status_code}`);
        }
      } else {
        console.log(chalk.red(`Not reachable`));
      }
      console.log(`  Message: ${data.message}`);
    } catch (err: any) {
      if (opts.json) {
        console.log(JSON.stringify({ error: err.message }));
      } else {
        console.error(chalk.red(`Error: ${err.message}`));
      }
      process.exit(1);
    }
  });

const deleteSubcommand = new Command("delete")
  .description("Delete (revoke) a stored credential")
  .argument("<id>", "Credential ID")
  .option("--json", "Output JSON")
  .action(async (id: string, opts) => {
    const apiKey = requireAuth();
    try {
      await apiRequest("DELETE", `/v1/credentials/${id}`, apiKey);

      if (opts.json) {
        console.log(JSON.stringify({ deleted: true, id }));
      } else {
        console.log(chalk.green(`Credential ${id} deleted.`));
      }
    } catch (err: any) {
      if (opts.json) {
        console.log(JSON.stringify({ error: err.message }));
      } else {
        console.error(chalk.red(`Error: ${err.message}`));
      }
      process.exit(1);
    }
  });

export const credentialsCommand = new Command("credentials")
  .description("Manage stored credentials")
  .addCommand(listSubcommand)
  .addCommand(testSubcommand)
  .addCommand(deleteSubcommand);
