"""Pydantic request models for the cert-reporter API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class GenerateRequest(BaseModel):
    """Request body for POST /api/generate/pdf and POST /api/generate/html."""
    agent_id: str = Field(..., description="Agent ID used in POST /api/v1/aggregation-certification")
    experiment_id: str = Field(..., description="Experiment ID used in POST /api/v1/aggregation-certification")
