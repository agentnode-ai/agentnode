/**
 * agentnode init — scaffold a new package project.
 */

import { Command } from "commander";
import chalk from "chalk";
import { writeFileSync, mkdirSync, existsSync } from "node:fs";
import { join, basename } from "node:path";
import { stringify as toYAML } from "yaml";

export const initCommand = new Command("init")
  .description("Scaffold a new AgentNode package project")
  .argument("[name]", "Package name (defaults to current directory name)")
  .option("-t, --type <type>", "Package type: toolpack, agent, or upgrade", "toolpack")
  .option("--publisher <slug>", "Publisher slug")
  .option("--dir <path>", "Output directory (defaults to ./<name>)")
  .action(async (name: string | undefined, opts) => {
    const pkgType = opts.type;
    if (!["toolpack", "agent", "upgrade"].includes(pkgType)) {
      console.error(chalk.red(`Invalid type '${pkgType}'. Must be toolpack, agent, or upgrade.`));
      process.exit(1);
    }

    const pkgName = name || basename(process.cwd());
    const outDir = opts.dir || (name ? join(process.cwd(), name) : process.cwd());
    const publisher = opts.publisher || "your-publisher";

    if (name && !existsSync(outDir)) {
      mkdirSync(outDir, { recursive: true });
    }

    const srcDir = join(outDir, pkgName.replace(/-/g, "_"));
    const testsDir = join(outDir, "tests");
    mkdirSync(srcDir, { recursive: true });
    mkdirSync(testsDir, { recursive: true });

    // Generate manifest
    const manifest = generateManifest(pkgName, pkgType, publisher);
    writeFileSync(join(outDir, "agentnode.yaml"), toYAML(manifest, { lineWidth: 120 }));

    // Generate pyproject.toml
    writeFileSync(join(outDir, "pyproject.toml"), generatePyproject(pkgName));

    // Generate source files
    writeFileSync(join(srcDir, "__init__.py"), "");
    writeFileSync(join(testsDir, "__init__.py"), "");

    if (pkgType === "agent") {
      writeFileSync(join(srcDir, "agent.py"), generateAgentPy(pkgName));
      writeFileSync(join(testsDir, "test_agent.py"), generateAgentTestPy(pkgName));
    } else {
      writeFileSync(join(srcDir, "tool.py"), generateToolPy(pkgName));
      writeFileSync(join(testsDir, "test_tool.py"), generateToolTestPy(pkgName));
    }

    console.log(chalk.green(`\n✓ Scaffolded ${pkgType} project: ${pkgName}\n`));
    console.log(chalk.dim("  " + outDir));
    console.log(chalk.dim("  ├── agentnode.yaml"));
    console.log(chalk.dim("  ├── pyproject.toml"));
    console.log(chalk.dim(`  ├── ${basename(srcDir)}/`));
    console.log(chalk.dim(`  │   ├── __init__.py`));
    console.log(chalk.dim(`  │   └── ${pkgType === "agent" ? "agent.py" : "tool.py"}`));
    console.log(chalk.dim("  └── tests/"));
    console.log(chalk.dim(`      └── ${pkgType === "agent" ? "test_agent.py" : "test_tool.py"}`));
    console.log();
    console.log(`Next steps:`);
    console.log(`  1. Edit ${chalk.cyan("agentnode.yaml")} with your package details`);
    console.log(`  2. Implement your ${pkgType === "agent" ? "agent" : "tool"} in ${chalk.cyan(basename(srcDir) + "/" + (pkgType === "agent" ? "agent.py" : "tool.py"))}`);
    console.log(`  3. Run ${chalk.cyan("agentnode validate agentnode.yaml")} to check your manifest`);
    console.log(`  4. Run ${chalk.cyan("agentnode publish")} to publish`);
  });

function generateManifest(name: string, pkgType: string, publisher: string): Record<string, any> {
  const moduleName = name.replace(/-/g, "_");
  const base: Record<string, any> = {
    manifest_version: "0.2",
    package_id: name,
    package_type: pkgType,
    name: name.replace(/-/g, " ").replace(/\b\w/g, c => c.toUpperCase()),
    publisher,
    version: "0.1.0",
    summary: `A ${pkgType} package for AgentNode.`,
    runtime: "python",
    install_mode: "package",
    hosting_type: "agentnode_hosted",
    entrypoint: `${moduleName}.${pkgType === "agent" ? "agent" : "tool"}`,
    capabilities: {
      tools: pkgType !== "agent" ? [{
        name: "example_tool",
        capability_id: "example",
        description: "An example tool",
        input_schema: {
          type: "object",
          properties: { input: { type: "string", description: "Input text" } },
          required: ["input"],
        },
      }] : [],
      resources: [],
      prompts: [],
    },
    compatibility: { frameworks: ["generic"], python: ">=3.10" },
    permissions: {
      network: { level: "none", allowed_domains: [] },
      filesystem: { level: "temp" },
      code_execution: { level: "none" },
      data_access: { level: "input_only" },
      user_approval: { required: "never" },
      external_integrations: [],
    },
    tags: [],
    categories: [],
    dependencies: [],
    security: {
      signature: "",
      provenance: { source_repo: "", commit: "", build_system: "" },
    },
  };

  if (pkgType === "agent") {
    base.agent = {
      entrypoint: `${moduleName}.agent:run`,
      goal: "Describe what this agent does",
      tool_access: {
        allowed_packages: [],
      },
      limits: {
        max_iterations: 10,
        max_tool_calls: 50,
        max_runtime_seconds: 300,
      },
      termination: {
        stop_on_final_answer: true,
        stop_on_consecutive_errors: 3,
      },
    };
  }

  return base;
}

function generatePyproject(name: string): string {
  const moduleName = name.replace(/-/g, "_");
  return `[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.backends._legacy:_Backend"

[project]
name = "${name}"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = []

[tool.setuptools.packages.find]
include = ["${moduleName}*"]
`;
}

function generateAgentPy(name: string): string {
  return `"""${name} agent entrypoint."""


async def run(goal: str, context: dict | None = None) -> dict:
    """Main agent entrypoint.

    Args:
        goal: The goal/task for the agent to accomplish.
        context: Optional context dict with prior state, tools, etc.

    Returns:
        A dict with at least 'result' key containing the agent's output.
    """
    # TODO: Implement your agent logic here
    return {
        "result": f"Agent completed goal: {goal}",
        "steps_taken": 0,
    }
`;
}

function generateAgentTestPy(name: string): string {
  const moduleName = name.replace(/-/g, "_");
  return `"""Tests for ${name} agent."""
import pytest
from ${moduleName}.agent import run


@pytest.mark.asyncio
async def test_run_returns_result():
    result = await run("test goal")
    assert "result" in result
    assert isinstance(result["result"], str)
`;
}

function generateToolPy(name: string): string {
  return `"""${name} tool entrypoint."""


def example_tool(input: str) -> str:
    """An example tool function.

    Args:
        input: Input text to process.

    Returns:
        Processed result.
    """
    # TODO: Implement your tool logic here
    return f"Processed: {input}"
`;
}

function generateToolTestPy(name: string): string {
  const moduleName = name.replace(/-/g, "_");
  return `"""Tests for ${name} tool."""
from ${moduleName}.tool import example_tool


def test_example_tool():
    result = example_tool("hello")
    assert "hello" in result
`;
}
