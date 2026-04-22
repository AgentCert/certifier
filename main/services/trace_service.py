import asyncio
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple


class TraceIngestionError(Exception):
    """Raised when trace acquisition fails with a structured *error_code*.

    Callers should inspect ``exc.error_code`` to build structured error responses
    rather than parsing the message string.

    Known codes: ``TRACE_NOT_FOUND``, ``TRACE_PARSE_ERROR``, ``LANGFUSE_FETCH_ERROR``.
    """

    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = error_code


class TraceService:
    """Acquires raw Langfuse trace data from either a local file or the Langfuse API."""

    async def acquire_trace(
        self,
        trace_source,   # FileTraceSource | LangfuseTraceSource
        dest_dir: Path,
    ) -> Tuple[Path, int]:
        """Copy or fetch a trace to ``dest_dir/raw_trace.json`` and validate its structure.

        Args:
            trace_source: A ``FileTraceSource`` or ``LangfuseTraceSource`` discriminated union.
            dest_dir:     Directory where ``raw_trace.json`` will be written.

        Returns:
            ``(path_to_raw_trace, observation_count)``

        Raises:
            TraceIngestionError: With codes TRACE_NOT_FOUND, TRACE_PARSE_ERROR,
                                 or LANGFUSE_FETCH_ERROR depending on failure mode.
        """
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "raw_trace.json"

        if trace_source.type == "file":
            await self._fetch_from_file(trace_source.file_path, dest)
        else:
            await self._fetch_from_langfuse(trace_source, dest)

        # Validate in a thread to avoid blocking the event loop on I/O + JSON parse
        data = await asyncio.to_thread(_load_and_validate, str(dest))
        return dest, len(data)

    # ── File source ───────────────────────────────────────────────────────────

    async def _fetch_from_file(self, file_path: str, dest: Path) -> None:
        """Copy a local trace file to the workspace directory."""
        try:
            # shutil.copy2 preserves metadata; run in a thread (blocking I/O)
            await asyncio.to_thread(shutil.copy2, file_path, str(dest))
        except FileNotFoundError:
            raise TraceIngestionError(
                "TRACE_NOT_FOUND", f"Trace file not found: {file_path}"
            )
        except OSError as exc:
            raise TraceIngestionError(
                "TRACE_NOT_FOUND", f"Cannot read trace file: {exc}"
            )

    # ── Langfuse source ───────────────────────────────────────────────────────

    async def _fetch_from_langfuse(self, source, dest: Path) -> None:
        """Fetch observations from the Langfuse API and write them to *dest* as a JSON array.

        The Langfuse SDK is synchronous, so the entire fetch is offloaded to a
        thread via ``asyncio.to_thread``.
        """
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


# ── Langfuse fetch (runs in thread — Langfuse SDK is synchronous) ─────────────

def _fetch_langfuse_observations(
    base_url: str,
    public_key: str,
    secret_key: str,
    from_timestamp: str,
    page_size: int,
    max_pages: int,
    include_observations: bool,
) -> List[Dict[str, Any]]:
    """Fetch all Langfuse traces + their observations from *from_timestamp* onward.

    Returns observations normalised into the pipeline's expected flat-list format
    (see ``_format_observations``).
    """
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
            # Fetch up to 500 observations per trace (Langfuse API max per request)
            obs_resp = client.api.legacy.observations_v1.get_many(
                trace_id=trace.id, limit=500
            )
            raw_obs = [o.model_dump() for o in obs_resp.data]
        else:
            raw_obs = []
        all_observations.extend(_format_observations(raw_obs))

    return all_observations


def _list_traces(client, from_utc: datetime, page_size: int, max_pages: int):
    """Paginate through the Langfuse trace list API up to *max_pages* pages."""
    results = []
    for page in range(1, max_pages + 1):
        resp = client.api.trace.list(
            from_timestamp=from_utc,
            page=page,
            limit=page_size,
        )
        results.extend(resp.data)
        # Stop if the API returned an empty page or we've consumed all pages
        if not resp.data or page >= resp.meta.total_pages:
            break
    return results


def _format_observations(raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalise Langfuse observation dicts into the pipeline's expected format.

    The pipeline expects observations sorted by (depth, startTime) so that
    parent spans always precede their children in the event list.
    """
    # Pre-compute tree depth so we can sort parent-before-child
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
    # Primary sort: depth (root first); secondary: startTime for stable ordering within a level
    out.sort(key=lambda x: (x["depth"], x["startTime"] or ""))
    return out


def _compute_depths(observations: List[Dict[str, Any]]) -> Dict[str, int]:
    """Compute the tree depth of each observation by walking up the parent chain.

    Uses memoisation (``cache``) so each node is computed at most once even in
    deeply nested traces.  Observations whose parent is not in the set are
    treated as roots (depth 0).
    """
    # Build a parent lookup: obs_id → parent_observation_id (or None for roots)
    parent_map = {o["id"]: o.get("parent_observation_id") for o in observations}
    cache: Dict[str, int] = {}

    def depth(obs_id: str) -> int:
        if obs_id in cache:
            return cache[obs_id]
        parent = parent_map.get(obs_id)
        # A missing or unknown parent means this node is effectively a root
        cache[obs_id] = 0 if not parent or parent not in parent_map else depth(parent) + 1
        return cache[obs_id]

    return {oid: depth(oid) for oid in parent_map}


def _fmt_ts(dt: Any) -> str | None:
    """Normalise a timestamp to the pipeline's UTC millisecond string format.

    Accepts ``datetime`` objects, ISO-8601 strings (with or without ``Z``),
    and ``None``.  Always emits ``YYYY-MM-DDTHH:MM:SS.mmmZ``.
    """
    if dt is None:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except ValueError:
            return dt  # Return unparseable strings unchanged rather than dropping them
    dt = dt.astimezone(timezone.utc)
    # Format to millisecond precision with explicit 'Z' suffix
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _to_json_str(val: Any) -> str | None:
    """Serialise *val* to a JSON string if it is not already a string or None."""
    if val is None:
        return None
    if isinstance(val, str):
        return val
    return json.dumps(val, ensure_ascii=False)


def _parse_iso_to_utc(ts: str) -> datetime:
    """Parse an ISO-8601 string to a timezone-aware UTC ``datetime``.

    Raises:
        TraceIngestionError: If the string cannot be parsed.
    """
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            # Treat naive datetimes as UTC
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError as exc:
        raise TraceIngestionError(
            "LANGFUSE_FETCH_ERROR",
            f"Invalid from_timestamp '{ts}': {exc}",
        )


# ── Shared helpers ────────────────────────────────────────────────────────────

def _load_and_validate(path: str) -> List[Any]:
    """Load a JSON file and validate that it is a non-empty list of observation dicts.

    Raises:
        TraceIngestionError: With TRACE_PARSE_ERROR for structural violations.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise TraceIngestionError(
            "TRACE_PARSE_ERROR", "Trace file must be a JSON array"
        )
    if not data:
        raise TraceIngestionError("TRACE_PARSE_ERROR", "Trace file is empty")
    # Spot-check the first element to catch obviously wrong files early
    if not isinstance(data[0], dict) or "id" not in data[0]:
        raise TraceIngestionError(
            "TRACE_PARSE_ERROR",
            "Trace entries must be objects with an 'id' field",
        )
    return data


def _write_json(data: Any, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
