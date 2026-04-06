"""
Data models for trace metrics extraction.
Contains local dataclasses used within the extraction pipeline.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from metrics_extractor.schema.metrics_model import (
    LLMQualitativeExtraction,
    LLMQuantitativeExtraction,
)


@dataclass
class TokenUsage:
    """Tracks token usage for LLM calls during metrics extraction."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    def add(self, usage: Dict[str, int]) -> None:
        """Add token counts from an LLM response."""
        self.input_tokens += usage.get("input_tokens", 0)
        self.output_tokens += usage.get("output_tokens", 0)
        self.total_tokens += usage.get("total_tokens", 0)

    def to_dict(self) -> Dict[str, int]:
        """Convert to dictionary."""
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass
class ExtractionResult:
    """Result of metrics extraction including token usage."""

    quantitative: LLMQuantitativeExtraction
    qualitative: LLMQualitativeExtraction
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    mongodb_document_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        result = {
            "quantitative": (
                self.quantitative.to_dict()
                if hasattr(self.quantitative, "to_dict")
                else self.quantitative.model_dump()
            ),
            "qualitative": (
                self.qualitative.to_dict()
                if hasattr(self.qualitative, "to_dict")
                else self.qualitative.model_dump()
            ),
            "token_usage": self.token_usage.to_dict(),
        }
        if self.mongodb_document_id:
            result["mongodb_document_id"] = self.mongodb_document_id
        return result
