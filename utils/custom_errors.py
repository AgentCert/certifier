"""
with these custom error, we can raise it in case of any error specific
to that module/class and catch it in the upper layer to handle it accordingly
"""

import os
import traceback

from utils.setup_logging import logger


class MyCustomError(Exception):
    """Custom error class for this application"""

    def __init__(self, message, original_exception=None):
        super().__init__(message)
        self.original_exception = original_exception

        # Log the error when it's created with traceback
        if os.getenv("TRACEBACK_ENABLED", "false").lower() == "true":
            if original_exception:
                tb_str = "".join(
                    traceback.format_exception(
                        type(original_exception),
                        original_exception,
                        original_exception.__traceback__,
                    )
                )
            else:
                tb_str = "".join(traceback.format_stack())
        else:
            tb_str = None

        if original_exception:

            logger.error(
                f"{message}. Original exception: {original_exception}"
                + f"\nTraceback:\n{tb_str}"
                if tb_str
                else ""
            )
        else:
            # Get current traceback
            logger.error(f"{message}" + f"\nTraceback:\n{tb_str}" if tb_str else "")


class AsyncPostgresUtilError(MyCustomError):
    """Custom error class for AsyncPostgresUtil class"""


class QuotaManagementError(MyCustomError):
    """Custom error class for QuotaManagement class"""


class SessionManagementError(MyCustomError):
    """Custom error class for SessionManagement class"""


class ChatHistoryError(MyCustomError):
    """Custom error class for ChatHistory class"""


class AuditLogError(MyCustomError):
    """Custom error class for AuditLog class"""


class AsyncFileStorageError(MyCustomError):
    """Custom error class for AsyncFileStorage class"""


class OrchestratorError(MyCustomError):
    """Custom error class for Orchestrator class"""


class ResponsibleAIUtilError(MyCustomError):
    """Custom error class for ResponsibleAIUtil class"""


class SemanticRedisCacheError(MyCustomError):
    """Custom error class for SemanticRedisCache class"""


class AzureOpenAIClientError(MyCustomError):
    """Custom error class for AzureOpenAIClient class"""


class LLMError(MyCustomError):
    """Custom error class for LLM class"""


class PythonGenerationAgentError(MyCustomError):
    """Custom error class for PythonGenerationAgent class"""


class RagAgentError(MyCustomError):
    """Custom error class for RAGAgent class"""


class PromptManagerError(MyCustomError):
    """Custom error class for PromptManager class"""


class OpenAIEmbeddingError(MyCustomError):
    """Custom error class for OpenAIEmbedding class"""


class SQLAgentError(MyCustomError):
    """Custom error class for SQLAgent class"""


class DataEncryptionError(MyCustomError):  # pragma: no cover - thin wrapper
    """Raised for encryption/decryption related errors"""
