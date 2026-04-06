"""
This module provides an interface for embedding text using OpenAI's Azure SDK.
It supports both remote and local models for embedding text.
The class `OpenAIEmbedding` is initialized with configuration details and can embed
single or batch text inputs. It also includes a test function to validate the local
model embedding functionality.
"""

import asyncio
import os
from typing import List

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AsyncAzureOpenAI, AzureOpenAI
from utils import custom_errors
from utils.load_config import ConfigLoader
from utils.setup_logging import logger


class OpenAIEmbedding:
    """
    This class provides an interface for embedding text using OpenAI's Azure SDK.
    """

    def __init__(
        self,
        config: dict,
    ):
        """Initialize the OpenAI embedding client using Azure SDK.
        param config: Configuration details containing API key and endpoint
        """
        self.__config = config.get("models", {}).get("embedding_model", {})
        self.model_name = "embedding_model"
        self.api_key = self.__config.get("apiKey", None) or os.getenv(
            "AZURE_OPENAI_API_KEY"
        )
        self.azure_endpoint = self.__config.get("endpoint", None) or os.getenv(
            "AZURE_OPENAI_ENDPOINT"
        )
        self.model = self.__config.get("deployment_name")
        self.api_version = self.__config.get("api_version")

        if not self.azure_endpoint:
            raise ValueError(
                "Azure endpoint must be provided or set as environment variable"
            )

        # Use DefaultAzureCredential if API key is not available
        if self.api_key:
            self.client = AsyncAzureOpenAI(
                azure_endpoint=self.azure_endpoint,
                api_key=self.api_key,
                api_version=self.api_version,
            )
            self.synchronous_client = AzureOpenAI(
                api_key=self.api_key,
                api_version=self.api_version,
                azure_endpoint=self.azure_endpoint,
            )
        else:
            # Use DefaultAzureCredential for authentication
            credential = DefaultAzureCredential()
            token_provider = get_bearer_token_provider(
                credential, "https://cognitiveservices.azure.com/.default"
            )
            self.client = AsyncAzureOpenAI(
                azure_endpoint=self.azure_endpoint,
                azure_ad_token_provider=token_provider,
                api_version=self.api_version,
            )
            self.synchronous_client = AzureOpenAI(
                azure_ad_token_provider=token_provider,
                api_version=self.api_version,
                azure_endpoint=self.azure_endpoint,
            )

    async def embed_text(self, text: str):
        """Embed a single text string.
        param text: The text to embed
        Returns: List of embedding values
        """
        try:
            response = await self.client.embeddings.create(input=text, model=self.model)
            return response.data[0].embedding
        except custom_errors.MyCustomError as specific_error:
            raise specific_error
        except Exception as e:
            raise custom_errors.OpenAIEmbeddingError(
                f"Error in {self.__class__.__name__}.embed_text: {str(e)}", e
            ) from e

    async def embed_batch(self, texts: List[str]):
        """Embed multiple text strings in batch.
        param texts: List of text strings to embed
        Returns: List of lists of embedding values
        """
        try:

            # Ensure the texts are not empty
            if not texts:
                return []

            # Split the texts into batches of 1000 to avoid exceeding the token limit
            batch_size = 1000
            batches = [
                texts[i : i + batch_size] for i in range(0, len(texts), batch_size)
            ]
            embeddings = []
            for batch in batches:
                response = await self.client.embeddings.create(
                    input=batch, model=self.model
                )
                embeddings.extend([item.embedding for item in response.data])

            return embeddings
        except custom_errors.MyCustomError as specific_error:
            raise specific_error
        except Exception as e:
            raise custom_errors.OpenAIEmbeddingError(
                f"Error in {self.__class__.__name__}.embed_batch: {str(e)}", e
            ) from e

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embedding function for semantic cache
        Args:
            texts: List of text to embed.
        Returns:
            List of embeddings.
        """
        try:
            embeddings = await self.client.embeddings.create(
                input=texts, model=self.model
            )
            return [item.embedding for item in embeddings.data]
        except custom_errors.MyCustomError as specific_error:
            raise specific_error
        except Exception as e:
            raise custom_errors.OpenAIEmbeddingError(
                f"Error in {self.__class__.__name__}.aembed_documents: {str(e)}", e
            ) from e

    async def aembed_query(self, text: str) -> list[float]:
        """Asynchronous Embed query text for semantic cache.
        Args:
            text: Text to embed.
        Returns:
            Embedding.
        """
        try:
            embedding = await self.client.embeddings.create(
                input=text, model=self.model
            )
            return embedding.data[0].embedding
        except custom_errors.MyCustomError as specific_error:
            raise specific_error
        except Exception as e:
            raise custom_errors.OpenAIEmbeddingError(
                f"Error in {self.__class__.__name__}.aembed_query: {str(e)}", e
            ) from e

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed search docs for semantic cache.
        Args:
            texts: List of text to embed.
        Returns:
            List of embeddings.
        """
        try:
            embeddings = self.synchronous_client.embeddings.create(
                input=texts, model=self.model
            )
            ans = [item.embedding for item in embeddings.data]
            return ans
            # embeddings, _ = asyncio.to_thread(self.embed_batch, texts)
            # return embeddings
        except custom_errors.MyCustomError as specific_error:
            raise specific_error
        except Exception as e:
            raise custom_errors.OpenAIEmbeddingError(
                f"Error in {self.__class__.__name__}.embed_documents: {str(e)}", e
            ) from e

    def embed_query(self, text: str) -> list[float]:
        """Embed query text for semantic cache.
        Args:
            text: Text to embed.
        Returns:
            Embedding.
        """
        try:
            embedding = self.synchronous_client.embeddings.create(
                input=text, model=self.model
            )
            ans = embedding.data[0].embedding
            return ans
            # embedding = asyncio.to_thread(self.embed_text, text)
            # return embedding
        except custom_errors.MyCustomError as specific_error:
            raise specific_error
        except Exception as e:
            raise custom_errors.OpenAIEmbeddingError(
                f"Error in {self.__class__.__name__}.embed_query: {str(e)}", e
            ) from e

    async def close(self):
        """Close the client connections to free up resources.
        This should be called when the embedding client is no longer needed.
        """
        try:
            await self.client.close()
            self.synchronous_client.close()
        except custom_errors.MyCustomError as specific_error:
            raise specific_error
        except Exception as e:
            raise custom_errors.OpenAIEmbeddingError(
                f"Error in {self.__class__.__name__}.close: {str(e)}", e
            ) from e


if __name__ == "__main__":
    import json

    async def test_local_embedding():
        """
        Test the local embedding functionality of the OpenAIEmbedding class.
        """
        # Configuration for local model
        config = ConfigLoader.load_config()

        # Initialize the embedding class with local model flag set to True
        embedding_model = OpenAIEmbedding(config)

        # Example sentences to encode
        sentences = [
            "This is the first test sentence.",
            "Here's another sentence to embed.",
            "The local model will process these sentences efficiently.",
        ]

        # Get embeddings for a single sentence
        single_embedding = await embedding_model.embed_text(sentences[0])
        logger.info(f"Single embedding dimension: {len(single_embedding)}")

        # Get embeddings for all sentences
        batch_embeddings = await embedding_model.embed_batch(sentences)
        logger.info(f"Number of batch embeddings: {len(batch_embeddings)}")
        logger.info(f"Dimension of each embedding: {len(batch_embeddings[0])}")

        # Return the embeddings for further use
        _ans = await embedding_model.aembed_documents(sentences)
        _ans2 = await embedding_model.aembed_query(sentences[0])

        _answer = embedding_model.embed_documents(sentences)
        _answer2 = embedding_model.embed_query(sentences[0])

        return single_embedding, batch_embeddings

    # To run this async function, you would need to use:
    single_emb, batch_embs = asyncio.run(test_local_embedding())
