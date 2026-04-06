# Phase 2F: Card Definitions & Data Logic

## Overview

Phase 2F builds 3 card data structures from Phase 1 metadata. Cards are simple key-value display elements for the Executive Summary section.

## Dependency

```
phase1_parsed_context.json -> meta  ──►  all 3 cards
```

---

## Card 1: identity_card

**Title**: "Agent Identity"
**Source**: `meta`

| Item Label           | Source Field                | Fallback       |
|----------------------|-----------------------------|----------------|
| Agent Name           | `meta.agent_name`           | `"—"`          |
| Agent ID             | `meta.agent_id`             | `"—"`          |
| Certification Run    | `meta.certification_run_id` | `"—"` if empty |
| Certification Date   | `meta.certification_date`   | `"—"`          |

**Note**: Empty string `""` for `certification_run_id` is treated as missing and displayed as em-dash (`—`).

---

## Card 2: scope_card

**Title**: "Evaluation Scope"
**Source**: `meta`

| Item Label        | Source Field                  | Fallback |
|-------------------|-------------------------------|----------|
| Fault Categories  | `meta.total_fault_categories` | `0`      |
| Faults Tested     | `meta.total_faults_tested`    | `0`      |
| Total Runs        | `meta.total_runs`             | `0`      |

Values are integers (not strings).

---

## Card 3: categories_card

**Title**: "Fault Categories Tested"
**Source**: `meta.categories_summary`

For each entry in `categories_summary`:

| Item Label             | Item Value                      |
|------------------------|---------------------------------|
| `"{name} Fault"`       | `"{fault} ({runs} runs)"`       |

Example output:
- `"Application Fault"` → `"container-kill (5 runs)"`
- `"Network Fault"` → `"pod-dns-error (5 runs)"`
- `"Resource Fault"` → `"disk-fill (5 runs)"`

---

## None Handling

All fields use `.get()` with sensible defaults:
- String fields default to em-dash `"—"`
- Integer fields default to `0`
- Empty `certification_run_id` (`""`) is treated as missing
- Missing `categories_summary` produces a card with an empty `items` list
