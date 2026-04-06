"""This module provides an Azure OpenAI client for interacting with Azure OpenAI services."""

import json
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional, Type, Union

import openai
from agent_framework import ChatAgent, ChatMessage
from agent_framework.azure import AzureOpenAIChatClient
from utils import custom_errors
from utils.load_config import ConfigLoader
from utils.setup_logging import logger


class AzureLLMClient:
    """A client for interacting with Azure OpenAI services using agent_framework's AzureOpenAIChatClient."""

    # Shared client instance (singleton pattern)
    _shared_client: Optional[AzureOpenAIChatClient] = None
    _shared_clients: Dict[str, AzureOpenAIChatClient] = {}
    _model_types: Dict[str, str] = {}

    def __init__(self, config: Optional[dict] = {}):
        """
        Initialize the Azure LLM Client.

        Args:
            config: Optional configuration dictionary with model settings.
                   If not provided, uses environment variables for Azure OpenAI config.
        """
        self.config = config.get("models", {}) or {}
        self.model_agents: Dict[str, ChatAgent] = {}

        try:
            # Initialize the shared Azure OpenAI client
            # AzureOpenAIChatClient reads from env vars automatically:
            # AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT
            self._clients = self.get_clients(self.config)
            # Store model types for parameter filtering
            for model_name, model_config in self.config.items():
                if isinstance(model_config, dict) and "model_type" in model_config:
                    self.__class__._model_types[model_name] = model_config["model_type"]
            logger.info("AzureLLMClient initialized successfully")
        except Exception as e:
            logger.error(f"Error initializing AzureLLMClient: {str(e)}")
            raise custom_errors.LLMError(
                f"Error in {self.__class__.__name__}.__init__: {str(e)}",
                e,
            ) from e

    @classmethod
    def get_client(cls, config: dict) -> AzureOpenAIChatClient:
        """Get or create shared AzureOpenAIChatClient instance."""
        if cls._shared_client is None:
            cls._shared_client = AzureOpenAIChatClient(
                endpoint=config.get("extraction_model", {}).get(
                    "endpoint", os.getenv("AZURE_OPENAI_ENDPOINT")
                ),
                api_key=config.get("extraction_model", {}).get(
                    "api_key", os.getenv("AZURE_OPENAI_API_KEY")
                ),
                deployment_name=config.get("extraction_model", {}).get(
                    "deployment_name", os.getenv("AZURE_OPENAI_DEPLOYMENT")
                ),
                api_version=config.get("extraction_model", {}).get(
                    "api_version", "2023-05-15"
                ),
            )
        return cls._shared_client

    @classmethod
    def get_clients(cls, config: dict) -> Dict[str, AzureOpenAIChatClient]:
        """
        Get or create shared AzureOpenAIChatClient instances for all models in config.
        Args:
            config: Configuration dictionary containing model settings
        Returns:
            Dictionary of model_name -> AzureOpenAIChatClient instances
        """
        for model_name, model_config in config.items():
            # Skip if client already exists for this model
            if model_name in cls._shared_clients:
                continue

            try:
                # Create a new client for this model
                client = AzureOpenAIChatClient(
                    endpoint=model_config.get(
                        "endpoint", os.getenv("AZURE_OPENAI_ENDPOINT")
                    ),
                    api_key=model_config.get(
                        "api_key", os.getenv("AZURE_OPENAI_API_KEY")
                    ),
                    deployment_name=model_config.get(
                        "deployment_name", os.getenv("AZURE_OPENAI_DEPLOYMENT")
                    ),
                    api_version=model_config.get("api_version", "2023-05-15"),
                )
                cls._shared_clients[model_name] = client
                logger.info(f"Created Azure OpenAI client for model: {model_name}")
            except Exception as e:
                logger.error(
                    f"Failed to create client for model {model_name}: {str(e)}"
                )
                raise custom_errors.LLMError(
                    f"Error creating client for model {model_name}: {str(e)}",
                    e,
                ) from e

        return cls._shared_clients

    def _get_or_create_agent(
        self,
        model_name: str,
        system_prompt: str = "",
        tools: Optional[List[Any]] = None,
    ) -> ChatAgent:
        """
        Get or create a ChatAgent for the specified model.

        Args:
            model_name: Identifier for the model/agent
            system_prompt: System instructions for the agent
            tools: Optional list of tools for the agent

        Returns:
            ChatAgent instance
        """
        cache_key = f"{model_name}_{hash(system_prompt)}"

        if model_name in self._clients:
            client = self._clients.get(model_name)
        else:
            logger.warning(
                f"No specific client found for model '{model_name}'. Using default client."
            )
            client = self.get_client(self.config)

        if cache_key not in self.model_agents:
            self.model_agents[cache_key] = ChatAgent(
                chat_client=client,
                instructions=system_prompt,
                agent_name=model_name,
                tools=tools or [],
            )

        return self.model_agents[cache_key]

    def is_reasoning_model(self, model_name: str) -> bool:
        """Check if a model is a reasoning model that doesn't support temperature/max_tokens."""
        return self._model_types.get(model_name, "standard") == "reasoning"

    def _convert_messages_to_chat_messages(
        self, messages: Union[List[Dict[str, str]], List[ChatMessage], str]
    ) -> List[ChatMessage]:
        """
        Convert various message formats to ChatMessage list.

        Args:
            messages: Messages in dict format, ChatMessage format, or string

        Returns:
            List of ChatMessage objects
        """
        if isinstance(messages, str):
            return [ChatMessage(role="user", text=messages)]

        chat_messages = []
        for msg in messages:
            if isinstance(msg, ChatMessage):
                chat_messages.append(msg)
            elif isinstance(msg, dict):
                role = msg.get("role", "user")
                content = msg.get("content", msg.get("text", ""))
                chat_messages.append(ChatMessage(role=role, text=content))

        return chat_messages

    async def call_llm(
        self,
        model_name: str,
        messages: Union[List[Dict[str, str]], List[ChatMessage], str],
        temperature: float = 0.7,
        max_tokens: int = 800,
        system_prompt: str = "",
        logging_extra: Optional[Dict] = None,
        **kwargs,
    ) -> tuple:
        """
        Call the LLM with the given parameters.

        Args:
            model_name: Identifier for the model/agent
            messages: List of messages or single message string
            temperature: Temperature for generation (note: may be limited by agent_framework)
            max_tokens: Maximum tokens for completion
            system_prompt: System instructions for the agent
            logging_extra: Additional logging context
            **kwargs: Additional arguments

        Returns:
            Tuple of (response_dict, cost)
        """
        logging_extra = logging_extra or {}

        try:
            # Create or get agent
            agent = self._get_or_create_agent(
                model_name=model_name,
                system_prompt=system_prompt,
            )

            # Convert messages to ChatMessage format
            chat_messages = self._convert_messages_to_chat_messages(messages)

            # Build generation kwargs — reasoning models (o-series, GPT-5)
            # don't support temperature.
            gen_kwargs: Dict[str, Any] = {}
            if not self.is_reasoning_model(model_name):
                gen_kwargs["temperature"] = temperature

            # Run the agent
            result = await agent.run(messages=chat_messages, **gen_kwargs)

            # Try to parse as JSON, otherwise return as text
            try:
                response_text = result.text
                # Strip markdown code fences if present
                if response_text.startswith("```json"):
                    response_text = response_text[7:]
                if response_text.startswith("```"):
                    response_text = response_text[3:]
                if response_text.endswith("```"):
                    response_text = response_text[:-3]
                response_content = json.loads(response_text.strip())
            except json.JSONDecodeError:
                response_content = {"response": result.text}

            return response_content, {
                "input_tokens": result.usage_details.input_token_count,
                "output_tokens": result.usage_details.output_token_count,
                "total_tokens": result.usage_details.total_token_count,
            }

        except custom_errors.MyCustomError as specific_error:
            raise specific_error
        except Exception as e:
            logger.error(f"Error in call_llm: {str(e)}", extra=logging_extra)
            raise custom_errors.LLMError(
                f"Error in {self.__class__.__name__}.call_llm: {str(e)}",
                e,
            ) from e

    async def with_structured_output(
        self,
        model_name: str,
        messages: Union[List[Dict[str, str]], List[ChatMessage], str],
        output_format: Type,
        temperature: float = 0.7,
        max_tokens: int = 800,
        system_prompt: str = "",
        logging_extra: Optional[Dict] = None,
    ) -> tuple:
        """
        Call the LLM with structured output using Pydantic model.

        Args:
            model_name: Identifier for the model/agent
            messages: List of messages or single message string
            output_format: Pydantic model class for structured output
            temperature: Temperature for generation
            max_tokens: Maximum tokens for completion
            system_prompt: System instructions for the agent
            logging_extra: Additional logging context

        Returns:
            Tuple of (structured_response, cost)
        """
        logging_extra = logging_extra or {}

        try:
            # Create agent with structured output instructions
            structured_prompt = system_prompt
            if output_format:
                # Add schema information to help guide structured output
                schema_info = ""
                if hasattr(output_format, "model_json_schema"):
                    schema_info = f"\n\nRespond in JSON format matching this schema:\n{json.dumps(output_format.model_json_schema(), indent=2)}"
                structured_prompt = f"{system_prompt}{schema_info}"

            agent = self._get_or_create_agent(
                model_name=f"{model_name}_structured",
                system_prompt=structured_prompt,
            )

            # Convert messages to ChatMessage format
            chat_messages = self._convert_messages_to_chat_messages(messages)

            # Build generation kwargs — reasoning models (o-series, GPT-5)
            # don't support temperature.
            gen_kwargs: Dict[str, Any] = {}
            if not self.is_reasoning_model(model_name):
                gen_kwargs["temperature"] = temperature

            # Run the agent
            result = await agent.run(messages=chat_messages, **gen_kwargs)

            # Parse and validate with Pydantic model
            try:
                response_text = result.text
                # Try to extract JSON from the response
                if response_text.startswith("```json"):
                    response_text = response_text[7:]
                if response_text.startswith("```"):
                    response_text = response_text[3:]
                if response_text.endswith("```"):
                    response_text = response_text[:-3]

                response_data = json.loads(response_text.strip())

                if output_format and hasattr(output_format, "model_validate"):
                    structured_response = output_format.model_validate(response_data)
                else:
                    structured_response = response_data

            except (json.JSONDecodeError, Exception) as parse_error:
                logger.warning(
                    f"Failed to parse structured output: {parse_error}. Returning raw text."
                )
                structured_response = {"response": result.text}

            return structured_response, {
                "input_tokens": result.usage_details.input_token_count,
                "output_tokens": result.usage_details.output_token_count,
                "total_tokens": result.usage_details.total_token_count,
            }

        except custom_errors.MyCustomError as specific_error:
            raise specific_error
        except Exception as e:
            logger.error(
                f"Error in with_structured_output: {str(e)}", extra=logging_extra
            )
            raise custom_errors.LLMError(
                f"Error in {self.__class__.__name__}.with_structured_output: {str(e)}",
                e,
            ) from e

    async def get_chat_completion(
        self,
        model_name: str,
        messages: Union[List[Dict[str, str]], List[ChatMessage], str],
        temperature: float = 0.7,
        max_tokens: int = 800,
        system_prompt: str = "",
        logging_extra: Optional[Dict] = None,
    ) -> tuple:
        """
        Asynchronously get chat completion from Azure OpenAI.

        Args:
            model_name: The key for the model/agent
            messages: List of messages for the chat completion
            temperature: Temperature for the chat completion
            max_tokens: Maximum tokens for the chat completion
            system_prompt: System instructions for the agent
            logging_extra: Additional logging context

        Returns:
            Tuple of (response, cost)
        """
        return await self.call_llm(
            model_name=model_name,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            system_prompt=system_prompt,
            logging_extra=logging_extra,
        )

    async def run_agent(
        self,
        agent_name: str,
        messages: Union[List[Dict[str, str]], List[ChatMessage], str],
        system_prompt: str = "",
        tools: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        """
        Run a ChatAgent with optional tools.

        Args:
            agent_name: Name for the agent
            messages: Messages to send to the agent
            system_prompt: System instructions
            tools: Optional list of tools/functions for the agent

        Returns:
            Dict with agent response
        """
        try:
            agent = self._get_or_create_agent(
                model_name=agent_name, system_prompt=system_prompt, tools=tools
            )

            chat_messages = self._convert_messages_to_chat_messages(messages)
            result = await agent.run(messages=chat_messages)

            return {
                "agent": agent_name,
                "response": result.text,
                "success": True,
                "usage": {
                    "input_tokens": result.usage_details.input_token_count,
                    "output_tokens": result.usage_details.output_token_count,
                    "total_tokens": result.usage_details.total_token_count,
                },
            }

        except Exception as e:
            logger.error(f"Error in run_agent: {str(e)}")
            return {
                "agent": agent_name,
                "response": f"Error: {str(e)}",
                "success": False,
                "error": str(e),
            }

    async def run_agent_stream(
        self,
        agent_name: str,
        messages: Union[List[Dict[str, str]], List[ChatMessage], str],
        system_prompt: str = "",
        tools: Optional[List[Any]] = None,
    ):
        """
        Run a ChatAgent with streaming response.

        Args:
            agent_name: Name for the agent
            messages: Messages to send to the agent
            system_prompt: System instructions
            tools: Optional list of tools/functions for the agent

        Yields:
            Streaming text updates from the agent
        """
        try:
            agent = self._get_or_create_agent(
                model_name=agent_name, system_prompt=system_prompt, tools=tools
            )

            chat_messages = self._convert_messages_to_chat_messages(messages)

            async for update in agent.run_stream(messages=chat_messages):
                yield update.text

        except Exception as e:
            logger.error(f"Error in run_agent_stream: {str(e)}")
            yield f"Error: {str(e)}"

    async def close(self):
        """Close all client sessions properly."""
        # agent_framework handles connection cleanup internally
        self.model_agents.clear()
        logger.info("AzureLLMClient closed")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    @classmethod
    async def warmup(cls):
        """Warm up the client to reduce first-call latency."""
        try:
            client = cls.get_client()
            warmup_agent = ChatAgent(
                chat_client=client,
                instructions="You are a test agent",
                agent_name="WarmupAgent",
            )
            await warmup_agent.run(messages=[ChatMessage(role="user", text="Hi")])
            logger.info("AzureLLMClient warmed up successfully")
        except Exception as e:
            logger.warning(f"AzureLLMClient warmup failed (non-critical): {e}")


if __name__ == "__main__":
    import asyncio

    async def main():

        config = ConfigLoader.load_config()
        # Example usage
        client = AzureLLMClient(config=config)

        # Test basic chat completion
        response, cost = await client.get_chat_completion(
            model_name="extraction_model",
            messages=[{"role": "user", "content": "Hello, how are you?"}],
            system_prompt="You are a helpful assistant.",
        )
        print(f"Response: {response}")

        await client.close()

    asyncio.run(main())
