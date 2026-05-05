"""Pydantic request / response models for the cert-reporter API."""

from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel, Field


class GenerateRequest(BaseModel):
    """JSON body for POST /api/generate when sending raw JSON content."""
    json_content: dict[str, Any] = Field(..., description="Full certification document JSON")
    formats: list[str] = Field(default=["html", "pdf"], description="Output formats: html, pdf")
    enrich_llm: bool = Field(default=False, description="Use LLM to improve narrative prose")
    model: str = Field(default="gpt-4.1-mini", description="LLM model name")
    provider: str = Field(default="openai", description="LLM provider: openai or anthropic")
    temperature: float = Field(default=0.4, ge=0.0, le=2.0)
    mode: str = Field(default="static", description="Pipeline mode: static | agentic")


class GenerateResponse(BaseModel):
    doc_id: str
    html_url: Optional[str] = None
    pdf_url: Optional[str] = None
    errors: list[str] = []
    token_usage: Optional[dict[str, int]] = None
    duration_seconds: float = 0.0


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "1.0.0"


class ReportItem(BaseModel):
    doc_id: str
    html_url: Optional[str] = None
    pdf_url: Optional[str] = None
    size_kb: Optional[float] = None
