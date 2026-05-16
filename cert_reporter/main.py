#!/usr/bin/env python3
"""
cert-reporter — FastAPI server and CLI for generating HTML/PDF certification reports.

Start the API server (default):
    python main.py
    python main.py serve
    python main.py serve --host 0.0.0.0 --port 8080

Run the report pipeline directly from the CLI:
    python main.py generate --agent-id my-agent --experiment-id exp-001
    python main.py generate -a my-agent -e exp-001 --format html
    python main.py generate -a my-agent -e exp-001 --enrich-llm --model gpt-4.1-mini

Reads:  workspace/{agent_id}/{experiment_id}/cert-builder/certification.json
Writes: workspace/{agent_id}/{experiment_id}/certification/
"""

from __future__ import annotations

import argparse
import logging
import os
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


# ---------------------------------------------------------------------------
# Subcommand: serve
# ---------------------------------------------------------------------------

def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn
    from api.app import create_app

    app = create_app()
    uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)
    return 0


# ---------------------------------------------------------------------------
# Subcommand: generate
# ---------------------------------------------------------------------------

def _cmd_generate(args: argparse.Namespace) -> int:
    log = logging.getLogger("cert-reporter")

    project_root = Path(__file__).resolve().parent        # cert_reporter/
    certifier_root = project_root.parent                  # certifier/
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    _ws_env = os.getenv("WORKSPACE_DIR")
    workspace_dir: Path = (
        Path(_ws_env) if (_ws_env and Path(_ws_env).is_absolute())
        else certifier_root / (_ws_env or "workspace")
    )
    input_path = workspace_dir / args.agent_id / args.experiment_id / "cert-builder" / "certification.json"
    output_dir = workspace_dir / args.agent_id / args.experiment_id / "certification"

    if not input_path.exists():
        log.error(
            "certification.json not found at %s. "
            "Run POST /api/v1/aggregation-certification first.",
            input_path,
        )
        return 1

    formats = [f.strip().lower() for f in args.format.split(",") if f.strip()]
    bad = set(formats) - {"html", "pdf"}
    if bad:
        log.error("Unknown format(s): %s. Use: html, pdf", ", ".join(bad))
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)

    log.info("cert-reporter starting")
    log.info("  Agent      : %s", args.agent_id)
    log.info("  Experiment : %s", args.experiment_id)
    log.info("  Input      : %s", input_path)
    log.info("  Output     : %s", output_dir)
    log.info("  Formats    : %s", ", ".join(formats))
    log.info("  Mode       : %s", args.mode)
    if args.enrich_llm:
        log.info("  LLM        : %s/%s", args.provider, args.model)

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

    for err in final_state.get("errors", []):
        log.warning("Pipeline warning: %s", err)

    html_path = final_state.get("html_path", "")
    pdf_path = final_state.get("pdf_path", "")

    if html_path:
        log.info("HTML report : %s", html_path)
        print(f"HTML -> {html_path}")
    if pdf_path:
        log.info("PDF report  : %s", pdf_path)
        print(f"PDF  -> {pdf_path}")

    token_usage = final_state.get("token_usage")
    if token_usage and token_usage.total > 0:
        log.info(
            "LLM tokens  : %d input + %d output = %d total",
            token_usage.input_tokens, token_usage.output_tokens, token_usage.total,
        )

    log.info("Done in %.1fs", elapsed)
    return 0 if (html_path or pdf_path) else 1


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cert-reporter",
        description="cert-reporter: generate HTML/PDF certification reports.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", default=False,
        help="Enable verbose logging.",
    )

    sub = parser.add_subparsers(dest="command")

    # ── serve ──────────────────────────────────────────────────────────────
    serve_p = sub.add_parser("serve", help="Start the FastAPI server (default command).")
    serve_p.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0).")
    serve_p.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000).")
    serve_p.add_argument("--reload", action="store_true", default=False, help="Enable auto-reload.")

    # ── generate ───────────────────────────────────────────────────────────
    gen_p = sub.add_parser("generate", help="Run the report pipeline from the CLI.")
    gen_p.add_argument("--agent-id", "-a", required=True, help="Agent ID.")
    gen_p.add_argument("--experiment-id", "-e", required=True, help="Experiment ID.")
    gen_p.add_argument(
        "--format", "-f", default="html,pdf",
        help="Comma-separated output formats: html,pdf (default: html,pdf).",
    )
    gen_p.add_argument(
        "--mode", default="static", choices=["static", "agentic"],
        help="Pipeline mode: static (default) or agentic.",
    )
    gen_p.add_argument("--enrich-llm", action="store_true", default=False)
    gen_p.add_argument("--model", default="gpt-4.1-mini")
    gen_p.add_argument("--provider", default="openai", choices=["openai", "anthropic"])
    gen_p.add_argument("--temperature", type=float, default=0.4)

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    # Default to "serve" when no subcommand is given
    if not args.command or args.command == "serve":
        if not args.command:
            # Inject serve defaults when called with no subcommand
            args.host = "0.0.0.0"
            args.port = 8000
            args.reload = False
        return _cmd_serve(args)

    if args.command == "generate":
        return _cmd_generate(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
