# cert-reporter

Converts a structured [AgentCert](https://github.com/agentcert) `certification.json` document into a polished **HTML** and **PDF** report.

The pipeline is a **LangGraph `StateGraph`** (5 nodes) with an optional LLM enrichment step that rewrites section introductions using LangChain. Two pipeline modes are available: **static** (deterministic, no LLM required) and **agentic** (LLM-driven section-by-section enrichment with domain detection). Both a **CLI** and a **FastAPI server** (with demo UI) are provided via a single `main.py` entry point.

**Report generation is triggered by `POST /api/v1/aggregation-certification`** (the main certifier API). The two endpoints in this module (`POST /api/generate/pdf` and `POST /api/generate/html`) only serve already-generated files from the workspace.

---

## Quick start

```bash
# 1 — install
pip install -r requirements.txt
python -m playwright install chromium   # headless browser for PDF

# 2 — configure API keys (only needed for --enrich-llm or --mode agentic)
cp .env.example .env
# edit .env and add OPENAI_API_KEY / ANTHROPIC_API_KEY

# 3a — start the API server (default when no subcommand is given)
python main.py
# open http://localhost:8000

# 3b — CLI: generate directly (reads from / writes to workspace)
python main.py generate --agent-id my-agent --experiment-id exp-001
```

---

## Project structure

```
cert-reporter/
│
├── main.py                     # Unified entry point: `serve` (default) + `generate` subcommands
├── requirements.txt
├── .env.example                # environment variable template → copy to .env
│
├── api/                        # FastAPI application
│   ├── app.py                  # application factory (loads .env, mounts routes)
│   ├── routes.py               # POST /generate/pdf, POST /generate/html (file-serve only)
│   └── models.py               # Pydantic request schema (agent_id + experiment_id)
│
├── pipeline/                   # LangGraph pipeline nodes
│   ├── graph.py                # static StateGraph  (build_graph / run_pipeline)
│   ├── agentic_graph.py        # agentic StateGraph (build_agentic_graph / run_agentic_pipeline)
│   ├── parameters.py           # GraphState TypedDict + ChartResult, LLMConfig, TokenUsage
│   ├── reader.py               # preprocess_node  — load & parse JSON
│   ├── schema.py               # normalise_document — optional upstream Pydantic adapter
│   ├── charts.py               # charts_node      — render chart blocks → SVG
│   ├── llm_nodes.py            # llm_enrich_node  — LangChain narrative rewrites (static mode)
│   ├── html_renderer.py        # html_renderer_node — Jinja2 → HTML
│   ├── pdf_renderer.py         # pdf_renderer_node — Playwright → PDF
│   └── agents/
│       ├── inspector.py        # inspect_document → DomainProfile (rule-based JSON analysis)
│       ├── planner.py          # build_report_plan (LLM report planner, experimental)
│       └── section_writer.py   # enrich_section   — per-section LLM intro writing
│
├── prompts/
│   └── enrichment.py           # standalone LLM prompt string constants
│
├── templates/                  # Jinja2 templates
│   ├── base.html               # master layout (identity card, findings, sections loop, footer)
│   ├── cover.html              # cover page (navy gradient, cert badge, scorecard)
│   ├── blocks/                 # one template per content block type
│   │   ├── assessment.html
│   │   ├── card.html
│   │   ├── chart.html
│   │   ├── fault_group.html
│   │   ├── findings.html
│   │   ├── heading.html
│   │   ├── table.html
│   │   └── text.html
│   └── sections/
│       └── section.html        # collapsible <details> section wrapper
│
├── static/
│   └── report.css              # inlined into HTML at render time
│
└── ui/
    └── index.html              # single-page demo UI (agent_id + experiment_id, format picker)
```

---

## How report generation works

```
POST /api/v1/aggregation-certification          ← triggers the full pipeline
  │
  ├── Phase 2: Aggregation  → workspace/{agent_id}/{experiment_id}/aggregation/aggregation.json
  ├── Phase 3: cert_builder → workspace/{agent_id}/{experiment_id}/cert-builder/certification.json
  └── cert_reporter pipeline (this module)
        → workspace/{agent_id}/{experiment_id}/certification/<doc_id>.html
        → workspace/{agent_id}/{experiment_id}/certification/<doc_id>.pdf

POST /api/generate/html   }
POST /api/generate/pdf    }  ← serve the already-generated file from workspace
```

The cert_reporter pipeline is called synchronously (via `asyncio.to_thread`) inside the background task of `POST /api/v1/aggregation-certification`. Report generation failure is non-fatal — the certification task still completes and the `pdf_report` / `html_report` paths in `storage_paths` will be empty strings if generation failed.

---

## Pipeline nodes

### Static mode (`--mode static`, default)

```
certification.json
         │
         ▼
preprocess_node   Load JSON → normalise schema → extract chart blocks
         │
         ▼
charts_node       Render chart blocks → SVG (Altair / vl-convert)
         │
         ▼
[llm_enrich_node] Optional — rewrite section intros + text/assessment bodies
         │
         ▼
html_renderer_node  Jinja2 renders all sections with inline charts, tables, fault cards
         │
         ▼
pdf_renderer_node   Playwright headless Chromium → A4 PDF (page numbers, headers)
```

### Agentic mode (`--mode agentic`)

```
certification.json
         │
         ▼
preprocess_node → charts_node → inspect_node (rule-based domain detection)
         │
         ▼ Send() fan-out — one branch per section, run in parallel
enrich_section_node × N   (LLM writes section.intro only; all content blocks unchanged)
         │
         ▼ fan-in assemble_node
html_renderer_node → pdf_renderer_node
```

LLM enrichment is **opt-in**. When disabled (`--mode static` without `--enrich-llm`) the pipeline runs fully deterministically with no network calls, completing in ~2 seconds.

---

## CLI

`main.py` exposes two subcommands.

### `serve` — start the API server (default)

```bash
python main.py                          # 0.0.0.0:8000
python main.py serve                    # same
python main.py serve --port 8080
python main.py serve --reload           # dev mode with auto-reload
```

| URL | Description |
|-----|-------------|
| `http://localhost:8000/` | Demo UI |
| `http://localhost:8000/docs` | Swagger / OpenAPI interactive docs |
| `http://localhost:8000/redoc` | ReDoc API reference |

### `generate` — run the pipeline directly from CLI

Reads `certification.json` from the workspace and writes HTML/PDF back to the workspace.

```
python main.py generate [OPTIONS]

Required:
  -a, --agent-id TEXT        Agent ID (matches POST /aggregation-certification)
  -e, --experiment-id TEXT   Experiment ID (matches POST /aggregation-certification)

Optional:
  -f, --format TEXT          Comma-separated formats: html,pdf  (default: html,pdf)
      --mode TEXT            Pipeline mode: static | agentic    (default: static)
      --enrich-llm           Enable LLM narrative enrichment
      --model TEXT           LLM model name                     (default: gpt-4.1-mini)
      --provider TEXT        openai | anthropic                  (default: openai)
      --temperature FLOAT    LLM temperature                    (default: 0.4)
  -v, --verbose
```

**Workspace paths:**

| Direction | Path |
|-----------|------|
| Input (reads) | `workspace/{agent_id}/{experiment_id}/cert-builder/certification.json` |
| Output (writes) | `workspace/{agent_id}/{experiment_id}/certification/` |

**Examples:**

```bash
# HTML + PDF, no LLM
python main.py generate -a my-agent -e exp-001

# HTML only
python main.py generate -a my-agent -e exp-001 --format html

# LLM enrichment (OpenAI)
python main.py generate -a my-agent -e exp-001 --enrich-llm --model gpt-4.1-mini

# LLM enrichment (Anthropic)
python main.py generate -a my-agent -e exp-001 \
  --enrich-llm --provider anthropic --model claude-3-5-haiku-20241022

# Agentic mode (parallel per-section enrichment)
python main.py generate -a my-agent -e exp-001 --mode agentic
```

---

## API server

```bash
python main.py              # start server on http://0.0.0.0:8000
python main.py serve --port 8080
python main.py serve --reload
```

### API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/generate/pdf`  | Return the PDF report for the given agent/experiment |
| `POST` | `/api/generate/html` | Return the HTML report for the given agent/experiment |
| `GET`  | `/docs`  | Swagger / OpenAPI interactive docs |
| `GET`  | `/redoc` | ReDoc API reference |

Both generate endpoints **serve a pre-generated file** from `workspace/{agent_id}/{experiment_id}/certification/`. They do **not** run the pipeline. Generation happens inside `POST /api/v1/aggregation-certification`.

---

#### `POST /api/generate/pdf` and `POST /api/generate/html`

**Request body:**

```json
{
  "agent_id":      "my-agent",
  "experiment_id": "exp-001"
}
```

**Response:** Binary file download (`application/pdf` or `text/html`).

**Error responses:**

| Code | Meaning |
|------|---------|
| `404` | No report file found — run `POST /api/v1/aggregation-certification` first |

**Workspace path read:**

```
workspace/{agent_id}/{experiment_id}/certification/<most-recent>.pdf
workspace/{agent_id}/{experiment_id}/certification/<most-recent>.html
```

---

#### curl examples

```bash
# Download PDF report
curl -X POST http://localhost:8000/api/generate/pdf \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "my-agent", "experiment_id": "exp-001"}' \
  -o report.pdf

# Download HTML report
curl -X POST http://localhost:8000/api/generate/html \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "my-agent", "experiment_id": "exp-001"}' \
  -o report.html
```

---

## Workspace layout

cert-reporter integrates with the AgentCert certifier pipeline workspace:

```
certifier/
└── workspace/
    └── {agent_id}/
        └── {experiment_id}/
            ├── fault-bucketing/          ← Phase 0+1 output
            │   └── {run_id}/
            │       ├── traces/
            │       ├── fault_buckets/
            │       └── metrics/
            ├── aggregation/
            │   └── aggregation.json      ← Phase 2 output
            ├── cert-builder/
            │   └── certification.json    ← Phase 3 output — READ by cert-reporter
            └── certification/
                ├── <doc_id>.html         ← cert-reporter HTML output
                └── <doc_id>.pdf          ← cert-reporter PDF output
```

The workspace root is resolved from the `WORKSPACE_DIR` environment variable. If `WORKSPACE_DIR` is a relative path (e.g. `"workspace"`) it is joined onto the certifier project root, not the cert_reporter directory. If not set, defaults to `certifier/workspace/`.

---

## Environment variables

Copy `.env.example` to `.env` in the cert_reporter directory.

```dotenv
# OpenAI — required when --provider openai and LLM is enabled
OPENAI_API_KEY=sk-...
# OPENAI_BASE_URL=https://your-proxy.example.com/v1   # optional custom base

# Anthropic — required when --provider anthropic and LLM is enabled
ANTHROPIC_API_KEY=sk-ant-...

# Workspace root (optional — defaults to certifier/workspace/)
# WORKSPACE_DIR=workspace

# LangSmith tracing (optional)
# LANGCHAIN_TRACING_V2=false
# LANGCHAIN_API_KEY=ls__...
# LANGCHAIN_PROJECT=cert-reporter
```

Keys are loaded with `python-dotenv` (`override=False`) — environment variables set before launch always take precedence.

---

## LLM enrichment

### Static mode (`--enrich-llm`)

`llm_enrich_node` is inserted after `charts_node`. It concurrently rewrites three categories of content block fields:

| Content | Field rewritten | Minimum length |
|---|---|---|
| `section.intro` | Full intro paragraph | — |
| `type:"text"` blocks | `body` field | 50 chars |
| `type:"assessment"` blocks | `body` field | 50 chars |

All LLM calls run via `asyncio.gather`; originals are preserved on failure.

### Agentic mode (`--mode agentic`)

`enrich_section_node` runs once per section in parallel (LangGraph `Send()` fan-out). It only writes `section.intro` using a data-grounded 2–4 sentence prompt derived from the section's content blocks. **Content blocks are never modified.**

### Supported models

| Provider | Example models |
|---|---|
| `openai` (default) | `gpt-4.1-mini`, `gpt-4o`, `gpt-4.1` |
| `anthropic` | `claude-3-5-haiku-20241022`, `claude-3-5-sonnet-20241022`, `claude-3-7-sonnet-20250219` |

---

## Output

| File | Description |
|------|-------------|
| `<doc_id>.html` | Self-contained HTML — CSS and charts inline, no external dependencies |
| `<doc_id>.pdf`  | A4 PDF, 15 mm margins all sides, running header + page-number footer |

The `doc_id` is derived from `meta.certification_run_id`, or `meta.agent_id + meta.certification_date` if the run ID is absent, falling back to `"cert-report"`.

Both outputs are visually identical — the PDF is generated directly from the HTML by Playwright headless Chromium.

---

## Input JSON format

The pipeline accepts a single JSON document with four top-level keys:

```json
{
  "meta":     { "agent_name": "...", "agent_id": "...", "certification_run_id": "...",
                "certification_date": "...", "subtitle": "..." },
  "header":   { "scorecard": [{"dimension": "...", "value": 0.0}],
                "findings": [{"severity": "concern|good|note", "text": "..."}] },
  "sections": [ { "id": "...", "number": "1", "part": null, "title": "...",
                  "intro": "...", "content": [...] } ],
  "footer":   "optional footer text"
}
```

Each section's `content` is a list of typed blocks. See [SCHEMA.md](SCHEMA.md) for the full block type reference.

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `altair` + `vl-convert-python` | Vega-Lite chart rendering → SVG |
| `jinja2` | HTML templating |
| `playwright` | HTML → PDF via headless Chromium |
| `langgraph` | Pipeline state-graph orchestration |
| `langchain-core/openai/anthropic` | LLM enrichment calls |
| `pydantic` | State schema + API models |
| `python-dotenv` | `.env` file loading |
| `fastapi` + `uvicorn` | HTTP API server |
