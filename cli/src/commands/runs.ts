/**
 * agentnode runs — list, show, and clean agent run logs.
 * Reads JSONL files directly from ~/.agentnode/runs/ (local, no backend).
 */

import { Command } from "commander";
import chalk from "chalk";
import { readFileSync, readdirSync, statSync, unlinkSync } from "node:fs";
import { join } from "node:path";
import { getConfigDir } from "../config.js";

function getRunsDir(): string {
  return join(getConfigDir(), "runs");
}

interface RunFile {
  id: string;
  path: string;
  mtime: number;
}

function listRunFiles(): RunFile[] {
  const dir = getRunsDir();
  try {
    const files = readdirSync(dir)
      .filter((f) => f.endsWith(".jsonl"))
      .map((f) => {
        const fullPath = join(dir, f);
        const stat = statSync(fullPath);
        return {
          id: f.replace(".jsonl", ""),
          path: fullPath,
          mtime: stat.mtimeMs,
        };
      });
    files.sort((a, b) => b.mtime - a.mtime);
    return files;
  } catch {
    return [];
  }
}

function readRunEvents(filePath: string): any[] {
  try {
    const content = readFileSync(filePath, "utf-8");
    return content
      .split("\n")
      .filter((line) => line.trim())
      .map((line) => JSON.parse(line));
  } catch {
    return [];
  }
}

const listSubcommand = new Command("list")
  .description("List recent agent runs (newest first)")
  .option("--limit <n>", "Max runs to show", "20")
  .option("--json", "Output JSON")
  .action((opts) => {
    const limit = parseInt(opts.limit, 10) || 20;
    const files = listRunFiles().slice(0, limit);

    if (files.length === 0) {
      if (opts.json) {
        console.log(JSON.stringify({ runs: [] }));
      } else {
        console.log(chalk.dim("No agent runs found."));
      }
      return;
    }

    if (opts.json) {
      const runs = files.map((f) => {
        const events = readRunEvents(f.path);
        const start = events.find((e) => e.event === "run_start");
        const end = events.find((e) => e.event === "run_end");
        return {
          run_id: f.id,
          slug: start?.slug || null,
          goal: start?.goal || null,
          success: end?.success ?? null,
          duration_ms: end?.duration_ms ?? null,
          started_at: start?.ts || null,
          events: events.length,
        };
      });
      console.log(JSON.stringify({ runs }, null, 2));
      return;
    }

    console.log(chalk.bold(`Agent Runs (${files.length}):\n`));
    for (const f of files) {
      const events = readRunEvents(f.path);
      const start = events.find((e) => e.event === "run_start");
      const end = events.find((e) => e.event === "run_end");
      const slug = start?.slug || "unknown";
      const success = end?.success;
      const status =
        success === true
          ? chalk.green("OK")
          : success === false
            ? chalk.red("FAIL")
            : chalk.yellow("?");
      const duration = end?.duration_ms
        ? `${(end.duration_ms / 1000).toFixed(1)}s`
        : "";
      const ts = start?.ts
        ? new Date(start.ts).toLocaleString()
        : new Date(f.mtime).toLocaleString();

      console.log(
        `  ${status} ${chalk.cyan(f.id.slice(0, 8))}  ${slug}  ${duration}  ${chalk.dim(ts)}`,
      );
    }
  });

const showSubcommand = new Command("show")
  .description("Show events for a specific agent run")
  .argument("<run_id>", "Run ID (full or prefix)")
  .option("--json", "Output JSON")
  .action((runId: string, opts) => {
    const files = listRunFiles();
    const match = files.find(
      (f) => f.id === runId || f.id.startsWith(runId),
    );

    if (!match) {
      if (opts.json) {
        console.log(JSON.stringify({ error: "Run not found (may have been cleaned up by retention policy)" }));
      } else {
        console.error(
          chalk.red(
            `Run '${runId}' not found. It may have been removed by the retention policy.`,
          ),
        );
      }
      process.exit(1);
    }

    const events = readRunEvents(match.path);

    if (opts.json) {
      console.log(JSON.stringify({ run_id: match.id, events }, null, 2));
      return;
    }

    console.log(chalk.bold(`Run: ${match.id}\n`));
    for (const e of events) {
      const ts = e.ts ? new Date(e.ts).toLocaleTimeString() : "";
      const event = e.event || "?";
      let detail = "";

      switch (event) {
        case "run_start":
          detail = `slug=${e.slug} goal="${(e.goal || "").slice(0, 60)}"`;
          break;
        case "run_end":
          detail = `success=${e.success} duration=${e.duration_ms}ms`;
          if (e.error) detail += ` error="${e.error}"`;
          break;
        case "tool_call":
          detail = `${e.slug}${e.tool_name ? ":" + e.tool_name : ""}`;
          break;
        case "tool_result":
          detail = `${e.slug} success=${e.success} ${e.duration_ms}ms`;
          if (e.error) detail += ` error="${e.error}"`;
          break;
        case "step_start":
          detail = `${e.step_name} tool=${e.tool}`;
          break;
        case "step_result":
          detail = `${e.step_name} success=${e.success}${e.skipped ? " (skipped)" : ""} ${e.duration_ms}ms`;
          break;
        case "iteration":
          detail = `#${e.iteration}`;
          break;
        case "truncated":
          detail = e.message || "";
          break;
      }

      const color =
        event === "run_end" && e.success === false
          ? chalk.red
          : event === "run_end" && e.success === true
            ? chalk.green
            : chalk.white;

      console.log(`  ${chalk.dim(ts)} ${color(event.padEnd(14))} ${detail}`);
    }
  });

const cleanSubcommand = new Command("clean")
  .description("Remove old run logs based on retention policy")
  .option("--dry-run", "Show what would be deleted without deleting")
  .option("--max-age <days>", "Max age in days (default: 30)")
  .option("--max-count <n>", "Max number of runs to keep (default: 500)")
  .option("--json", "Output JSON")
  .action((opts) => {
    const maxAge = parseInt(opts.maxAge, 10) || 30;
    const maxCount = parseInt(opts.maxCount, 10) || 500;
    const dryRun = !!opts.dryRun;

    const files = listRunFiles();
    const now = Date.now();
    const cutoff = now - maxAge * 86400 * 1000;

    const toDelete: RunFile[] = [];
    for (let i = 0; i < files.length; i++) {
      if (i >= maxCount || files[i].mtime < cutoff) {
        toDelete.push(files[i]);
      }
    }

    if (opts.json) {
      console.log(
        JSON.stringify({
          total_runs: files.length,
          to_delete: toDelete.length,
          dry_run: dryRun,
          files: toDelete.map((f) => f.id),
        }, null, 2),
      );
      if (!dryRun) {
        for (const f of toDelete) {
          try { unlinkSync(f.path); } catch { /* ignore */ }
        }
      }
      return;
    }

    if (toDelete.length === 0) {
      console.log(
        chalk.dim(
          `No runs to clean (${files.length} total, max_age=${maxAge}d, max_count=${maxCount}).`,
        ),
      );
      return;
    }

    if (dryRun) {
      console.log(
        chalk.yellow(
          `Would delete ${toDelete.length} of ${files.length} run logs:`,
        ),
      );
      for (const f of toDelete) {
        console.log(`  ${chalk.dim(f.id)}`);
      }
    } else {
      let deleted = 0;
      for (const f of toDelete) {
        try {
          unlinkSync(f.path);
          deleted++;
        } catch {
          /* ignore */
        }
      }
      console.log(
        chalk.green(
          `Deleted ${deleted} of ${files.length} run logs.`,
        ),
      );
    }
  });

export const runsCommand = new Command("runs")
  .description("Manage agent run logs")
  .addCommand(listSubcommand)
  .addCommand(showSubcommand)
  .addCommand(cleanSubcommand);
