"""Tests du helper MCP `run_job` / `require_bridge` (sans serveur ni GPU).

On monkeypatch la `job_queue` importée dans `app.mcp.tools` pour simuler les
quatre issues : succès, timeout, erreur plugin, file saturée.
"""

from __future__ import annotations

import asyncio

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from app.mcp import tools as mcp_tools
from app.server.models import JobResult, JobType


class _FakeQueue:
    def __init__(self, *, wait_result=None, submit_raises=False, connected=True):
        self._wait = wait_result
        self._submit_raises = submit_raises
        self._connected = connected
        self.submitted = []

    def submit(self, job_type, payload=None):
        if self._submit_raises:
            raise RuntimeError("File de jobs saturée (100 en attente) — pont inactif ?")
        self.submitted.append((job_type, payload))
        return "job-123"

    def wait_result(self, job_id, timeout):
        return self._wait

    def bridge_connected(self, threshold: float = 5.0):
        return self._connected


def _patch(monkeypatch, queue):
    monkeypatch.setattr(mcp_tools, "job_queue", queue)


def test_run_job_success(monkeypatch):
    res = JobResult(job_id="job-123", status="ok", applied=3, matched=3, total=3)
    _patch(monkeypatch, _FakeQueue(wait_result=res))
    out = asyncio.run(mcp_tools.run_job(JobType.APPLY_ADJUSTMENTS, {"adjustments": []}))
    assert out is res
    assert out.applied == 3


def test_run_job_timeout(monkeypatch):
    _patch(monkeypatch, _FakeQueue(wait_result=None))
    with pytest.raises(ToolError, match="Timeout"):
        asyncio.run(mcp_tools.run_job(JobType.GET_SELECTED_PHOTOS, None, timeout=0.1))


def test_run_job_plugin_error(monkeypatch):
    res = JobResult(job_id="job-123", status="error", error="boom côté Lr")
    _patch(monkeypatch, _FakeQueue(wait_result=res))
    with pytest.raises(ToolError, match="boom"):
        asyncio.run(mcp_tools.run_job(JobType.TEST, None))


def test_run_job_queue_saturated(monkeypatch):
    _patch(monkeypatch, _FakeQueue(submit_raises=True))
    with pytest.raises(ToolError, match="satur"):
        asyncio.run(mcp_tools.run_job(JobType.TEST, None))


def test_require_bridge_disconnected(monkeypatch):
    _patch(monkeypatch, _FakeQueue(connected=False))
    with pytest.raises(ToolError, match="Pont Lightroom non connect"):
        mcp_tools.require_bridge()


def test_require_bridge_connected(monkeypatch):
    _patch(monkeypatch, _FakeQueue(connected=True))
    mcp_tools.require_bridge()  # ne lève pas
