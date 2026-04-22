/**
 * agentnode.lock management.
 * Spec §13.2
 */

import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { join } from "node:path";

export interface ToolEntry {
  name: string;
  entrypoint: string;
  capability_id: string;
}

export interface LockEntry {
  version: string;
  package_type: string;
  entrypoint: string;
  capability_ids: string[];
  tools: ToolEntry[];
  artifact_hash: string;
  installed_at: string;
  source: string;
  // Agent config (when package_type === "agent")
  agent?: Record<string, unknown>;
  // Trust and permissions (for policy checks)
  trust_level?: string;
  permissions?: Record<string, unknown>;
}

export interface Lockfile {
  lockfile_version: string;
  updated_at: string;
  packages: Record<string, LockEntry>;
}

function lockfilePath(): string {
  return join(process.cwd(), "agentnode.lock");
}

export function readLockfile(): Lockfile {
  const path = lockfilePath();
  if (!existsSync(path)) {
    return {
      lockfile_version: "0.1",
      updated_at: new Date().toISOString(),
      packages: {},
    };
  }
  try {
    const raw = readFileSync(path, "utf-8");
    return JSON.parse(raw) as Lockfile;
  } catch {
    console.warn("Warning: agentnode.lock contains invalid JSON, treating as empty");
    return {
      lockfile_version: "0.1",
      updated_at: new Date().toISOString(),
      packages: {},
    };
  }
}

export function writeLockfile(lockfile: Lockfile): void {
  lockfile.updated_at = new Date().toISOString();
  try {
    writeFileSync(lockfilePath(), JSON.stringify(lockfile, null, 2), "utf-8");
  } catch (err: any) {
    throw new Error(`Failed to write lockfile: ${err.message}. Check disk space and permissions.`);
  }
}

/**
 * Check if a package is already installed at the given version.
 * Returns: "same" | "different" | "missing"
 */
export function checkInstalled(slug: string, version: string): "same" | "different" | "missing" {
  const lf = readLockfile();
  const entry = lf.packages[slug];
  if (!entry) return "missing";
  if (entry.version === version) return "same";
  return "different";
}

export function updateLockEntry(
  slug: string,
  entry: LockEntry
): void {
  const lf = readLockfile();
  lf.packages[slug] = entry;
  writeLockfile(lf);
}

export function removeLockEntry(slug: string): void {
  const lf = readLockfile();
  delete lf.packages[slug];
  writeLockfile(lf);
}
