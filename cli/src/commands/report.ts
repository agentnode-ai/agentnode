/**
 * agentnode report <slug> — report a package.
 * Spec §13.5
 */

import { Command } from "commander";
import chalk from "chalk";
import { reportPackage } from "../api.js";
import * as readline from "node:readline";

const VALID_REASONS = ["malware", "typosquatting", "spam", "misleading", "policy_violation", "other"];

export const reportCommand = new Command("report")
  .description("Report a package for policy violation or abuse")
  .argument("<slug>", "Package slug")
  .option("-r, --reason <reason>", `Reason: ${VALID_REASONS.join(", ")}`)
  .option("-d, --description <text>", "Description of the issue")
  .option("--json", "Output JSON")
  .action(async (slug: string, opts) => {
    try {
      let reason = opts.reason;
      let description = opts.description;

      // Interactive prompts if not provided
      if (!reason) {
        console.log(chalk.dim("Report reasons:"));
        VALID_REASONS.forEach((r, i) => console.log(`  ${i + 1}. ${r}`));
        reason = await prompt("Select reason (name or number): ");
        const num = parseInt(reason);
        if (num >= 1 && num <= VALID_REASONS.length) {
          reason = VALID_REASONS[num - 1];
        }
      }

      if (!VALID_REASONS.includes(reason)) {
        throw new Error(`Invalid reason. Must be one of: ${VALID_REASONS.join(", ")}`);
      }

      if (!description) {
        description = await prompt("Description: ");
      }

      if (!description) {
        throw new Error("Description is required");
      }

      const result = await reportPackage(slug, reason, description);

      if (opts.json) {
        console.log(JSON.stringify(result));
      } else {
        console.log(chalk.green(`✓ Report submitted (${result.report_id})`));
        console.log(chalk.dim(`  Status: ${result.status}`));
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

function prompt(question: string): Promise<string> {
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
  });
  return new Promise((resolve) => {
    rl.question(question, (answer) => {
      rl.close();
      resolve(answer.trim());
    });
  });
}
