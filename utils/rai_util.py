import os

from azure.ai.contentsafety.aio import ContentSafetyClient
from azure.ai.contentsafety.models import AnalyzeTextOptions, TextCategory
from azure.core.credentials import AzureKeyCredential
from azure.identity import DefaultAzureCredential, ManagedIdentityCredential

from utils.setup_logging import logger


class RAIContentSafety:
    """Wrapper for Azure Content Safety client."""

    def __init__(self, rai_config: dict = None):
        endpoint = os.getenv("AZURE_CONTENT_SAFETY_ENDPOINT")
        api_key = os.getenv("AZURE_CONTENT_SAFETY_API_KEY")

        if not endpoint:
            raise ValueError(
                "Content Safety endpoint not set in environment variables."
            )

        # # Use API key credential or managed identity as needed
        credential = AzureKeyCredential(api_key)
        # credential = DefaultAzureCredential()
        self.client = ContentSafetyClient(endpoint=endpoint, credential=credential)
        self.severity_threshold = (
            rai_config.get("rai_severity_threshold", {}) if rai_config else {}
        )
        self.overall_severity_threshold = (
            rai_config.get("rai_overall_severity_threshold", 1) if rai_config else 1
        )

    async def analyze_text(self, text: str):
        """Analyze text for content safety."""
        options = AnalyzeTextOptions(text=text)
        response = await self.client.analyze_text(options)

        rai_result = {}
        for item in response.categories_analysis:
            if item.severity >= self.severity_threshold.get(
                item.category, self.overall_severity_threshold
            ):
                logger.warning(
                    f"Content Safety Alert - Category: {item.category}, Severity: {item.severity}"
                )
                rai_result[item.category] = item.severity

        return rai_result

    async def close(self):
        """Close the client session."""
        await self.client.close()


if __name__ == "__main__":
    import asyncio
    import json

    async def main():
        with open("configs/config.json", "r") as f:
            config = json.load(f)
        rai_cs = RAIContentSafety(config)
        test_text = "How is the weather today?"
        result = await rai_cs.analyze_text(test_text)
        logger.info(result)

        await rai_cs.close()

    asyncio.run(main())
