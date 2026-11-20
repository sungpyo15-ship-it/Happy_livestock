"""목장별 엑셀 + 개체 기록부 앱 생성."""
from __future__ import annotations

import re
import sqlite3
from datetime import date
from pathlib import Path

from .app import build_app
from .breeding import build_records, farm_kpis
from .excel import build_farm_book
from .health import farm_health, memo_count
from .store import Store


def build_all(smx_db: Path, cow_db: Path, out_dir: Path, today: date, farm_filter: list[str] | None = None,
              memo_only: bool = False, excel: bool = True) -> dict:
    smx = sqlite3.connect(str(smx_db))
    farms = smx.execute("SELECT farm_id, name FROM farms ORDER BY name").fetchall()
    if farm_filter:
        farms = [f for f in farms if f[0] in farm_filter or any(k in f[1] for k in farm_filter)]
    items, files = [], []
    with Store(cow_db) as st:
        for fid, name in farms:
            beef = "한우" in name or "비육" in name
            recs = build_records(smx, fid, today, gestation=285 if beef else 280, dry_before=0 if beef else 60)
            health = farm_health(st.conn, smx, fid)
            n_memo = memo_count(health)
            if memo_only and not n_memo:
                continue
            if not recs and not health:
                continue
            kpi = farm_kpis(recs)
            items.append({"id": fid, "name": name, "beef": beef, "kpi": kpi, "recs": recs, "health": health, "memo_n": n_memo})
            if excel:
                safe = re.sub(r'[\\/:*?"<>|]', "_", name)[:40]
                files.append(build_farm_book(name, recs, kpi, health, today, out_dir / f"개체기록부_{safe}_{today:%Y%m%d}.xlsx", beef))
    smx.close()
    items.sort(key=lambda x: (-x["memo_n"], x["name"]))
    app = build_app(items, today, out_dir / "개체기록부.html")
    return {"farms": len(items), "files": files, "app": app}
