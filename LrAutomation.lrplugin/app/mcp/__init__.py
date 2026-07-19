"""Serveur MCP (Model Context Protocol) de Lr_automation.

Expose le pont plugin↔App existant comme outils MCP pour que Claude pilote
Lightroom Classic. Monté in-process sur le serveur FastAPI (`app/server/api.py`)
via `app.mount("/mcp", ...)` : les outils partagent directement le singleton
`job_queue` (soumission d'un job + attente bloquante du résultat, offloadée sur
un thread worker — cf. `app/mcp/tools.py`).

NB : `app.mcp` (ce paquet) ≠ `mcp` (SDK PyPI, top-level). Les imports absolus
`from mcp.server.fastmcp import ...` visent toujours le SDK.
"""
