"""
This module is used to load environment variables from the system.
"""

import json
import os
from pathlib import Path


class EnvLoader:
    """
    This class is used to load environment variables from the system.
    """

    @staticmethod
    def load_env_vars(variable_name, compulsory=False):
        """
        This function is used to load environment variables.
        :param variableName: Name of the environment variable.
        :param compulsory: Whether the variable should be mandatory or not.
        :return: Value of the environment variable.
        """
        value = os.getenv(variable_name, None)
        if value is None and compulsory:
            raise ValueError(
                f"Compulsory environment variable '{variable_name}' is missing."
            )
        if value in ["True", "true", "TRUE"]:
            return True
        elif value in ["False", "false", "FALSE"]:
            return False
        return value


class ConfigLoader:
    """
    This class is used to load configuration from environment variables.
    """

    @staticmethod
    def _resolve_env_values(config_data):
        """
        Recursively resolve environment variables in config values.
        If a value starts with 'ENV_', replace it with the corresponding environment variable.

        :param config_data: Configuration data (dict, list, or primitive value)
        :return: Configuration data with resolved environment variables
        """
        if isinstance(config_data, dict):
            return {
                key: ConfigLoader._resolve_env_values(value)
                for key, value in config_data.items()
            }
        elif isinstance(config_data, list):
            return [ConfigLoader._resolve_env_values(item) for item in config_data]
        elif isinstance(config_data, str) and config_data.startswith("ENV_"):
            env_var_name = config_data[4:]  # Remove 'ENV_' prefix
            return EnvLoader.load_env_vars(env_var_name, compulsory=False)
        else:
            return config_data

    @staticmethod
    def load_config():
        """
        Load configuration from configs.json file.
        Replaces values starting with 'ENV_' with corresponding environment variables.

        :return: Configuration dictionary with resolved environment variables
        """
        # Find the configs.json file relative to this module
        current_dir = Path(__file__).parent.parent
        config_path = current_dir / "configs" / "configs.json"

        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")

        with open(config_path, "r", encoding="utf-8") as config_file:
            config_data = json.load(config_file)

        # Resolve environment variables in the config
        resolved_config = ConfigLoader._resolve_env_values(config_data)

        return resolved_config


if __name__ == "__main__":
    # Example usage
    config = ConfigLoader.load_config()
    print(json.dumps(config, indent=4))
