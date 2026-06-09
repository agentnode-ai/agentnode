"""Throwaway spike: route an agent entrypoint through a sandbox.

NOT production code. NOT imported by the SDK. De-risks the two hard problems of
the future agent-sandbox bow:
  A) an agent in a container calls tools via the host's gated runner.run_tool;
  B) an agent gets LLM answers without the container ever seeing host secrets.

Deletable. See README.md.
"""
