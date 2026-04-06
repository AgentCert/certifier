"""
Module to setup logging for the application.
"""

import logging
from pathlib import Path


class SetupLogging:
    """
    Class to setup logging for the application.
    """

    def __init__(self):
        configure_azure_logging()

    @staticmethod
    def get_logger(log_file="data/app.log", level=logging.INFO):
        """Function to setup a logger
        :param log_file: Path of the log file to store the logs
        :param level: Default level of logging
        :return: A logger object"""

        # Create a custom logger
        logger = logging.getLogger(__name__)

        if not logger.handlers:
            # Set the log level
            logger.setLevel(level)

            # Disable propagation to prevent duplicate logs
            logger.propagate = False

            # Create handlers
            c_handler = logging.StreamHandler()
            # create log_file if it does not exist
            log_path = Path(log_file)
            log_dir = log_path.parent
            if not log_dir.exists():
                try:
                    log_dir.mkdir(parents=True, exist_ok=True)
                    print(f"Created log directory: {log_dir}")
                except OSError as e:
                    raise OSError(
                        f"Failed to create log directory {log_dir}: {e}"
                    ) from e
            f_handler = logging.FileHandler(log_file)

            c_handler.setLevel(level)
            f_handler.setLevel(level)

            # Create formatters and add them to handlers
            c_format = logging.Formatter(
                "%(asctime)s - [%(filename)s : %(funcName)s : %(lineno)d]"
                " - %(levelname)s - %(message)s"
            )
            f_format = logging.Formatter(
                "%(asctime)s - [%(filename)s : %(funcName)s : %(lineno)d]"
                " - %(levelname)s - %(message)s"
            )

            c_handler.setFormatter(c_format)
            f_handler.setFormatter(f_format)

            # Add handlers to the logger
            logger.addHandler(c_handler)
            logger.addHandler(f_handler)

        return logger


# # Example usage in another script
# if __name__ == "__main__":
#     log = SetupLogging.get_logger()
#     log.info("This is an info message")
#     log.error("This is an error message")


def configure_azure_logging():
    """Configure Azure SDK logging to reduce verbosity"""
    logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(
        logging.WARNING
    )
    logging.getLogger("azure.storage").setLevel(logging.WARNING)
    logging.getLogger("azure.core").setLevel(logging.WARNING)
    logging.getLogger("azure.identity").setLevel(logging.WARNING)

    # Suppress httpx HTTP request logs
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # Suppress redisvl logs
    logging.getLogger("redisvl").setLevel(logging.WARNING)
    logging.getLogger("redisvl.index.index").setLevel(logging.WARNING)

    # Suppress other common noisy loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)


logger = SetupLogging.get_logger()
