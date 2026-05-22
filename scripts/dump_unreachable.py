#!/usr/bin/env python3
"""Выгружает домены где сайт не открылся (site_unreachable=true) — для
ручного прохода. Печатает домены + кликабельные ссылки на лид в Bitrix.

    python scripts/dump_unreachable.py > unreachable.txt
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.config import settings  # noqa: E402

state = json.loads(Path("b24_processed.json").read_text(encoding="utf-8"))

unreachable: list[tuple[str, dict]] = sorted(
    [(d, v) for d, v in state.items() if v.get("site_unreachable")],
    key=lambda x: x[0],
)
skipped: list[tuple[str, dict]] = sorted(
    [(d, v) for d, v in state.items() if v.get("status") == "skip_recon"],
    key=lambda x: x[0],
)

base = f"https://{settings.bitrix_domain}/crm/lead/details"

print(f"# Сайты не открылись (но лид создан) — {len(unreachable)} шт.\n")
for d, v in unreachable:
    lid = v.get("lead_id") or "—"
    print(f"https://{d}  →  лид {base}/{lid}/")

print(f"\n\n# Совсем без данных (лид НЕ создан) — {len(skipped)} шт.\n")
for d, v in skipped:
    err = v.get("error", "")[:100]
    print(f"https://{d}  ({err})")
