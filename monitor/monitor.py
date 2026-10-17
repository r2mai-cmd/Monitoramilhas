#!/usr/bin/env python3
"""Monitoramilhas — descoberta conservadora de possíveis promoções.

Os resultados de busca são pistas, não confirmação de campanha ativa.
O monitor mantém evidências em data/monitor_fontes.json e nunca publica
automaticamente candidatos em data/promocoes.json.
"""
from __future__ import annotations

import html
import json
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
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
MAX_AGE_DAYS = 14
MAX_RESULTS_PER_QUERY = 8

OFFICIAL_PAGES = [
    {"programa": "Azul Fidelidade", "parceiro": "Banco BV", "url": "https://www.voeazul.com.br/br/pt/ofertas/bv"},
    {"programa": "Azul Fidelidade", "parceiro": "Ofertas Azul", "url": "https://www.voeazul.com.br/br/pt/ofertas"},
    {"programa": "LATAM Pass", "parceiro": "Promoções LATAM Pass", "url": "https://latampass.latam.com/pt_br/promocoes"},
    {"programa": "LATAM Pass", "parceiro": "Promoções de bancos LATAM Pass", "url": "https://web.latampass.latam.com/pt_br/promocao/bancos-milhas-extras"},
    {"programa": "Smiles", "parceiro": "Campanhas Smiles", "url": "https://www.smiles.com.br/portal/campanhas"},
    {"programa": "Livelo", "parceiro": "Livelo", "url": "https://www.livelo.com.br/"},
    {"programa": "Esfera", "parceiro": "Esfera", "url": "https://www.esfera.com.vc/"},
]

NEWS_QUERIES = [
    '"transferência bonificada" milhas clube 2026',
    'site:voeazul.com.br bônus transferência pontos clube Azul Fidelidade',
    'site:smiles.com.br bônus transferência pontos Clube Smiles',
    'site:latampass.latam.com bônus transferência pontos clube LATAM Pass',
    'site:livelo.com.br transferência bonificada milhas',
    'site:esfera.com.vc transferência bonificada milhas',
    'bônus transferência pontos milhas assinantes clube outubro 2026',
    'site:voeazul.com.br Banco BV transferência pontos bônus outubro 2026',
    'C6 Átomos transferência bonificada milhas outubro 2026',
    'site:melhoresdestinos.com.br/milhas transferência bonificada milhas outubro 2026',
    'site:mestredasmilhas.com transferência bonificada milhas outubro 2026',
    'site:passageirodeprimeira.com transferência bonificada milhas outubro 2026',
]

KEYWORDS = ("transfer", "bônus", "bonus", "milhas", "pontos", "clube", "assinante", "promoção", "promocao")
TRANSFER_RE = re.compile(r"transfer|transfira|transferência|transferencia|envio de pontos|enviar pontos|envie pontos", re.I)
BONUS_RE = re.compile(r"b[oô]nus|bonificad|milhas extras|pontos extras|at[eé] \d{1,3}\s*%", re.I)
URGENT_ENDING_RE = re.compile(r"últimas horas|ultimas horas|termina hoje|terminam hoje|acaba hoje|acabam hoje|encerra hoje|encerram hoje|último dia|ultimo dia", re.I)
END_DATE_RE = re.compile(r"(?:válid[oa] até|valida até|até|ate|encerra(?:mento)? em|termina em)\s*(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?", re.I)
CAMPAIGN_RE = re.compile(r"transfer|transfira|transferência|transferencia|envio de pontos|enviar pontos", re.I)


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
        "User-Agent": "Mozilla/5.0 (compatible; Monitoramilhas/3.0; +https://r2mai-cmd.github.io/Monitoramilhas/)",
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
        for match in re.finditer(r"transfer|b[oô]nus|clube|assinante|milhas extras|pontos extras", text, re.I):
            snippet = text[max(0, match.start()-150):min(len(text), match.end()+250)].strip()
            if snippet and snippet not in excerpts:
                excerpts.append(snippet)
            if len(excerpts) >= 4:
                break
        return {**base, "resultado": "consultado", "http_status": status, "content_type": content_type,
                "titulo_pagina": title[:220], "termos_encontrados": hits,
                "trechos_para_revisao": excerpts,
                "observacao": "A página foi consultada, mas isso não confirma por si só uma promoção ativa."}
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
        status, _content_type, body = http_get(url, "application/rss+xml,application/xml,text/xml,*/*")
        root = ET.fromstring(body)
        for item in root.findall(".//item")[:MAX_RESULTS_PER_QUERY]:
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
                "observacao": "Pista de busca: confirmar campanha, validade e regras em fonte oficial antes de divulgar."
            })
        return {"consulta": query, "resultado": "consultado", "http_status": status, "candidatos": found}
    except HTTPError as e:
        return {"consulta": query, "resultado": "erro_http", "http_status": e.code, "candidatos": []}
    except Exception as e:
        return {"consulta": query, "resultado": "erro_consulta", "erro": f"{type(e).__name__}: {str(e)[:220]}", "candidatos": []}


def parse_publication_date(value):
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(TZ)
    except (TypeError, ValueError, OverflowError):
        return None


def extract_campaign_end_date(text, published):
    """Extrai datas explícitas de encerramento; sem ano, usa o ano da publicação."""
    match = END_DATE_RE.search(text or "")
    if not match:
        return None
    day, month, year = match.groups()
    year_num = int(year) if year else published.year
    if year_num < 100:
        year_num += 2000
    try:
        return datetime(year_num, int(month), int(day), 23, 59, tzinfo=TZ)
    except ValueError:
        return None


def normalize_title(value):
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def classify_candidates(news_results, now):
    unique = {}
    rejected = {"antigos": 0, "data_ausente_ou_invalida": 0, "sem_sinal_de_transferencia_bonificada": 0, "provavelmente_encerrados": 0, "duplicados": 0}
    for result in news_results:
        for item in result.get("candidatos", []):
            published = parse_publication_date(item.get("publicado_em", ""))
            if published is None:
                rejected["data_ausente_ou_invalida"] += 1
                continue
            age = now - published
            if age > timedelta(days=MAX_AGE_DAYS) or age < timedelta(days=-1):
                rejected["antigos"] += 1
                continue
            combined = item.get("titulo", "") + " " + item.get("descricao", "")
            end_date = extract_campaign_end_date(combined, published)
            if end_date and now > end_date:
                rejected["provavelmente_encerrados"] += 1
                continue
            # Manchetes urgentes envelhecem rapidamente; após 24h não entram como candidatos prioritários.
            if URGENT_ENDING_RE.search(item.get("titulo", "")) and age > timedelta(hours=24):
                rejected["provavelmente_encerrados"] += 1
                continue
            if not TRANSFER_RE.search(combined) or not BONUS_RE.search(combined):
                rejected["sem_sinal_de_transferencia_bonificada"] += 1
                continue

            item["idade_dias"] = max(0, age.days)
            item["data_encerramento_detectada"] = end_date.isoformat() if end_date else None
            item["status_validacao"] = "pendente_confirmacao_oficial"
            item["observacao"] = (
                "Publicação recente com sinais de transferência bonificada. "
                "Ainda não está confirmada como ativa; verificar regulamento oficial e validade."
            )
            key = normalize_title(item.get("titulo", ""))
            if not key:
                key = item.get("link_resultado", "")
            if key in unique:
                rejected["duplicados"] += 1
                # Preferir item com link não vazio e preservar as consultas que o encontraram.
                existing = unique[key]
                queries = set(existing.get("consultas_encontradas", [existing.get("consulta", "")]))
                queries.add(item.get("consulta", ""))
                existing["consultas_encontradas"] = sorted(q for q in queries if q)
                if len(item.get("descricao", "")) > len(existing.get("descricao", "")):
                    existing["descricao"] = item.get("descricao", "")
                continue
            item["consultas_encontradas"] = [item.get("consulta", "")]
            unique[key] = item

    candidates = list(unique.values())
    # Uma pista recente não prova que a promoção ainda está ativa. Separe títulos
    # que indicam encerramento iminente para evitar tratá-los como oportunidades.
    active_possible = []
    likely_expired = []
    needs_validation = []
    for item in candidates:
        title = item.get("titulo", "")
        combined = title + " " + item.get("descricao", "")
        if URGENT_ENDING_RE.search(title):
            item["status_validacao"] = "provavelmente_encerrada"
            item["motivo_status"] = "Título menciona últimas horas, termina hoje ou último dia."
            likely_expired.append(item)
        else:
            item["status_validacao"] = "pendente_confirmacao_oficial"
            item["motivo_status"] = "Não foi possível confirmar a validade em fonte oficial."
            needs_validation.append(item)
            active_possible.append(item)
    key = lambda x: (x.get("idade_dias", 9999), x.get("titulo", "").casefold())
    active_possible.sort(key=key)
    likely_expired.sort(key=key)
    needs_validation.sort(key=key)
    return active_possible, rejected, likely_expired, needs_validation


def main():
    now = datetime.now(TZ)
    checked_at = now.isoformat(timespec="seconds")
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
        print(f"    {result['resultado']}; pistas brutas: {len(result.get('candidatos', []))}")
        news_results.append(result)
        if i < len(NEWS_QUERIES)-1:
            time.sleep(1)

    raw_count = sum(len(x.get("candidatos", [])) for x in news_results)
    candidates, rejected, likely_expired, needs_validation = classify_candidates(news_results, now)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "atualizado_em": checked_at,
        "fuso_horario": "America/Sao_Paulo",
        "criterios_triagem": {
            "idade_maxima_dias": MAX_AGE_DAYS,
            "exige_sinal_de_transferencia": True,
            "exige_sinal_de_bonus": True,
            "fontes_secundarias": ["Melhores Destinos", "Mestre das Milhas", "Passageiro de Primeira"],
            "uso_das_fontes_secundarias": "Descoberta de pistas; não confirma atividade sem validade/regulamento.",
            "deduplicacao": "título normalizado sem acentos e pontuação",
            "datas_de_encerramento": "descarta candidatos quando encontra data explícita já vencida",
            "publicacao_automatica": False
        },
        "total_paginas_oficiais": len(official_results),
        "paginas_oficiais_ok": sum(x["resultado"] == "consultado" for x in official_results),
        "total_buscas_publicas": len(news_results),
        "buscas_publicas_ok": sum(x["resultado"] == "consultado" for x in news_results),
        "total_pistas_brutas": raw_count,
        "total_candidatos_triados": len(candidates),
        "total_campanhas_provavelmente_encerradas": len(likely_expired),
        "total_campanhas_aguardando_validacao": len(needs_validation),
        "motivos_exclusao": rejected,
        "aviso": (
            "Candidatos triados não equivalem a promoções ativas. Campanhas com linguagem de encerramento iminente são separadas como provavelmente encerradas; " 
            "as demais permanecem pendentes de validação oficial. A data de publicação é apenas um filtro de recência, "
            "não a validade da campanha. Antes de divulgar, confirmar regulamento, período, bônus por perfil e elegibilidade "
            "em fonte oficial. O monitor não altera data/promocoes.json automaticamente."
        ),
        "paginas_oficiais": official_results,
        "buscas_publicas": news_results,
        "candidatos": candidates,
        "campanhas_provavelmente_encerradas": likely_expired,
        "campanhas_aguardando_validacao": needs_validation,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Relatório salvo em: {OUT}")
    print(f"Páginas oficiais acessíveis: {payload['paginas_oficiais_ok']}/{len(official_results)}")
    print(f"Buscas públicas acessíveis: {payload['buscas_publicas_ok']}/{len(news_results)}")
    print(f"Pistas brutas: {raw_count}; candidatos após triagem: {len(candidates)}")
    print(f"Exclusões: {json.dumps(rejected, ensure_ascii=False)}")
    if payload["paginas_oficiais_ok"] == 0 and payload["buscas_publicas_ok"] == 0:
        raise SystemExit("Nenhuma fonte de consulta respondeu.")


if __name__ == "__main__":
    main()
