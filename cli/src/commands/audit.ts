/**
 * agentnode audit — view and manage the policy decision audit trail.
 *
 * Reads ~/.agentnode/audit.jsonl (append-only JSONL written by the SDK
 * policy kernel) and presents it in human-readable or JSON format.
 *
 * Subcommands:
 *   show   — display recent audit entries (tabular or JSON)
 *   stats  — summary statistics (allow/deny/prompt counts, top slugs)
 *   clear  — delete the audit log (requires --yes confirmation)
 */

import { Command } from "commander";
import chalk from "chalk";
import {
  readFileSync,
  existsSync,
  unlinkSync,
  statSync,
} from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { createInterface } from "node:readline";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getAuditPath(): string {
  const override = process.env.AGENTNODE_CONFIG;
  if (override) {
    // If override points to a .json file, use its parent dir
    const dir = override.endsWith(".json")
      ? override.slice(0, override.lastIndexOf("/"))
      : override;
    return join(dir, "audit.jsonl");
  }
  return join(homedir(), ".agentnode", "audit.jsonl");
}

interface AuditRecord {
  ts: string;
  event: string;
  slug: string;
  tool_name: string | null;
  action: string;
  source: string;
  reason: string;
  trust: string;
  env: string;
  request_id: string | null;
}

function readAuditEntries(): AuditRecord[] {
  const path = getAuditPath();
  if (!existsSync(path)) {
    return [];
  }

  const content = readFileSync(path, "utf-8");
  const lines = content.trim().split("\n").filter(Boolean);
  const entries: AuditRecord[] = [];

  for (const line of lines) {
    try {
      entries.push(JSON.parse(line) as AuditRecord);
    } catch {
      // Skip malformed lines
    }
  }

  return entries;
}

function colorAction(action: string): string {
  switch (action) {
    case "allow":
      return chalk.green(action);
    case "deny":
      return chalk.red(action);
    case "prompt":
      return chalk.yellow(action);
    default:
      return chalk.dim(action);
  }
}

function formatTimestamp(ts: string): string {
  try {
    const d = new Date(ts);
    return d.toISOString().replace("T", " ").slice(0, 19);
  } catch {
    return ts.slice(0, 19);
  }
}

function pad(s: string, len: number): string {
  return s.length >= len ? s.slice(0, len) : s + " ".repeat(len - s.length);
}

async function promptConfirm(message: string): Promise<boolean> {
  return new Promise((resolve) => {
    const rl = createInterface({ input: process.stdin, output: process.stdout });
    rl.question(`${message} [y/N] `, (answer: string) => {
      rl.close();
      resolve(answer.trim().toLowerCase() === "y");
    });
  });
}

// ---------------------------------------------------------------------------
// Subcommands
// ---------------------------------------------------------------------------

const showSubcommand = new Command("show")
  .description("Show recent audit entries")
  .option("-n, --limit <count>", "Number of entries to show", "20")
  .option("--json", "Output raw JSON lines")
  .action(async (opts) => {
    const entries = readAuditEntries();

    if (entries.length === 0) {
      if (opts.json) {
        console.log("[]");
      } else {
        console.log(chalk.dim("No audit entries found."));
        console.log(chalk.dim(`Audit log: ${getAuditPath()}`));
      }
      return;
    }

    const limit = Math.min(parseInt(opts.limit, 10) || 20, entries.length);
    const recent = entries.slice(-limit);

    if (opts.json) {
      console.log(JSON.stringify(recent, null, 2));
      return;
    }

    // Table header
    console.log(
      chalk.dim(
        `${pad("TIMESTAMP", 19)}  ${pad("EVENT", 16)}  ${pad("SLUG", 24)}  ${pad("ACTION", 8)}  ${pad("SOURCE", 22)}  TRUST`,
      ),
    );
    console.log(chalk.dim("─".repeat(110)));

    for (const entry of recent) {
      const ts = formatTimestamp(entry.ts);
      const event = pad(entry.event || "?", 16);
      const slug = pad(entry.slug || "?", 24);
      const action = pad(colorAction(entry.action || "?"), 8 + (colorAction(entry.action || "?").length - (entry.action || "?").length));
      const source = pad(entry.source || "?", 22);
      const trust = entry.trust || "?";
      console.log(`${ts}  ${event}  ${slug}  ${action}  ${source}  ${trust}`);
    }

    console.log(chalk.dim(`\nShowing ${recent.length} of ${entries.length} entries. Use --limit to show more.`));
  });

const statsSubcommand = new Command("stats")
  .description("Show audit summary statistics")
  .option("--json", "Output JSON")
  .action(async (opts) => {
    const entries = readAuditEntries();

    if (entries.length === 0) {
      if (opts.json) {
        console.log(JSON.stringify({ total: 0 }));
      } else {
        console.log(chalk.dim("No audit entries found."));
      }
      return;
    }

    // Count actions
    const actionCounts: Record<string, number> = {};
    const slugCounts: Record<string, number> = {};
    const eventCounts: Record<string, number> = {};
    let oldest = entries[0]?.ts || "";
    let newest = entries[entries.length - 1]?.ts || "";

    for (const entry of entries) {
      actionCounts[entry.action] = (actionCounts[entry.action] || 0) + 1;
      slugCounts[entry.slug] = (slugCounts[entry.slug] || 0) + 1;
      eventCounts[entry.event] = (eventCounts[entry.event] || 0) + 1;
    }

    // Top slugs (top 10)
    const topSlugs = Object.entries(slugCounts)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 10);

    if (opts.json) {
      console.log(
        JSON.stringify(
          {
            total: entries.length,
            actions: actionCounts,
            events: eventCounts,
            top_slugs: Object.fromEntries(topSlugs),
            period: { from: oldest, to: newest },
          },
          null,
          2,
        ),
      );
      return;
    }

    console.log(chalk.bold("\nAudit Statistics\n"));

    console.log(`  Total entries:  ${entries.length}`);
    console.log(`  Period:         ${formatTimestamp(oldest)} → ${formatTimestamp(newest)}`);

    // File size
    try {
      const stat = statSync(getAuditPath());
      const sizeMb = (stat.size / (1024 * 1024)).toFixed(2);
      console.log(`  File size:      ${sizeMb} MB`);
    } catch {
      // ignore
    }

    console.log(chalk.dim("\nActions:"));
    for (const [action, count] of Object.entries(actionCounts).sort((a, b) => b[1] - a[1])) {
      const pct = ((count / entries.length) * 100).toFixed(1);
      console.log(`  ${colorAction(action).padEnd(20)}  ${String(count).padStart(6)}  (${pct}%)`);
    }

    console.log(chalk.dim("\nEvent types:"));
    for (const [event, count] of Object.entries(eventCounts).sort((a, b) => b[1] - a[1])) {
      console.log(`  ${pad(event, 18)}  ${String(count).padStart(6)}`);
    }

    console.log(chalk.dim("\nTop packages:"));
    for (const [slug, count] of topSlugs) {
      console.log(`  ${pad(slug, 28)}  ${String(count).padStart(6)}`);
    }

    console.log();
  });

const clearSubcommand = new Command("clear")
  .description("Delete the audit log file")
  .option("--yes", "Skip confirmation prompt")
  .option("--json", "Output JSON")
  .action(async (opts) => {
    const path = getAuditPath();

    if (!existsSync(path)) {
      if (opts.json) {
        console.log(JSON.stringify({ cleared: false, reason: "no audit file" }));
      } else {
        console.log(chalk.dim("No audit file found."));
      }
      return;
    }

    if (!opts.yes) {
      const confirmed = await promptConfirm(
        `Delete audit log at ${path}?`,
      );
      if (!confirmed) {
        console.log("Cancelled.");
        return;
      }
    }

    try {
      unlinkSync(path);

      // Also remove rotated files
      for (let i = 1; i <= 10; i++) {
        const rotated = `${path}.${i}`;
        if (existsSync(rotated)) {
          unlinkSync(rotated);
        } else {
          break;
        }
      }

      if (opts.json) {
        console.log(JSON.stringify({ cleared: true }));
      } else {
        console.log(chalk.green("Audit log cleared."));
      }
    } catch (err: any) {
      if (opts.json) {
        console.log(JSON.stringify({ cleared: false, error: err.message }));
      } else {
        console.error(chalk.red(`Failed to clear audit log: ${err.message}`));
      }
      process.exit(1);
    }
  });

// ---------------------------------------------------------------------------
// Main audit command
// ---------------------------------------------------------------------------

export const auditCommand = new Command("audit")
  .description("View and manage the policy decision audit trail")
  .addCommand(showSubcommand)
  .addCommand(statsSubcommand)
  .addCommand(clearSubcommand)
  .action(async () => {
    // No subcommand given — show help
    console.log(`\n${chalk.bold("agentnode audit")} — policy decision audit trail\n`);
    console.log(`  ${chalk.cyan("agentnode audit show")}         Show recent audit entries`);
    console.log(`  ${chalk.cyan("agentnode audit stats")}        Summary statistics`);
    console.log(`  ${chalk.cyan("agentnode audit clear --yes")}  Delete audit log\n`);
    console.log(`Audit log: ${chalk.dim(getAuditPath())}`);
  });
