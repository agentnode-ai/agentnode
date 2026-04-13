/**
 * agentnode update <slug> — check for updates and install latest.
 * agentnode update --all — batch check + update all packages.
 * Spec §13.5
 */

import { Command } from "commander";
import chalk from "chalk";
import { checkUpdates, getInstallMetadata, trackInstall } from "../api.js";
import { installPackage } from "../installer.js";
import { readLockfile } from "../lockfile.js";

export const updateCommand = new Command("update")
  .description("Update a package to its latest version")
  .argument("[slug]", "Package slug (omit with --all)")
  .option("--all", "Update all installed packages")
  .option("--verbose", "Show detailed output")
  .option("--json", "Output JSON")
  .action(async (slug: string | undefined, opts) => {
    try {
      if (opts.all) {
        await updateAll(opts);
      } else if (slug) {
        await updateSingle(slug, opts);
      } else {
        console.error(chalk.red("✗ Provide a package slug or use --all"));
        process.exit(1);
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

async function updateSingle(slug: string, opts: any) {
  const lf = readLockfile();
  const current = lf.packages[slug];
  if (!current) {
    console.error(chalk.red(`✗ ${slug} is not installed`));
    process.exit(1);
  }

  const updates = await checkUpdates([{ slug, version: current.version }]);
  const info = updates.updates[0];

  if (!info.has_update) {
    if (opts.json) {
      console.log(JSON.stringify({ slug, current_version: current.version, has_update: false }));
    } else {
      console.log(chalk.green(`✓ ${slug}@${current.version} is already up to date`));
    }
    return;
  }

  console.log(`Updating ${slug} ${current.version} → ${info.latest_version}...`);
  const meta = await getInstallMetadata(slug);

  if (!meta.artifact?.url) {
    console.log(chalk.yellow(`No artifact available for ${slug}@${info.latest_version}`));
    return;
  }

  try { await trackInstall(slug, meta.version, "update"); } catch { /* non-fatal */ }

  const result = await installPackage(
    {
      artifact_url: meta.artifact.url,
      artifact_hash: meta.artifact.hash_sha256 || "",
      entrypoint: meta.entrypoint || "",
      post_install_code: "",
      package_type: meta.package_type || "toolpack",
      capability_ids: (meta.capabilities || []).map((c: any) => c.capability_id),
      tools: (meta.capabilities || [])
        .filter((c: any) => c.entrypoint)
        .map((c: any) => ({
          name: c.name,
          entrypoint: c.entrypoint,
          capability_id: c.capability_id,
        })),
      deprecated: false,
    },
    slug,
    meta.version,
    "update",
    opts.verbose,
  );

  if (opts.json) {
    console.log(JSON.stringify({ slug, previous: current.version, updated: result.version }));
  } else {
    console.log(chalk.green(`✓ Updated ${slug} ${current.version} → ${result.version}`));
  }
}

async function updateAll(opts: any) {
  const lf = readLockfile();
  const slugs = Object.keys(lf.packages);

  if (slugs.length === 0) {
    console.log("No packages installed.");
    return;
  }

  const packages = slugs.map((s) => ({ slug: s, version: lf.packages[s].version }));
  const result = await checkUpdates(packages);
  const updatable = result.updates.filter((u: any) => u.has_update);

  if (updatable.length === 0) {
    if (opts.json) {
      console.log(JSON.stringify({ updated: [] }));
    } else {
      console.log(chalk.green("✓ All packages are up to date"));
    }
    return;
  }

  console.log(`${updatable.length} update(s) available:\n`);
  for (const u of updatable) {
    console.log(`  ${u.slug}: ${u.current_version} → ${u.latest_version}`);
  }
  console.log("");

  const results: any[] = [];
  for (const u of updatable) {
    try {
      await updateSingle(u.slug, { ...opts, json: false });
      results.push({ slug: u.slug, updated: true });
    } catch (err: any) {
      console.error(chalk.red(`  ✗ ${u.slug}: ${err.message}`));
      results.push({ slug: u.slug, updated: false, error: err.message });
    }
  }

  if (opts.json) {
    console.log(JSON.stringify({ updated: results }));
  }
}
