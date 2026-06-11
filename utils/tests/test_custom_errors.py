"""Unit tests for utils.custom_errors."""

import logging

import pytest

from utils import custom_errors
from utils.custom_errors import MyCustomError


# Every subclass declared in the module that derives from MyCustomError.
SUBCLASSES = [
    custom_errors.AsyncPostgresUtilError,
    custom_errors.QuotaManagementError,
    custom_errors.SessionManagementError,
    custom_errors.ChatHistoryError,
    custom_errors.AuditLogError,
    custom_errors.AsyncFileStorageError,
    custom_errors.OrchestratorError,
    custom_errors.ResponsibleAIUtilError,
    custom_errors.SemanticRedisCacheError,
    custom_errors.AzureOpenAIClientError,
    custom_errors.LLMError,
    custom_errors.PythonGenerationAgentError,
    custom_errors.RagAgentError,
    custom_errors.PromptManagerError,
    custom_errors.OpenAIEmbeddingError,
    custom_errors.SQLAgentError,
    custom_errors.DataEncryptionError,
    custom_errors.FaultBucketingError,
    custom_errors.FaultClassifierError,
    custom_errors.MetricsExtractorError,
    custom_errors.ConfigLoaderError,
    custom_errors.AggregatorError,
    custom_errors.CertBuilderError,
]


class TestMyCustomError:
    def test_construction_message_only(self):
        err = MyCustomError("boom")
        assert str(err) == "boom"
        assert err.original_exception is None

    def test_is_exception_subclass(self):
        assert issubclass(MyCustomError, Exception)

    def test_original_exception_chaining(self):
        original = ValueError("root cause")
        err = MyCustomError("wrapper", original)
        assert err.original_exception is original
        assert str(err) == "wrapper"

    def test_can_be_raised_and_caught(self):
        with pytest.raises(MyCustomError) as excinfo:
            raise MyCustomError("raised")
        assert str(excinfo.value) == "raised"

    def test_args_propagated_to_base(self):
        err = MyCustomError("the message")
        assert err.args == ("the message",)


class TestTracebackEnabled:
    def test_traceback_path_no_original(self, monkeypatch):
        # When TRACEBACK_ENABLED is true and no original exception is given,
        # the format_stack branch is exercised. Should not raise.
        monkeypatch.setenv("TRACEBACK_ENABLED", "true")
        err = MyCustomError("with stack")
        assert err.original_exception is None
        assert str(err) == "with stack"

    def test_traceback_path_with_original(self, monkeypatch):
        monkeypatch.setenv("TRACEBACK_ENABLED", "TRUE")
        try:
            raise RuntimeError("inner")
        except RuntimeError as inner:
            err = MyCustomError("with tb", inner)
        assert err.original_exception is not None
        assert isinstance(err.original_exception, RuntimeError)

    def test_traceback_disabled_default(self, monkeypatch):
        monkeypatch.delenv("TRACEBACK_ENABLED", raising=False)
        err = MyCustomError("default", ValueError("x"))
        assert str(err) == "default"

    def test_logger_invoked_on_construction(self, monkeypatch):
        monkeypatch.setenv("TRACEBACK_ENABLED", "false")
        calls = []
        monkeypatch.setattr(
            custom_errors.logger, "error", lambda *a, **k: calls.append(a)
        )
        MyCustomError("logged message")
        assert calls, "logger.error should be called during construction"


class TestSubclasses:
    @pytest.mark.parametrize("cls", SUBCLASSES)
    def test_subclass_inheritance(self, cls):
        assert issubclass(cls, MyCustomError)
        assert issubclass(cls, Exception)

    @pytest.mark.parametrize("cls", SUBCLASSES)
    def test_subclass_construction_and_chaining(self, cls):
        original = KeyError("oops")
        err = cls("subclass message", original)
        assert str(err) == "subclass message"
        assert err.original_exception is original

    @pytest.mark.parametrize("cls", SUBCLASSES)
    def test_subclass_catchable_as_base(self, cls):
        with pytest.raises(MyCustomError):
            raise cls("caught as base")
