import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cowrec  # noqa: E402,F401  (smaxtec_monitor 경로 추가)
