#!/usr/bin/env python3
"""Base do monitor. Coletores reais e Telegram serão integrados na próxima etapa."""
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "promocoes.json"

def main():
    now = datetime.now(ZoneInfo("America/Sao_Paulo")).isoformat(timespec="seconds")
    try:
        payload = json.loads(DATA.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        payload = {"promocoes": []}
    payload["verificado_em"] = now
    payload.setdefault("promocoes", [])
    DATA.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Monitoramilhas: estrutura verificada em {now}; coletores ainda não configurados.")
if __name__ == "__main__":
    main()
