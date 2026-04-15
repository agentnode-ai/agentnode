/**
 * agentnode auth — manage local credentials for connector packages.
 *
 * Mental model:
 * - `agentnode auth` = local (own tokens, no account needed)
 * - `agentnode credentials` = server-side (OAuth via AgentNode backend)
 */

import { Command } from "commander";
import chalk from "chalk";
import { createInterface } from "node:readline";
import {
  readFileSync,
  writeFileSync,
  mkdirSync,
  existsSync,
  chmodSync,
  renameSync,
  unlinkSync,
} from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { randomBytes } from "node:crypto";

// ---------------------------------------------------------------------------
// Provider configuration (static — no registry lookup)
// ---------------------------------------------------------------------------

interface ProviderConfig {
  displayName: string;
  tokenUrl: string;
  tokenInstructions: string;
  recommendedScopes: string[];
  validateUrl: string; // GET endpoint to validate the token
  authHeader: (token: string) => Record<string, string>;
}

const PROVIDERS: Record<string, ProviderConfig> = {
  github: {
    displayName: "GitHub",
    tokenUrl: "https://github.com/settings/tokens",
    tokenInstructions:
      "Create a GitHub Personal Access Token (classic) at the URL above.\n" +
      "  For fine-grained tokens: https://github.com/settings/tokens?type=beta",
    recommendedScopes: ["repo", "read:user"],
    validateUrl: "https://api.github.com/user",
    authHeader: (token) => ({ Authorization: `Bearer ${token}` }),
  },
  slack: {
    displayName: "Slack",
    tokenUrl: "https://api.slack.com/apps",
    tokenInstructions:
      "Create a Slack App and generate a Bot Token (xoxb-...) at the URL above.",
    recommendedScopes: ["chat:write"],
    validateUrl: "https://slack.com/api/auth.test",
    authHeader: (token) => ({ Authorization: `Bearer ${token}` }),
  },
};

// ---------------------------------------------------------------------------
// Local credential file helpers
// ---------------------------------------------------------------------------

function getCredentialsPath(): string {
  return join(homedir(), ".agentnode", "credentials.json");
}

interface CredentialEntry {
  access_token: string;
  auth_type: string;
  scopes: string[];
  stored_at: string;
}

interface CredentialsFile {
  version: number;
  providers: Record<string, CredentialEntry>;
}

function loadLocalCredentials(): CredentialsFile {
  const path = getCredentialsPath();
  if (!existsSync(path)) {
    return { version: 1, providers: {} };
  }
  try {
    const raw = readFileSync(path, "utf-8");
    const data = JSON.parse(raw);
    if (typeof data !== "object" || data === null) {
      return { version: 1, providers: {} };
    }
    return {
      version: data.version ?? 1,
      providers: typeof data.providers === "object" && data.providers !== null
        ? data.providers
        : {},
    };
  } catch {
    return { version: 1, providers: {} };
  }
}

function saveLocalCredentials(data: CredentialsFile): void {
  const path = getCredentialsPath();
  const dir = join(homedir(), ".agentnode");
  mkdirSync(dir, { recursive: true });

  const content = JSON.stringify(data, null, 2) + "\n";

  // Atomic write: temp file + rename
  const tmpPath = join(dir, `.credentials_${randomBytes(6).toString("hex")}.tmp`);
  try {
    writeFileSync(tmpPath, content, "utf-8");
    if (process.platform !== "win32") {
      try {
        chmodSync(tmpPath, 0o600);
      } catch {
        // ignore
      }
    }
    renameSync(tmpPath, path);
  } catch (err) {
    try {
      unlinkSync(tmpPath);
    } catch {
      // ignore
    }
    throw err;
  }
}

function hasLocalCredential(provider: string): boolean {
  const data = loadLocalCredentials();
  return provider.toLowerCase() in data.providers;
}

// ---------------------------------------------------------------------------
// Hidden-input prompt (reused from login.ts pattern)
// ---------------------------------------------------------------------------

async function promptSecret(prompt: string): Promise<string> {
  return new Promise((resolveFn) => {
    const rl = createInterface({ input: process.stdin, output: process.stdout });
    const rlAny = rl as unknown as { _writeToOutput: (s: string) => void };
    process.stdout.write(prompt);
    rlAny._writeToOutput = (s: string) => {
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

async function promptConfirm(message: string): Promise<boolean> {
  return new Promise((resolveFn) => {
    const rl = createInterface({ input: process.stdin, output: process.stdout });
    rl.question(`${message} [y/N] `, (answer: string) => {
      rl.close();
      resolveFn(answer.trim().toLowerCase() === "y");
    });
  });
}

// ---------------------------------------------------------------------------
// Token validation
// ---------------------------------------------------------------------------

async function validateToken(
  provider: string,
  token: string,
): Promise<{ valid: boolean; error?: string }> {
  const config = PROVIDERS[provider];
  if (!config) {
    return { valid: false, error: `Unknown provider: ${provider}` };
  }

  try {
    const resp = await fetch(config.validateUrl, {
      method: "GET",
      headers: {
        ...config.authHeader(token),
        Accept: "application/json",
      },
      signal: AbortSignal.timeout(10_000),
    });

    // Slack's auth.test returns 200 with { ok: false } for bad tokens
    if (provider === "slack") {
      const body = await resp.json() as { ok?: boolean };
      if (!body.ok) {
        return { valid: false, error: "Token validation failed (invalid token)" };
      }
      return { valid: true };
    }

    if (resp.status === 401 || resp.status === 403) {
      return { valid: false, error: "Token validation failed (invalid or expired token)" };
    }
    if (resp.status === 429) {
      // Rate limited — token might be valid, don't block
      return { valid: true };
    }
    if (!resp.ok) {
      return { valid: false, error: `Token validation failed (HTTP ${resp.status})` };
    }

    return { valid: true };
  } catch (err: any) {
    if (err.name === "TimeoutError") {
      return { valid: false, error: "Validation failed (request timed out)" };
    }
    return { valid: false, error: `Validation failed (network error: ${err.message})` };
  }
}

// ---------------------------------------------------------------------------
// Subcommands
// ---------------------------------------------------------------------------

/**
 * `agentnode auth <provider>` — store a token for a provider.
 * Exported for inline use from install command.
 */
export async function runAuthFlow(provider: string, opts: { noValidate?: boolean; json?: boolean } = {}): Promise<boolean> {
  provider = provider.toLowerCase();
  const config = PROVIDERS[provider];

  if (!config) {
    const known = Object.keys(PROVIDERS).join(", ");
    if (opts.json) {
      console.log(JSON.stringify({ error: `Unknown provider: ${provider}. Supported: ${known}` }));
    } else {
      console.error(chalk.red(`Unknown provider: ${provider}`));
      console.error(`Supported providers: ${known}`);
    }
    return false;
  }

  if (!opts.json) {
    console.log(`\n${chalk.bold(`Set up ${config.displayName} credentials`)}\n`);
    console.log(`  Create a token at: ${chalk.cyan(config.tokenUrl)}`);
    console.log(`  ${config.tokenInstructions}`);
    console.log(`  Recommended scopes: ${chalk.yellow(config.recommendedScopes.join(", "))}\n`);
  }

  const token = await promptSecret("Paste your token: ");
  if (!token) {
    if (opts.json) {
      console.log(JSON.stringify({ error: "No token provided" }));
    } else {
      console.error(chalk.red("No token provided."));
    }
    return false;
  }

  // Validate unless --no-validate
  if (!opts.noValidate) {
    if (!opts.json) {
      process.stdout.write("Validating token... ");
    }
    const validation = await validateToken(provider, token);

    if (validation.valid) {
      if (!opts.json) {
        console.log(chalk.green("valid"));
      }
    } else {
      if (!opts.json) {
        console.log(chalk.yellow("failed"));
        console.log(chalk.yellow(`  ${validation.error}`));
        console.log(chalk.dim("  Token saved anyway. Re-run `agentnode auth " + provider + "` to update."));
      }
    }
  }

  // Save
  const data = loadLocalCredentials();
  data.providers[provider] = {
    access_token: token,
    auth_type: "oauth2",
    scopes: config.recommendedScopes,
    stored_at: new Date().toISOString(),
  };
  saveLocalCredentials(data);

  if (opts.json) {
    console.log(JSON.stringify({ provider, stored: true }));
  } else {
    console.log(chalk.green(`\n✓ ${config.displayName} credential stored. You can now use ${config.displayName}-connected packages.`));
  }
  return true;
}

// Provider subcommand (dynamic — `agentnode auth github`, `agentnode auth slack`)
const providerAction = async (provider: string, opts: any) => {
  const success = await runAuthFlow(provider, opts);
  if (!success) {
    process.exit(1);
  }
};

const listSubcommand = new Command("list")
  .description("List locally stored credentials")
  .option("--json", "Output JSON")
  .action(async (opts) => {
    const data = loadLocalCredentials();
    const providers = Object.entries(data.providers);

    if (opts.json) {
      const result: Record<string, any> = {};
      for (const [name, entry] of providers) {
        result[name] = {
          auth_type: entry.auth_type,
          scopes: entry.scopes,
          stored_at: entry.stored_at,
          status: "stored",
        };
      }
      console.log(JSON.stringify(result, null, 2));
      return;
    }

    if (providers.length === 0) {
      console.log(chalk.dim("No local credentials stored."));
      console.log(chalk.dim(`Run ${chalk.cyan("agentnode auth <provider>")} to add one.`));
      return;
    }

    console.log(chalk.bold(`Local credentials (${providers.length}):\n`));
    for (const [name, entry] of providers) {
      const scopes = entry.scopes?.length
        ? entry.scopes.join(", ")
        : chalk.dim("none");
      const storedAt = entry.stored_at
        ? new Date(entry.stored_at).toLocaleDateString()
        : "unknown";
      console.log(
        `  ${chalk.cyan(name.padEnd(12))} ${entry.auth_type.padEnd(8)} scopes: ${scopes}  stored: ${storedAt}`,
      );
    }
  });

const removeSubcommand = new Command("remove")
  .description("Remove a locally stored credential")
  .argument("<provider>", "Provider name (e.g. github, slack)")
  .option("--yes", "Skip confirmation prompt")
  .option("--json", "Output JSON")
  .action(async (provider: string, opts) => {
    provider = provider.toLowerCase();
    const data = loadLocalCredentials();

    if (!(provider in data.providers)) {
      if (opts.json) {
        console.log(JSON.stringify({ error: `No local credential stored for ${provider}.` }));
      } else {
        console.log(chalk.dim(`No local credential stored for ${provider}.`));
      }
      return;
    }

    if (!opts.yes) {
      const confirmed = await promptConfirm(
        `Remove local credential for ${provider}?`,
      );
      if (!confirmed) {
        console.log("Cancelled.");
        return;
      }
    }

    delete data.providers[provider];
    saveLocalCredentials(data);

    if (opts.json) {
      console.log(JSON.stringify({ provider, removed: true }));
    } else {
      console.log(chalk.green(`✓ Local credential for ${provider} removed.`));
    }
  });

// ---------------------------------------------------------------------------
// Main auth command
// ---------------------------------------------------------------------------

export const authCommand = new Command("auth")
  .description("Manage local credentials for connector packages (no account needed)")
  .argument("[provider]", "Provider to authenticate (e.g. github, slack)")
  .option("--no-validate", "Skip token validation")
  .option("--json", "Output JSON")
  .addCommand(listSubcommand)
  .addCommand(removeSubcommand)
  .action(async (provider: string | undefined, opts) => {
    if (!provider) {
      // No provider given — show help
      const known = Object.keys(PROVIDERS).join(", ");
      console.log(`\n${chalk.bold("agentnode auth")} — manage local credentials\n`);
      console.log(`  ${chalk.cyan("agentnode auth <provider>")}  Store a token (e.g. github, slack)`);
      console.log(`  ${chalk.cyan("agentnode auth list")}        List stored credentials`);
      console.log(`  ${chalk.cyan("agentnode auth remove <p>")}  Remove a credential\n`);
      console.log(`Supported providers: ${known}`);
      return;
    }
    // If the argument matches a subcommand, Commander already handled it.
    // Otherwise, treat it as a provider name.
    await providerAction(provider, opts);
  });

// Re-export for use from install command
export { hasLocalCredential, PROVIDERS };
