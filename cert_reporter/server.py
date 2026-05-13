#!/usr/bin/env python3
"""
cert-reporter server entry point.

Usage:
    python server.py                        # default: 0.0.0.0:8000
    python server.py --host 127.0.0.1 --port 8080
    python server.py --reload               # dev mode with auto-reload
"""

from __future__ import annotations

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the cert-reporter API server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="Enable hot-reload (dev mode)")
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()

    try:
        import uvicorn
    except ImportError:
        print("uvicorn not installed. Run: pip install uvicorn[standard]")
        sys.exit(1)

    uvicorn.run(
        "api.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=args.workers if not args.reload else 1,
        log_level="info",
    )


if __name__ == "__main__":
    main()
