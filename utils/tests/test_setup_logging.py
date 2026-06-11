"""Unit tests for utils.setup_logging."""

import logging

import pytest

import utils.setup_logging as sl
from utils.setup_logging import SetupLogging, configure_azure_logging


class TestConfigureAzureLogging:
    def test_sets_noisy_loggers_to_warning(self):
        configure_azure_logging()
        for name in [
            "azure.core.pipeline.policies.http_logging_policy",
            "azure.storage",
            "azure.core",
            "azure.identity",
            "httpx",
            "redisvl",
            "redisvl.index.index",
            "urllib3",
            "openai",
        ]:
            assert logging.getLogger(name).level == logging.WARNING


class TestGetLogger:
    def test_returns_logger_instance(self, tmp_path):
        log = SetupLogging.get_logger(log_file=str(tmp_path / "logs" / "app.log"))
        assert isinstance(log, logging.Logger)

    def test_creates_log_directory(self, tmp_path):
        log_dir = tmp_path / "newdir" / "nested"
        assert not log_dir.exists()
        # Use a fresh logger name so handler-setup branch runs.
        log_file = log_dir / "app.log"
        # get_logger uses module __name__ logger; clear handlers to force setup.
        target = logging.getLogger(sl.__name__)
        target.handlers.clear()
        SetupLogging.get_logger(log_file=str(log_file))
        assert log_dir.exists()
        target.handlers.clear()

    def test_idempotent_no_duplicate_handlers(self, tmp_path):
        target = logging.getLogger(sl.__name__)
        target.handlers.clear()
        log_file = str(tmp_path / "logs" / "app.log")
        SetupLogging.get_logger(log_file=log_file)
        count_after_first = len(target.handlers)
        SetupLogging.get_logger(log_file=log_file)
        # Second call should not add more handlers (handlers already present).
        assert len(target.handlers) == count_after_first
        target.handlers.clear()

    def test_respects_custom_level(self, tmp_path):
        target = logging.getLogger(sl.__name__)
        target.handlers.clear()
        log = SetupLogging.get_logger(
            log_file=str(tmp_path / "logs" / "app.log"), level=logging.DEBUG
        )
        assert log.level == logging.DEBUG
        assert log.propagate is False
        target.handlers.clear()

    def test_module_level_logger_exists(self):
        assert isinstance(sl.logger, logging.Logger)


class TestSetupLoggingInit:
    def test_init_configures_azure_logging(self):
        # Constructor calls configure_azure_logging(); should not raise.
        instance = SetupLogging()
        assert instance is not None
        assert logging.getLogger("azure.core").level == logging.WARNING
