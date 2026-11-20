"""리포트 일괄 생성 (PC 명령 report 와 서버가 같은 코드를 사용)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import ConfigError, Settings, load_farm_overrides, load_toml
from .normalize import AlertTypeMapper

log = logging.getLogger("smxmon")


@dataclass
class Outputs:
    an: object
    daily: Path
    visit: Path | None = None
    trials: Path | None = None
    dashboard: Path | None = None
    text: str = ""
    files: list[Path] = field(default_factory=list)


def generate(s: Settings, as_of: datetime, *, db_path: Path | None = None, out: Path | None = None,
             trials_path: Path | None = None, overrides: dict | None = None) -> Outputs:
    from .analysis import analyze
    from .dashboard import build_dashboard
    from .db import Database
    from .report import build_excel, build_text_summary
    from .trials import TRIALS_FILE, compute_all, ensure_template
    from .visit import build_visit_workbook, visit_targets

    db_path = db_path or s.db_path
    if not db_path.exists():
        raise ConfigError(f"DB 가 없습니다: {db_path} (먼저 collect 또는 demo 실행)")
    rules, mapper = load_toml("rules.toml"), AlertTypeMapper(load_toml("alert_types.toml"))
    trials_path = trials_path or s.trials_file
    if trials_path == TRIALS_FILE:
        ensure_template(TRIALS_FILE)   # PC: 처음이면 빈 양식 생성
    with Database(db_path) as db:
        an = analyze(db.conn, as_of, mapper, rules, load_farm_overrides() if overrides is None else overrides)
        try:
            an.trials = compute_all(db.conn, as_of, mapper, rules, trials_path)
        except Exception as e:  # noqa: BLE001  (엑셀에서 열려 있는 등 → 리포트는 계속)
            log.warning("투입비교 계산 실패(투입기록.xlsx 확인): %s", e)
            an.trials = []
    out = out or s.report_dir / f"smaxtec_daily_{as_of:%Y%m%d_%H%M}.xlsx"
    build_excel(an, mapper, rules, out)
    o = Outputs(an, out)
    if an.trials:
        from .trials_report import build_trials_workbook
        o.trials = build_trials_workbook(an.trials, out.with_name(out.name.replace("smaxtec_daily_", "투입비교_")))
    targets = visit_targets(an)
    if targets:
        o.visit = build_visit_workbook(an, targets, mapper, out.with_name(out.name.replace("smaxtec_daily_", "방문요약_")))
    o.dashboard = build_dashboard(an, mapper, out.with_name(out.name.replace("smaxtec_daily_", "dashboard_")).with_suffix(".html"))
    o.text = build_text_summary(an)
    o.files = [p for p in (o.daily, o.visit, o.trials, o.dashboard) if p]
    return o
