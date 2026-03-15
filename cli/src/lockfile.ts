/**
 * agentnode.lock management.
 * Spec §13.2
 */

import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { join } from "node:path";

export interface LockEntry {
  version: string;
  package_type: string;
  entrypoint: string;
  capability_ids: string[];
  artifact_hash: string;
  installed_at: string;
  source: string;
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
    return {
      lockfile_version: "0.1",
      updated_at: new Date().toISOString(),
      packages: {},
    };
  }
}

export function writeLockfile(lockfile: Lockfile): void {
  lockfile.updated_at = new Date().toISOString();
  writeFileSync(lockfilePath(), JSON.stringify(lockfile, null, 2), "utf-8");
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
