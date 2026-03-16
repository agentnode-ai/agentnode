# AgentNode Publish Action

GitHub Action to publish ANP packages to the AgentNode registry.

## Usage

```yaml
- uses: agentnode-ai/agentnode/publish-action@v1
  with:
    api-key: ${{ secrets.AGENTNODE_API_KEY }}
```

## Inputs

| Input | Required | Default | Description |
|---|---|---|---|
| `api-key` | Yes | - | AgentNode API key (`ank_...`) |
| `package-dir` | No | `.` | Path to package directory |
| `registry-url` | No | `https://api.agentnode.net` | Registry URL |

## Example Workflow

```yaml
name: Publish to AgentNode
on:
  push:
    tags: ["v*"]

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: agentnode-ai/agentnode/publish-action@v1
        with:
          api-key: ${{ secrets.AGENTNODE_API_KEY }}
          package-dir: "./my-pack"
```

## Requirements

- `agentnode.yaml` must exist in the package directory
- `pyproject.toml` must exist in the package directory
- Valid AgentNode API key with publisher permissions
