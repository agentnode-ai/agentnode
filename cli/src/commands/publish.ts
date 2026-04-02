import { Command } from "commander";
import { readFileSync, existsSync } from "node:fs";
import { join, extname, resolve, dirname } from "node:path";
import { execSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { parse as parseYAML } from "yaml";
import { publishPackage } from "../api.js";

/**
 * Directories and file patterns excluded from the artifact tar.gz.
 * Mirrors the exclusions used by the publish GitHub Action.
 */
const ARTIFACT_EXCLUDES = [
  ".git",
  "node_modules",
  "__pycache__",
  ".venv",
  "venv",
  "env",
  "dist",
  "build",
  "*.egg-info",
  ".pytest_cache",
  ".mypy_cache",
  ".ruff_cache",
  ".DS_Store",
  ".env",
  ".env.local",
];

function loadManifest(pathOrDir: string): { json: string; dir: string } {
  let filePath = pathOrDir;

  // If directory, look for agentnode.yaml or agentnode.json
  try {
    const stat = readFileSync(filePath);
    // If it reads, it's a file — handled below
  } catch {
    // Not a file or doesn't exist as-is — check as directory
  }

  if (
    !existsSync(filePath) ||
    (!filePath.endsWith(".yaml") &&
      !filePath.endsWith(".yml") &&
      !filePath.endsWith(".json"))
  ) {
    // Treat as directory
    const yamlPath = join(pathOrDir, "agentnode.yaml");
    const ymlPath = join(pathOrDir, "agentnode.yml");
    const jsonPath = join(pathOrDir, "agentnode.json");

    if (existsSync(yamlPath)) filePath = yamlPath;
    else if (existsSync(ymlPath)) filePath = ymlPath;
    else if (existsSync(jsonPath)) filePath = jsonPath;
    else
      throw new Error(
        `No manifest found in ${pathOrDir}. Expected agentnode.yaml or agentnode.json`
      );
  }

  const raw = readFileSync(filePath, "utf-8");
  const ext = extname(filePath).toLowerCase();
  const dir = resolve(dirname(filePath));

  if (ext === ".yaml" || ext === ".yml") {
    const parsed = parseYAML(raw);
    if (!parsed || typeof parsed !== "object") {
      throw new Error(`Invalid YAML in ${filePath}`);
    }
    return { json: JSON.stringify(parsed), dir };
  }

  // JSON
  JSON.parse(raw); // validate
  return { json: raw, dir };
}

/**
 * Build a tar.gz artifact from the package directory.
 * Uses the system `tar` command (available on Linux, macOS, and Windows 10+).
 * Returns the artifact bytes, or null if the build fails.
 */
function buildArtifact(packageDir: string): Uint8Array {
  const tempDir = mkdtempSync(join(tmpdir(), "agentnode-publish-"));
  const artifactPath = join(tempDir, "package.tar.gz");

  try {
    // Build --exclude flags
    const excludeFlags = ARTIFACT_EXCLUDES.map((p) => `--exclude='${p}'`).join(
      " "
    );

    // Use forward slashes for Windows bsdtar compatibility
    const safeArtifactPath = artifactPath.replace(/\\/g, "/");
    const safePackageDir = packageDir.replace(/\\/g, "/");

    // Create tar.gz from the package directory
    // The -C flag changes to the parent directory and we archive the directory name,
    // so the archive has a single top-level directory (consistent with convention).
    execSync(
      `tar czf "${safeArtifactPath}" ${excludeFlags} --force-local -C "${safePackageDir}" .`,
      {
        timeout: 60_000,
        stdio: "pipe",
      }
    );

    const artifactBytes = readFileSync(artifactPath);

    // Basic sanity check: must be non-empty and under 50MB
    if (artifactBytes.length === 0) {
      throw new Error("Artifact tar.gz is empty");
    }
    const maxSize = 50 * 1024 * 1024;
    if (artifactBytes.length > maxSize) {
      throw new Error(
        `Artifact is ${(artifactBytes.length / 1024 / 1024).toFixed(1)} MB, exceeds 50 MB limit`
      );
    }

    return new Uint8Array(artifactBytes);
  } finally {
    try {
      rmSync(tempDir, { recursive: true, force: true });
    } catch {
      // ignore cleanup errors
    }
  }
}

export const publishCommand = new Command("publish")
  .description("Publish a package to the AgentNode registry")
  .argument(
    "<path>",
    "Path to agentnode.yaml manifest file or package directory"
  )
  .requiredOption("--token <token>", "Authentication token")
  .option("--no-artifact", "Publish metadata only (skip artifact upload)")
  .action(async (pathArg: string, opts) => {
    try {
      const { json: manifest, dir: packageDir } = loadManifest(pathArg);

      // Build artifact unless --no-artifact was passed
      let artifactBytes: Uint8Array | undefined;
      if (opts.artifact !== false) {
        console.log("Building artifact...");
        try {
          artifactBytes = buildArtifact(packageDir);
          const sizeKb = (artifactBytes.length / 1024).toFixed(1);
          console.log(`  Artifact: ${sizeKb} KB`);
        } catch (err: any) {
          console.error(
            `Warning: Failed to build artifact: ${err.message}`
          );
          console.error(
            "  Publishing metadata-only. Use --no-artifact to suppress this warning."
          );
        }
      }

      console.log("Publishing package...");
      const result = await publishPackage(manifest, opts.token, artifactBytes);

      console.log(`\n  Published: ${result.slug}@${result.version}`);
      console.log(`  Type:      ${result.package_type}`);
      if (artifactBytes) {
        console.log(`  Artifact:  included`);
      } else {
        console.log(`  Artifact:  none (metadata-only)`);
      }
      console.log(`  ${result.message}\n`);
    } catch (err: any) {
      console.error(`Error: ${err.message}`);
      process.exit(1);
    }
  });
