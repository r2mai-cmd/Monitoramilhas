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
MONTH_NAMES = {"janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12}
END_DATE_WORD_RE = re.compile(r"(?:válid[oa] até|valida até|até|ate|encerra(?:mento)? em|termina em)\s*(\d{1,2})\s+de\s+(janeiro|fevereiro|março|marco|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)(?:\s+de\s+(\d{4}))?", re.I)
CAMPAIGN_RE = re.compile(r"transfer|transfira|transferência|transferencia|envio de pontos|enviar pontos", re.I)


class LinkExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
    def handle_starttag(self, tag, attrs):
        if tag.lower() == 'a':
            data = dict(attrs)
            href = data.get('href', '')
            if href:
                self.links.append({'href': href, 'title': data.get('title', '')})


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
    google_url = "https://news.google.com/rss/search?q=" + quote_plus(query) + "&hl=pt-BR&gl=BR&ceid=BR:pt-419"
    bing_url = "https://www.bing.com/news/search?q=" + quote_plus(query) + "&format=rss"
    found = []
    attempts = []
    body = ""
    status = None
    provider = None
    for provider_name, url in (("Google News RSS", google_url), ("Bing News RSS", bing_url)):
        try:
            status, _content_type, body = http_get(url, "application/rss+xml,application/xml,text/xml,*/*")
            provider = provider_name
            break
        except HTTPError as e:
            attempts.append({"fonte": provider_name, "http_status": e.code})
        except Exception as e:
            attempts.append({"fonte": provider_name, "erro": f"{type(e).__name__}: {str(e)[:160]}"})
    if provider is None:
        return {"consulta": query, "resultado": "erro_consulta", "tentativas": attempts, "candidatos": []}
    try:
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
                "fonte_busca": provider,
                "status_validacao": "pendente",
                "observacao": "Pista de busca; confirmar campanha, validade e regras em fonte oficial antes de divulgar."
            })
        return {"consulta": query, "resultado": "consultado", "fonte": provider, "http_status": status, "tentativas_anteriores": attempts, "candidatos": found}
    except Exception as e:
        return {"consulta": query, "resultado": "erro_consulta", "fonte": provider, "erro": f"{type(e).__name__}: {str(e)[:220]}", "tentativas_anteriores": attempts, "candidatos": []}
def discover_direct_source(source, checked_at):
    """Busca nos portais especializados usando consultas recentes e genéricas."""
    base = source['url'].rstrip('/')
    search_terms = ['transferência bonificada outubro 2026', 'bônus transferência pontos outubro 2026', 'transferência bonificada milhas']
    results = []
    seen = set()
    attempts = []
    from urllib.parse import urljoin
    for term in search_terms:
        search_url = base + '/?s=' + quote_plus(term) + '&orderby=date&order=desc'
        try:
            status, content_type, body = http_get(search_url)
            parser = LinkExtractor()
            parser.feed(body)
            for link in parser.links:
                href = urljoin(base + '/', html.unescape(link['href']).strip())
                title = re.sub(r'\s+', ' ', html.unescape(link['title'])).strip()
                if not href.startswith(base) or href.rstrip('/') == base or href in seen or not title:
                    continue
                # Título deve sinalizar milhas/pontos e bônus; o artigo será validado por data.
                if not (TRANSFER_RE.search(title) and BONUS_RE.search(title)):
                    continue
                seen.add(href)
                article_title, published_at, description = title, '', ''
                article_status = 'nao_consultado'
                try:
                    article_status_code, _article_type, article_body = http_get(href)
                    article_status = str(article_status_code)
                    date_patterns = [
                        r"<meta[^>]+property=['\"]article:published_time['\"][^>]+content=['\"]([^'\"]+)",
                        r"<meta[^>]+name=['\"]datePublished['\"][^>]+content=['\"]([^'\"]+)",
                        r'"datePublished"\s*:\s*"([^"]+)"',
                        r"<time[^>]+datetime=['\"]([^'\"]+)",
                    ]
                    for date_pattern in date_patterns:
                        date_match = re.search(date_pattern, article_body, re.I)
                        if date_match:
                            published_at = date_match.group(1).strip()
                            break
                    og_title = re.search(r"<meta[^>]+property=['\"]og:title['\"][^>]+content=['\"]([^'\"]+)", article_body, re.I)
                    if og_title:
                        article_title = html.unescape(og_title.group(1)).strip() or article_title
                    article_parser = TextExtractor()
                    article_parser.feed(article_body)
                    description = re.sub(r'\s+', ' ', ' '.join(article_parser.parts)).strip()[:700]
                except Exception:
                    pass
                publication_dt = parse_publication_date(published_at) if published_at else None
                checked_dt = parse_publication_date(checked_at) if checked_at else None
                if publication_dt and checked_dt and (checked_dt - publication_dt).days > MAX_AGE_DAYS:
                    # Artigos antigos ficam no histórico das buscas, mas não entram na descoberta de oportunidades recentes.
                    continue
                results.append({'consulta': search_url, 'titulo': article_title[:300], 'publicador': source['nome'],
                    'publicado_em': published_at, 'link_resultado': href, 'descricao': description, 'consultado_em': checked_at,
                    'fonte_busca': 'busca_direta_site', 'status_validacao': 'pendente_confirmacao_oficial',
                    'http_artigo': article_status,
                    'observacao': 'Artigo encontrado diretamente; confirmar validade no regulamento oficial.'})
                if len(results) >= MAX_RESULTS_PER_QUERY:
                    break
            attempts.append({'consulta': search_url, 'resultado': 'consultado', 'http_status': status, 'itens': len(results)})
        except HTTPError as e:
            attempts.append({'consulta': search_url, 'resultado': 'erro_http', 'http_status': e.code})
        except Exception as e:
            attempts.append({'consulta': search_url, 'resultado': 'erro_consulta', 'erro': f'{type(e).__name__}: {str(e)[:140]}'})
        if len(results) >= MAX_RESULTS_PER_QUERY:
            break
        time.sleep(0.5)
    if results:
        return {'consulta': base, 'resultado': 'consultado', 'fonte': source['nome'], 'tentativas': attempts, 'candidatos': results}
    return {'consulta': base, 'resultado': 'sem_resultados', 'fonte': source['nome'], 'tentativas': attempts, 'candidatos': []}


def parse_publication_date(value):
    if not value:
        return None
    try:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(TZ)
    except (TypeError, ValueError, OverflowError):
        return None


def extract_campaign_end_date(text, published):
    """Extrai datas explícitas de encerramento em formato numérico ou por extenso."""
    text = text or ""
    match = END_DATE_RE.search(text)
    if match:
        day, month, year = match.groups()
        month_num = int(month)
    else:
        match = END_DATE_WORD_RE.search(text)
        if not match:
            return None
        day, month_name, year = match.groups()
        month_num = MONTH_NAMES.get(month_name.casefold())
    year_num = int(year) if year else published.year
    if year_num < 100:
        year_num += 2000
    try:
        return datetime(year_num, month_num, int(day), 23, 59, tzinfo=TZ)
    except (ValueError, TypeError):
        return None


def resolve_article_url(url):
    """Resolve links de notícias e evita confundir a página inicial do Google com o artigo."""
    if "news.google.com/" not in (url or ""):
        return url, None
    try:
        req = Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; Monitoramilhas/3.0)",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.5",
        })
        with urlopen(req, timeout=TIMEOUT_SECONDS) as response:
            body = response.read(MAX_BYTES).decode(response.headers.get_content_charset() or "utf-8", errors="replace")
            final_url = response.geturl()
        for pattern in (
            r"<link[^>]+rel=[\"\']canonical[\"\'][^>]+href=[\"\']([^\"\']+)",
            r"<meta[^>]+property=[\"\']og:url[\"\'][^>]+content=[\"\']([^\"\']+)",
            r"<meta[^>]+content=[\"\']([^\"\']+)[\"\'][^>]+property=[\"\']og:url[\"\']",
        ):
            match = re.search(pattern, body, re.I)
            if match and "news.google.com" not in match.group(1).casefold():
                return html.unescape(match.group(1)), final_url
        if "news.google.com" not in final_url.casefold():
            return final_url, final_url
        return url, final_url
    except Exception:
        return url, None


def inspect_candidate_article(item, now):
    """Tenta ler a notícia de origem para obter evidências adicionais de validade."""
    url = item.get("link_resultado", "")
    if not url:
        item["verificacao_artigo"] = {"resultado": "sem_link"}
        return item
    try:
        original_url, resolved_url = resolve_article_url(url)
        status, content_type, body = http_get(original_url, "text/html,application/xhtml+xml,*/*")
        if "news.google.com" in original_url.casefold():
            item["verificacao_artigo"] = {"resultado": "link_intermediario_nao_resolvido", "url_artigo_resolvida": original_url, "url_intermediaria_resolvida": resolved_url, "http_status": status, "texto_extraido": False}
            item["motivo_status"] = "O link permaneceu no Google Notícias; artigo original e validade não foram confirmados."
            return item
        parser = TextExtractor()
        parser.feed(body)
        full_text = re.sub(r"\\s+", " ", " ".join(parser.parts)).strip()
        # Priorizar o trecho próximo ao título do artigo. O restante da página costuma
        # conter cupons, banners e ofertas laterais com datas que não pertencem à campanha.
        article_title = re.sub(r"\\s+", " ", item.get("titulo", "")).strip()
        title_position = full_text.casefold().find(article_title.casefold()) if article_title else -1
        article_text = full_text[title_position:title_position + 6500] if title_position >= 0 else full_text[:6500]
        # Limitar o texto analisado ao corpo próximo ao artigo e manter evidência rastreável.
        published = parse_publication_date(item.get("publicado_em", "")) or now
        end_date = extract_campaign_end_date(article_text, published)
        item["verificacao_artigo"] = {
            "resultado": "consultado",
            "url_artigo_resolvida": original_url,
            "url_intermediaria_resolvida": resolved_url,
            "http_status": status,
            "content_type": content_type,
            "texto_extraido": bool(article_text),
            "trecho_validade": (article_text[max(0, match.start()-100):min(len(article_text), match.end()+180)]
                                if (match := (END_DATE_RE.search(article_text) or END_DATE_WORD_RE.search(article_text))) else None),
        }
        if end_date:
            item["data_encerramento_detectada"] = end_date.isoformat()
            if now > end_date:
                item["status_validacao"] = "provavelmente_encerrada"
                item["motivo_status"] = "A notícia contém uma data explícita de encerramento anterior à consulta; confirmar no regulamento."
            else:
                item["motivo_status"] = "A notícia contém uma data futura de encerramento, mas a atividade ainda requer confirmação oficial."
        else:
            item["motivo_status"] = "Artigo consultado, mas não foi encontrada uma data de encerramento reconhecível."
    except HTTPError as e:
        item["verificacao_artigo"] = {"resultado": "erro_http", "http_status": e.code}
    except Exception as e:
        item["verificacao_artigo"] = {"resultado": "erro_consulta", "erro": f"{type(e).__name__}: {str(e)[:180]}"}
    return item


def normalize_title(value):
    value = unicodedata.normalize('NFKD', value.casefold())
    value = ''.join(ch for ch in value if not unicodedata.combining(ch))
    # Remove sufixos editoriais para reconhecer a mesma notícia em fontes diferentes.
    value = re.sub(r'\s+-\s+(melhores destinos|passageiro de primeira|pontos pra voar|mestre das milhas)\s*$', '', value)
    value = re.sub(r'\s*\|\s*(melhores destinos|passageiro de primeira|mestre das milhas)\s*$', '', value)
    value = re.sub(r'[^a-z0-9]+', ' ', value)
    return re.sub(r'\s+', ' ', value).strip()


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

    direct_sources = [
        {'nome': 'Melhores Destinos', 'url': 'https://www.melhoresdestinos.com.br'},
        {'nome': 'Mestre das Milhas', 'url': 'https://mestredasmilhas.com'},
        {'nome': 'Passageiro de Primeira', 'url': 'https://passageirodeprimeira.com'},
    ]
    direct_results = []
    for i, source in enumerate(direct_sources):
        print(f"[Busca direta {i+1}/{len(direct_sources)}] {source['nome']}")
        result = discover_direct_source(source, checked_at)
        print(f"    {result['resultado']}; artigos encontrados: {len(result.get('candidatos', []))}")
        direct_results.append(result)
        if i < len(direct_sources)-1:
            time.sleep(1)
    news_results.extend(direct_results)

    raw_count = sum(len(x.get("candidatos", [])) for x in news_results)
    candidates, rejected, likely_expired, needs_validation = classify_candidates(news_results, now)

    # Consulta os artigos dos candidatos triados para procurar validade explícita.
    # Falhas de acesso não eliminam a pista: ela continua pendente de confirmação.
    enriched = []
    for i, item in enumerate(candidates):
        print(f"[Leitura de artigo {i+1}/{len(candidates)}] {item.get('titulo', '')[:100]}")
        enriched.append(inspect_candidate_article(item, now))
        if i < len(candidates)-1:
            time.sleep(1)
    candidates = [x for x in enriched if x.get("status_validacao") != "provavelmente_encerrada"]
    newly_expired = [x for x in enriched if x.get("status_validacao") == "provavelmente_encerrada"]
    likely_expired.extend(newly_expired)
    expired_keys = {normalize_title(x.get("titulo", "")) for x in likely_expired}
    needs_validation = [x for x in candidates if x.get("status_validacao") == "pendente_confirmacao_oficial"]
    for item in candidates:
        if item.get("data_encerramento_detectada") and item not in needs_validation:
            item["status_validacao"] = "validade_futura_nao_confirmada"
    candidates.sort(key=lambda x: (x.get("idade_dias", 9999), x.get("titulo", "").casefold()))

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
        "buscas_diretas_fontes_especializadas": direct_results,
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
