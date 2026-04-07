from typing import Any, Dict, List

from run_bucketing_and_extraction_pipeline import run_pipeline


class PipelineService:
    async def execute_pipeline(
        self,
        trace_file: str,
        output_dir: str,
        batch_size: int,
        store_to_mongodb: bool,
        config: dict,
    ) -> List[Dict[str, Any]]:
        """Thin adapter over run_pipeline(). Any exception propagates to the caller."""
        return await run_pipeline(
            trace_file=trace_file,
            output_dir=output_dir,
            batch_size=batch_size,
            store_to_mongodb=store_to_mongodb,
            config=config,
        )
