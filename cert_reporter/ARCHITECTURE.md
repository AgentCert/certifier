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
13. [Extension Points](#13-extension-points)
14. [Resilience Patterns](#14-resilience-patterns)
15. [Design Decisions and Trade-offs](#15-design-decisions-and-trade-offs)
16. [Dependency Map](#16-dependency-map)

---

## 1. System Overview

```
                    ┌────────────────────────────────────────────────────────┐
                    │                    cert-reporter                        │
                    │                                                         │
  ┌──────────┐      │  ┌──────────┐    ┌─────────────────────┐  ┌─────────┐  │
  │   CLI    │──────┼─▶│ main.py  │───▶│   LangGraph         │─▶│ output/ │  │
  │ argparse │      │  └──────────┘    │   StateGraph        │  │ .html   │  │
  └──────────┘      │                  │   (static or        │  │ .pdf    │  │
                    │  ┌──────────┐    │    agentic)         │  └─────────┘  │
  ┌──────────┐      │  │server.py │    └─────────────────────┘               │
  │  HTTP    │──────┼─▶│ uvicorn  │               ▲                          │
  │  Client  │      │  └──────────┘               │                          │
  └──────────┘      │       │            ┌─────────────────┐                  │
                    │       └───────────▶│  FastAPI routes │                  │
                    │                    └─────────────────┘                  │
                    └────────────────────────────────────────────────────────┘
```

Both the CLI and the API server funnel into the same `run_pipeline()` / `run_agentic_pipeline()` functions in `pipeline/graph.py` and `pipeline/agentic_graph.py`.

---

## 2. Entry Points

### `main.py` — CLI

Parses command-line arguments via `argparse` and dispatches to one of two pipeline functions:

| `--mode` | Function called |
|---|---|
| `static` (default) | `pipeline.graph.run_pipeline()` |
| `agentic` | `pipeline.agentic_graph.run_agentic_pipeline()` |

**Exit codes:** `0` on success (at least one output file written), `1` on failure or zero outputs.

Full argument reference:

```
-i / --input PATH          Path to certification JSON  [required]
-o / --output-dir PATH     Output directory            (default: ./output)
-f / --format TEXT         html,pdf                    (default: html,pdf)
     --mode TEXT           static | agentic            (default: static)
     --enrich-llm          Enable LLM enrichment (static mode)
     --model TEXT          LLM model name              (default: gpt-4.1-mini)
     --provider TEXT       openai | anthropic          (default: openai)
     --temperature FLOAT   LLM temperature             (default: 0.4)
-v / --verbose             DEBUG logging
```

### `server.py` — HTTP Server

```bash
python server.py [--host HOST] [--port PORT] [--reload] [--workers N]
```

Calls `uvicorn.run("api.app:create_app", factory=True, ...)`. The `factory=True` flag means uvicorn calls `create_app()` in each worker process, ensuring `.env` is loaded per-process.

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
    raw_doc:           dict                  # full JSON as loaded
    meta:              dict                  # meta top-level object
    header:            dict                  # header top-level object
    sections:          list[dict]            # sections[] — each a plain dict
    footer:            str                   # footer string
    charts_to_render:  list[dict]            # chart blocks extracted from sections
    chart_results:     dict[str, ChartResult]  # empty; populated by charts_node
    enriched_sections: dict[str, dict]       # empty; populated by llm_enrich_node
    html_path:         str                   # empty; populated by html_renderer_node
    pdf_path:          str                   # empty; populated by pdf_renderer_node
    token_usage:       TokenUsage
    errors:            list[str]

    # ── Set by charts_node ────────────────────────────────────────────
    # chart_results: dict[str, ChartResult] — keyed by block._chart_id

    # ── Set by llm_enrich_node or agentic assemble_node ───────────────
    # enriched_sections: dict[str, dict] — keyed by section id

    # ── Set by html_renderer_node ─────────────────────────────────────
    # html_path: str

    # ── Set by pdf_renderer_node ──────────────────────────────────────
    # pdf_path: str
```

**Key models:**

```python
class ChartResult(BaseModel):
    chart_id:   str
    chart_type: str
    title:      str
    svg:        str = ""              # rendered SVG string
    alt_text:   str = ""
    width_px:   int = 600
    height_px:  int = 400
    error:      Optional[str] = None  # set on render failure, no crash

class LLMConfig(BaseModel):
    model:       str   = "gpt-4.1-mini"
    temperature: float = 0.4
    max_tokens:  int   = 4096
    provider:    str   = "openai"     # "openai" | "anthropic"

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

The conditional edge function checks `state["enrich_llm"]` and routes to either `"llm_enrich"` or `"html_render"`.

`run_pipeline(input_path, output_dir, formats, enrich_llm, model, provider, temperature, verbose, schema_class=None) -> dict`

Builds an initial state with all input keys, compiles the graph, and invokes it. Returns the final state dict.

---

## 5. Agentic Pipeline

Defined in `pipeline/agentic_graph.py`. Uses an extended `AgenticState` TypedDict:

```python
class AgenticState(GraphState):
    domain_profile:    DomainProfile          # from inspect_node
    current_section:   dict                   # injected per-section fan-out
    agentic_sections:  Annotated[list[dict], operator.add]  # fan-in accumulator
```

```
START
  │
  ▼
preprocess_node
  │
  ▼
charts_node
  │
  ▼
inspect_node              Rule-based JSON analysis → DomainProfile
  │
  ▼
dispatch_sections_node    Returns [Send("enrich_section_node", {section}) for section in sections]
  │                       Each Send() spawns an independent branch
  ├──▶ enrich_section_node (section 0)
  ├──▶ enrich_section_node (section 1)
  ├──▶ enrich_section_node (section N)
  │    All run concurrently; each appends to agentic_sections[]
  │
  ▼
assemble_node             Sort by section.number; set state["sections"]
  │
  ▼
html_renderer_node
  │
  ▼
pdf_renderer_node
  │
  END
```

**LangGraph `Send()` fan-out:** `dispatch_sections_node` returns a list of `Send("enrich_section_node", payload)` objects. LangGraph executes all of them in parallel, collecting results in `agentic_sections` via the `operator.add` reducer (concurrent list append).

**Zero data loss contract:** `enrich_section_node` only writes `section["intro"]`. All content blocks pass through unchanged. The LLM cannot add, remove, or reorder data.

---

## 6. Node Reference

### `preprocess_node` (`pipeline/reader.py`)

1. Reads and JSON-parses `state["input_path"]`.
2. Passes raw dict through `normalise_document()` (see §7).
3. Calls `_ensure_dicts()`: iterates sections, converts any non-dict items to plain dicts.
4. Calls `_extract_chart_blocks(sections)`:
   - Walks every `section.content[]` looking for `block["type"] == "chart"`.
   - Assigns a unique `_chart_id = f"{section_id}_{chart_type}_{i}"` to each.
   - Appends the block (with `_chart_id` added) to `charts_to_render`.
5. Populates all state keys: `raw_doc`, `meta`, `header`, `sections`, `footer`, `charts_to_render`.
6. Initialises downstream keys to empty values: `chart_results: {}`, `enriched_sections: {}`, `html_path: ""`, `pdf_path: ""`, `token_usage: TokenUsage()`, `errors: []`.

All JSON field accesses use `.get(key, default)` — missing keys produce empty values, never exceptions.

---

### `charts_node` (`pipeline/charts.py`)

Renders each chart block from `state["charts_to_render"]` into a `ChartResult`.

**Rendering backend (priority order):**
1. `vl_convert` — `vlc.vegalite_to_svg(json.dumps(spec))` — fastest, pure C
2. `altair` — `chart.to_image(format="svg")` — fallback
3. Placeholder SVG — if both fail

**Chart builders:**

| `chart_type` | Builder | Key input fields | SVG size |
|---|---|---|---|
| `radar` | `_build_radar` | `dimensions[{dimension, value}]` (0–1 scores) | 420×350 |
| `grouped_bar` | `_build_grouped_bar` | `categories[]`, `series[{name, values[]}]`, `y_axis`, `reference_lines[{value, label}]` | 500×320 |
| `stacked_bar` | `_build_stacked_bar` | `categories[]`, `series[{name, values[]}]`, `y_axis` | 500×320 |
| `heatmap` | `_build_heatmap` | `x_labels[]`, `y_labels[]`, `values[][]`, `display_values[][]` | 520×300 |

Each builder accepts both plain dicts and Pydantic model objects (via `isinstance`/`hasattr` + `getattr(..., default)` guards).

Unknown `chart_type` produces `ChartResult(error="Unknown chart type: X")` — the chart area in the template shows an error message rather than crashing the pipeline.

**Score colour helper** (used for radar point colours):
```
≥ 0.90 → #2ecc71 green
≥ 0.75 → #3498db blue
≥ 0.60 → #f39c12 amber
  else → #e74c3c red
```

---

### `llm_enrich_node` (`pipeline/llm_nodes.py`)

Skipped entirely when `state["enrich_llm"]` is `False`.

**What gets enriched** (all calls via `asyncio.gather`):

| Content type | Field | Minimum length |
|---|---|---|
| Every section | `intro` | — |
| `type:"text"` blocks | `body` | 50 chars |
| `type:"assessment"` blocks | `body` | 50 chars |

**Prompts:**
- `_SYSTEM_PROMPT`: Technical writer persona; preserve all factual content; improve clarity, flow, tone; return only improved text.
- `_SECTION_INTRO_PROMPT`: Polished 2–4 sentence intro grounded in the section's data.
- `_TEXT_BLOCK_PROMPT`: Rewrite for clarity; keep factual details.
- `_ASSESSMENT_BLOCK_PROMPT`: Same as text, applied to assessment bodies.

**Provider dispatch:**
```python
"openai"     → ChatOpenAI(model=..., temperature=..., api_key=...)
"anthropic"  → ChatAnthropic(model=..., temperature=..., api_key=...)
```

**Failure handling:** Any individual LLM call failure silently returns the original text. Node-level failure appends to `state["errors"]` and returns state unchanged.

**Output:** `state["enriched_sections"]` — `{section_id: {**section, enriched_fields}}` dict. The html_renderer_node merges enriched sections over the original sections at render time via `_effective_sections()`.

---

### `html_renderer_node` (`pipeline/html_renderer.py`)

**`_effective_sections(state) -> list[dict]`**

Merges enriched sections over originals:
```python
enriched = state.get("enriched_sections", {})
for sec in state["sections"]:
    sid = sec.get("id", "")
    if sid in enriched:
        yield {**sec, **enriched[sid]}
    else:
        yield sec
```

**`_group_fault_blocks(content) -> list`** — see §10 for full details.

**Jinja2 context** passed to `base.html`:
```python
{
    "meta":       state["meta"],
    "header":     state["header"],
    "sections":   _effective_sections(state),   # merged originals + enriched
    "charts":     state["chart_results"],        # {_chart_id: ChartResult}
    "footer":     state["footer"],
    "css":        _read_css(),                    # full content of static/report.css
    "token_usage": state.get("token_usage"),
}
```

**`_make_doc_id(state) -> str`:**

Priority chain for filename:
1. `meta["certification_run_id"]` (slugified)
2. `meta["agent_id"]` + `meta["certification_date"]`
3. `"cert-report"` (final fallback)

Slugification: lowercase, spaces/underscores → hyphens, non-alphanumeric stripped, truncated to 60 chars.

Output written to `{output_dir}/{doc_id}.html`. `state["html_path"]` updated.

---

### `pdf_renderer_node` (`pipeline/pdf_renderer.py`)

Skipped when `"pdf"` not in `state["formats"]`.

**Async render sequence (`_render_pdf`):**
1. `page.goto(f"file://{html_path}", wait_until="networkidle")` — waits for all resources to settle.
2. `page.evaluate(...)` — opens all `<details>` elements by setting the `open` attribute.
3. `page.evaluate("() => document.body.offsetHeight")` — forces a synchronous layout reflow so Chromium recalculates element heights before computing page break positions.
4. `page.pdf(format="A4", margin=15mm, print_background=True)` — generates the PDF.

**Header template:** right-aligned "Agent Certification Report" at 9px sans-serif.
**Footer template:** centred `{pageNumber} / {totalPages}` at 9px.

**Event loop safety (`_run_in_thread`):**
```python
def _worker():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_render_pdf(...))
    loop.close()

t = threading.Thread(target=_worker, daemon=True)
t.start(); t.join()
```

This pattern prevents `asyncio.run() cannot be called from a running event loop` errors when invoked from within FastAPI/uvicorn's event loop.

---

### `inspect_node` (`pipeline/agents/inspector.py`) — Agentic only

Calls `inspect_document(state["raw_doc"]) -> DomainProfile`. Pure rule-based analysis, no LLM.

**`_walk(obj, path, depth=0)`** recursively classifies every JSON field (max depth 6, max 200 fields total):

| Classification | Condition |
|---|---|
| `kv_list` | `list` whose items are dicts with `{key,value}` or `{label,value}` keys |
| `table` | `list` of dicts with ≥ 2 shared keys and ≥ 2 rows |
| `array` | `list` of primitives |
| `narrative` | `str` with length ≥ 100 |
| `scalar` | `int`, `float`, `bool` |
| `nested` | any other dict |

**`_infer_domain`:** Scores document text against keyword vocabularies for `cybersecurity`, `sre`, `ai_evaluation`, `financial`, `compliance`; returns highest-scoring domain or `"general"`.

**`DomainProfile`** output:
- `domain`, `title`, `agent_name`, `cert_level`, `cert_score`
- `fields: list[FieldInfo]`, `scalars: dict`, `narratives: dict`, `tables: dict[str, FieldInfo]`

---

### `enrich_section_node` (`pipeline/agents/section_writer.py`) — Agentic only

Called once per section via `Send()` fan-out.

**`_section_data_summary(section, max_chars=800) -> str`:**
Builds a compact text description from content blocks for the LLM prompt:

| Block type | Summary format |
|---|---|
| `heading` | `"Heading: {title} — {detail}"` |
| `text` | `"Text: {body[:200]}"` |
| `table` | `"Table '{title}': {N} rows, columns: {headers}" + sample row` |
| `findings` | Up to 3: `"Finding ({sev}): {text[:100]}"` |
| `assessment` | `"Assessment '{title}': rating={rating}, {body[:150]}"` |
| `card` | `"Card '{title}': label=value, ..."` |
| `chart` | `"Chart ({chart_type}): {title}"` |

**`enrich_section(section, domain, llm=None) -> dict`:**
- If `llm is None`: returns section unchanged (preserves existing intro if present).
- Otherwise: calls LLM with `_ENRICH_SYSTEM` + `_ENRICH_HUMAN` (section title, domain, data summary).
- LLM prompt: "2–4 sentence introduction, data-grounded only, plain prose, no markdown or bullet points."
- Returns `{**section, "intro": llm_result}`.

---

## 7. Schema Normalisation Layer

`pipeline/schema.py` is a thin adapter that validates raw JSON through a caller-supplied Pydantic schema class. **The pipeline never imports or references any schema package directly** — the schema class is injected via `GraphState["schema_class"]`.

```python
def normalise_document(raw: dict, schema_class=None) -> dict:
    if not _is_framework_format(raw):
        return raw           # non-canonical format — pass through
    if schema_class is None:
        return raw           # no schema provided — pass through
    try:
        doc = schema_class.model_validate(raw)
        return doc.model_dump(mode="python")
    except Exception as exc:
        log.warning("Schema validation failed (%s), falling back to raw dict", exc)
        return raw
```

**Injection chain:**

```
run_pipeline(schema_class=CertificationReport)
     │
     └─▶ initial_state["schema_class"] = CertificationReport
              │
              └─▶ preprocess_node reads state["schema_class"]
                       │
                       └─▶ normalise_document(raw, schema_class=...)
```

The schema class is optional at every level. Default `schema_class=None` means no validation — the raw dict flows through unchanged.

**Why caller-injection:** The upstream schema package location changes across versions and deployments. Hardcoding an import path into the pipeline ties it to a particular directory structure and package name. With injection, callers that have the schema pass it in; callers that don't (the API server, the CLI by default) leave it as `None`.

**Detection:** `_is_framework_format(raw)` checks for `"meta"` and `"sections"` top-level keys. Normalisation is skipped entirely for documents that don't match the canonical format.

**Side effects of normalisation:**
- Pydantic `model_dump(mode="python")` converts most Enum values to their `.value` string.
- Some Enum fields (`rating`, `confidence` in assessment blocks) may remain as Enum objects. Templates handle both cases: `| default('')` and `{% if sev is not string %}{% set sev = sev.value %}{% endif %}`.
- If model objects reach `_ensure_dicts()` in `reader.py`, they are converted via `.model_dump()` before Jinja2 rendering.

---

## 8. Template Architecture

All templates live in `templates/`. The Jinja2 environment is configured with:
```python
autoescape=True
trim_blocks=True
lstrip_blocks=True
```

### Template hierarchy

```
base.html                     Master layout
  ├── cover.html               Navy gradient header + cert badge
  └── sections/section.html   One <details> per section
        └── blocks/            Dispatched by block.type
              ├── assessment.html
              ├── card.html
              ├── chart.html
              ├── fault_group.html   (synthetic — from _group_fault_blocks)
              ├── findings.html
              ├── heading.html
              ├── table.html
              └── text.html
```

### `base.html` rendering sequence

1. `<style>{{ css | safe }}</style>` — inlines `static/report.css`
2. `{% include "cover.html" %}`
3. **Identity card** — 3-column grid with Agent ID, Certification Date, Certification Run
4. **Key Findings** (`<details open>`) — from `header.findings[]`; each item gets a `.finding-{severity}` class
5. **Part dividers** — `namespace(current_part='')` tracks `section.part` changes; inserts `.part-divider` div when the part label transitions; cleared (set to `''`) when part is null/empty
6. **Sections loop** — `{% include "sections/section.html" %}` for each section
7. **Footer** — uses `footer` string if non-empty, otherwise `meta.certification_run_id + date + "AgentCert"`

### `cover.html`

Computes `overall_score` from `header.scorecard[].value` when `header.overall_score` is absent:
```jinja2
{% set scores = header.scorecard | default([]) | map(attribute='value') | list %}
{% set overall_score = (scores | sum / scores | length * 100) | round(1) if scores else 0 %}
```

Shows the cert badge only when `cert_level` or `overall_score` is truthy. Both fields are optional in the current JSON schema — the cover degrades gracefully to just showing the title and subtitle.

### `sections/section.html`

Block type dispatch order:

```jinja2
{% if block_type == 'chart' %}       → blocks/chart.html
{% elif block_type == 'heading' %}   → blocks/heading.html
{% elif block_type == 'text' %}      → blocks/text.html
{% elif block_type == 'table' %}     → blocks/table.html
{% elif block_type == 'findings' %}  → blocks/findings.html
{% elif block_type == 'assessment' %}→ blocks/assessment.html
{% elif block_type == 'fault_group' %}→ blocks/fault_group.html
{% elif block_type == 'card' %}      → blocks/card.html
{% elif block_type %}                → <details><pre>{{ block | tojson(indent=2) }}</pre></details>
{% endif %}
```

The final `{% elif block_type %}` fallback ensures unknown block types render as collapsible raw JSON — **no data is ever silently dropped**.

The first section (`section_num == 1`) has its `<details>` opened by default.

---

## 9. Custom Jinja2 Filters

All registered in `html_renderer.py`:

| Filter | Signature | Output |
|---|---|---|
| `score_class` | `(score: float\|str) -> str` | `"excellent"` ≥0.90 / `"good"` ≥0.75 / `"adequate"` ≥0.60 / `"poor"` |
| `cert_class` | `(level: str) -> str` | `"cert-gold"` / `"cert-silver"` / `"cert-bronze"` / `"cert-none"` |
| `fmt_num` | `(value) -> str` | Integers: no decimal. Floats: 2dp. Large numbers: comma-separated. `None` → `"—"` |
| `status_class` | `(status: str) -> str` | `"status-pass"` if starts with pass/true/yes/ok; `"status-fail"` if fail/false/no/error; `"status-warn"` if warn/caution |
| `severity_class` | `(severity: str) -> str` | `"finding-concern"` / `"finding-good"` / `"finding-note"` |
| `tag_class` | `(value: str) -> str` | `"tag-excellent"` / `"tag-good"` / `"tag-warn"` / `"tag-bad"` / `""` |
| `replace_underscore` | `lambda s: str.replace("_"," ").title()` | `"fault_type"` → `"Fault Type"` |
| `md` | `(text: str) -> Markup` | Markdown → safe HTML |

**`_md(text)` filter implementation details:**
1. HTML-escapes input via `markupsafe.escape()` (prevents XSS since autoescape is on).
2. Regex transforms in order:
   - `**text**` → `<strong>text</strong>`
   - `*text*` → `<em>text</em>`
   - `` `text` `` → `<code>text</code>`
3. Blank lines → `</p><p>` (paragraph breaks).
4. Remaining `\n` → `<br>` (line breaks).
5. Wraps result in `<p>...</p>`.
6. Returns `markupsafe.Markup(result)` to prevent Jinja2's autoescape from re-escaping the HTML tags.

Applied to: `section.intro`, `text` block `body`, `assessment` block `body`, `heading` block `detail`, `fault_group` assessment `body`.

---

## 10. Fault Group Post-Processor

`_group_fault_blocks(content: list) -> list` in `html_renderer.py`.

This post-processor runs on every section's content list before Jinja2 rendering. It **merges** `heading` blocks that are immediately followed by one or more `assessment` blocks into a single synthetic `fault_group` block:

```
Input:                              Output:
──────────────────────────────      ───────────────────────────────
{type: "heading", title: "Auth"}    {
{type: "assessment", ...}    ──▶     type: "fault_group",
{type: "assessment", ...}            title: "Auth",
                                     detail: <from heading>,
                                     assessments: [
                                       {type:"assessment", ...},
                                       {type:"assessment", ...}
                                     ]
                                    }
```

**CRITICAL implementation detail:** The synthetic block uses key `"assessments"` (not `"items"`). In Jinja2, `block.items` resolves to the Python built-in `dict.items()` method rather than a key lookup, causing a template error. Always access via `block.assessments`.

**Pass-through rules:**
- A `heading` not followed by an `assessment` passes through unchanged as a plain `heading` block.
- Any other block type passes through unchanged.
- `assessment` blocks not preceded by a `heading` pass through unchanged (rendered by `blocks/assessment.html` as a standalone `.fault-card`).

**Why at render time, not schema time:** The grouping is a display decision, not a data model decision. The same JSON structure renders correctly in both the HTML report (grouped into fault cards) and any other consumer (raw assessment blocks).

---

## 11. PDF Print Layout

All print rules are in the `@media print` section of `static/report.css`. The CSS is inlined into the HTML, so the PDF contains a fully self-contained document.

### Critical rules and their rationale

| Rule | Rationale |
|---|---|
| `details { break-inside: auto !important; overflow: visible !important; }` | `break-inside: avoid` was causing Chromium to leave large blank page bottoms when sections were tall enough to force a page jump. `overflow: hidden` was clipping content in print layout. |
| `details summary { break-after: avoid; }` | Prevents the section title being stranded alone at the bottom of a page. |
| `.fault-card { break-inside: auto; }` | Fault cards with multiple assessment blocks and long narratives can exceed 800px — `avoid` on tall elements causes blank-bottom-of-page gaps. Allow the card to split. |
| `.fault-card-header { break-after: avoid; }` | Keep the fault card header row pinned to the first content item. |
| `.narrative { break-after: avoid; }` | Prevents a page break between a notes block and the chart/card that follows it within a section (fixes "gap between notes and graph" issue). |
| `.kv-grid, .chart-card, .finding-item, .limitation-item, tr { break-inside: avoid; }` | Small atomic elements that would be meaningless if split — keep on one page. |
| `.chart-card svg { width: 100% !important; height: auto !important; }` | SVGs are rendered with fixed pixel dimensions (e.g. 500×320). In print, the column width is ~680px at 96dpi; without this rule SVGs overflow the page margin. |
| `.report-page { max-width: none; width: 100%; }` | The web view uses `max-width: 1060px` centred. In print this must be removed so content fills the full page width. |
| `.narrative p { margin: 0; } .narrative p + p { margin-top: 6px; }` | `_md()` wraps paragraphs in `<p>` tags. Without this, browsers apply ~1em top/bottom margin to each `<p>`, creating excessive whitespace in print. |

### `print_background: True`

Required for Playwright `page.pdf()`. Without it, Chromium strips all background colours and gradients, producing an unformatted black-and-white output. Specific elements also have `print-color-adjust: exact` to prevent UA stylesheet overrides:
- `.report-header`, `.report-footer`, `.data-table thead tr`, `.narrative`, `.fault-card`, `.part-divider`, `.assessment-badge`

### Layout reflow after `<details>` expansion

Playwright's `page.pdf()` computes page break positions based on the current rendered layout. If `<details>` elements are opened via JavaScript after the initial render but before `page.pdf()` is called, Chromium may use stale element heights for page break calculations. The sequence:

```python
# 1. Open all sections
await page.evaluate(
    "() => document.querySelectorAll('details').forEach(el => el.setAttribute('open', ''))"
)
# 2. Force synchronous reflow — triggers height recalculation
await page.evaluate("() => document.body.offsetHeight")
# 3. Now generate PDF with correct heights
await page.pdf(...)
```

---

## 12. API Server

### Application factory (`api/app.py`)

`create_app() -> FastAPI`:
1. Loads `.env` from project root using `python-dotenv` if available, otherwise manual `os.environ` parsing.
2. Creates `FastAPI` instance with `title="cert-reporter API"`.
3. Adds `CORSMiddleware` with `allow_origins=["*"]` (suitable for local/demo use).
4. Mounts `output/` as `/reports` static files directory.
5. Registers API router at prefix `/api`.
6. Registers `GET /` to serve `ui/index.html`.

### Request lifecycle

```
POST /api/generate/upload
  │
  ├─ Parse multipart form → extract file bytes + params
  ├─ json.loads(file bytes) → json_content dict
  ├─ Write json_content to NamedTemporaryFile
  ├─ Dispatch to run_pipeline() or run_agentic_pipeline() in thread executor
  │    (FastAPI is async; pipeline is sync; run in threadpool via asyncio.to_thread)
  ├─ Extract doc_id, html_url, pdf_url from final state
  ├─ Delete temp file
  └─ Return GenerateResponse
```

### API models (`api/models.py`)

```python
class GenerateRequest(BaseModel):
    json_content: dict
    formats:      list[str] = ["html", "pdf"]
    mode:         str       = "static"       # "static" | "agentic"
    enrich_llm:   bool      = False
    model:        str       = "gpt-4.1-mini"
    provider:     str       = "openai"
    temperature:  float     = Field(default=0.4, ge=0.0, le=2.0)

class GenerateResponse(BaseModel):
    doc_id:           str
    html_url:         Optional[str]
    pdf_url:          Optional[str]
    errors:           list[str]
    token_usage:      Optional[dict[str, int]]
    duration_seconds: float

class ReportItem(BaseModel):
    doc_id:   str
    html_url: Optional[str]
    pdf_url:  Optional[str]
    size_kb:  Optional[float]
```

---

## 13. Extension Points

### Adding a new block type

1. **Template:** Create `templates/blocks/{newtype}.html`. Follow the convention: `block` variable in scope, all field accesses with `| default(...)` guards.
2. **Section dispatch:** Add `{% elif block_type == 'newtype' %}{% include "blocks/newtype.html" %}` in `templates/sections/section.html` before the unknown-type fallback.
3. **Schema docs:** Add to `SCHEMA.md` content block type table.
4. **Optional — post-processing:** If the new type needs pre-processing (like `fault_group`), add a pass in `_group_fault_blocks()` in `html_renderer.py`.

No Python pipeline changes required for new block types.

### Adding a new chart type

1. **Builder:** Add `_build_{newtype}(block: dict) -> dict` in `pipeline/charts.py` that returns a Vega-Lite spec dict.
2. **Registration:** Add `"newtype": _build_{newtype}` to the `_BUILDERS` dict.
3. **Fallback:** No change needed — unknown chart types already return a `ChartResult(error=...)`, which renders as an error message in the chart card without crashing.

### Adding a new LLM provider

1. Add a new `elif provider == "newprovider":` branch in the `llm_enrich_node` provider dispatch in `pipeline/llm_nodes.py`.
2. Update `pipeline/agents/section_writer.py` with the same provider dispatch.
3. Add the corresponding LangChain package to `requirements.txt`.

### Changing the output format

HTML and PDF share the same Jinja2 render. To add a new format (e.g. DOCX):
1. Add the format string to `formats`.
2. Add a new `{newformat}_renderer_node` that reads `state["html_path"]` or builds from the Jinja2 context.
3. Register the node in `pipeline/graph.py`.

### Adding new meta fields to the identity card

The identity card in `base.html` (lines 22–40) is a simple grid. Add a new `.identity-item` div:
```jinja2
<div class="identity-item">
  <span class="identity-label">New Field Label</span>
  <span class="identity-value">{{ meta.new_field | default('—') }}</span>
</div>
```

Update `.identity-grid` in `report.css` if the column count changes (currently `repeat(3, 1fr)`).

---

## 14. Resilience Patterns

The pipeline is designed to never crash on partial or unexpected input. Key patterns:

### Python layer

| Pattern | Where used | Protects against |
|---|---|---|
| `doc.get("key") or default` | `reader.py` | Missing top-level JSON keys |
| `section.get("content", [])` | `reader.py`, `html_renderer.py` | Missing section content |
| `block.get("type") == "chart"` | `reader.py` `_extract_chart_blocks` | Missing block type field |
| `getattr(obj, "attr", default)` | `charts.py` after `hasattr` checks | Pydantic model missing optional attribute |
| `try/except Exception` wrapping entire chart builder | `charts.py` `_render_chart` | Any chart rendering failure → `ChartResult(error=...)` |
| `state.get("key", default)` | all nodes | Missing pipeline state keys |

### Template layer

| Pattern | Where used | Protects against |
|---|---|---|
| `\| default('')` / `\| default([])` | All template field accesses | Undefined or None values |
| `{% if field %}...{% endif %}` | Optional fields like `section.intro` | Empty/None fields silently omitted |
| `{% if sev is not string %}{% set sev = sev.value %}{% endif %}` | Severity in `base.html` | Enum objects from Pydantic returning `.value` vs raw strings |
| `block["items"] \| default([])` | `card.html`, `findings.html` | Bracket access: Jinja2 converts KeyError → Undefined, `\| default` handles it |
| `{% elif block_type %}` fallback | `section.html` | Unknown block types → `<pre>` JSON dump |
| `table.headers is defined and table.headers is not none` | `components/table.html` | Undefined vs None distinction in Jinja2 |
| `subsection.key_value_pairs \| default([])` | `components/kv_pairs.html` | Missing field in for loop (would be UndefinedError) |

### Pipeline layer

| Pattern | Where used | Protects against |
|---|---|---|
| `{**state, key: new_value}` return pattern | All nodes | Preserves all state keys; nodes cannot accidentally zero out upstream data |
| LLM node skipped on `enrich_llm=False` | `graph.py` conditional edge | Pipeline runs fully without LLM |
| Optional `agentcert` import | `schema.py` | Missing sibling repo |
| `errors: list[str]` accumulator | All nodes | Soft errors reported without crashing; all outputs still written |

---

## 15. Design Decisions and Trade-offs

### 1. CSS inlined into HTML

`static/report.css` is read at render time and embedded as `<style>{{ css | safe }}</style>`. This makes the HTML file fully self-contained — it can be opened in any browser, emailed, or archived without dependencies.

**Trade-off:** The HTML file is larger (~80KB for a full report). Production variants could serve CSS from a CDN, but self-containment was prioritised for portability.

### 2. Charts as inline SVG

Charts are rendered to SVG strings at pipeline time and embedded directly in the HTML. No external image files. SVGs scale cleanly for both screen (responsive CSS) and print (explicit width/height override in `@media print`).

**Trade-off:** SVG strings can be large for complex charts (~50KB for a heatmap). For very large reports with many charts, consider base64-encoded PNG instead.

### 3. No server-side caching

Each `POST /api/generate` request runs the full pipeline. For the same JSON input, results are identical (when `enrich_llm=False`). No caching was added to keep the server stateless.

**Trade-off:** Repeated generations of the same document are wasteful. If throughput is a concern, add a content-hash cache keyed on `sha256(json_content)`.

### 4. Fault groups as a render-time transformation

`_group_fault_blocks()` merges `heading + assessment` blocks at render time rather than in the schema. This means:
- The source JSON never needs to know about `fault_group`.
- Any consumer of the JSON (not just this renderer) sees clean, semantic `heading` and `assessment` blocks.
- The grouping logic is isolated in one function and is easy to modify.

**Trade-off:** The render layer is stateful in a way that would surprise someone reading the template — there is no `fault_group` block type in the schema docs. The `SCHEMA.md` file documents this transformation explicitly.

### 5. Playwright in a dedicated thread

The `_run_in_thread` wrapper always runs the async Playwright render in a fresh `threading.Thread`. This avoids `asyncio.run()` failures when called from within FastAPI's event loop, at the cost of thread overhead (~5ms).

**Trade-off:** The thread pool is unbounded. Under high load, many concurrent PDF requests could spawn many threads. For production, use a `ThreadPoolExecutor` with a fixed `max_workers` bound.

### 6. `_md()` filter — regex-based markdown

The markdown filter handles `**bold**`, `*italic*`, `` `code` ``, paragraph breaks, and line breaks using regular expressions. No external dependency (mistune, markdown, etc.).

**Trade-off:** Does not handle: headings (`#`), lists (`-`, `*`, `1.`), links (`[text](url)`), tables (`|---|`). If report narratives begin using these constructs, the filter will need to be ext ended or replaced with a library.

### 7. `agents/__init__.py` exports `write_section` (does not exist)

`pipeline/agents/__init__.py` currently exports `write_section`, but the actual function in `section_writer.py` is named `enrich_section`. The agentic graph imports `enrich_section` directly from `section_writer` and does not use the `__init__.py`. This would cause an `ImportError` if the `__init__.py` were imported.

---

## 16. Dependency Map

```
main.py ──────────────────────────┐
                                  ▼
server.py ──▶ api/app.py ──▶ api/routes.py
                                  │
                                  ▼
                        pipeline/graph.py          pipeline/agentic_graph.py
                                  │                          │
                    ┌─────────────┼───────────┐    ┌─────────┤
                    ▼             ▼           ▼    ▼         ▼
             reader.py       charts.py   llm_nodes.py    agents/
             schema.py       altair                       inspector.py
             parameters.py   vl_convert                  section_writer.py
                    │                                     planner.py (unused)
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
  python-multipart            → file upload
  python-dotenv               → .env loading
```
