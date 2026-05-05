# cert-reporter — JSON Input Schema

Reference for the `certification_report.json` input format consumed by cert-reporter.

---

## Table of Contents

1. [Top-level structure](#1-top-level-structure)
2. [meta](#2-meta)
3. [header](#3-header)
4. [sections](#4-sections)
5. [Content block types](#5-content-block-types)
   - [heading](#51-heading)
   - [text](#52-text)
   - [findings](#53-findings)
   - [card](#54-card)
   - [table](#55-table)
   - [chart](#56-chart)
   - [assessment](#57-assessment)
6. [footer](#6-footer)
7. [Fault group rendering (display-only)](#7-fault-group-rendering-display-only)
8. [Field resilience rules](#8-field-resilience-rules)
9. [Minimal valid document](#9-minimal-valid-document)
10. [Full example](#10-full-example)

---

## 1. Top-level structure

```json
{
  "meta":     { ... },
  "header":   { ... },
  "sections": [ ... ],
  "footer":   "optional string"
}
```

All four keys are optional — missing keys produce graceful fallbacks throughout the pipeline. In practice, a useful report requires at least `sections`.

---

## 2. meta

Metadata about the certification run and the agent being certified.

```json
{
  "meta": {
    "agent_name":               "string — displayed as the report title and in the identity card",
    "agent_id":                 "string — agent version / unique identifier",
    "certification_run_id":     "string — used as the output filename; slugified",
    "certification_date":       "string — ISO 8601 date preferred, e.g. 2026-03-26",
    "subtitle":                 "string — small caption below the report title in cover",
    "total_runs":               0,
    "total_faults":             0,
    "total_categories":         0,
    "runs_per_fault_configured": 0,
    "categories":               ["string", "..."]
  }
}
```

| Field | Required | Display location |
|---|---|---|
| `agent_name` | — | Browser tab title, identity card (bold heading), cover |
| `agent_id` | — | Identity card "Agent ID / Version" |
| `certification_run_id` | — | Identity card, PDF footer, output filename |
| `certification_date` | — | Identity card, PDF footer |
| `subtitle` | — | Cover page caption (default: "Comprehensive Evaluation & Certification") |
| `total_runs`, `total_faults`, `total_categories`, `runs_per_fault_configured`, `categories` | — | Not currently rendered in templates; available to LLM enrichment context |

All fields are optional. Missing fields render as `—` in the identity card.

**Output filename derivation:**
1. `certification_run_id` (slugified, max 60 chars)
2. `{agent_id}-{certification_date}` (slugified)
3. `"cert-report"` (final fallback)

---

## 3. header

Summary-level data shown above the section list.

```json
{
  "header": {
    "scorecard": [
      { "dimension": "string — capability label", "value": 0.85 }
    ],
    "findings": [
      { "severity": "concern|good|note", "text": "string" }
    ],
    "overall_score":       88.5,
    "certification_level": "GOLD|SILVER|BRONZE|NONE"
  }
}
```

### `header.scorecard[]`

Rendered in `cover.html` as a scored dimension list. Each dimension value must be a float in the range **0.0–1.0**. The average across all dimensions is computed and displayed as `overall_score` if the field is absent.

```json
{ "dimension": "Task Completion",   "value": 0.92 },
{ "dimension": "Tool Reliability",  "value": 0.87 },
{ "dimension": "Error Recovery",    "value": 0.71 }
```

### `header.findings[]`

Rendered in the collapsed "Key Findings" section that appears above all report sections.

| `severity` | CSS class | Badge colour |
|---|---|---|
| `"concern"` | `.finding-concern` | Red |
| `"good"` | `.finding-good` | Green |
| `"note"` | `.finding-note` | Gray |

Any unrecognised severity falls back to `"note"`.

### `header.overall_score` and `header.certification_level`

Optional. Not present in the current standard JSON schema — the cover computes `overall_score` from `scorecard` if absent. `certification_level` controls the cert badge colour:

| Value | Badge style |
|---|---|
| `"GOLD"` | Gold gradient |
| `"SILVER"` | Silver gradient |
| `"BRONZE"` | Bronze gradient |
| `""` or absent | Neutral gray |

---

## 4. sections

The core content of the report. An ordered list of section objects.

```json
{
  "sections": [
    {
      "id":     "string — unique, used as HTML anchor and enrichment key",
      "number": "string — displayed as the section badge, e.g. '1' or '2.1'",
      "part":   "string or null — part label for grouping sections under banners",
      "title":  "string — section heading text",
      "intro":  "string — introductory paragraph, markdown supported",
      "content": [ ... ]
    }
  ]
}
```

| Field | Required | Notes |
|---|---|---|
| `id` | Recommended | Used as the `<details id="section-{id}">` HTML anchor and as the key for LLM enrichment merge |
| `number` | — | Rendered as a circled badge beside the title |
| `part` | — | When non-null and changed from the previous section, a `.part-divider` banner is inserted. Set to `null` or omit to suppress. Common values: `"Agent Capability Assessment"`, `"Fault Injection Analysis"` |
| `title` | — | Section heading text |
| `intro` | — | Introductory paragraph. Supports markdown (`**bold**`, `*italic*`, `` `code` ``, blank lines for paragraphs). Written by LLM when enrichment is enabled. |
| `content` | — | List of typed content blocks (see §5) |

**Part dividers:** When sections belong to named parts, the report renders a labelled divider banner when the part label changes:

```json
{ "id": "s1", "number": "1", "part": "Agent Capability Assessment", ... }
{ "id": "s2", "number": "2", "part": "Agent Capability Assessment", ... }
{ "id": "s3", "number": "3", "part": "Fault Injection Analysis",    ... }
```

This produces one "Agent Capability Assessment" banner before section 1 and one "Fault Injection Analysis" banner before section 3.

---

## 5. Content block types

Every item in `section.content[]` is a typed block object. The `type` field determines which template is used.

---

### 5.1 heading

A sub-section title with an optional introductory detail paragraph.

```json
{
  "type":   "heading",
  "title":  "string — displayed as a bold sub-section label",
  "detail": "string or null — introductory text below the heading, markdown supported"
}
```

**Rendering:** `.sub-section-title` div for `title`; `.narrative` div for `detail` (if present), with markdown filter applied.

**Special behaviour:** When a `heading` block is immediately followed by one or more `assessment` blocks, they are merged into a single `fault_group` block at render time. See §7.

---

### 5.2 text

A narrative paragraph or prose block.

```json
{
  "type": "text",
  "body": "string — narrative text, markdown supported"
}
```

**Rendering:** `.narrative` div with markdown filter applied. Blank lines in `body` produce paragraph breaks. LLM enrichment (`--enrich-llm`) may rewrite `body` when its length is ≥ 50 characters.

---

### 5.3 findings

A numbered list of findings with severity labels.

```json
{
  "type": "findings",
  "items": [
    { "severity": "concern|good|note|high|medium|low", "text": "string" }
  ]
}
```

**Rendering:** Each item renders as a `.limitation-item` with:
- A numbered circle badge
- `[Severity]` label coloured by CSS class (`.sev-concern`, `.sev-good`, `.sev-note`, `.sev-high`, `.sev-medium`, `.sev-low`)
- Finding text

---

### 5.4 card

A key-value metric grid.

```json
{
  "type":  "card",
  "title": "string or null",
  "items": [
    { "label": "string", "value": 123 },
    { "label": "string", "value": "string" }
  ]
}
```

**Rendering:** `.kv-grid` CSS grid (auto-fill, min 160px per cell). Each item is a `.kv-item` with:
- Label: uppercased, underscores → spaces (via `replace_underscore` filter)
- Value: numeric values formatted with `fmt_num` (commas, no trailing decimal for integers); string values displayed as-is

---

### 5.5 table

A data table with optional headers.

```json
{
  "type":    "table",
  "title":   "string or null",
  "headers": ["column 1", "column 2", "column 3"],
  "rows":    [
    ["value", "value", "value"],
    {"column 1": "value", "column 2": "value", "column 3": "value"}
  ]
}
```

**Rows can be either lists or dicts (even mixed within the same table):**

| Row format | Rendering |
|---|---|
| `list` | Cells rendered in order |
| `dict` | Cells matched to `headers` by key; missing keys render as `""` |

**Special rows:** A row whose first cell is `"OVERALL"`, `"GRAND TOTAL"`, or `"TOTAL"` (case-insensitive) automatically gets the `.total-row` CSS class (bold, navy top border).

**Cell formatting:**
- Numeric values: right-aligned, `fmt_num` filter applied
- String values: `tag_class` filter applied — matching values like "PASS", "FAIL", "WARN" are wrapped in a coloured `.tag` badge

**`headers` field:**
- Present list: used as column order and cell matching key
- `null` / absent: inferred from `rows[0].keys()` if `rows[0]` is a dict; otherwise no header row is rendered

---

### 5.6 chart

An inline chart rendered to SVG by the `charts_node`.

```json
{
  "type":       "chart",
  "chart_type": "radar|grouped_bar|stacked_bar|heatmap",
  "title":      "string",
  "width_px":   500,
  "height_px":  350
}
```

Common fields (`chart_type` independent):

| Field | Default | Notes |
|---|---|---|
| `chart_type` | — | Required for routing to the correct builder |
| `title` | `""` | Displayed above the SVG; also used as alt text |
| `width_px` | `500` | SVG intrinsic width; overridden by CSS to `100%` in print |
| `height_px` | `350` | SVG intrinsic height; overridden to `auto` in print (proportional) |

#### `chart_type: "radar"`

Displays performance across multiple dimensions as a line chart approximating a radar shape.

```json
{
  "type":       "chart",
  "chart_type": "radar",
  "title":      "Capability Dimensions",
  "dimensions": [
    { "dimension": "Task Completion",  "value": 0.92 },
    { "dimension": "Tool Use",         "value": 0.88 },
    { "dimension": "Error Recovery",   "value": 0.71 }
  ]
}
```

`value` must be in **0.0–1.0** range. The polygon is closed (first dimension repeated at end).

#### `chart_type: "grouped_bar"`

Groups of bars for comparing multiple series across the same categories.

```json
{
  "type":       "chart",
  "chart_type": "grouped_bar",
  "title":      "Pass Rate by Fault Category",
  "categories": ["auth", "network", "memory"],
  "y_axis":     "Pass Rate",
  "series": [
    { "name": "Baseline", "values": [0.90, 0.75, 0.82] },
    { "name": "Stressed",  "values": [0.72, 0.60, 0.68] }
  ],
  "reference_lines": [
    { "value": 0.80, "label": "Target" }
  ]
}
```

`values` must have the same length as `categories`. `reference_lines` is optional.

#### `chart_type: "stacked_bar"`

Stacked bars for showing composition across categories.

```json
{
  "type":       "chart",
  "chart_type": "stacked_bar",
  "title":      "Token Distribution",
  "categories": ["planning", "execution", "reflection"],
  "y_axis":     "Token Count",
  "series": [
    { "name": "Input",  "values": [1200, 3400, 800] },
    { "name": "Output", "values": [400,  900,  300] }
  ]
}
```

#### `chart_type: "heatmap"`

Grid of cells coloured by value, with optional display text per cell.

```json
{
  "type":       "chart",
  "chart_type": "heatmap",
  "title":      "Fault Category × Run Outcome",
  "x_labels":   ["Run 1", "Run 2", "Run 3"],
  "y_labels":   ["auth", "network", "memory"],
  "values": [
    [0.90, 0.88, 0.92],
    [0.75, 0.70, 0.78],
    [0.82, 0.85, 0.80]
  ],
  "display_values": [
    ["90%", "88%", "92%"],
    ["75%", "70%", "78%"],
    ["82%", "85%", "80%"]
  ]
}
```

`values` are floats in 0.0–1.0. `display_values` is optional — defaults to the raw value if absent. The colour scale is `"blues"` (white → dark blue); cells with value > 0.6 show white text for contrast.

**Unknown `chart_type`:** Renders an error card showing "Unknown chart type: X" — the pipeline does not crash.

---

### 5.7 assessment

A qualitative evaluation block for a single capability or fault category.

```json
{
  "type":       "assessment",
  "title":      "string — capability or fault category being assessed",
  "rating":     "STRONG|ADEQUATE|WEAK|PASS|FAIL|EXCELLENT|GOOD|POOR|CRITICAL",
  "confidence": "HIGH|MEDIUM|LOW",
  "agreement":  0.85,
  "body":       "string — narrative assessment text, markdown supported"
}
```

| Field | Notes |
|---|---|
| `title` | Shown as the card heading |
| `rating` | Controls the `.assess-*` badge colour (see below) |
| `confidence` | Displayed as metadata beside the rating |
| `agreement` | Optional. Numeric (0.0–1.0): displayed as integer `%`. String: displayed as-is. |
| `body` | Narrative text, markdown supported. Rewritten by LLM enrichment when length ≥ 50 chars. |

**Rating badge CSS classes:**

| Rating values | CSS class | Colour |
|---|---|---|
| `STRONG`, `PASS`, `EXCELLENT`, `GOOD` | `.assess-strong` | Green |
| `WEAK`, `FAIL`, `POOR`, `CRITICAL` | `.assess-weak` | Red |
| Everything else (`ADEQUATE`, unknown) | `.assess-adequate` | Amber |

**Standalone vs grouped:** A standalone `assessment` block renders as a `.fault-card`. When preceded by a `heading` block in the same `content[]` array, it is merged into a `fault_group` at render time (see §7).

---

## 6. footer

Optional top-level string displayed in the report footer.

```json
{ "footer": "Confidential — AgentCert Evaluation — Do not distribute" }
```

When absent or empty, the footer shows: `Document ID: {certification_run_id} | {certification_date} | AgentCert`.

---

## 7. Fault group rendering (display-only)

When a `heading` block in `section.content[]` is immediately followed by one or more `assessment` blocks, the HTML renderer merges them into a single `fault_group` display structure **at render time**. This transformation is not part of the JSON schema — the source JSON always uses plain `heading` and `assessment` blocks.

**Pattern that triggers grouping:**
```json
{ "type": "heading",    "title": "Authentication Fault",   "detail": "..." },
{ "type": "assessment", "title": "Basic Auth Probing",     "rating": "STRONG", ... },
{ "type": "assessment", "title": "Session Fixation",       "rating": "ADEQUATE", ... }
```

**Rendered as a single fault card:**
```
┌─ Authentication Fault ──────────────────────────────────────┐
│  [detail text]                                               │
│                                                              │
│  Basic Auth Probing          ● STRONG    Confidence: HIGH   │
│  [assessment body]                                           │
│                                                              │
│  Session Fixation            ● ADEQUATE  Confidence: MEDIUM │
│  [assessment body]                                           │
└──────────────────────────────────────────────────────────────┘
```

This grouping is purely visual. The JSON structure remains unchanged. Any external consumer of the JSON sees standard `heading` and `assessment` blocks with no special encoding.

---

## 8. Field resilience rules

The pipeline is designed to handle partial or evolving schemas without crashing.

| Scenario | Behaviour |
|---|---|
| Missing top-level key (`meta`, `header`, `sections`, `footer`) | Returns `{}` / `[]` / `""` — no crash |
| Missing `section.content` | Section renders with intro only, no content blocks |
| Unknown `block.type` | Renders as a collapsible `<pre>` containing the raw JSON block |
| Unknown `chart_type` | Renders chart card with error message |
| Missing `block.title` | Renders as empty string |
| Missing `block.body` | Block rendered with no content |
| `rating` / `confidence` as Pydantic Enum (from upstream schema) | Templates detect non-string and call `.value` |
| `assessment.agreement` as float or string | Both handled; float × 100 shown as integer `%` |
| `table.rows` mixing list and dict rows | Both handled in the same table |
| `table.headers` absent | Inferred from first row keys (if dict row) or omitted |

---

## 9. Minimal valid document

The smallest document that produces a non-empty report:

```json
{
  "sections": [
    {
      "id":      "s1",
      "number":  "1",
      "title":   "Summary",
      "content": [
        { "type": "text", "body": "This is the summary." }
      ]
    }
  ]
}
```

All other fields will render with fallback values or be omitted gracefully.

---

## 10. Full example

A realistic document demonstrating all block types:

```json
{
  "meta": {
    "agent_name":               "ARIA-7 Financial Agent",
    "agent_id":                 "aria7-v2.3.1",
    "certification_run_id":     "cert-aria7-2026-03-26-a1b2c3",
    "certification_date":       "2026-03-26",
    "subtitle":                 "Fault Injection & Capability Certification",
    "total_runs":               500,
    "total_faults":             25,
    "total_categories":         5,
    "runs_per_fault_configured": 20,
    "categories":               ["auth", "network", "memory", "data_corruption", "latency"]
  },

  "header": {
    "scorecard": [
      { "dimension": "Task Completion",  "value": 0.94 },
      { "dimension": "Tool Reliability", "value": 0.88 },
      { "dimension": "Error Recovery",   "value": 0.76 },
      { "dimension": "Safety Adherence", "value": 0.92 },
      { "dimension": "Consistency",      "value": 0.85 }
    ],
    "findings": [
      { "severity": "good",    "text": "Passes 94% of task completion scenarios." },
      { "severity": "concern", "text": "Network fault recovery drops to 61% under load." },
      { "severity": "note",    "text": "Memory fault handling is marginal; monitor closely." }
    ]
  },

  "sections": [
    {
      "id":     "overview",
      "number": "1",
      "part":   null,
      "title":  "Executive Overview",
      "intro":  "ARIA-7 demonstrates strong overall performance with **94%** task completion. Recovery under network fault injection requires attention.",
      "content": [
        {
          "type":  "card",
          "title": "Certification Metrics",
          "items": [
            { "label": "total_runs",           "value": 500 },
            { "label": "pass_rate",            "value": "94.0%" },
            { "label": "faults_tested",        "value": 25 },
            { "label": "categories_covered",   "value": 5 }
          ]
        },
        {
          "type":       "chart",
          "chart_type": "radar",
          "title":      "Capability Dimensions",
          "dimensions": [
            { "dimension": "Task Completion",  "value": 0.94 },
            { "dimension": "Tool Reliability", "value": 0.88 },
            { "dimension": "Error Recovery",   "value": 0.76 },
            { "dimension": "Safety Adherence", "value": 0.92 },
            { "dimension": "Consistency",      "value": 0.85 }
          ]
        }
      ]
    },
    {
      "id":     "fault_analysis",
      "number": "2",
      "part":   "Fault Injection Analysis",
      "title":  "Fault Category Analysis",
      "intro":  "Fault injection tests exercised five failure categories across 500 runs.",
      "content": [
        {
          "type":    "table",
          "title":   "Pass Rate by Category",
          "headers": ["Category", "Runs", "Pass Rate", "Status"],
          "rows": [
            ["auth",             100, "95%", "PASS"],
            ["network",          100, "72%", "WARN"],
            ["memory",           100, "78%", "PASS"],
            ["data_corruption",  100, "91%", "PASS"],
            ["latency",          100, "88%", "PASS"],
            ["OVERALL",          500, "85%", "PASS"]
          ]
        },
        {
          "type":       "chart",
          "chart_type": "grouped_bar",
          "title":      "Pass Rate by Category and Fault Intensity",
          "categories": ["auth", "network", "memory", "data_corruption", "latency"],
          "y_axis":     "Pass Rate",
          "series": [
            { "name": "Low intensity",  "values": [0.98, 0.85, 0.88, 0.97, 0.94] },
            { "name": "High intensity", "values": [0.92, 0.59, 0.68, 0.85, 0.82] }
          ],
          "reference_lines": [
            { "value": 0.80, "label": "Target (80%)" }
          ]
        },
        {
          "type": "heading",
          "title": "Authentication Fault",
          "detail": "Tests probing credential handling and session management under fault conditions."
        },
        {
          "type":       "assessment",
          "title":      "Basic Credential Probing",
          "rating":     "STRONG",
          "confidence": "HIGH",
          "agreement":  0.91,
          "body":       "The agent correctly rejects malformed credentials in all tested scenarios. Retry logic is well-bounded."
        },
        {
          "type":       "assessment",
          "title":      "Session Fixation Resistance",
          "rating":     "ADEQUATE",
          "confidence": "MEDIUM",
          "agreement":  0.73,
          "body":       "Session handling is adequate but shows occasional token reuse under rapid re-authentication."
        },
        {
          "type": "heading",
          "title": "Network Fault",
          "detail": "Tests covering packet loss, latency spikes, and connection drops."
        },
        {
          "type":       "assessment",
          "title":      "Packet Loss Recovery",
          "rating":     "WEAK",
          "confidence": "HIGH",
          "agreement":  0.88,
          "body":       "Recovery from sustained >20% packet loss falls below the target threshold. The agent's retry strategy does not use exponential backoff, leading to thundering-herd retry storms."
        }
      ]
    },
    {
      "id":     "safety",
      "number": "3",
      "part":   "Agent Capability Assessment",
      "title":  "Safety and Compliance",
      "intro":  "ARIA-7 was evaluated against safety constraints across all fault scenarios.",
      "content": [
        {
          "type": "findings",
          "items": [
            { "severity": "good",    "text": "No safety constraint violations in 500 runs." },
            { "severity": "note",    "text": "One instance of excessive retry volume under network fault." },
            { "severity": "concern", "text": "Latency degradation under memory fault may cause timeout SLA breach." }
          ]
        },
        {
          "type": "text",
          "body": "Overall safety posture is **strong**. The identified retry volume issue should be addressed before production deployment in high-availability environments."
        },
        {
          "type":       "chart",
          "chart_type": "heatmap",
          "title":      "Safety Metric × Fault Category",
          "x_labels":   ["auth", "network", "memory", "data_corr", "latency"],
          "y_labels":   ["No violations", "SLA compliance", "Retry bounds"],
          "values": [
            [1.00, 1.00, 1.00, 1.00, 1.00],
            [1.00, 0.72, 0.88, 0.95, 0.91],
            [1.00, 0.61, 0.95, 1.00, 0.98]
          ],
          "display_values": [
            ["✓", "✓", "✓", "✓", "✓"],
            ["100%", "72%", "88%", "95%", "91%"],
            ["100%", "61%", "95%", "100%", "98%"]
          ]
        }
      ]
    }
  ],

  "footer": "Confidential — AgentCert Evaluation — cert-aria7-2026-03-26"
}
```
