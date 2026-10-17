#!/usr/bin/env python3
# Monitoramilhas — coletor inicial de fontes oficiais
# Esta versão consulta páginas oficiais e registra evidências para revisão.
# Não publica promoções automaticamente: isso evita exibir bônus expirados
# ou condições de clubes interpretadas incorretamente.

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "monitor_fontes.json"
TZ = ZoneInfo("America/Sao_Paulo")
TIMEOUT_SECONDS = 20
MAX_TEXT_CHARS = 5000

# Apenas páginas oficiais conhecidas. Páginas podem mudar de endereço ou bloquear robôs.
SOURCES = [
    {
        "programa": "Azul Fidelidade",
        "parceiro": "Banco BV",
        "url": "https://www.voeazul.com.br/br/pt/ofertas/bv",
    },
    {
        "programa": "Azul Fidelidade",
        "parceiro": "Ofertas e promoções Azul",
        "url": "https://www.voeazul.com.br/br/pt/ofertas",
    },
    {
        "programa": "LATAM Pass",
        "parceiro": "Promoções LATAM Pass",
        "url": "https://latampass.latam.com/pt_br/promocoes",
    },
    {
        "programa": "LATAM Pass",
        "parceiro": "Promoções de bancos LATAM Pass",
        "url": "https://web.latampass.latam.com/pt_br/promocao/bancos-milhas-extras",
    },
    {
        "programa": "Smiles",
        "parceiro": "Campanhas de bancos Smiles",
        "url": "https://www.smiles.com.br/portal/campanhas",
    },
    {
        "programa": "Livelo",
        "parceiro": "Promoções Livelo",
        "url": "https://www.livelo.com.br/",
    },
    {
        "programa": "Esfera",
        "parceiro": "Promoções Esfera",
        "url": "https://www.esfera.com.vc/",
    },
]

KEYWORDS = (
    "transferência", "transferir", "pontos", "milhas", "bônus", "bonus",
    "clube", "assinante", "percentual", "ganhe", "promoção", "promocao",
)

class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip_depth = 0
        self.title_parts = []
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in ("script", "style", "noscript", "svg"):
            self.skip_depth += 1
        if tag == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("script", "style", "noscript", "svg") and self.skip_depth:
            self.skip_depth -= 1
        if tag == "title":
            self.in_title = False
        if tag in ("p", "div", "li", "br", "h1", "h2", "h3", "section"):
            self.parts.append(" ")

    def handle_data(self, data):
        if self.skip_depth:
            return
        cleaned = re.sub(r"\s+", " ", data).strip()
        if not cleaned:
            return
        self.parts.append(cleaned)
        if self.in_title:
            self.title_parts.append(cleaned)

def fetch_source(source):
    req = Request(
        source["url"],
        headers={
            "User-Agent": "MonitoramilhasBot/1.0 (monitor de promoções públicas; contato: consulte o repositório)",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.5",
        },
    )
    checked_at = datetime.now(TZ).isoformat(timespec="seconds")
    try:
        with urlopen(req, timeout=TIMEOUT_SECONDS) as response:
            status_code = getattr(response, "status", 200)
            content_type = response.headers.get("Content-Type", "")
            raw = response.read(2_000_000)
            charset = response.headers.get_content_charset() or "utf-8"
        html = raw.decode(charset, errors="replace")
        parser = TextExtractor()
        parser.feed(html)
        text = re.sub(r"\s+", " ", " ".join(parser.parts)).strip()
        title = re.sub(r"\s+", " ", " ".join(parser.title_parts)).strip()
        lower = text.casefold()
        found = [word for word in KEYWORDS if word.casefold() in lower]
        # Trechos curtos ajudam a conferir a evidência, mas não são confirmação de campanha.
        excerpts = []
        for match in re.finditer(r"transfer|b[oô]nus|clube|assinante|milhas extras|pontos extras", text, re.I):
            start = max(0, match.start() - 180)
            end = min(len(text), match.end() + 260)
            excerpt = text[start:end].strip()
            if excerpt and all(excerpt not in old and old not in excerpt for old in excerpts):
                excerpts.append(excerpt)
            if len(excerpts) >= 5:
                break
        return {
            **source,
            "consultado_em": checked_at,
            "resultado": "consultado",
            "http_status": status_code,
            "content_type": content_type,
            "titulo_pagina": title[:250],
            "termos_encontrados": found,
            "trechos_para_revisao": excerpts,
            "observacao": "Página consultada; promoção e validade ainda não foram confirmadas automaticamente.",
        }
    except HTTPError as exc:
        return {**source, "consultado_em": checked_at, "resultado": "erro_http",
                "http_status": exc.code, "erro": str(exc)[:300]}
    except (URLError, TimeoutError, OSError, UnicodeError) as exc:
        return {**source, "consultado_em": checked_at, "resultado": "erro_consulta",
                "erro": str(exc)[:300]}
    except Exception as exc:
        return {**source, "consultado_em": checked_at, "resultado": "erro_inesperado",
                "erro": f"{type(exc).__name__}: {str(exc)[:250]}"}

def main():
    now = datetime.now(TZ)
    results = []
    for index, source in enumerate(SOURCES):
        print(f"[{index + 1}/{len(SOURCES)}] Consultando {source['programa']}: {source['url']}")
        result = fetch_source(source)
        print(f"    Resultado: {result['resultado']}" +
              (f" (HTTP {result.get('http_status')})" if result.get("http_status") else ""))
        results.append(result)
        # Pausa curta para não fazer muitas requisições seguidas.
        if index < len(SOURCES) - 1:
            time.sleep(2)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "atualizado_em": now.isoformat(timespec="seconds"),
        "fuso_horario": "America/Sao_Paulo",
        "total_fontes": len(results),
        "fontes_consultadas": sum(1 for x in results if x["resultado"] == "consultado"),
        "fontes_com_erro": sum(1 for x in results if x["resultado"] != "consultado"),
        "nota": (
            "Relatório de consulta de páginas oficiais. Não equivale a uma lista de promoções ativas. "
            "Não publique uma campanha sem confirmar datas, percentual, regras de elegibilidade "
            "e condições de clubes diretamente na fonte oficial."
        ),
        "fontes": results,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Relatório salvo em: {OUT}")
    print(f"Fontes consultadas: {payload['fontes_consultadas']}/{payload['total_fontes']}")
    # Falhas individuais ficam registradas, mas não interrompem todas as outras fontes.
    # Se todas falharem, o job falha para que o problema fique visível no Actions.
    if payload["fontes_consultadas"] == 0:
        raise SystemExit("Nenhuma fonte oficial pôde ser consultada.")

if __name__ == "__main__":
    main()
