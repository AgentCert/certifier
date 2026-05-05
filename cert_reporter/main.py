#!/usr/bin/env python3
"""
cert-reporter — Convert a certification JSON document to HTML and PDF.

Usage:
    python main.py --input certification_document[97].json --output-dir ./output
    python main.py --input cert.json --output-dir ./out --format html
    python main.py --input cert.json --output-dir ./out --enrich-llm --model gpt-4.1-mini
    python main.py --input cert.json --output-dir ./out --provider anthropic --model claude-3-5-haiku-20241022
    python main.py --input cert.json --output-dir ./out --mode agentic --enrich-llm
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-7s  %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="cert-reporter",
        description="Convert a certification JSON document to HTML and/or PDF report.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--input", "-i", required=True,
        help="Path to the certification JSON file.",
    )
    parser.add_argument(
        "--output-dir", "-o", default="./output",
        help="Directory to write output files (default: ./output).",
    )
    parser.add_argument(
        "--format", "-f", default="html,pdf",
        help="Comma-separated list of output formats: html,pdf (default: html,pdf).",
    )
    parser.add_argument(
        "--mode", default="static", choices=["static", "agentic"],
        help=(
            "Pipeline mode: 'static' uses the original schema-driven pipeline "
            "(default); 'agentic' uses LLM-driven inspect→plan→write fan-out "
            "that adapts to any domain without code changes."
        ),
    )
    parser.add_argument(
        "--enrich-llm", action="store_true", default=False,
        help="Use an LLM to enrich narrative prose (requires API key env var).",
    )
    parser.add_argument(
        "--model", default="gpt-4.1-mini",
        help="LLM model name for enrichment (default: gpt-4.1-mini).",
    )
    parser.add_argument(
        "--provider", default="openai", choices=["openai", "anthropic"],
        help="LLM provider (default: openai).",
    )
    parser.add_argument(
        "--temperature", type=float, default=0.4,
        help="LLM temperature (default: 0.4).",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", default=False,
        help="Enable verbose logging.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _setup_logging(args.verbose)
    log = logging.getLogger("cert-reporter")

    # Validate input
    input_path = Path(args.input)
    if not input_path.exists():
        log.error("Input file not found: %s", input_path)
        return 1

    formats = [f.strip().lower() for f in args.format.split(",") if f.strip()]
    valid_formats = {"html", "pdf"}
    bad = set(formats) - valid_formats
    if bad:
        log.error("Unknown format(s): %s. Use: html, pdf", ", ".join(bad))
        return 1

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    log.info("cert-reporter starting")
    log.info("  Input  : %s", input_path)
    log.info("  Output : %s", output_dir)
    log.info("  Formats: %s", ", ".join(formats))
    log.info("  Mode   : %s", args.mode)
    if args.enrich_llm:
        log.info("  LLM    : %s/%s", args.provider, args.model)

    # Add the project root to sys.path so imports work
    project_root = Path(__file__).parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    pipeline_kwargs = dict(
        input_path=str(input_path),
        output_dir=str(output_dir),
        formats=formats,
        enrich_llm=args.enrich_llm,
        model=args.model,
        provider=args.provider,
        temperature=args.temperature,
        verbose=args.verbose,
    )

    t0 = time.monotonic()
    try:
        if args.mode == "agentic":
            from pipeline.agentic_graph import run_agentic_pipeline
            final_state = run_agentic_pipeline(**pipeline_kwargs)
        else:
            from pipeline.graph import run_pipeline
            final_state = run_pipeline(**pipeline_kwargs)
    except Exception as exc:
        log.error("Pipeline failed: %s", exc, exc_info=args.verbose)
        return 1

    elapsed = time.monotonic() - t0

    # Report results
    errors = final_state.get("errors", [])
    if errors:
        for err in errors:
            log.warning("Pipeline warning: %s", err)

    html_path = final_state.get("html_path", "")
    pdf_path = final_state.get("pdf_path", "")

    if html_path:
        log.info("HTML report : %s", html_path)
        print(f"HTML → {html_path}")

    if pdf_path:
        log.info("PDF report  : %s", pdf_path)
        print(f"PDF  → {pdf_path}")

    token_usage = final_state.get("token_usage")
    if token_usage and token_usage.total > 0:
        log.info(
            "LLM tokens  : %d input + %d output = %d total",
            token_usage.input_tokens, token_usage.output_tokens, token_usage.total,
        )

    log.info("Done in %.1fs", elapsed)

    return 0 if (html_path or pdf_path) else 1


if __name__ == "__main__":
    sys.exit(main())
