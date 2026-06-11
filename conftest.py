"""Root conftest for the certifier test suite.

Ensures the certifier package root is importable regardless of the working
directory pytest is invoked from, so module imports like ``from utils...``
and ``from aggregator...`` resolve consistently.
"""

import sys
from pathlib import Path

_CERTIFIER_ROOT = Path(__file__).resolve().parent
if str(_CERTIFIER_ROOT) not in sys.path:
    sys.path.insert(0, str(_CERTIFIER_ROOT))
