"""ABELr's MCP (Model Context Protocol) server.

Exposes the existing plugin<->App bridge as MCP tools so Claude can drive
Lightroom Classic. Mounted in-process on the FastAPI server (`app/server/api.py`)
via `app.mount("/mcp", ...)`: the tools directly share the `job_queue`
singleton (job submission + blocking wait for the result, offloaded to
a worker thread — see `app/mcp/tools.py`).

NB: `app.mcp` (this package) != `mcp` (PyPI SDK, top-level). Absolute imports
`from mcp.server.fastmcp import ...` always target the SDK.
"""
