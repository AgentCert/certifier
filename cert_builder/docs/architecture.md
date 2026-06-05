# AgentCert Certification Framework — Migration Architecture

## Objective

Migrate the certification framework from the current `engine/` structure (with deeply nested `phase2a/`, `phase3b/`, etc. sub-folders) into a **flat, unified `scripts/` codebase** where each phase has a semantically named folder with all its builder scripts at the top level — no sub-folder nesting.

---

## Upstream Data Quality and Pipeline Dependencies

The certification framework depends on **high-quality upstream data** from the fault bucketing and detection pipeline (Phase 0 and Phase 1 of the full AgentCert pipeline). Understanding these quality guarantees is critical for interpreting certification results.

### Fault Bucketing Quality Baseline

The input data (`aggregated_scorecard_output.json`) is derived from fault-bucketed traces with the following validated quality metrics:

| Metric | Value | Validation Method |
|--------|-------|-------------------|
| **Exact + Partial Match Accuracy** | 93.7% | Manual ground truth labeling (50 fault injections) |
| **Bucketing Precision** | 96.4% | Events correctly classified into fault buckets |
| **Bucketing Recall** | 66.2% | Proportion of actual fault events detected |
| **Detection Timestamp Precision** | Millisecond-level | Conservative high-precision detection logic |

### Conservative Behavior Design

**Precision-Recall Trade-off**: The fault bucketing pipeline is intentionally designed with **high precision (96.4%)** at the cost of **moderate recall (66.2%)**. This conservative approach is appropriate for safety-critical AI agent certification because:

- **False positives are costly**: Incorrectly attributing events to the wrong fault category would corrupt certification metrics
- **High confidence required**: Only events with strong evidence are classified; ambiguous events are excluded
- **Safety-first philosophy**: Under-reporting is preferable to mis-reporting in safety-critical systems

### Implications for Certification Reports

**What this means for certification consumers**:

1. **Metrics are high-confidence**: Reported detection rates, TTD/TTM values, and fault analysis are based on reliably bucketed events (96.4% precision)
2. **Conservative estimates**: Actual agent performance may be slightly better than reported due to the 66.2% recall — some successful detections may not be captured
3. **Known limitation**: ~34% of fault lifecycle events may not be captured by the bucketing pipeline — this is documented in Section 11 (Limitations) of every certification report
4. **Ground truth validated**: All bucketing methodology has been validated against manually labeled ground truth data

### Detection Methodology Improvements

Recent enhancements to the fault detection pipeline include:

- **Context injection**: Full fault bucket context provided to detection logic for improved accuracy
- **Token optimization**: Structured fault context reduces token usage while improving precision
- **Timestamp precision**: Millisecond-precision timestamps for accurate TTD/TTM calculations
- **Ground truth validation**: All detection logic validated against manually labeled fault injection runs

**Reference**: See `docs/Methodologies/06-Observations/6.4-Fault-Bucketing-And-Detection-Findings.md` for detailed fault bucketing quality analysis.

---

## Migration Principles

1. **Builder logic stays untouched** — the Python code inside each builder file (`scorecard_builder.py`, `key_findings_builder.py`, etc.) does not change
2. **Flatten sub-phases** — `phase2a/`, `phase2b/`, … become individual files inside one `computation/` folder
3. **Semantic folder names** — `ingestion/` not `phase1/`, `computation/` not `phase2/`, `narratives/` not `phase3/`
4. **Consolidate shared assets** — prompts, configs, docs, data move into top-level folders (not scattered per sub-phase)
5. **OOP at two levels only** — phase assembler classes + one pipeline orchestrator class. Builder scripts stay as simple functions.
6. **Single entry point** — `CertificationPipeline` class takes `input_path`, `output_path`, `debug` and runs everything

---

## Target Folder Structure

```
certification_framework/
│
├── scripts/
│   │
│   ├── ingestion/                              # ── Phase 1: Parse & Validate ──
│   │   ├── __init__.py
│   │   └── ingestor.py                         #   ScorecardIngestor (unchanged logic)
│   │                                           #   Parses raw JSON → ParsedContext
│   │
│   ├── computation/                            # ── Phase 2: Deterministic Builders ──
│   │   ├── __init__.py
│   │   ├── assembler.py                        #   ComputationAssembler (class)
│   │   │                                       #     Calls all 6 builders, merges outputs
│   │   ├── scorecard_builder.py                #   was: phase2a/scorecard_builder.py
│   │   ├── table_builder.py                    #   was: phase2b/table_builder.py
│   │   ├── chart_builder.py                    #   was: phase2c/chart_builder.py
│   │   ├── chart_renderer.py                   #   was: phase2c/chart_renderer.py
│   │   ├── assessment_formatter.py             #   was: phase2d/assessment_formatter.py
│   │   ├── hardcoded_loader.py                 #   was: phase2e/hardcoded_loader.py
│   │   └── card_builder.py                     #   was: phase2f/card_builder.py
│   │
│   ├── narratives/                             # ── Phase 3: LLM Generation ──
│   │   ├── __init__.py
│   │   ├── assembler.py                        #   NarrativeAssembler (class)
│   │   │                                       #     Runs 6 concurrent LLM calls + 1 sequential
│   │   ├── llm_client.py                       #   Shared Azure OpenAI client + call_llm()
│   │   ├── scope_narrative_builder.py          #   Call 1 (concurrent)
│   │   ├── key_findings_builder.py             #   Call 2 (concurrent)
│   │   ├── qualitative_builder.py              #   Call 3 (concurrent)
│   │   ├── fault_analysis_builder.py           #   Call 4 (concurrent)
│   │   ├── limitation_builder.py               #   Call 5 (concurrent)
│   │   ├── fairness_builder.py                 #   Call 6 (concurrent) — Fairness/consistency score
│   │   ├── recommendation_builder.py           #   Call 7 (sequential, depends on limitations)
│   │   ├── hypothesis_overlay_builder.py       #   Invoked by ReportAssembler for §5–§10 strips
│   │   └── sample_size_notice_builder.py       #   Invoked by ReportAssembler for §1 sample-size notice
│   │
│   └── report_assembler.py                     #   ReportAssembler (class) — Phase 4
│                                               #     Merges Phase 1+2+3 → 12-section report
│                                               #     Validates against CertificationReport schema
│   │
│   └── certification_pipeline.py               # ── Main Orchestrator ──
│                                               #   CertificationPipeline (class)
│                                               #     Entry point: input_path, output_path, debug
│                                               #     Chains: ingestion → computation → narratives → assembly
│
├── schema/                                     # ── Pydantic Report Schema ──
│   ├── __init__.py                             #   Re-exports all models/enums
│   └── certification_schema.py                 #   CertificationReport, Meta, Header, Section,
│                                               #   ContentBlock (10 block types), enums
│                                               #   NOTE: intermediate models stay in builder files
│
├── config/                                     # ── All Configuration (consolidated) ──
│   ├── scorecard_config.yaml                   #   was: phase2a/scorecard_config.yaml
│   ├── table_config.yaml                       #   was: phase2b/table_config.yaml
│   ├── chart_config.yaml                       #   was: phase2c/chart_config.yaml
│   └── hardcoded_content.yaml                  #   was: phase2e/hardcoded_content.yaml
│
├── prompts/                                    # ── All LLM Prompt Templates (consolidated) ──
│   ├── scope_narrative_prompt.yaml             #   was: phase3a/prompt_config.yaml
│   ├── key_findings_prompt.yaml                #   was: phase3b/prompt_config.yaml
│   ├── qualitative_prompt.yaml                 #   was: phase3c/prompt_config.yaml
│   ├── fault_analysis_prompt.yaml              #   was: phase3d/prompt_config.yaml
│   ├── limitation_prompt.yaml                  #   was: phase3e/prompt_config.yaml
│   └── recommendation_prompt.yaml              #   was: phase3f/prompt_config.yaml
│
├── data/                                       # ── Input, Intermediate & Final Output ──
│   ├── input/
│   │   └── aggregated_scorecard_output.json    #   Raw input (source of truth)
│   ├── ingestion/                              #   Phase 1 output (when debug=True)
│   │   └── parsed_context.json
│   ├── computation/                            #   Phase 2 output (when debug=True)
│   │   └── computed_content.json
│   ├── narratives/                             #   Phase 3 output (when debug=True)
│   │   ├── narratives.json                     #     Combined narrative output
│   │   ├── scope_narrative.json                #     Individual builder outputs
│   │   ├── key_findings.json
│   │   ├── qualitative_findings.json
│   │   ├── fault_analysis.json
│   │   ├── limitations.json
│   │   └── recommendations.json
│   └── output/
│       └── certification_report.json           #   Final validated report (always written)
│
├── docs/                                       # ── All Documentation (consolidated) ──
│   ├── architecture.md                         #   ← This file
│   ├── metric_calculation_logic.md             #   was: phase2a/docs/
│   ├── table_definitions.md                    #   was: phase2b/docs/
│   ├── chart_definitions.md                    #   was: phase2c/docs/
│   ├── assessment_definitions.md               #   was: phase2d/docs/
│   ├── content_definitions.md                  #   was: phase2e/docs/
│   ├── card_definitions.md                     #   was: phase2f/docs/
│   ├── phase3_requirements.md                  #   was: phase3/docs/
│   ├── scope_narrative_requirements.md         #   was: phase3a/docs/
│   ├── key_findings_requirements.md            #   was: phase3b/docs/
│   ├── qualitative_requirements.md             #   was: phase3c/docs/
│   ├── fault_analysis_requirements.md          #   was: phase3d/docs/
│   ├── limitation_requirements.md              #   was: phase3e/docs/
│   └── recommendation_requirements.md          #   was: phase3f/docs/
│
├── notebooks/                                  # ── Jupyter Notebooks ──
│   ├── run_pipeline.ipynb                      #   Full end-to-end pipeline
│   ├── run_ingestion.ipynb                     #   Phase 1 only
│   ├── run_computation.ipynb                   #   Phase 2 assembled (all 6 builders)
│   ├── run_scorecard.ipynb                     #   Phase 2 — scorecard builder only
│   ├── run_tables.ipynb                        #   Phase 2 — table builder only
│   ├── run_charts.ipynb                        #   Phase 2 — chart builder only
│   ├── run_assessments.ipynb                   #   Phase 2 — assessment formatter only
│   ├── run_hardcoded.ipynb                     #   Phase 2 — hardcoded loader only
│   ├── run_cards.ipynb                         #   Phase 2 — card builder only
│   ├── run_narratives.ipynb                    #   Phase 3 assembled (all 6 LLM calls)
│   └── run_assembly.ipynb                      #   Phase 4 report assembly
│
├── tests/                                      # ── Tests ──
│   ├── __init__.py
│   ├── test_ingestion.py
│   ├── test_computation.py
│   ├── test_narratives.py
│   ├── test_assembly.py
│   └── test_pipeline_e2e.py
│
├── temp/                                       # ── Temporary artifacts ──
│   └── charts/                                 #   Rendered chart PNGs
│
├── .env                                        #   Azure OpenAI credentials
│                                               #   (moved from engine/phase3/.env)
└── requirements.txt
```

---

## File Migration Map

### Phase 1 — Ingestion

| Old Path (engine/) | New Path (scripts/) | Changes |
|---------------------|---------------------|---------|
| `phase1/ingestor.py` | `ingestion/ingestor.py` | None — logic unchanged |

### Phase 2 — Computation (flatten 6 sub-folders into 1 folder)

| Old Path (engine/) | New Path (scripts/) | Changes |
|---------------------|---------------------|---------|
| `phase2/assembler.py` | `computation/assembler.py` | Update imports; wrap in `ComputationAssembler` class |
| `phase2/phase2a/scorecard_builder.py` | `computation/scorecard_builder.py` | Update config path to `config/scorecard_config.yaml` |
| `phase2/phase2b/table_builder.py` | `computation/table_builder.py` | Update config path to `config/table_config.yaml` |
| `phase2/phase2c/chart_builder.py` | `computation/chart_builder.py` | Update config path to `config/chart_config.yaml` |
| `phase2/phase2c/chart_renderer.py` | `computation/chart_renderer.py` | None |
| `phase2/phase2d/assessment_formatter.py` | `computation/assessment_formatter.py` | None |
| `phase2/phase2e/hardcoded_loader.py` | `computation/hardcoded_loader.py` | Update content path to `config/hardcoded_content.yaml` |
| `phase2/phase2f/card_builder.py` | `computation/card_builder.py` | None |

### Phase 3 — Narratives (flatten 6 sub-folders into 1 folder)

| Old Path (engine/) | New Path (scripts/) | Changes |
|---------------------|---------------------|---------|
| `phase3/assembler.py` | `narratives/assembler.py` | Update imports; wrap in `NarrativeAssembler` class |
| `phase3/llm_client.py` | `narratives/llm_client.py` | Update `.env` path to root `.env` |
| `phase3/phase3a/scope_narrative_builder.py` | `narratives/scope_narrative_builder.py` | Update prompt path to `prompts/scope_narrative_prompt.yaml` |
| `phase3/phase3b/key_findings_builder.py` | `narratives/key_findings_builder.py` | Update prompt path to `prompts/key_findings_prompt.yaml` |
| `phase3/phase3c/qualitative_builder.py` | `narratives/qualitative_builder.py` | Update prompt path to `prompts/qualitative_prompt.yaml` |
| `phase3/phase3d/fault_analysis_builder.py` | `narratives/fault_analysis_builder.py` | Update prompt path to `prompts/fault_analysis_prompt.yaml` |
| `phase3/phase3e/limitation_builder.py` | `narratives/limitation_builder.py` | Update prompt path to `prompts/limitation_prompt.yaml` |
| `phase3/phase3f/recommendation_builder.py` | `narratives/recommendation_builder.py` | Update prompt path to `prompts/recommendation_prompt.yaml` |

### Phase 4 — Assembly

| Old Path (engine/) | New Path (scripts/) | Changes |
|---------------------|---------------------|---------|
| `phase4/assembler.py` | `assembly/report_assembler.py` | Update imports; wrap in `ReportAssembler` class |

### Config & Prompts (consolidate from sub-folders)

| Old Path (engine/) | New Path (top-level) |
|---------------------|----------------------|
| `phase2/phase2a/scorecard_config.yaml` | `config/scorecard_config.yaml` |
| `phase2/phase2b/table_config.yaml` | `config/table_config.yaml` |
| `phase2/phase2c/chart_config.yaml` | `config/chart_config.yaml` |
| `phase2/phase2e/hardcoded_content.yaml` | `config/hardcoded_content.yaml` |
| `phase3/phase3a/prompt_config.yaml` | `prompts/scope_narrative_prompt.yaml` |
| `phase3/phase3b/prompt_config.yaml` | `prompts/key_findings_prompt.yaml` |
| `phase3/phase3c/prompt_config.yaml` | `prompts/qualitative_prompt.yaml` |
| `phase3/phase3d/prompt_config.yaml` | `prompts/fault_analysis_prompt.yaml` |
| `phase3/phase3e/prompt_config.yaml` | `prompts/limitation_prompt.yaml` |
| `phase3/phase3f/prompt_config.yaml` | `prompts/recommendation_prompt.yaml` |
| `phase3/.env` | `.env` (root) |

### Docs (consolidate from per-sub-phase docs/ folders)

| Old Path | New Path |
|----------|----------|
| `phase2/phase2a/docs/metric_calculation_logic.md` | `docs/metric_calculation_logic.md` |
| `phase2/phase2b/docs/table_definitions.md` | `docs/table_definitions.md` |
| `phase2/phase2c/docs/chart_definitions.md` | `docs/chart_definitions.md` |
| `phase2/phase2d/docs/assessment_definitions.md` | `docs/assessment_definitions.md` |
| `phase2/phase2e/docs/content_definitions.md` | `docs/content_definitions.md` |
| `phase2/phase2f/docs/card_definitions.md` | `docs/card_definitions.md` |
| `phase3/docs/phase3_requirements.md` | `docs/phase3_requirements.md` |
| `phase3/phase3a/docs/scope_narrative_requirements.md` | `docs/scope_narrative_requirements.md` |
| `phase3/phase3b/docs/key_findings_requirements.md` | `docs/key_findings_requirements.md` |
| `phase3/phase3c/docs/qualitative_requirements.md` | `docs/qualitative_requirements.md` |
| `phase3/phase3d/docs/fault_analysis_requirements.md` | `docs/fault_analysis_requirements.md` |
| `phase3/phase3e/docs/limitation_requirements.md` | `docs/limitation_requirements.md` |
| `phase3/phase3f/docs/recommendation_requirements.md` | `docs/recommendation_requirements.md` |

---

## OOP Design (Two Levels Only)

### Level 1 — Phase Assemblers

Each multi-step phase gets an assembler **class** that calls its builders and merges outputs.

#### `ComputationAssembler` (`scripts/computation/assembler.py`)

```python
class ComputationAssembler:
    """Assembles all Phase 2 computation outputs into one dict."""

    def __init__(self, parsed_context: dict, debug: bool = False):
        self.parsed_context = parsed_context
        self.debug = debug

    def assemble(self) -> dict:
        """Run all 6 builders, merge results, return combined output."""
        scorecard, findings = build_scorecard(self.parsed_context)    # scorecard_builder.py
        tables             = build_tables(self.parsed_context)        # table_builder.py
        charts             = build_charts(self.parsed_context, scorecard)  # chart_builder.py (needs scorecard)
        assessments        = format_assessments(self.parsed_context)  # assessment_formatter.py
        hardcoded          = load_hardcoded_content()                 # hardcoded_loader.py
        cards              = build_cards(self.parsed_context)         # card_builder.py

        result = {
            "scorecard": scorecard,
            "findings": findings,
            "tables": tables,
            "charts": charts,
            "assessments": assessments,
            "hardcoded": hardcoded,
            "cards": cards,
        }

        if self.debug:
            save_json(result, "data/computation/computed_content.json")

        return result
```

#### `NarrativeAssembler` (`scripts/narratives/assembler.py`)

```python
class NarrativeAssembler:
    """Assembles all Phase 3 LLM narrative outputs into one dict."""

    def __init__(self, parsed_context: dict, computed_content: dict, debug: bool = False):
        self.parsed_context = parsed_context
        self.computed_content = computed_content
        self.debug = debug
        self.llm_client = LLMClient()

    async def assemble(self) -> dict:
        """Run 7 LLM calls (Calls 1-6 concurrent, Call 7 sequential), merge results."""

        # Calls 1-6: run concurrently via asyncio.gather + asyncio.to_thread
        outcomes = await asyncio.gather(
            _safe_call("scope_narrative", build_scope_narrative, phase1),
            _safe_call("key_findings",    build_key_findings,    phase1, phase2),
            _safe_call("qualitative",     build_qualitative_findings, phase1, phase2),
            _safe_call("fault_analysis",  build_fault_analysis,  phase1, phase2),
            _safe_call("limitations",     build_limitations,     phase1, phase2),
            _safe_call("fairness_score",  build_fairness_score,  phase1, phase2),
        )

        # Call 7: sequential — depends on limitations_enriched from Call 5
        recommendations = await _safe_call(
            "recommendations", build_recommendations,
            phase1, phase2, limitations_enriched,
        )

        result = {
            "scope_narrative": scope,
            "key_findings": findings,
            "qualitative_findings": qualitative,
            "fault_analysis": fault_analysis,
            "limitations": limitations,
            "recommendations": recommendations,
        }

        if self.debug:
            save_json(result, "data/narratives/narratives.json")
            # also save individual outputs
            save_json(scope, "data/narratives/scope_narrative.json")
            save_json(findings, "data/narratives/key_findings.json")
            # ... etc.

        return result
```

#### `ReportAssembler` (`scripts/assembly/report_assembler.py`)

```python
class ReportAssembler:
    """Assembles Phase 1+2+3 outputs into the final 12-section CertificationReport."""

    def __init__(self, parsed_context: dict, computed_content: dict, narratives: dict, debug: bool = False):
        self.parsed_context = parsed_context
        self.computed_content = computed_content
        self.narratives = narratives
        self.debug = debug

    def assemble(self) -> dict:
        """Build meta, header, 12 sections, footer. Validate against schema. Return dict."""
        report = {
            "meta": self._build_meta(),
            "header": self._build_header(),
            "sections": self._build_sections(),
        }

        # Validate through Pydantic
        validated = CertificationReport(**report)

        if self.debug:
            save_json(validated.model_dump(), "data/output/certification_report.json")

        return validated.model_dump()
```

### Level 2 — Pipeline Orchestrator

One top-level class that chains the four phases. This is the **only entry point**.

#### `CertificationPipeline` (`scripts/certification_pipeline.py`)

```python
class CertificationPipeline:
    """
    Main entry point for certification report generation.
    Chains: ingestion → computation → narratives → assembly.
    """

    def __init__(self, input_path: str, output_path: str, debug: bool = False):
        self.input_path = input_path
        self.output_path = output_path
        self.debug = debug

    async def run(self) -> dict:
        """Execute the full 4-phase pipeline."""

        # Phase 1 — Ingestion
        parsed_context = ingest(self.input_path)              # ingestor.py function
        if self.debug:
            save_json(parsed_context, "data/ingestion/parsed_context.json")

        # Phase 2 — Computation
        computation = ComputationAssembler(parsed_context, debug=self.debug)
        computed_content = computation.assemble()

        # Phase 3 — Narratives
        narrative = NarrativeAssembler(parsed_context, computed_content, debug=self.debug)
        narratives = await narrative.assemble()

        # Phase 4 — Assembly
        assembly = ReportAssembler(parsed_context, computed_content, narratives, debug=self.debug)
        report = assembly.assemble()

        # Write final output (always, regardless of debug)
        save_json(report, self.output_path)

        return report
```

**Usage**:
```python
pipeline = CertificationPipeline(
    input_path="data/input/aggregated_scorecard_output.json",
    output_path="data/output/certification_report.json",
    debug=True
)
report = await pipeline.run()
```

---

## Builder Scripts — No OOP

Builder scripts are **plain functions**, not classes. Each file exports one `build_*()` / `format_*()` / `load_*()` function. The assembler calls them directly.

| File | Entry Function | Signature |
|------|---------------|-----------|
| `scorecard_builder.py` | `build_scorecard(parsed_context) → (scorecard, findings)` | Returns tuple |
| `table_builder.py` | `build_tables(parsed_context) → dict` | Returns 13-table dict |
| `chart_builder.py` | `build_charts(parsed_context, scorecard) → dict` | Returns 9-chart dict |
| `chart_renderer.py` | `render_chart(chart_data, output_path)` | Side-effect: writes PNG |
| `assessment_formatter.py` | `format_assessments(parsed_context) → list` | Returns assessment blocks |
| `hardcoded_loader.py` | `load_hardcoded_content() → dict` | Returns static content |
| `card_builder.py` | `build_cards(parsed_context) → list` | Returns 3 cards |
| `scope_narrative_builder.py` | `build_scope_narrative(phase1) → dict` | Call 1 — concurrent, no LLM client arg (built-in via `get_client()`) |
| `key_findings_builder.py` | `build_key_findings(phase1, phase2) → dict` | Call 2 — concurrent |
| `qualitative_builder.py` | `build_qualitative_findings(phase1, phase2) → dict` | Call 3 — concurrent |
| `fault_analysis_builder.py` | `build_fault_analysis(phase1, phase2) → dict` | Call 4 — concurrent |
| `limitation_builder.py` | `build_limitations(phase1, phase2) → dict` | Call 5 — concurrent |
| `fairness_builder.py` | `build_fairness_score(phase1, phase2) → dict` | Call 6 — concurrent; deterministic-from-hypothesis / LLM / fallback score resolution |
| `recommendation_builder.py` | `build_recommendations(phase1, phase2, limitations_enriched) → dict` | Call 7 — sequential after Call 5 |
| `hypothesis_overlay_builder.py` | `build_hypothesis_overlay(phase1)` | Called by `ReportAssembler`, not the narrative assembler; emits §5–§10 hypothesis strips |
| `sample_size_notice_builder.py` | `build_sample_size_notice(phase1)` | Called by `ReportAssembler`; emits §1 NoticeBlock when sample size below framework minimum |

---

## Data Flow

```
data/input/aggregated_scorecard_output.json
         │
    ┌────▼──────────────────────────────────────────────────────┐
    │  Phase 1: ingest()                                        │
    │  scripts/ingestion/ingestor.py                            │
    └────┬──────────────────────────────────────────────────────┘
         │  → data/ingestion/parsed_context.json  (debug)
         │
    ┌────▼──────────────────────────────────────────────────────┐
    │  Phase 2: ComputationAssembler.assemble()                 │
    │  scripts/computation/assembler.py                         │
    │  ├─ scorecard_builder   → scorecard + findings            │
    │  ├─ table_builder       → 13 tables                       │
    │  ├─ chart_builder       → 9 charts (needs scorecard)      │
    │  ├─ assessment_formatter → per-category assessments       │
    │  ├─ hardcoded_loader    → static YAML content             │
    │  └─ card_builder        → 3 executive cards               │
    └────┬──────────────────────────────────────────────────────┘
         │  → data/computation/computed_content.json  (debug)
         │
    ┌────▼──────────────────────────────────────────────────────┐
    │  Phase 3: NarrativeAssembler.assemble()                   │
    │  scripts/narratives/assembler.py                          │
    │  ├─ scope_narrative_builder     → scope paragraph         │  ┐
    │  ├─ key_findings_builder        → 5-7 findings            │  │
    │  ├─ qualitative_builder         → 7-dim findings          │  │ concurrent
    │  ├─ fault_analysis_builder      → per-cat analysis        │  │ (asyncio.gather
    │  ├─ limitation_builder          → enriched limitations    │  │  + asyncio.to_thread)
    │  └─ fairness_builder            → fairness/consistency    │  ┘
    │  └─ recommendation_builder      → enriched recs           │  sequential (after limitations)
    │  (token totals aggregated into phase_3_tokens)            │
    └────┬──────────────────────────────────────────────────────┘
         │  → narratives.json  (debug → data/intermediate/)
         │
    ┌────▼──────────────────────────────────────────────────────┐
    │  Phase 4: ReportAssembler.assemble()                      │
    │  scripts/report_assembler.py                              │
    │  Merge all → 12 sections → validate CertificationReport   │
    │  Also invokes:                                            │
    │    - build_sample_size_notice() for §1                    │
    │    - build_hypothesis_overlay() for §5–§10 strips         │
    └────┬──────────────────────────────────────────────────────┘
         │
    data/output/certification_report.json  (always written)
```

---

## Notebook Mapping

Each notebook calls the relevant assembler or builder directly.

| Notebook | What It Runs | Old Equivalent |
|----------|-------------|----------------|
| `run_pipeline.ipynb` | `CertificationPipeline.run()` | (new) |
| `run_ingestion.ipynb` | `ingest()` | `phase1_ingest_validate.ipynb` |
| `run_computation.ipynb` | `ComputationAssembler.assemble()` | `phase2_assembled.ipynb` |
| `run_scorecard.ipynb` | `build_scorecard()` | `phase2a_scorecard_findings.ipynb` |
| `run_tables.ipynb` | `build_tables()` | `phase2b_tables.ipynb` |
| `run_charts.ipynb` | `build_charts()` | `phase2c_charts.ipynb` |
| `run_assessments.ipynb` | `format_assessments()` | `phase2d_assessments.ipynb` |
| `run_hardcoded.ipynb` | `load_hardcoded_content()` | `phase2e_hardcoded.ipynb` |
| `run_cards.ipynb` | `build_cards()` | `phase2f_cards.ipynb` |
| `run_narratives.ipynb` | `NarrativeAssembler.assemble()` | `phase3_assembled.ipynb` |
| `run_assembly.ipynb` | `ReportAssembler.assemble()` | `phase4_assembled.ipynb` |

---

## Debug Mode Behavior

When `debug=True`:
- Phase 1 writes `data/ingestion/parsed_context.json`
- Phase 2 writes `data/computation/computed_content.json`
- Phase 3 writes `data/narratives/narratives.json` + 6 individual builder outputs
- Phase 4 writes to the `output_path` (this always happens regardless of debug)

When `debug=False`:
- Only the final `data/output/certification_report.json` is written
- No intermediate files

---

## Dependency Graph

```
Phase 1: ingest()
    │
    ▼
Phase 2: ComputationAssembler
    │
    ├── scorecard_builder ──→ chart_builder  (charts need scorecard dimensions)
    ├── table_builder       (independent)
    ├── assessment_formatter (independent)
    ├── hardcoded_loader    (independent)
    └── card_builder        (independent)
    │
    ▼
Phase 3: NarrativeAssembler
    │
    ├── scope_narrative_builder   ─┐
    ├── key_findings_builder      ─┤
    ├── qualitative_builder       ─┼── concurrent (asyncio.gather)
    ├── fault_analysis_builder    ─┤
    ├── limitation_builder        ─┤
    └── fairness_builder          ─┘
            │
            ▼
    recommendation_builder         ── sequential (consumes limitations_enriched)
    │
    ▼
Phase 4: ReportAssembler (also invokes hypothesis_overlay + sample_size_notice)
```

---

## Architectural Rules

1. **Builder scripts = plain functions** — no classes, no inheritance, no base classes
2. **Assemblers = simple classes** — constructor takes inputs + debug flag, one `assemble()` method
3. **Pipeline = one class** — `CertificationPipeline(input_path, output_path, debug)` with one `run()` method
4. **No shared state** — each phase receives explicit inputs, returns explicit outputs; no global singletons
5. **Schema boundary** — only certified report types in `schema/`; intermediate Pydantic models stay in their builder file
6. **Config locality** — all YAML configs in `config/`, all prompts in `prompts/`, all docs in `docs/`
7. **Imports stay flat** — `from scripts.computation.scorecard_builder import build_scorecard` (no nested sub-packages)
