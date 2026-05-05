# cert-reporter

Converts a structured [AgentCert](https://github.com/agentcert) certification JSON document into a polished **HTML** and **PDF** report.

The pipeline is a **LangGraph `StateGraph`** (5 nodes) with an optional LLM enrichment step that rewrites section introductions using LangChain. Two pipeline modes are available: **static** (deterministic, no LLM required) and **agentic** (LLM-driven section-by-section enrichment with domain detection). Both a **CLI** and a **FastAPI server** (with demo UI) are provided.

---

## Quick start

```bash
# 1 — install
pip install -r requirements.txt
python -m playwright install chromium   # headless browser for PDF

# 2 — configure API keys (only needed for --enrich-llm or --mode agentic)
cp .env.example .env
# edit .env and add OPENAI_API_KEY / ANTHROPIC_API_KEY

# 3a — CLI (generates HTML + PDF in ./output/)
python main.py --input certification_report.json --output-dir ./output

# 3b — API server + demo UI
python server.py
# open http://localhost:8000
```

---

## Project structure

```
cert-reporter/
│
├── main.py                     # CLI entry point
├── server.py                   # uvicorn server entry point
├── requirements.txt
├── .env.example                # environment variable template → copy to .env
│
├── api/                        # FastAPI application
│   ├── app.py                  # application factory (loads .env, mounts routes)
│   ├── routes.py               # GET /health, POST /generate, GET /reports, ...
│   └── models.py               # Pydantic request / response schemas
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
│   │   ├── assessment.html     # standalone assessment card
│   │   ├── card.html           # key-value grid card
│   │   ├── chart.html          # inline SVG chart card
│   │   ├── fault_group.html    # merged heading + assessments card (synthetic block)
│   │   ├── findings.html       # numbered findings list
│   │   ├── heading.html        # sub-section heading with optional detail
│   │   ├── table.html          # data table (list or dict rows)
│   │   └── text.html           # narrative text (markdown rendered)
│   ├── sections/
│   │   └── section.html        # collapsible <details> section wrapper
│   └── components/             # reusable sub-components (legacy / advanced use)
│       ├── chart_embed.html
│       ├── kv_pairs.html
│       ├── narrative_block.html
│       └── table.html
│
├── static/
│   └── report.css              # inlined into HTML at render time
│
├── ui/
│   └── index.html              # single-page demo UI (drag-and-drop upload, preview)
│
├── tests/
│   └── __init__.py
│
└── output/                     # generated reports written here
```

---

## Pipeline nodes

### Static mode (`--mode static`, default)

```
certification_report.json
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
certification_report.json
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

```
python main.py [OPTIONS]

Options:
  -i, --input PATH          Path to the certification JSON file  [required]
  -o, --output-dir PATH     Output directory (default: ./output)
  -f, --format TEXT         Comma-separated formats: html,pdf  (default: html,pdf)
      --mode TEXT           Pipeline mode: static | agentic  (default: static)
      --enrich-llm          Enable LLM narrative enrichment (static mode only)
      --model TEXT          LLM model name  (default: gpt-4.1-mini)
      --provider TEXT       openai | anthropic  (default: openai)
      --temperature FLOAT   LLM temperature  (default: 0.4)
  -v, --verbose
```

### Examples

```bash
# HTML + PDF, no LLM
python main.py -i cert.json -o ./output

# HTML only
python main.py -i cert.json -o ./output --format html

# LLM enrichment (OpenAI)
python main.py -i cert.json -o ./output --enrich-llm --model gpt-4.1-mini

# LLM enrichment (Anthropic)
python main.py -i cert.json -o ./output \
  --enrich-llm --provider anthropic --model claude-3-5-haiku-20241022

# Agentic mode (parallel per-section enrichment)
python main.py -i cert.json -o ./output --mode agentic
```

---

## API server

```bash
python server.py                         # http://0.0.0.0:8000
python server.py --port 8080             # custom port
python server.py --reload                # dev mode with auto-reload
python server.py --workers 4             # multi-process (production, no --reload)
```

| URL | Description |
|-----|-------------|
| `http://localhost:8000/` | Demo UI |
| `http://localhost:8000/docs` | Swagger / OpenAPI interactive docs |
| `http://localhost:8000/redoc` | ReDoc API reference |

### API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/health` | Health check |
| `POST` | `/api/generate` | Generate from JSON body |
| `POST` | `/api/generate/upload` | Generate from file upload (multipart) |
| `GET`  | `/api/reports` | List all generated reports |
| `GET`  | `/api/reports/{filename}` | Serve a report file (HTML or PDF) |

#### `POST /api/generate` — JSON body

```json
{
  "json_content": { "...certification document..." },
  "formats": ["html", "pdf"],
  "mode": "static",
  "enrich_llm": false,
  "model": "gpt-4.1-mini",
  "provider": "openai",
  "temperature": 0.4
}
```

#### `POST /api/generate/upload` — multipart form

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `file` | file | — | Certification JSON file (required) |
| `formats` | string | `"html,pdf"` | Comma-separated output formats |
| `mode` | string | `"static"` | `static` or `agentic` |
| `enrich_llm` | bool | `false` | Enable LLM enrichment (static mode) |
| `model` | string | `"gpt-4.1-mini"` | LLM model |
| `provider` | string | `"openai"` | `openai` or `anthropic` |
| `temperature` | float | `0.4` | LLM temperature |

#### Response

```json
{
  "doc_id": "cert-doc-2026-02-25-1f006d",
  "html_url": "/reports/cert-doc-2026-02-25-1f006d.html",
  "pdf_url":  "/reports/cert-doc-2026-02-25-1f006d.pdf",
  "errors": [],
  "token_usage": { "input_tokens": 4200, "output_tokens": 980, "total": 5180 },
  "duration_seconds": 2.3
}
```

`html_url` / `pdf_url` are relative — prepend the server origin for the full URL.
`token_usage` is `null` when no LLM is used.

#### curl examples

```bash
# Health check
curl http://localhost:8000/api/health

# Generate from file
curl -X POST http://localhost:8000/api/generate/upload \
  -F "file=@certification_report.json" \
  -F "formats=html,pdf"

# Generate with LLM enrichment
curl -X POST http://localhost:8000/api/generate/upload \
  -F "file=@cert.json" \
  -F "formats=html,pdf" \
  -F "enrich_llm=true" \
  -F "model=gpt-4.1-mini"

# Agentic mode
curl -X POST http://localhost:8000/api/generate/upload \
  -F "file=@cert.json" \
  -F "mode=agentic"

# Download PDF
curl -O http://localhost:8000/reports/cert-doc-2026-02-25-1f006d.pdf
```

---

## Environment variables

Copy `.env.example` to `.env` in the project root.

```dotenv
# OpenAI — required when --provider openai and LLM is enabled
OPENAI_API_KEY=sk-...
# OPENAI_BASE_URL=https://your-proxy.example.com/v1   # optional custom base

# Anthropic — required when --provider anthropic and LLM is enabled
ANTHROPIC_API_KEY=sk-ant-...

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
| `python-multipart` | File upload support |
