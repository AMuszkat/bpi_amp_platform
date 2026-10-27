"""The virtual pair tests (README.md).

The modules import the harness as `harness`, which `python3 -m unittest discover -s tests/vpair`
puts on the path. This puts it there for `python3 -m unittest tests.vpair.<module>` too.
"""

import sys
from pathlib import Path

_here = str(Path(__file__).resolve().parent)
if _here not in sys.path:
    sys.path.insert(0, _here)
