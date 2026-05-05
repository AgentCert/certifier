# cert-reporter Architecture

Comprehensive reference covering system design, data flow, component interactions, extension points, and design decisions.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Entry Points](#2-entry-points)
3. [GraphState — Central Data Carrier](#3-graphstate--central-data-carrier)
4. [Static Pipeline](#4-static-pipeline)
5. [Agentic Pipeline](#5-agentic-pipeline)
6. [Node Reference](#6-node-reference)
7. [Schema Normalisation Layer](#7-schema-normalisation-layer)
8. [Template Architecture](#8-template-architecture)
9. [Custom Jinja2 Filters](#9-custom-jinja2-filters)
10. [Fault Group Post-Processor](#10-fault-group-post-processor)
11. [PDF Print Layout](#11-pdf-print-layout)
12. [API Server](#12-api-server)
13. [Workspace Layout](#13-workspace-layout)
14. [Extension Points](#14-extension-points)
15. [Resilience Patterns](#15-resilience-patterns)
16. [Design Decisions and Trade-offs](#16-design-decisions-and-trade-offs)
17. [Dependency Map](#17-dependency-map)

---

## 1. System Overview

```
  ┌──────────────────────────────────────────────────────────────────────┐
  │  POST /api/v1/aggregation-certification  (main certifier API)        │
  │                                                                       │
  │    Phase 2+3 pipeline ──▶ cert-builder/certification.json            │
  │                                   │                                   │
  │                                   ▼                                   │
  │              cert-reporter pipeline (this module)                     │
  │                   (called via generate_cert_report_documents)         │
  │                                   │                                   │
  │                                   ▼                                   │
  │           workspace/{agent_id}/{experiment_id}/certification/         │
  │                   ├── <doc_id>.html                                   │
  │                   └── <doc_id>.pdf                                    │
  └──────────────────────────────────────────────────────────────────────┘

  ┌──────────┐      ┌────────────────────────────────────────────────────┐
  │   CLI    │─────▶│  main.py `generate` subcmd                         │
  │ generate │      │  run_pipeline() / run_agentic_pipeline()           │
  └──────────┘      └────────────────────────────────────────────────────┘

  ┌──────────┐      ┌────────────────────────────────────────────────────┐
  │  HTTP    │─────▶│  main.py `serve` subcmd → api/app.py               │
  │  Client  │      │  GET /api/certification/pdf  → serve .pdf from workspace │
  └──────────┘      │  GET /api/certification/html → serve .html from workspace│
                    └────────────────────────────────────────────────────┘
```

Report **generation** is driven by `POST /api/v1/aggregation-certification` in the main certifier API, which calls `generate_cert_report_documents()` (in `main/services/pipeline_service.py`) after Phase 3 writes `certification.json`.

The two cert-reporter API endpoints only **serve** already-generated files from the workspace — they do not run the pipeline.

The CLI `generate` subcommand is a standalone path that calls `run_pipeline()` / `run_agentic_pipeline()` directly, bypassing the API layer entirely.

---

## 2. Entry Points

### `main.py` — Unified CLI + Server

`main.py` uses `argparse` subparsers to expose two subcommands:

#### `serve` (default when no subcommand is given)

```bash
python main.py                    # starts server on 0.0.0.0:8000
python main.py serve --port 8080
python main.py serve --reload
```

Calls `uvicorn.run(create_app(), host=..., port=..., reload=...)`.

#### `generate`

Parses `--agent-id` and `--experiment-id`, resolves paths from the workspace, and dispatches to the pipeline:

| `--mode` | Function called |
|---|---|
| `static` (default) | `pipeline.graph.run_pipeline()` |
| `agentic` | `pipeline.agentic_graph.run_agentic_pipeline()` |

**Arguments:**

```
-a / --agent-id TEXT          Agent ID (required)
-e / --experiment-id TEXT     Experiment ID (required)
-f / --format TEXT            html,pdf            (default: html,pdf)
     --mode TEXT              static | agentic    (default: static)
     --enrich-llm             Enable LLM enrichment (static mode)
     --model TEXT             LLM model name      (default: gpt-4.1-mini)
     --provider TEXT          openai | anthropic  (default: openai)
     --temperature FLOAT      LLM temperature     (default: 0.4)
-v / --verbose                DEBUG logging
```

**Workspace path resolution:**

```
input_path = workspace_dir / agent_id / experiment_id / "cert-builder" / "certification.json"
output_dir = workspace_dir / agent_id / experiment_id / "certification"
```

**Exit codes:** `0` on success (at least one output file written), `1` on failure or zero outputs.

---

## 3. GraphState — Central Data Carrier

`pipeline/parameters.py` defines the typed state dict that flows between every pipeline node.

```python
class GraphState(TypedDict):
    # ── Inputs (set at pipeline entry) ──────────────────────────────
    input_path:    str          # absolute path to the source JSON file
    output_dir:    str          # where to write output files
    formats:       list[str]    # ["html"] | ["pdf"] | ["html","pdf"]
    enrich_llm:    bool         # whether to run LLM enrichment
    llm_config:    LLMConfig    # provider, model, temperature, max_tokens
    verbose:       bool

    # ── Set by preprocess_node ────────────────────────────────────────
    raw_doc:           dict
    meta:              dict
    header:            dict
    sections:          list[dict]
    footer:            str
    charts_to_render:  list[dict]
    chart_results:     dict[str, ChartResult]
    enriched_sections: dict[str, dict]
    html_path:         str
    pdf_path:          str
    token_usage:       TokenUsage
    errors:            list[str]
```

**Key models:**

```python
class ChartResult(BaseModel):
    chart_id:   str
    chart_type: str
    title:      str
    svg:        str = ""
    alt_text:   str = ""
    width_px:   int = 600
    height_px:  int = 400
    error:      Optional[str] = None

class LLMConfig(BaseModel):
    model:       str   = "gpt-4.1-mini"
    temperature: float = 0.4
    max_tokens:  int   = 4096
    provider:    str   = "openai"

class TokenUsage(BaseModel):
    input_tokens:  int = 0
    output_tokens: int = 0
    def add(self, inp: int, out: int): ...
    @property total -> int
```

**Design principle:** Every node receives the full state, modifies only its own output keys, and returns `{**state, key: new_value}`. Nodes never mutate the input state dict.

---

## 4. Static Pipeline

Defined in `pipeline/graph.py`.

```
START
  │
  ▼
preprocess_node       Load JSON, extract chart blocks, initialise state
  │
  ▼
charts_node           Render chart blocks → SVGs
  │
  ├─[enrich_llm=True]──▶ llm_enrich_node ──▶ html_renderer_node
  │                                                   │
  └─[enrich_llm=False]──────────────────────▶ html_renderer_node
                                                       │
                                                       ▼
                                              pdf_renderer_node
                                                       │
                                                      END
```

`run_pipeline(input_path, output_dir, formats, enrich_llm, model, provider, temperature, verbose, schema_class=None) -> dict`

---

## 5. Agentic Pipeline

Defined in `pipeline/agentic_graph.py`. Uses an extended `AgenticState` TypedDict:

```python
class AgenticState(GraphState):
    domain_profile:    DomainProfile
    current_section:   dict
    agentic_sections:  Annotated[list[dict], operator.add]
```

```
START
  │
  ▼
preprocess_node → charts_node → inspect_node
  │
  ▼
dispatch_sections_node  [Send() fan-out per section]
  ├──▶ enrich_section_node (section 0)
  ├──▶ enrich_section_node (section 1)
  ├──▶ enrich_section_node (section N)
  │
  ▼
assemble_node → html_renderer_node → pdf_renderer_node
  │
 END
```

**Zero data loss contract:** `enrich_section_node` only writes `section["intro"]`. All content blocks pass through unchanged.

---

## 6. Node Reference

### `preprocess_node` (`pipeline/reader.py`)

1. Reads and JSON-parses `state["input_path"]`.
2. Passes raw dict through `normalise_document()` (see §7).
3. Calls `_ensure_dicts()`: converts any non-dict section items to plain dicts.
4. Calls `_extract_chart_blocks(sections)`: assigns `_chart_id` to each chart block.
5. Populates all state keys; initialises downstream keys to empty values.

---

### `charts_node` (`pipeline/charts.py`)

**Rendering backend (priority order):**
1. `vl_convert` — fastest, pure C
2. `altair` — fallback
3. Placeholder SVG — if both fail

**Chart builders:**

| `chart_type` | Key input fields | SVG size |
|---|---|---|
| `radar` | `dimensions[{dimension, value}]` (0–1) | 420×350 |
| `grouped_bar` | `categories[]`, `series[{name, values[]}]`, `reference_lines[]` | 500×320 |
| `stacked_bar` | `categories[]`, `series[{name, values[]}]`, `y_axis` | 500×320 |
| `heatmap` | `x_labels[]`, `y_labels[]`, `values[][]`, `display_values[][]` | 520×300 |

---

### `llm_enrich_node` (`pipeline/llm_nodes.py`)

Skipped when `state["enrich_llm"]` is `False`.

| Content type | Field enriched | Minimum length |
|---|---|---|
| Every section | `intro` | — |
| `type:"text"` | `body` | 50 chars |
| `type:"assessment"` | `body` | 50 chars |

All calls via `asyncio.gather`; originals preserved on failure.

---

### `html_renderer_node` (`pipeline/html_renderer.py`)

Merges enriched sections, groups fault blocks, renders Jinja2 template with inlined CSS and SVG charts. Writes `{output_dir}/{doc_id}.html`.

**`_make_doc_id(state)` priority chain:**
1. `meta["certification_run_id"]` (slugified)
2. `meta["agent_id"]` + `meta["certification_date"]`
3. `"cert-report"` (fallback)

---

### `pdf_renderer_node` (`pipeline/pdf_renderer.py`)

Skipped when `"pdf"` not in `state["formats"]`.

Opens `file://{html_path}` in Playwright headless Chromium, expands all `<details>` elements, forces layout reflow, then calls `page.pdf(format="A4", margin=15mm)`. Runs in a dedicated thread to avoid event-loop conflicts with FastAPI.

---

### `inspect_node` / `enrich_section_node` — Agentic only

`inspect_node`: rule-based domain detection → `DomainProfile`.
`enrich_section_node`: LLM rewrites `section["intro"]` using a compact data summary as context.

---

## 7. Schema Normalisation Layer

`pipeline/schema.py` is a thin adapter. The pipeline never imports schema classes directly — they are injected via `GraphState["schema_class"]`.

```python
def normalise_document(raw: dict, schema_class=None) -> dict:
    if not _is_framework_format(raw):
        return raw
    if schema_class is None:
        return raw
    try:
        doc = schema_class.model_validate(raw)
        return doc.model_dump(mode="python")
    except Exception as exc:
        log.warning("Schema validation failed (%s), falling back to raw dict", exc)
        return raw
```

Detection: `_is_framework_format(raw)` checks for `"meta"` and `"sections"` top-level keys.

---

## 8. Template Architecture

All templates live in `templates/`. Jinja2 is configured with `autoescape=True`, `trim_blocks=True`, `lstrip_blocks=True`.

```
base.html
  ├── cover.html
  └── sections/section.html
        └── blocks/
              ├── assessment.html
              ├── card.html
              ├── chart.html
              ├── fault_group.html
              ├── findings.html
              ├── heading.html
              ├── table.html
              └── text.html
```

Block type dispatch in `section.html` — unknown types fall back to `<pre>` JSON dump (no data is silently dropped).

---

## 9. Custom Jinja2 Filters

| Filter | Output |
|---|---|
| `score_class` | `"excellent"` ≥0.90 / `"good"` ≥0.75 / `"adequate"` ≥0.60 / `"poor"` |
| `cert_class` | `"cert-gold"` / `"cert-silver"` / `"cert-bronze"` / `"cert-none"` |
| `fmt_num` | Integers: no decimal. Floats: 2dp. Large: comma-separated. `None` → `"—"` |
| `status_class` | `"status-pass"` / `"status-fail"` / `"status-warn"` |
| `severity_class` | `"finding-concern"` / `"finding-good"` / `"finding-note"` |
| `tag_class` | `"tag-excellent"` / `"tag-good"` / `"tag-warn"` / `"tag-bad"` / `""` |
| `replace_underscore` | `"fault_type"` → `"Fault Type"` |
| `md` | Markdown → safe HTML (bold, italic, code, paragraph breaks) |

---

## 10. Fault Group Post-Processor

`_group_fault_blocks(content)` in `html_renderer.py` merges `heading` + consecutive `assessment` blocks into a synthetic `fault_group` block at render time. The source JSON is unchanged.

**CRITICAL:** The synthetic block uses key `"assessments"` (not `"items"`). `block.items` in Jinja2 resolves to `dict.items()`, causing a template error.

---

## 11. PDF Print Layout

All print rules are in the `@media print` section of `static/report.css` (inlined into HTML).

Key rules:
- `details { break-inside: auto !important; }` — prevents blank page gaps
- `.fault-card { break-inside: auto; }` — allows tall cards to split
- `.chart-card svg { width: 100% !important; height: auto !important; }` — prevents SVG overflow
- `print_background: True` in Playwright — required for colours and gradients

---

## 12. API Server

### Application factory (`api/app.py`)

`create_app() -> FastAPI`:
1. Loads `.env` from cert_reporter root.
2. Creates FastAPI instance.
3. Adds `CORSMiddleware` with `allow_origins=["*"]`.
4. Registers API router at prefix `/api`.
5. Registers `GET /` to serve `ui/index.html`.

### Routes (`api/routes.py`)

#### Workspace resolution

```python
_CERTIFIER_ROOT = Path(__file__).resolve().parent.parent.parent   # cert_reporter/api/ → certifier/

_ws_env = os.getenv("WORKSPACE_DIR")
_WORKSPACE_DIR = (
    Path(_ws_env) if (_ws_env and Path(_ws_env).is_absolute())
    else _CERTIFIER_ROOT / (_ws_env or "workspace")
)
```

`WORKSPACE_DIR` relative values (e.g. `"workspace"`) are always joined onto `_CERTIFIER_ROOT`, preventing incorrect resolution from the cert_reporter working directory.

#### Endpoint summary

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/certification/pdf`  | Serve the latest `.pdf` from `workspace/{agent_id}/{experiment_id}/certification/` |
| `GET` | `/api/certification/html` | Serve the latest `.html` from `workspace/{agent_id}/{experiment_id}/certification/` |

Both endpoints return a **direct binary file download** (`FileResponse`). They do **not** run the report pipeline.

#### `GET /api/certification/pdf` and `GET /api/certification/html` request lifecycle

```
GET /api/certification/{fmt}?agent_id={agent_id}&experiment_id={experiment_id}
  │
  ├─ cert_dir = workspace/{agent_id}/{experiment_id}/certification/
  ├─ 404 "No PDF found" / "No HTML found" if directory does not exist or contains no .{fmt} files
  ├─ Pick most-recently-modified .{fmt} file
  └─ Return FileResponse (application/pdf or text/html)
```

#### File discovery

```python
def _find_latest(agent_id, experiment_id, ext) -> Path | None:
    cert_dir = _WORKSPACE_DIR / agent_id / experiment_id / "certification"
    files = sorted(cert_dir.glob(f"*.{ext}"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None
```

### API models (`api/models.py`)

No request body models — both endpoints use query parameters (`agent_id`, `experiment_id`) declared via FastAPI `Query()`.

---

## 13. Workspace Layout

cert-reporter is deployed alongside the AgentCert certifier pipeline and shares its workspace:

```
certifier/                                ← _CERTIFIER_ROOT
└── workspace/                            ← _WORKSPACE_DIR
    └── {agent_id}/
        └── {experiment_id}/
            ├── fault-bucketing/          ← Phase 0+1 output (bucketing-extraction)
            │   └── {run_id}/
            │       ├── traces/
            │       ├── fault_buckets/
            │       └── metrics/
            ├── aggregation/
            │   └── aggregation.json      ← Phase 2 output (aggregation)
            ├── cert-builder/
            │   └── certification.json   ← Phase 3 output (certification) — READ by cert-reporter
            └── certification/
                ├── <doc_id>.html        ← cert-reporter HTML output
                └── <doc_id>.pdf         ← cert-reporter PDF output
```

---

## 14. Extension Points

### Adding a new block type

1. Create `templates/blocks/{newtype}.html`.
2. Add dispatch in `templates/sections/section.html`.
3. Document in `SCHEMA.md`.

### Adding a new chart type

1. Add `_build_{newtype}(block) -> dict` to `pipeline/charts.py`.
2. Register in `_BUILDERS` dict.

### Adding a new LLM provider

1. Add `elif provider == "newprovider":` branch in `pipeline/llm_nodes.py`.
2. Same in `pipeline/agents/section_writer.py`.
3. Add LangChain package to `requirements.txt`.

---

## 15. Resilience Patterns

### Python layer

| Pattern | Protects against |
|---|---|
| `doc.get("key") or default` | Missing top-level JSON keys |
| `try/except` wrapping chart builders | Chart rendering failures → `ChartResult(error=...)` |
| `state.get("key", default)` | Missing pipeline state keys |

### Template layer

| Pattern | Protects against |
|---|---|
| `\| default('')` / `\| default([])` | Undefined or None values |
| `{% if field %}...{% endif %}` | Empty/None fields silently omitted |
| `{% elif block_type %}` fallback | Unknown block types → `<pre>` JSON dump |

### Pipeline layer

| Pattern | Protects against |
|---|---|
| `{**state, key: new_value}` return | Nodes cannot zero out upstream data |
| `errors: list[str]` accumulator | Soft errors without crashing |
| Optional `agentcert` import in schema.py | Missing sibling repo |

---

## 16. Design Decisions and Trade-offs

### 1. Generation triggered by aggregation-certification, not by the report endpoints

Report generation (`run_pipeline`) is invoked inside the background task of `POST /api/v1/aggregation-certification` (via `generate_cert_report_documents` in `main/services/pipeline_service.py`), not by the cert-reporter API endpoints. The two cert-reporter endpoints only serve the already-generated files.

This design keeps the certifier pipeline and the report renderer in a single atomic operation — callers get both the `certification.json` and the rendered HTML/PDF from one API call, without needing a second round-trip.

**Trade-off:** The cert-reporter service must be deployed on the same filesystem as the certifier pipeline. For distributed deployments, the workspace would need to be a shared mount or replaced with object storage. Report generation failure is non-fatal (logged as a warning); callers can use `python main.py generate` or the CLI to re-render if needed.

### 2. CSS inlined into HTML

`static/report.css` is embedded as `<style>...</style>`. The HTML file is fully self-contained.

**Trade-off:** HTML files are ~80KB larger. Production variants could serve CSS from a CDN.

### 3. Charts as inline SVG

No external image files. SVGs scale cleanly for screen and print.

**Trade-off:** SVGs can be large (~50KB for a heatmap). For very large reports, consider base64-encoded PNG.

### 4. Fault groups as render-time transformation

`_group_fault_blocks()` is a display decision, not a data model decision. Source JSON always uses plain `heading` and `assessment` blocks.

### 5. Playwright in a dedicated thread

`_run_in_thread` runs async Playwright in a fresh `threading.Thread` to avoid `asyncio.run()` failures inside FastAPI's event loop.

**Trade-off:** Thread pool is unbounded. For production, use `ThreadPoolExecutor` with `max_workers`.

### 6. WORKSPACE_DIR relative-path anchoring

If `WORKSPACE_DIR` is a relative path (e.g. `"workspace"`), cert-reporter anchors it to `_CERTIFIER_ROOT` rather than the process working directory. This makes the workspace location independent of where `python main.py` is executed from.

---

## 17. Dependency Map

```
main certifier API:
  POST /api/v1/aggregation-certification
    └── cert_task_runner.run_cert_task()
          └── pipeline_service.generate_cert_report_documents()
                └── pipeline/graph.run_pipeline()   ←── generates HTML + PDF

cert-reporter main.py:
  ├── serve  ──▶ api/app.py ──▶ api/routes.py
  │                                  │
  │                          GET /certification/pdf   → _find_latest() → FileResponse
  │                          GET /certification/html  → _find_latest() → FileResponse
  │
  └── generate ──────────────▶ pipeline/graph.py
                                pipeline/agentic_graph.py
                                      │
                    ┌─────────────────┼─────────────────┐
                    ▼                 ▼                  ▼
             reader.py           charts.py          llm_nodes.py
             schema.py           altair             agents/
             parameters.py       vl_convert           inspector.py
                    │                                section_writer.py
                    ▼
             html_renderer.py ──▶ jinja2 ──▶ templates/
             pdf_renderer.py  ──▶ playwright

External dependencies:
  altair, vl-convert-python  → chart SVG rendering
  jinja2, markupsafe          → HTML templating
  playwright                  → PDF generation (requires `playwright install chromium`)
  langgraph                   → pipeline state graph
  langchain-core              → LLM abstraction
  langchain-openai            → OpenAI provider
  langchain-anthropic         → Anthropic provider
  pydantic                    → state models, API models
  fastapi, uvicorn            → HTTP server
  python-dotenv               → .env loading
```
