"""How anything reaches AgentNode.

One contract, several transports over it, and no security decision anywhere but the server. What
lives here is the part that is the same for every client: which systems can reach us at all
(`compatibility`), and -- as it is built -- the canonical operation contract that MCP, REST, the
SDK, the CLI and the web client are each a rendering of.

Nothing here is specific to any AI vendor. A provider name may appear in a test or in a
compatibility record as data; it may not appear in a decision.
"""
