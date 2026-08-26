
#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from stages.prepare_dataset.prepare_dataset import main


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
