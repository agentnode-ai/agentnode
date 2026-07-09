"""Package templates for `agentnode init`."""
from __future__ import annotations

TEMPLATES = {
    "local": {
        "label": "Local tool (pure computation, no network, no files)",
        "description": "A tool that transforms input data locally. No external API calls, no file I/O.",
        "files": {
            "agentnode.yaml": """\
manifest_version: "0.2"
package_id: "{package_id}"
package_type: "toolpack"
name: "{name}"
publisher: "{publisher}"
version: "1.0.0"
summary: "{summary}"
description: |
  {description}

runtime: "python"
install_mode: "package"
hosting_type: "agentnode_hosted"
entrypoint: "{module_name}.tool"

capabilities:
  tools:
    - name: "{tool_name}"
      capability_id: "{capability_id}"
      description: "{tool_description}"
      entrypoint: "{module_name}.tool:{tool_name}"
      input_schema:
        type: "object"
        properties:
          data:
            type: "string"
            description: "Input data to process"
        required: ["data"]
      output_schema:
        type: "object"
        properties:
          result:
            type: "string"
  resources: []
  prompts: []

tags: []
categories: []

compatibility:
  frameworks: ["generic"]
  python: ">=3.10"

dependencies: []

permissions:
  network:
    level: "none"
    allowed_domains: []
  filesystem:
    level: "none"
  code_execution:
    level: "none"
  data_access:
    level: "input_only"
  user_approval:
    required: "never"
  external_integrations: []

verification:
  cases:
    - name: "basic_transform"
      tool: "{tool_name}"
      input:
        data: "hello world"
      expected:
        return_type: "dict"
        required_keys: ["result"]
    - name: "empty_input"
      tool: "{tool_name}"
      input:
        data: ""
      expected:
        return_type: "dict"
        required_keys: ["result"]
""",
            "pyproject.toml": """\
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "{package_id}"
version = "1.0.0"
requires-python = ">=3.10"
dependencies = []

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
""",
            "src/{module_name}/__init__.py": """\
from {module_name}.tool import {tool_name} as run
""",
            "src/{module_name}/tool.py": """\
def {tool_name}(data: str, **kwargs) -> dict:
    \"\"\"Process input data and return result.\"\"\"
    if not data:
        return {{"result": "", "length": 0}}

    result = data.upper()  # Replace with your logic
    return {{"result": result, "length": len(result)}}


def run(data: str, **kwargs) -> dict:
    \"\"\"Default entrypoint.\"\"\"
    return {tool_name}(data=data, **kwargs)
""",
            "tests/__init__.py": "",
            "tests/test_tool.py": """\
from {module_name}.tool import {tool_name}


def test_basic():
    result = {tool_name}(data="hello")
    assert isinstance(result, dict)
    assert "result" in result


def test_empty_input():
    result = {tool_name}(data="")
    assert result["result"] == ""
""",
        },
    },
    "api": {
        "label": "API connector (external API with VCR cassette for Gold)",
        "description": "A tool that calls an external HTTP API. Includes VCR cassette setup for Gold verification.",
        "files": {
            "agentnode.yaml": """\
manifest_version: "0.2"
package_id: "{package_id}"
package_type: "toolpack"
name: "{name}"
publisher: "{publisher}"
version: "1.0.0"
summary: "{summary}"
description: |
  {description}

runtime: "python"
install_mode: "package"
hosting_type: "agentnode_hosted"
entrypoint: "{module_name}.tool"

capabilities:
  tools:
    - name: "{tool_name}"
      capability_id: "{capability_id}"
      description: "{tool_description}"
      entrypoint: "{module_name}.tool:{tool_name}"
      input_schema:
        type: "object"
        properties:
          query:
            type: "string"
            description: "Search query or API input"
        required: ["query"]
      output_schema:
        type: "object"
        properties:
          results:
            type: "array"
  resources: []
  prompts: []

tags: []
categories: []

compatibility:
  frameworks: ["generic"]
  python: ">=3.10"

dependencies: []

permissions:
  network:
    level: "restricted"
    allowed_domains: ["api.example.com"]
  filesystem:
    level: "none"
  code_execution:
    level: "none"
  data_access:
    level: "input_only"
  user_approval:
    required: "never"
  external_integrations: []

verification:
  cases:
    - name: "basic_query"
      tool: "{tool_name}"
      input:
        query: "test query"
      cassette: "fixtures/cassettes/basic_query.yaml"
      expected:
        return_type: "dict"
        required_keys: ["results"]
    - name: "empty_query"
      tool: "{tool_name}"
      input:
        query: ""
      cassette: "fixtures/cassettes/empty_query.yaml"
      expected:
        return_type: "dict"
        required_keys: ["results"]
""",
            "pyproject.toml": """\
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "{package_id}"
version = "1.0.0"
requires-python = ">=3.10"
dependencies = [
    "httpx>=0.25",
]

[project.optional-dependencies]
dev = ["pytest", "vcrpy"]

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
""",
            "MANIFEST.in": """\
recursive-include fixtures *
include agentnode.yaml
""",
            "src/{module_name}/__init__.py": """\
from {module_name}.tool import {tool_name} as run
""",
            "src/{module_name}/tool.py": """\
import httpx


API_BASE = "https://api.example.com"


def {tool_name}(query: str, **kwargs) -> dict:
    \"\"\"Call external API with query and return results.\"\"\"
    if not query:
        return {{"results": [], "total": 0}}

    with httpx.Client(timeout=10) as client:
        resp = client.get(f"{{API_BASE}}/search", params={{"q": query}})
        resp.raise_for_status()
        data = resp.json()

    return {{
        "results": data.get("items", []),
        "total": data.get("total", 0),
    }}


def run(query: str, **kwargs) -> dict:
    \"\"\"Default entrypoint.\"\"\"
    return {tool_name}(query=query, **kwargs)
""",
            "fixtures/cassettes/.gitkeep": "",
            "record_fixtures.py": """\
\"\"\"Record VCR cassettes for verification fixtures.

Run this script once with real API credentials:
    python record_fixtures.py

Then commit the generated cassette files in fixtures/cassettes/.
\"\"\"
import os
import vcr

from {module_name}.tool import {tool_name}

os.makedirs("fixtures/cassettes", exist_ok=True)

my_vcr = vcr.VCR(
    cassette_library_dir="fixtures/cassettes",
    record_mode="new_episodes",
    match_on=["method", "uri"],
    filter_headers=["Authorization"],  # Don't record API keys
)

print("Recording cassettes...")

with my_vcr.use_cassette("basic_query.yaml"):
    result = {tool_name}(query="test query")
    print(f"  basic_query: {{len(result.get('results', []))}} results")

with my_vcr.use_cassette("empty_query.yaml"):
    result = {tool_name}(query="")
    print(f"  empty_query: {{len(result.get('results', []))}} results")

print("Done. Check fixtures/cassettes/ for recorded files.")
print("Remember to review cassettes for leaked credentials!")
""",
            "tests/__init__.py": "",
            "tests/test_tool.py": """\
from unittest.mock import patch, MagicMock
from {module_name}.tool import {tool_name}


def test_basic_query():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {{"items": [{{"title": "Test"}}], "total": 1}}
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.Client") as mock_client:
        mock_client.return_value.__enter__ = lambda s: s
        mock_client.return_value.__exit__ = MagicMock(return_value=False)
        mock_client.return_value.get.return_value = mock_resp
        result = {tool_name}(query="test")

    assert "results" in result
    assert result["total"] == 1


def test_empty_query():
    result = {tool_name}(query="")
    assert result == {{"results": [], "total": 0}}
""",
        },
    },
    "file": {
        "label": "File-based tool (processes CSV, JSON, images, etc.)",
        "description": "A tool that reads and processes files. Includes test fixture files for Gold verification.",
        "files": {
            "agentnode.yaml": """\
manifest_version: "0.2"
package_id: "{package_id}"
package_type: "toolpack"
name: "{name}"
publisher: "{publisher}"
version: "1.0.0"
summary: "{summary}"
description: |
  {description}

runtime: "python"
install_mode: "package"
hosting_type: "agentnode_hosted"
entrypoint: "{module_name}.tool"

capabilities:
  tools:
    - name: "{tool_name}"
      capability_id: "{capability_id}"
      description: "{tool_description}"
      entrypoint: "{module_name}.tool:{tool_name}"
      input_schema:
        type: "object"
        properties:
          file_path:
            type: "string"
            description: "Path to the input file"
        required: ["file_path"]
      output_schema:
        type: "object"
        properties:
          rows:
            type: "integer"
          columns:
            type: "array"
  resources: []
  prompts: []

tags: []
categories: []

compatibility:
  frameworks: ["generic"]
  python: ">=3.10"

dependencies: []

permissions:
  network:
    level: "none"
    allowed_domains: []
  filesystem:
    level: "workspace_read"
  code_execution:
    level: "none"
  data_access:
    level: "input_only"
  user_approval:
    required: "never"
  external_integrations: []

verification:
  cases:
    - name: "process_sample_file"
      tool: "{tool_name}"
      input:
        file_path: "/workspace/fixtures/sample.csv"
      expected:
        return_type: "dict"
        required_keys: ["rows", "columns"]
    - name: "process_empty_file"
      tool: "{tool_name}"
      input:
        file_path: "/workspace/fixtures/empty.csv"
      expected:
        return_type: "dict"
        required_keys: ["rows", "columns"]
""",
            "pyproject.toml": """\
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "{package_id}"
version = "1.0.0"
requires-python = ">=3.10"
dependencies = []

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
""",
            "MANIFEST.in": """\
recursive-include fixtures *
include agentnode.yaml
""",
            "fixtures/sample.csv": """\
id,name,value
1,alpha,100
2,beta,200
3,gamma,300
""",
            "fixtures/empty.csv": """\
id,name,value
""",
            "src/{module_name}/__init__.py": """\
from {module_name}.tool import {tool_name} as run
""",
            "src/{module_name}/tool.py": """\
import csv
from pathlib import Path


def {tool_name}(file_path: str, **kwargs) -> dict:
    \"\"\"Read and analyze a CSV file.\"\"\"
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {{file_path}}")

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        columns = reader.fieldnames or []

    return {{
        "rows": len(rows),
        "columns": list(columns),
        "sample": rows[:5] if rows else [],
    }}


def run(file_path: str, **kwargs) -> dict:
    \"\"\"Default entrypoint.\"\"\"
    return {tool_name}(file_path=file_path, **kwargs)
""",
            "tests/__init__.py": "",
            "tests/test_tool.py": """\
import tempfile
import os
from {module_name}.tool import {tool_name}


def test_process_csv():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        f.write("id,name\\n1,test\\n2,demo\\n")
        f.flush()
        result = {tool_name}(file_path=f.name)

    os.unlink(f.name)
    assert result["rows"] == 2
    assert "id" in result["columns"]


def test_empty_csv():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        f.write("id,name\\n")
        f.flush()
        result = {tool_name}(file_path=f.name)

    os.unlink(f.name)
    assert result["rows"] == 0
""",
        },
    },
    "skill": {
        "label": "Skill (prompt + assets, no code)",
        "description": "A prompt-based skill package. Delivers instructions and reference materials — no code execution.",
        "files": {
            "agentnode.yaml": """\
manifest_version: "0.3"
package_id: "{package_id}"
package_type: "skill"
name: "{name}"
publisher: "{publisher}"
version: "1.0.0"
summary: "{summary}"
description: |
  {description}

runtime: "none"
install_mode: "prompt_only"
hosting_type: "agentnode_hosted"

capabilities:
  tools: []
  resources: []
  prompts:
    - name: "{module_name}"
      description: "{summary}"
      template: "SKILL.md"
      arguments:
        - name: "topic"
          description: "The topic to apply this skill to"
          required: true

assets:
  - id: "example"
    type: "document"
    path: "assets/examples/example.md"
    description: "Example output for reference"

tags: []
categories: []

compatibility:
  frameworks: ["generic"]

permissions:
  network:
    level: "none"
    allowed_domains: []
  filesystem:
    level: "none"
  code_execution:
    level: "none"
  data_access:
    level: "input_only"
  user_approval:
    required: "never"
  external_integrations: []
""",
            "SKILL.md": """\
# {name}

You are an expert at {{{{topic}}}}.

## Instructions

1. Analyze the given topic carefully
2. Apply structured thinking
3. Provide a clear, actionable result

## Output Format

Return your response in clear Markdown with headings and bullet points.

## Reference

See the example in `assets/examples/example.md` for the expected style and quality.
""",
            "assets/examples/example.md": """\
# Example: Sample Topic

This is an example of the expected output quality and format.

## Key Points

- Point one with supporting detail
- Point two with evidence
- Point three with recommendation

## Summary

A concise summary of the analysis.
""",
        },
    },
    "mcp": {
        "label": "MCP server (list an existing npm/PyPI MCP server)",
        "description": "A listing for an MCP server already published to npm or PyPI. No code is uploaded — you point AgentNode at the package. Submit with `agentnode mcp submit` after `agentnode mcp verify`.",
        "files": {
            "agentnode.yaml": """\
manifest_version: "0.3"
package_id: "{package_id}"
package_type: "toolpack"
name: "{name}"
publisher: "{publisher}"
version: "1.0.0"
summary: "{summary}"
description: |
  {description}

runtime: "mcp"
install_mode: "package"
hosting_type: "agentnode_hosted"

# The MCP server to run. Fill in ONE of npm_package / pypi_package and pin the
# EXACT version in the command (e.g. @0.1.0 for npm, ==0.1.0 for PyPI) — an
# unpinned command is not reproducible and is flagged on submit.
mcp_server:
  # npm example (delete the pypi_package line):
  command: ["npx", "-y", "REPLACE_ME_PACKAGE@REPLACE_VERSION"]
  npm_package: "REPLACE_ME_PACKAGE"
  # pypi example (swap the command + use pypi_package instead of npm_package):
  #   command: ["uvx", "REPLACE_ME_PACKAGE==REPLACE_VERSION"]
  #   pypi_package: "REPLACE_ME_PACKAGE"
  # Ownership is checked against this repo — it must match the package's
  # registry metadata (npm/PyPI "repository" / homepage).
  source_repo: "https://github.com/REPLACE_ME_OWNER/REPLACE_ME_REPO"

tags: []
categories: []

compatibility:
  frameworks: ["generic"]

# Declare honestly what the MCP server needs. Verification compares these
# against what the server actually requests.
permissions:
  network:
    level: "restricted"
    allowed_domains: []
  filesystem:
    level: "none"
  code_execution:
    level: "none"
  data_access:
    level: "input_only"
  user_approval:
    required: "high_risk_only"
  external_integrations: []
""",
            "README.md": """\
# {name} (MCP server listing)

This package LISTS an existing MCP server (published on npm or PyPI) in the
AgentNode catalog. No code is uploaded — AgentNode points at the upstream
package.

## Before you submit

1. Edit `agentnode.yaml`:
   - Set `mcp_server.npm_package` OR `mcp_server.pypi_package` (not both).
   - Pin the EXACT version in `mcp_server.command` (npm `@x.y.z`, PyPI `==x.y.z`).
   - Set `mcp_server.source_repo` to the repo that owns the package.
   - Declare `permissions` honestly.
2. Verify locally:

   ```
   agentnode mcp verify . --test
   ```

3. Submit for the catalog (server re-verifies authoritatively):

   ```
   agentnode mcp submit . --token $AGENTNODE_API_KEY
   ```

`agentnode publish` is for toolpacks / skills / agents; MCP listings go
through `agentnode mcp submit`.
""",
        },
    },
    "agent": {
        "label": "Agent (orchestrates tools to accomplish goals)",
        "description": "An autonomous agent that uses other tool packages to accomplish goals via an LLM.",
        "files": {
            "agentnode.yaml": """\
manifest_version: "0.2"
package_id: "{package_id}"
package_type: "agent"
name: "{name}"
publisher: "{publisher}"
version: "1.0.0"
summary: "{summary}"
description: |
  {description}

runtime: "python"
install_mode: "package"
hosting_type: "agentnode_hosted"
entrypoint: "{module_name}.agent"

capabilities:
  tools: []
  resources: []
  prompts: []

tags: []
categories: []

compatibility:
  frameworks: ["generic"]
  python: ">=3.10"

dependencies: []

permissions:
  network:
    level: "none"
    allowed_domains: []
  filesystem:
    level: "none"
  code_execution:
    level: "none"
  data_access:
    level: "input_only"
  user_approval:
    required: "never"
  external_integrations: []

agent:
  entrypoint: "{module_name}.agent:run"
  goal: "{agent_goal}"
  tier: "llm_only"
  llm:
    required: true
  llm_access:
    # Host-brokered LLM access for SANDBOXED community runs (opt-in).
    # When your agent runs sandboxed on someone else's machine, it can only
    # reach their LLM credentials through the host broker, and only if you
    # set enabled: true here. Default false = no access (calls fail gracefully).
    # The host's own ceiling (agent_sandbox.llm in the host config) ALWAYS
    # wins: it can lower these caps or disable access entirely.
    enabled: false
    max_calls: 20
    max_input_chars: 24000
    max_output_chars: 24000
    # Optionally restrict which host-chosen models may serve this agent
    # (the agent cannot pick a model; the host does). Omit = any host model.
    # An explicit empty list means no model is acceptable.
    # allowed_models: ["gpt-4o-mini", "claude-3-5-haiku-latest"]
  system_prompt: |
    You are a helpful agent that accomplishes goals step by step.
    Think carefully, use available tools, and return a clear result.
  tool_access:
    allowed_packages: []
  limits:
    max_iterations: 10
    max_tool_calls: 50
    max_runtime_seconds: 300
  termination:
    stop_on_final_answer: true
    stop_on_consecutive_errors: 3
  isolation: "thread"
  state:
    persistence: "none"
  verification:
    cases:
      - name: "basic_goal"
        goal: "Summarize the key points of: AI agents can use tools to accomplish tasks autonomously."
        expected:
          required_keys: ["result", "done"]
          done: true
      - name: "short_input"
        goal: "What is 2+2?"
        expected:
          required_keys: ["result", "done"]
          done: true
""",
            "pyproject.toml": """\
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "{package_id}"
version = "1.0.0"
requires-python = ">=3.10"
dependencies = []

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
""",
            "src/{module_name}/__init__.py": """\
\"\"\"{name} — an AgentNode agent.\"\"\"\n""",
            "src/{module_name}/agent.py": """\
async def run(context, **kwargs) -> dict:
    \"\"\"Agent entrypoint.

    Args:
        context: AgentContext provided by the runtime. Provides:
            - context.goal: the goal string
            - context.llm(prompt): call the LLM
            - context.run_tool(package, tool, **kwargs): call a tool
            - context.next_iteration(): signal progress

    Returns:
        Dict with 'result' and 'done' keys.
    \"\"\"
    goal = context.goal

    # Step 1: Think about the goal
    response = await context.llm(
        f"You are given this goal: {{goal}}\\n\\n"
        "Provide a clear, concise answer. Be direct."
    )

    await context.next_iteration()

    return {{
        "result": response,
        "done": True,
        "steps_taken": 1,
    }}
""",
        },
    },
}
