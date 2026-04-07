import asyncio
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple


class TraceIngestionError(Exception):
    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = error_code


class TraceService:
    async def acquire_trace(
        self,
        trace_source,   # FileTraceSource | LangfuseTraceSource
        dest_dir: Path,
    ) -> Tuple[Path, int]:
        """
        Acquire a trace from either a local file or Langfuse, save to
        dest_dir/raw_trace.json, validate it, and return (path, observation_count).

        Raises:
            TraceIngestionError: TRACE_NOT_FOUND, TRACE_PARSE_ERROR, LANGFUSE_FETCH_ERROR
        """
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "raw_trace.json"

        if trace_source.type == "file":
            await self._fetch_from_file(trace_source.file_path, dest)
        else:
            await self._fetch_from_langfuse(trace_source, dest)

        data = await asyncio.to_thread(_load_and_validate, str(dest))
        return dest, len(data)

    # ── File source ───────────────────────────────────────────────────────

    async def _fetch_from_file(self, file_path: str, dest: Path) -> None:
        try:
            await asyncio.to_thread(shutil.copy2, file_path, str(dest))
        except FileNotFoundError:
            raise TraceIngestionError(
                "TRACE_NOT_FOUND", f"Trace file not found: {file_path}"
            )
        except OSError as exc:
            raise TraceIngestionError(
                "TRACE_NOT_FOUND", f"Cannot read trace file: {exc}"
            )

    # ── Langfuse source ───────────────────────────────────────────────────

    async def _fetch_from_langfuse(self, source, dest: Path) -> None:
        """Fetch observations from Langfuse and write to dest as a JSON array."""
        try:
            observations = await asyncio.to_thread(
                _fetch_langfuse_observations,
                base_url=source.base_url,
                public_key=source.public_key,
                secret_key=source.secret_key,
                from_timestamp=source.from_timestamp,
                page_size=source.page_size,
                max_pages=source.max_pages,
                include_observations=source.include_observations,
            )
        except TraceIngestionError:
            raise
        except Exception as exc:
            raise TraceIngestionError(
                "LANGFUSE_FETCH_ERROR", f"Langfuse fetch failed: {exc}"
            )

        await asyncio.to_thread(_write_json, observations, str(dest))


# ── Langfuse fetch (runs in thread — Langfuse SDK is synchronous) ─────────

def _fetch_langfuse_observations(
    base_url: str,
    public_key: str,
    secret_key: str,
    from_timestamp: str,
    page_size: int,
    max_pages: int,
    include_observations: bool,
) -> List[Dict[str, Any]]:
    try:
        from langfuse import Langfuse
    except ImportError:
        raise TraceIngestionError(
            "LANGFUSE_FETCH_ERROR",
            "langfuse package is not installed (pip install langfuse)",
        )

    from_utc = _parse_iso_to_utc(from_timestamp)

    client = Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        host=base_url,
    )

    raw_traces = _list_traces(client, from_utc, page_size, max_pages)

    if not raw_traces:
        raise TraceIngestionError(
            "TRACE_NOT_FOUND",
            f"No traces found in Langfuse after {from_timestamp}",
        )

    all_observations: List[Dict[str, Any]] = []
    for trace in raw_traces:
        if include_observations:
            obs_resp = client.api.legacy.observations_v1.get_many(
                trace_id=trace.id, limit=500
            )
            raw_obs = [o.model_dump() for o in obs_resp.data]
        else:
            raw_obs = []
        all_observations.extend(_format_observations(raw_obs))

    return all_observations


def _list_traces(client, from_utc: datetime, page_size: int, max_pages: int):
    results = []
    for page in range(1, max_pages + 1):
        resp = client.api.trace.list(
            from_timestamp=from_utc,
            page=page,
            limit=page_size,
        )
        results.extend(resp.data)
        if not resp.data or page >= resp.meta.total_pages:
            break
    return results


def _format_observations(raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalise Langfuse observation dicts to the pipeline's expected format."""
    depth_map = _compute_depths(raw)
    out = []
    for o in raw:
        out.append({
            "id": o.get("id"),
            "type": o.get("type"),
            "name": o.get("name"),
            "startTime": _fmt_ts(o.get("start_time")),
            "endTime": _fmt_ts(o.get("end_time")),
            "depth": depth_map.get(o.get("id", ""), 0),
            "input": _to_json_str(o.get("input")),
            "output": _to_json_str(o.get("output")),
            "metadata": _to_json_str(o.get("metadata")),
        })
    out.sort(key=lambda x: (x["depth"], x["startTime"] or ""))
    return out


def _compute_depths(observations: List[Dict[str, Any]]) -> Dict[str, int]:
    parent_map = {o["id"]: o.get("parent_observation_id") for o in observations}
    cache: Dict[str, int] = {}

    def depth(obs_id: str) -> int:
        if obs_id in cache:
            return cache[obs_id]
        parent = parent_map.get(obs_id)
        cache[obs_id] = 0 if not parent or parent not in parent_map else depth(parent) + 1
        return cache[obs_id]

    return {oid: depth(oid) for oid in parent_map}


def _fmt_ts(dt: Any) -> str | None:
    if dt is None:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except ValueError:
            return dt
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _to_json_str(val: Any) -> str | None:
    if val is None:
        return None
    if isinstance(val, str):
        return val
    return json.dumps(val, ensure_ascii=False)


def _parse_iso_to_utc(ts: str) -> datetime:
    """Parse ISO-8601 string to UTC datetime."""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError as exc:
        raise TraceIngestionError(
            "LANGFUSE_FETCH_ERROR",
            f"Invalid from_timestamp '{ts}': {exc}",
        )


# ── Shared helpers ────────────────────────────────────────────────────────

def _load_and_validate(path: str) -> List[Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise TraceIngestionError(
            "TRACE_PARSE_ERROR", "Trace file must be a JSON array"
        )
    if not data:
        raise TraceIngestionError("TRACE_PARSE_ERROR", "Trace file is empty")
    if not isinstance(data[0], dict) or "id" not in data[0]:
        raise TraceIngestionError(
            "TRACE_PARSE_ERROR",
            "Trace entries must be objects with an 'id' field",
        )
    return data


def _write_json(data: Any, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
