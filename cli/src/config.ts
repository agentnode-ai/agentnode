/**
 * CLI configuration management (~/.agentnode/config.json)
 * Spec §13.1
 */

import { readFileSync, writeFileSync, mkdirSync, existsSync, chmodSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

export interface CliConfig {
  api_url: string;
  api_key?: string;
  username?: string;
}

const CONFIG_DIR = join(homedir(), ".agentnode");
const CONFIG_FILE = join(CONFIG_DIR, "config.json");
const DEFAULT_API_URL = "https://agentnode.net";

export function getConfigDir(): string {
  return CONFIG_DIR;
}

export function loadConfig(): CliConfig {
  if (!existsSync(CONFIG_FILE)) {
    return { api_url: DEFAULT_API_URL };
  }

  // Warn if permissions are too open (Unix only)
  if (process.platform !== "win32") {
    try {
      const stat = statSync(CONFIG_FILE);
      const mode = stat.mode & 0o777;
      if (mode & 0o077) {
        console.warn(
          `Warning: ${CONFIG_FILE} has too-open permissions (${mode.toString(8)}). ` +
          `Run: chmod 600 ${CONFIG_FILE}`
        );
      }
    } catch {
      // ignore
    }
  }

  try {
    const raw = readFileSync(CONFIG_FILE, "utf-8");
    return JSON.parse(raw) as CliConfig;
  } catch {
    return { api_url: DEFAULT_API_URL };
  }
}

export function saveConfig(config: CliConfig): void {
  mkdirSync(CONFIG_DIR, { recursive: true });
  writeFileSync(CONFIG_FILE, JSON.stringify(config, null, 2), "utf-8");

  // Set 0600 permissions (Unix only)
  if (process.platform !== "win32") {
    try {
      chmodSync(CONFIG_FILE, 0o600);
    } catch {
      // ignore
    }
  }
}

export function getApiUrl(): string {
  return process.env.AGENTNODE_API_URL || loadConfig().api_url || DEFAULT_API_URL;
}

export function getApiKey(): string | undefined {
  return process.env.AGENTNODE_API_KEY || loadConfig().api_key;
}
