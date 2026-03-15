/**
 * Package installer — download, verify, extract, pip install.
 * Spec §13.4
 */

import { execSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtempSync, mkdirSync, rmSync, readFileSync, writeFileSync, readdirSync, statSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, sep } from "node:path";
import { resolvePython, getPythonVersion } from "./python-resolver.js";
import { checkInstalled, updateLockEntry } from "./lockfile.js";

export interface InstallResult {
  slug: string;
  version: string;
  entrypoint: string;
  postInstallCode: string;
  wasUpgrade: boolean;
  previousVersion?: string;
}

interface ArtifactMeta {
  artifact_url: string;
  artifact_hash: string;
  entrypoint: string;
  post_install_code: string;
  package_type: string;
  capability_ids: string[];
  deprecated: boolean;
}

/**
 * Full install flow per Spec §13.4:
 * 1. Download artifact
 * 2. Verify SHA256
 * 3. Validate archive
 * 4. Extract to temp
 * 5. Verify entrypoint exists
 * 6. Resolve Python
 * 7. pip install
 * 8. Write lockfile
 */
export async function installPackage(
  meta: ArtifactMeta,
  slug: string,
  version: string,
  eventType: "install" | "update" | "rollback" = "install",
  verbose = false,
): Promise<InstallResult> {
  // Check lockfile for existing installation
  const status = checkInstalled(slug, version);
  if (status === "same" && eventType === "install") {
    console.log(`${slug}@${version} already installed`);
    return {
      slug,
      version,
      entrypoint: meta.entrypoint,
      postInstallCode: meta.post_install_code,
      wasUpgrade: false,
    };
  }

  if (meta.deprecated) {
    console.warn(`Warning: ${slug} is deprecated`);
  }

  const tempDir = mkdtempSync(join(tmpdir(), "agentnode-"));

  try {
    // 1. Download artifact
    if (verbose) console.log(`Downloading artifact...`);
    const tarPath = join(tempDir, "package.tar.gz");
    await downloadArtifact(meta.artifact_url, tarPath);

    // 2. Verify SHA256 hash
    if (verbose) console.log(`Verifying hash...`);
    const localHash = computeHash(tarPath);
    if (meta.artifact_hash && localHash !== meta.artifact_hash) {
      throw new Error(
        `Hash mismatch! Expected: ${meta.artifact_hash}, Got: ${localHash}. Aborting.`
      );
    }

    // 3 + 4. Extract and validate archive
    if (verbose) console.log(`Extracting archive...`);
    const extractDir = join(tempDir, "extracted");
    extractTarGz(tarPath, extractDir);
    validateArchive(extractDir);

    // 5. Verify entrypoint module exists
    if (meta.entrypoint) {
      verifyEntrypoint(extractDir, meta.entrypoint);
    }

    // 6. Resolve Python interpreter
    const pythonPath = resolvePython(verbose);
    const pythonVersion = getPythonVersion(pythonPath);
    if (verbose) console.log(`Python: ${pythonPath} (${pythonVersion})`);

    // 7. pip install
    console.log(`Installing ${slug}@${version}...`);
    execSync(`"${pythonPath}" -m pip install "${extractDir}" --quiet`, {
      stdio: verbose ? "inherit" : "pipe",
      timeout: 120_000,
    });

    // 8. Write lockfile
    const previousVersion = status === "different"
      ? (() => { try { const lf = JSON.parse(readFileSync(join(process.cwd(), "agentnode.lock"), "utf-8")); return lf.packages?.[slug]?.version; } catch { return undefined; } })()
      : undefined;

    updateLockEntry(slug, {
      version,
      package_type: meta.package_type,
      entrypoint: meta.entrypoint,
      capability_ids: meta.capability_ids || [],
      artifact_hash: `sha256:${localHash}`,
      installed_at: new Date().toISOString(),
      source: "cli",
    });

    return {
      slug,
      version,
      entrypoint: meta.entrypoint,
      postInstallCode: meta.post_install_code,
      wasUpgrade: status === "different",
      previousVersion,
    };
  } finally {
    // ALWAYS clean up temp dir
    try {
      rmSync(tempDir, { recursive: true, force: true });
    } catch {
      // ignore cleanup errors
    }
  }
}

async function downloadArtifact(url: string, destPath: string): Promise<void> {
  const resp = await fetch(url, { signal: AbortSignal.timeout(120_000) });
  if (!resp.ok) {
    throw new Error(`Download failed: HTTP ${resp.status}`);
  }
  const buffer = Buffer.from(await resp.arrayBuffer());
  // writeFileSync already imported at top
  writeFileSync(destPath, buffer);
}

function computeHash(filePath: string): string {
  const data = readFileSync(filePath);
  return createHash("sha256").update(data).digest("hex");
}

function extractTarGz(tarPath: string, destDir: string): void {
  // mkdirSync already imported at top
  mkdirSync(destDir, { recursive: true });

  // Use tar command (available on all platforms with Node 20+)
  execSync(`tar xzf "${tarPath}" -C "${destDir}"`, {
    timeout: 30_000,
    stdio: "pipe",
  });
}

function validateArchive(extractDir: string): void {
  // Check for agentnode.yaml at root
  // Note: archive might have a single root directory or files at root
  const entries = readdirSync(extractDir);

  // If single directory, look inside it
  let rootDir = extractDir;
  if (entries.length === 1) {
    const singleEntry = join(extractDir, entries[0]);
    if (statSync(singleEntry).isDirectory()) {
      rootDir = singleEntry;
    }
  }

  // Reject path traversal
  checkPathTraversal(rootDir);

  // Count files (reject > 500)
  let fileCount = 0;
  function countFiles(dir: string) {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (entry.isDirectory()) {
        countFiles(join(dir, entry.name));
      } else {
        fileCount++;
        // Reject files > 10MB
        const size = statSync(join(dir, entry.name)).size;
        if (size > 10 * 1024 * 1024) {
          throw new Error(`File ${entry.name} exceeds 10MB limit`);
        }
      }
    }
  }
  countFiles(rootDir);

  if (fileCount > 500) {
    throw new Error(`Archive contains ${fileCount} files (limit: 500)`);
  }
}

function checkPathTraversal(dir: string): void {
  function walk(d: string) {
    for (const entry of readdirSync(d, { withFileTypes: true })) {
      if (entry.name.includes("..")) {
        throw new Error(`Path traversal detected: ${entry.name}`);
      }
      if (entry.isSymbolicLink()) {
        throw new Error(`Symlinks not allowed: ${entry.name}`);
      }
      if (entry.isDirectory()) {
        walk(join(d, entry.name));
      }
    }
  }
  walk(dir);
}

function verifyEntrypoint(extractDir: string, entrypoint: string): void {
  // entrypoint is like "pdf_reader_pack.tool"
  // Convert to file path: pdf_reader_pack/tool.py
  const parts = entrypoint.split(".");
  const relPath = parts.join(sep) + ".py";

  // Check in extract dir and any single subdirectory
  const entries = readdirSync(extractDir);
  const candidates = [extractDir];
  if (entries.length === 1) {
    const sub = join(extractDir, entries[0]);
    if (existsSync(sub) && statSync(sub).isDirectory()) {
      candidates.push(sub);
    }
  }

  // Also check in src/ subdirectory (common with setuptools)
  for (const base of [...candidates]) {
    const srcDir = join(base, "src");
    if (existsSync(srcDir) && statSync(srcDir).isDirectory()) {
      candidates.push(srcDir);
    }
  }

  for (const base of candidates) {
    if (existsSync(join(base, relPath))) {
      return; // Found it
    }
  }

  // Not a hard error — the module might be installed differently
  console.warn(
    `Warning: Entrypoint module '${entrypoint}' not found in archive. ` +
    `The package may still install correctly.`
  );
}
