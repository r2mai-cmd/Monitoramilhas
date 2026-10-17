#!/usr/bin/env python3
"""Monitoramilhas — coleta conservadora de fontes oficiais e resultados públicos.

Esta versão:
- tenta consultar páginas oficiais diretamente;
- consulta feeds RSS públicos do Google Notícias para descobrir campanhas;
- guarda evidências em data/monitor_fontes.json;
- NÃO publica candidatos em data/promocoes.json automaticamente.
"""
from __future__ import annotations

import json
import re
import time
import html
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "monitor_fontes.json"
TZ = ZoneInfo("America/Sao_Paulo")
TIMEOUT_SECONDS = 20
MAX_BYTES = 1_500_000

OFFICIAL_PAGES = [
    {"programa": "Azul Fidelidade", "parceiro": "Banco BV", "url": "https://www.voeazul.com.br/br/pt/ofertas/bv"},
    {"programa": "Azul Fidelidade", "parceiro": "Ofertas Azul", "url": "https://www.voeazul.com.br/br/pt/ofertas"},
    {"programa": "LATAM Pass", "parceiro": "Promoções LATAM Pass", "url": "https://latampass.latam.com/pt_br/promocoes"},
    {"programa": "LATAM Pass", "parceiro": "Promoções de bancos LATAM Pass", "url": "https://web.latampass.latam.com/pt_br/promocao/bancos-milhas-extras"},
    {"programa": "Smiles", "parceiro": "Campanhas Smiles", "url": "https://www.smiles.com.br/portal/campanhas"},
    {"programa": "Livelo", "parceiro": "Livelo", "url": "https://www.livelo.com.br/"},
    {"programa": "Esfera", "parceiro": "Esfera", "url": "https://www.esfera.com.vc/"},
]

# Consultas amplas para descoberta; os resultados são pistas, não confirmação de promoção.
NEWS_QUERIES = [
    '"transferência bonificada" milhas clube 2026',
    'site:voeazul.com.br bônus transferência pontos clube Azul Fidelidade',
    'site:smiles.com.br bônus transferência pontos Clube Smiles',
    'site:latampass.latam.com bônus transferência pontos clube LATAM Pass',
    'site:livelo.com.br transferência bonificada milhas',
    'site:esfera.com.vc transferência bonificada milhas',
    'bônus transferência pontos milhas assinantes clube outubro 2026',
]

KEYWORDS = ("transfer", "bônus", "bonus", "milhas", "pontos", "clube", "assinante", "promoção", "promocao")

class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.title_parts = []
        self.in_title = False
        self.skip_depth = 0
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
        value = re.sub(r"\s+", " ", data).strip()
        if value:
            self.parts.append(value)
            if self.in_title:
                self.title_parts.append(value)

def http_get(url, accept="text/html,application/xhtml+xml,application/rss+xml,application/xml;q=0.9,*/*;q=0.8"):
    req = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; Monitoramilhas/2.0; +https://r2mai-cmd.github.io/Monitoramilhas/)",
        "Accept": accept,
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.5",
    })
    with urlopen(req, timeout=TIMEOUT_SECONDS) as response:
        body = response.read(MAX_BYTES)
        charset = response.headers.get_content_charset() or "utf-8"
        return getattr(response, "status", 200), response.headers.get("Content-Type", ""), body.decode(charset, errors="replace")

def consult_official(source, checked_at):
    base = {**source, "consultado_em": checked_at, "tipo": "pagina_oficial"}
    try:
        status, content_type, body = http_get(source["url"])
        parser = TextExtractor()
        parser.feed(body)
        text = re.sub(r"\s+", " ", " ".join(parser.parts)).strip()
        title = re.sub(r"\s+", " ", " ".join(parser.title_parts)).strip()
        lower = text.casefold()
        hits = [k for k in KEYWORDS if k in lower]
        excerpts = []
        for m in re.finditer(r"transfer|b[oô]nus|clube|assinante|milhas extras|pontos extras", text, re.I):
            snippet = text[max(0, m.start()-150):min(len(text), m.end()+250)].strip()
            if snippet and snippet not in excerpts:
                excerpts.append(snippet)
            if len(excerpts) >= 4:
                break
        return {**base, "resultado": "consultado", "http_status": status, "content_type": content_type,
                "titulo_pagina": title[:220], "termos_encontrados": hits,
                "trechos_para_revisao": excerpts,
                "observacao": "Consulta à página oficial; o conteúdo não confirma por si só uma promoção ativa."}
    except HTTPError as e:
        return {**base, "resultado": "erro_http", "http_status": e.code, "erro": str(e)[:220]}
    except (URLError, TimeoutError, OSError, UnicodeError) as e:
        return {**base, "resultado": "erro_consulta", "erro": str(e)[:220]}
    except Exception as e:
        return {**base, "resultado": "erro_inesperado", "erro": f"{type(e).__name__}: {str(e)[:220]}"}

def discover_news(query, checked_at):
    url = "https://news.google.com/rss/search?q=" + quote_plus(query) + "&hl=pt-BR&gl=BR&ceid=BR:pt-419"
    found = []
    try:
        status, content_type, body = http_get(url, "application/rss+xml,application/xml,text/xml,*/*")
        root = ET.fromstring(body)
        for item in root.findall(".//item")[:8]:
            title = html.unescape((item.findtext("title") or "").strip())
            link = (item.findtext("link") or "").strip()
            pubdate = (item.findtext("pubDate") or "").strip()
            description = html.unescape(re.sub(r"<[^>]+>", " ", item.findtext("description") or ""))
            description = re.sub(r"\s+", " ", description).strip()
            source_node = item.find("source")
            publisher = (source_node.text or "").strip() if source_node is not None else ""
            combined = (title + " " + description).casefold()
            if not title or not any(k in combined for k in KEYWORDS):
                continue
            found.append({
                "consulta": query, "titulo": title[:300], "publicador": publisher[:160],
                "publicado_em": pubdate, "link_resultado": link,
                "descricao": description[:700], "consultado_em": checked_at,
                "status_validacao": "pendente",
                "observacao": "Resultado de busca é apenas pista. Confirmar a campanha, datas e regras na página oficial antes de publicar."
            })
        return {"consulta": query, "resultado": "consultado", "http_status": status, "candidatos": found}
    except HTTPError as e:
        return {"consulta": query, "resultado": "erro_http", "http_status": e.code, "candidatos": []}
    except Exception as e:
        return {"consulta": query, "resultado": "erro_consulta", "erro": f"{type(e).__name__}: {str(e)[:220]}", "candidatos": []}

def main():
    checked_at = datetime.now(TZ).isoformat(timespec="seconds")
    official_results = []
    for i, source in enumerate(OFFICIAL_PAGES):
        print(f"[Página oficial {i+1}/{len(OFFICIAL_PAGES)}] {source['programa']} — {source['url']}")
        result = consult_official(source, checked_at)
        print(f"    {result['resultado']}" + (f" HTTP {result.get('http_status')}" if result.get("http_status") else ""))
        official_results.append(result)
        if i < len(OFFICIAL_PAGES)-1:
            time.sleep(1)

    news_results = []
    for i, query in enumerate(NEWS_QUERIES):
        print(f"[Busca pública {i+1}/{len(NEWS_QUERIES)}] {query}")
        result = discover_news(query, checked_at)
        print(f"    {result['resultado']}; candidatos: {len(result.get('candidatos', []))}")
        news_results.append(result)
        if i < len(NEWS_QUERIES)-1:
            time.sleep(1)

    candidates = []
    seen = set()
    for result in news_results:
        for item in result.get("candidatos", []):
            key = (item.get("titulo", "").casefold(), item.get("link_resultado", ""))
            if key not in seen:
                seen.add(key)
                candidates.append(item)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "atualizado_em": checked_at,
        "fuso_horario": "America/Sao_Paulo",
        "total_paginas_oficiais": len(official_results),
        "paginas_oficiais_ok": sum(x["resultado"] == "consultado" for x in official_results),
        "total_buscas_publicas": len(news_results),
        "buscas_publicas_ok": sum(x["resultado"] == "consultado" for x in news_results),
        "total_candidatos_busca": len(candidates),
        "aviso": "Este relatório não é uma lista de promoções ativas. Resultados de busca e páginas consultadas precisam de validação oficial antes de publicação. Não são extraídos nem inferidos automaticamente percentuais, datas de validade ou elegibilidade de clubes.",
        "paginas_oficiais": official_results,
        "buscas_publicas": news_results,
        "candidatos": candidates,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Relatório salvo em: {OUT}")
    print(f"Páginas oficiais acessíveis: {payload['paginas_oficiais_ok']}/{len(official_results)}")
    print(f"Buscas públicas acessíveis: {payload['buscas_publicas_ok']}/{len(news_results)}")
    print(f"Candidatos para revisão: {len(candidates)}")
    if payload["paginas_oficiais_ok"] == 0 and payload["buscas_publicas_ok"] == 0:
        raise SystemExit("Nenhuma fonte de consulta respondeu.")

if __name__ == "__main__":
    main()
