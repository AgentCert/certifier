# AgentCert Certifier — Documentation

AgentCert is an AI Agent Benchmarking Platform for Chaos Engineering. The **certifier** is the analytical backbone: it consumes raw Langfuse traces produced when an AI agent operates on a Kubernetes cluster under fault injection, and produces comprehensive, evidence-based certification reports.

---

## What the certifier does

1. Takes a raw Langfuse trace (a JSON file recording every LLM call, tool call, and observation the agent made during a chaos experiment).
2. Sorts and classifies events into per-fault buckets.
3. Extracts quantitative and qualitative metrics from each bucket.
4. Aggregates metrics across many runs into a scorecard.
5. Generates a structured, 12-section certification report.

---

## Documentation Index

| File | What it covers |
|---|---|
| [architecture.md](architecture.md) | System components, data-flow diagram, technology stack, component interactions |
| [pipeline.md](pipeline.md) | Detailed walkthrough of all four pipeline phases (0 → 3) |
| [api.md](api.md) | REST API reference (both endpoints, request/response schemas, error codes) |
| [data-models.md](data-models.md) | All Pydantic/dataclass models across every module |
| [configuration.md](configuration.md) | Every configuration file, all keys, environment variable resolution |
| [deployment.md](deployment.md) | Prerequisites, environment setup, running the pipelines |

---

## Quick orientation — directory layout

```
certifier/
├── fault_analyzer/          # Phase 0 — bucket raw trace events by fault
├── metrics_extractor/       # Phase 1 — extract metrics from each bucket
├── aggregator/              # Phase 2 — aggregate per-run metrics into a scorecard
├── cert_builder/            # Phase 3 — build the final certification report
├── mock_trace_generator/    # Test-data utilities
├── utils/                   # Shared clients, config loader, logging
├── configs/                 # Global configuration (Azure OpenAI, MongoDB)
├── run_bucketing_and_extraction_pipeline.py       # CLI: phases 0 + 1
├── run_aggregation_and_certification_pipeline.py  # CLI: phases 2 + 3
├── api-spec.md              # REST API specification (source of truth)
└── docs/                    # ← you are here
```

---

## End-to-end in one picture

```
Raw Langfuse Trace (JSON)
        │
        ▼
┌─────────────────────┐
│  Phase 0            │  fault_analyzer/
│  Fault Bucketing    │  LLM classifies every event into per-fault buckets
└────────┬────────────┘
         │  per-fault bucket JSON files
         ▼
┌─────────────────────┐
│  Phase 1            │  metrics_extractor/
│  Metrics Extraction │  LLM + code extract quantitative & qualitative metrics
└────────┬────────────┘
         │  per-run *_metrics.json files (+ optional MongoDB)
         ▼
┌─────────────────────┐
│  Phase 2            │  aggregator/
│  Aggregation        │  Pure-function numeric agg + LLM Council text synthesis
└────────┬────────────┘
         │  CertificationScorecard JSON
         ▼
┌─────────────────────┐
│  Phase 3            │  cert_builder/
│  Certification      │  6 deterministic builders + 6 LLM narrative builders
└────────┬────────────┘
         │
         ▼
  CertificationReport (12 sections, JSON)
```

---

## Running the pipelines

**Phases 0 + 1** (trace → per-fault metrics):

```bash
python run_bucketing_and_extraction_pipeline.py \
    --trace-file  path/to/trace.json \
    --output-dir  ./output/run-001
```

**Phases 2 + 3** (metrics → certification report):

```bash
python run_aggregation_and_certification_pipeline.py \
    --metrics-dir  ./output/run-001/metrics \
    --output-dir   ./output/report-001 \
    --agent-id     agent-001 \
    --agent-name   "My SRE Agent"
```

Or use the REST API — see [api.md](api.md).
