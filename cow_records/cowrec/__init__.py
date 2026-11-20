"""개체 기록부 (cow_records): smaXtec 기록으로 번식기록지·개체별 질병·치료 내역을 정리.

smaXtec 로그인·이벤트 수집은 같은 저장소의 smaxtec_monitor(smxmon) 를 재사용한다.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SMX_DIR = ROOT.parent / "smaxtec_monitor"
if str(SMX_DIR) not in sys.path:
    sys.path.insert(0, str(SMX_DIR))

__version__ = "0.1.0"
