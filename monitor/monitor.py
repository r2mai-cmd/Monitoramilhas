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
from urllib.parse import parse_qs, quote_plus, urlparse
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
END_DATE_CONTEXT_RE = re.compile(r"(?:válid[oa]\s+até|valida\s+até|até|encerra(?:mento)?\s+em|termina\s+em)\s+(?:amanhã[,\s]+\s*)?(?:(?:segunda(?:-feira)?|ter[cç]a(?:-feira)?|quarta(?:-feira)?|quinta(?:-feira)?|sexta(?:-feira)?|s[áa]bado|domingo)[,\s]+\s*)?(\d{1,2})\s+de\s+(janeiro|fevereiro|março|marco|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)(?:\s+de\s+(\d{4}))?", re.I)
END_DATE_TOMORROW_RE = re.compile(r"\b(?:válid[oa]\s+)?(?:até|encerra(?:mento)?(?:\s+em)?|termina(?:\s+em)?)\s+(?:às\s+\d{1,2}(?:[:h]\d{2})?\s*(?:h|horas)?\s+)?(?:de\s+)?amanhã(?:\s*,?\s*\(?(\d{1,2})\)?)?", re.I)
END_DATE_WEEKDAY_RE = re.compile(r"\bat[eé]\s+(?:(?:a|na|nesta|esta)\s+)?(?:segunda(?:-feira)?|ter[cç]a(?:-feira)?|quarta(?:-feira)?|quinta(?:-feira)?|sexta(?:-feira)?|s[áa]bado|domingo)\s*[,()]?\s*(?:dia\s*)?\(?(\d{1,2})\)?", re.I)
CAMPAIGN_RE = re.compile(r"transfer|transfira|transferência|transferencia|envio de pontos|enviar pontos", re.I)


class LinkExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.current_anchor = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() == 'a':
            data = dict(attrs)
            href = data.get('href', '')
            if href:
                self.current_anchor = {'href': href, 'title_attr': data.get('title', ''), 'text_parts': []}

    def handle_data(self, data):
        if self.current_anchor is not None:
            text_value = re.sub(r'\s+', ' ', data).strip()
            if text_value:
                self.current_anchor['text_parts'].append(text_value)

    def handle_endtag(self, tag):
        if tag.lower() == 'a' and self.current_anchor is not None:
            title = self.current_anchor['title_attr'] or ' '.join(self.current_anchor['text_parts'])
            self.links.append({'href': self.current_anchor['href'], 'title': re.sub(r'\s+', ' ', title).strip()})
            self.current_anchor = None


class TextExtractor(HTMLParser):
    VOID_TAGS = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}
    CONTENT_CLASSES = {'entry-content', 'article-content', 'post-content', 'article-body', 'post-body', 'news-article-content', 'single-content'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.title_parts = []
        self.h1_parts = []
        self.article_parts = []
        self.in_title = False
        self.in_h1 = False
        self.skip_depth = 0
        self.article_scope_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs_dict = dict(attrs)
        classes = set((attrs_dict.get('class') or '').casefold().split())
        if tag in ('script', 'style', 'noscript', 'svg'):
            self.skip_depth += 1
        if tag == 'title':
            self.in_title = True
        if tag == 'h1':
            self.in_h1 = True
        if self.article_scope_depth == 0:
            if tag == 'article' or classes.intersection(self.CONTENT_CLASSES):
                self.article_scope_depth = 1
        elif tag not in self.VOID_TAGS:
            self.article_scope_depth += 1

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ('script', 'style', 'noscript', 'svg') and self.skip_depth:
            self.skip_depth -= 1
        if tag == 'title':
            self.in_title = False
        if tag == 'h1':
            self.in_h1 = False
        if self.article_scope_depth and tag not in self.VOID_TAGS:
            self.article_scope_depth -= 1
        if tag in ('p', 'div', 'li', 'br', 'h1', 'h2', 'h3', 'section', 'article'):
            self.parts.append(' ')
            if self.article_scope_depth or tag == 'article':
                self.article_parts.append(' ')

    def handle_data(self, data):
        if self.skip_depth:
            return
        value = re.sub(r'\s+', ' ', data).strip()
        if value:
            self.parts.append(value)
            if self.in_title:
                self.title_parts.append(value)
            if self.in_h1:
                self.h1_parts.append(value)
            if self.article_scope_depth:
                self.article_parts.append(value)


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
    """Consulta Google News e Bing News; preserva links para que Bing possa ser resolvido ao artigo original."""
    urls = (
        ('Google News RSS', 'https://news.google.com/rss/search?q=' + quote_plus(query) + '&hl=pt-BR&gl=BR&ceid=BR:pt-419'),
        ('Bing News RSS', 'https://www.bing.com/news/search?q=' + quote_plus(query) + '&format=rss'),
    )
    found = []
    attempts = []
    successes = []
    for provider_name, url in urls:
        try:
            status, _content_type, body = http_get(url, 'application/rss+xml,application/xml,text/xml,*/*')
            root = ET.fromstring(body)
            count_before = len(found)
            for rss_item in root.findall('.//item')[:MAX_RESULTS_PER_QUERY]:
                title = html.unescape((rss_item.findtext('title') or '').strip())
                link = (rss_item.findtext('link') or '').strip()
                pubdate = (rss_item.findtext('pubDate') or '').strip()
                description = html.unescape(re.sub(r'<[^>]+>', ' ', rss_item.findtext('description') or ''))
                description = re.sub(r'\s+', ' ', description).strip()
                source_node = rss_item.find('source')
                publisher = (source_node.text or '').strip() if source_node is not None else ''
                combined = (title + ' ' + description).casefold()
                if not title or not any(k in combined for k in KEYWORDS):
                    continue
                found.append({
                    'consulta': query, 'titulo': title[:300], 'publicador': publisher[:160],
                    'publicado_em': pubdate, 'link_resultado': link, 'descricao': description[:700],
                    'consultado_em': checked_at, 'fonte_busca': provider_name,
                    'status_validacao': 'pendente',
                    'observacao': 'Pista de busca; confirmar campanha, validade e regras em fonte oficial antes de divulgar.'
                })
            successes.append({'fonte': provider_name, 'http_status': status, 'pistas': len(found)-count_before})
        except HTTPError as e:
            attempts.append({'fonte': provider_name, 'http_status': e.code})
        except Exception as e:
            attempts.append({'fonte': provider_name, 'erro': f'{type(e).__name__}: {str(e)[:160]}'})
    if successes:
        return {'consulta': query, 'resultado': 'consultado', 'fontes': successes, 'tentativas': attempts, 'candidatos': found}
    return {'consulta': query, 'resultado': 'erro_consulta', 'tentativas': attempts, 'candidatos': []}


def discover_direct_source(source, checked_at):
    """Procura artigos por RSS de busca e usa a pesquisa HTML como fallback."""
    base = source["url"].rstrip("/")
    # Consultas específicas primeiro aumentam a chance de recuperar a URL direta
    # das campanhas atuais que o RSS agregado traz apenas via Google Notícias.
    search_terms = [
        "Banco BV Azul bônus transferência pontos",
        "Banestes transferência pontos bônus outubro 2026",
        "C6 Bank LATAM Pass transferência bonificada",
        "transferência bonificada outubro 2026",
        "bônus transferência pontos outubro 2026",
        "transferência bonificada milhas",
    ]
    results = []
    seen = set()
    attempts = []
    from urllib.parse import urljoin
    base_host = (urlparse(base).netloc or "").casefold()

    def add_result(title, href, published_at, description, search_url, provider):
        title = re.sub(r"\s+", " ", html.unescape(title or "")).strip()
        href = urljoin(base + "/", html.unescape(href or "").strip())
        description = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", description or ""))).strip()
        href_host = (urlparse(href).netloc or "").casefold()
        if not title or not href.startswith(("https://", "http://")):
            return
        if not (href_host == base_host or href_host.endswith("." + base_host)):
            return
        if href.rstrip("/") == base or href in seen:
            return
        combined = title + " " + description
        if not TRANSFER_RE.search(combined) or not BONUS_RE.search(combined):
            return
        published_dt = parse_publication_date(published_at) if published_at else None
        checked_dt = parse_publication_date(checked_at) if checked_at else None
        if published_dt and checked_dt and (checked_dt - published_dt).days > MAX_AGE_DAYS:
            return
        seen.add(href)
        results.append({
            "consulta": search_url,
            "titulo": title[:300],
            "publicador": source["nome"],
            "publicado_em": published_at or "",
            "link_resultado": href,
            "descricao": description[:900],
            "consultado_em": checked_at,
            "fonte_busca": provider,
            "status_validacao": "pendente_confirmacao_oficial",
            "observacao": "Artigo encontrado diretamente; confirmar prazo e regulamento oficial."
        })

    for term in search_terms:
        search_url = base + "/?s=" + quote_plus(term) + "&orderby=date&order=desc"
        feed_url = base + "/?s=" + quote_plus(term) + "&feed=rss2"
        feed_count_before = len(results)
        try:
            status, _content_type, feed_body = http_get(feed_url, "application/rss+xml,application/xml,text/xml,*/*")
            root = ET.fromstring(feed_body)
            feed_items = root.findall(".//item")
            for rss_item in feed_items[:MAX_RESULTS_PER_QUERY]:
                add_result(
                    rss_item.findtext("title") or "",
                    rss_item.findtext("link") or "",
                    rss_item.findtext("pubDate") or "",
                    rss_item.findtext("description") or "",
                    feed_url,
                    "RSS direto: " + source["nome"]
                )
                if len(results) >= MAX_RESULTS_PER_QUERY:
                    break
            attempts.append({"consulta": feed_url, "tipo": "rss", "resultado": "consultado",
                             "http_status": status, "itens_feed": len(feed_items),
                             "candidatos_novos": len(results) - feed_count_before})
        except HTTPError as exc:
            attempts.append({"consulta": feed_url, "tipo": "rss", "resultado": "erro_http",
                             "http_status": exc.code})
        except Exception as exc:
            attempts.append({"consulta": feed_url, "tipo": "rss", "resultado": "erro_consulta",
                             "erro": f"{type(exc).__name__}: {str(exc)[:130]}"})

        # O RSS de busca nem sempre é implementado pelo portal. Se não trouxer
        # nenhum candidato novo, tenta a página HTML já usada pela versão anterior.
        if len(results) == feed_count_before and len(results) < MAX_RESULTS_PER_QUERY:
            try:
                status, _content_type, body = http_get(search_url)
                parser = LinkExtractor()
                parser.feed(body)
                html_count_before = len(results)
                for link in parser.links:
                    add_result(link.get("title", ""), link.get("href", ""), "", "",
                               search_url, "busca_direta_site")
                    if len(results) >= MAX_RESULTS_PER_QUERY:
                        break
                attempts.append({"consulta": search_url, "tipo": "html", "resultado": "consultado",
                                 "http_status": status, "candidatos_novos": len(results) - html_count_before})
            except HTTPError as exc:
                attempts.append({"consulta": search_url, "tipo": "html", "resultado": "erro_http",
                                 "http_status": exc.code})
            except Exception as exc:
                attempts.append({"consulta": search_url, "tipo": "html", "resultado": "erro_consulta",
                                 "erro": f"{type(exc).__name__}: {str(exc)[:130]}"})

        if len(results) >= MAX_RESULTS_PER_QUERY:
            break
        time.sleep(0.5)

    if results:
        return {"consulta": base, "resultado": "consultado", "fonte": source["nome"],
                "tentativas": attempts, "candidatos": results}
    return {"consulta": base, "resultado": "sem_resultados", "fonte": source["nome"],
            "tentativas": attempts, "candidatos": []}



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


def find_campaign_end_date(text, published):
    """Retorna (data, trecho) apenas quando o contexto é de prazo da campanha."""
    text = text or ""
    # Remove cláusulas que falam apenas do crédito ou da validade das milhas bônus.
    text = re.sub(
        r"(?is)(?:milhas?\s+|pontos?\s+)?b[oô]nus[^.;!?]{0,220}?(?:ser[aã]o\s+)?creditad[oa]s?[^.;!?]{0,180}",
        " ",
        text,
    )
    patterns = (
        END_DATE_RE,
        END_DATE_CONTEXT_RE,
        END_DATE_WORD_RE,
        END_DATE_TOMORROW_RE,
        END_DATE_WEEKDAY_RE,
    )
    matches = []
    for pattern in patterns:
        matches.extend((match, pattern) for match in pattern.finditer(text))
    matches.sort(key=lambda pair: pair[0].start())

    for match, pattern in matches:
        before = text[max(0, match.start() - 240):match.start()].casefold()
        after = text[match.end():min(len(text), match.end() + 180)].casefold()
        context = before + " " + after

        # Datas de crédito, expiração ou validade das milhas recebidas não são
        # prazo para transferir pontos. Ignorar explicitamente esses contextos.
        if re.search(r"(?:milhas?|pontos?)\s+b[oô]nus.{0,160}(?:creditad|válid[oa]s?\s+por|expir|venc)", before):
            continue
        if re.search(r"(?:creditad|crédito|validade das milhas|válid[oa]s?\s+por|expira|vencimento)", before[-140:]):
            continue
        if re.search(r"(?:milhas?|pontos?)\s+b[oô]nus.{0,100}(?:creditad|válid[oa]s?\s+por|expir|venc)", context):
            continue

        # A data precisa estar próxima de uma referência à própria oferta/
        # campanha/transferência. Datas de cupons e banners laterais ficam de fora.
        if not re.search(r"transfer|transfira|transferência|transferencia|envio de pontos|enviar pontos|promoção|promocao|campanha|oferta|bonifica|bônus|bonus", context):
            continue
        if re.search(r"central de cupons|awardwallet|cashback|seguro viagem", before[-160:] + " " + after[:120]):
            continue

        day = month = year = None
        if pattern is END_DATE_RE:
            day, month, year = match.groups()
            month_num = int(month)
        elif pattern in (END_DATE_CONTEXT_RE, END_DATE_WORD_RE):
            day, month_name, year = match.groups()
            month_num = MONTH_NAMES.get(month_name.casefold())
        elif pattern is END_DATE_TOMORROW_RE:
            day = match.group(1)
            year = str(published.year)
            month_num = published.month
            if day is None:
                tomorrow = published + timedelta(days=1)
                day, month_num, year = str(tomorrow.day), tomorrow.month, str(tomorrow.year)
            elif int(day) < published.day:
                month_num += 1
                if month_num > 12:
                    month_num = 1
                    year = str(published.year + 1)
        else:
            day = match.group(1)
            year = str(published.year)
            month_num = published.month
            if int(day) < published.day:
                month_num += 1
                if month_num > 12:
                    month_num = 1
                    year = str(published.year + 1)

        year_num = int(year) if year else published.year
        if year_num < 100:
            year_num += 2000
        try:
            expiry = datetime(year_num, month_num, int(day), 23, 59, tzinfo=TZ)
        except (ValueError, TypeError):
            continue
        snippet = text[max(0, match.start()-100):min(len(text), match.end()+180)]
        return expiry, snippet
    return None, None


def extract_campaign_end_date(text, published):
    """Compatibilidade com a triagem: retorna apenas a data de encerramento validada."""
    expiry, _snippet = find_campaign_end_date(text, published)
    return expiry



def resolve_article_url(url, publisher=''):
    """Resolve redirecionadores de notícias para a página original do artigo."""
    if not url:
        return url, None
    parsed = urlparse(url)
    host = (parsed.netloc or "").casefold()
    if "bing.com" in host and parsed.path.endswith("/news/apiclick.aspx"):
        target = parse_qs(parsed.query).get("url", [""])[0]
        if target.startswith(("https://", "http://")):
            return target, url
    if "news.google.com/" not in url:
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
        # Alguns links do Google Notícias não redirecionam nem expõem canonical.
        # Procurar links externos no HTML que correspondam ao publicador conhecido.
        publisher_domains = {
            "Passageiro de Primeira": ("passageirodeprimeira.com",),
            "Melhores Destinos": ("melhoresdestinos.com.br",),
            "Mestre das Milhas": ("mestredasmilhas.com",),
            "Pontos pra Voar": ("pontospravoar.com",),
            "Melhores Cartões": ("melhorescartoes.com.br",),
        }
        expected_domains = publisher_domains.get(publisher, ())
        if expected_domains:
            external_links = re.findall(r"""href=["'](https?://[^"']+)["']""", body, re.I)
            for candidate_url in external_links:
                candidate_url = html.unescape(candidate_url).replace("&amp;", "&")
                candidate_host = (urlparse(candidate_url).netloc or "").casefold()
                if any(domain in candidate_host for domain in expected_domains):
                    return candidate_url, final_url
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
        original_url, resolved_url = resolve_article_url(url, item.get('publicador', ''))
        status, content_type, body = http_get(original_url, "text/html,application/xhtml+xml,*/*")
        if "news.google.com" in original_url.casefold():
            item["verificacao_artigo"] = {"resultado": "link_intermediario_nao_resolvido", "url_artigo_resolvida": original_url, "url_intermediaria_resolvida": resolved_url, "http_status": status, "texto_extraido": False}
            item["motivo_status"] = "O link permaneceu no Google Notícias; artigo original e validade não foram confirmados."
            return item
        parser = TextExtractor()
        parser.feed(body)
        full_text = " ".join(parser.parts).strip()
        scoped_text = " ".join(parser.article_parts).strip()
        article_title = item.get("titulo", "").strip()
        # RSS e títulos editoriais podem diferir do H1. Ancorar no parceiro citado
        # e no programa de fidelidade da campanha para incluir o parágrafo de validade.
        title_position = full_text.casefold().find(article_title.casefold()) if article_title else -1
        if title_position >= 0:
            article_text = full_text[title_position:title_position + 16000]
        else:
            title_folded = article_title.casefold()
            partner_aliases = (
                "banco do nordeste", "banco bv", "banestes", "sicredi",
                "c6 bank", "livelo", "credicard", "itaú", "itau", "bnb",
            )
            partner_positions = [
                full_text.casefold().find(alias)
                for alias in partner_aliases
                if alias in title_folded and full_text.casefold().find(alias) >= 0
            ]
            if partner_positions:
                partner_pos = min(partner_positions)
                program_positions = [
                    full_text.casefold().rfind(program, max(0, partner_pos - 700), partner_pos + 1)
                    for program in ("azul fidelidade", "azul", "latam pass", "smiles")
                ]
                program_positions = [pos for pos in program_positions if pos >= 0]
                anchor_pos = max(program_positions) if program_positions else partner_pos
                article_text = full_text[max(0, anchor_pos - 180):anchor_pos + 16000]
            elif scoped_text and article_title.casefold().split(" - ")[0] in scoped_text.casefold():
                article_text = scoped_text[:16000]
            else:
                article_text = scoped_text[:16000] if scoped_text else full_text[:12000]
        published = parse_publication_date(item.get("publicado_em", "")) or now
        end_date, validity_snippet = find_campaign_end_date(article_text, published)
        item["verificacao_artigo"] = {
            "resultado": "consultado",
            "url_artigo_resolvida": original_url,
            "url_intermediaria_resolvida": resolved_url,
            "http_status": status,
            "content_type": content_type,
            "texto_extraido": bool(article_text),
            "trecho_validade": validity_snippet,
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
                # Se a mesma notícia veio de um agregador e depois de uma fonte direta,
                # preferir o link original para permitir ler o corpo e a validade da campanha.
                old_url = existing.get("link_resultado", "")
                new_url = item.get("link_resultado", "")
                old_is_aggregator = "news.google.com/" in old_url or "bing.com/news/apiclick.aspx" in old_url
                new_is_direct = bool(new_url) and "news.google.com/" not in new_url and "bing.com/news/apiclick.aspx" not in new_url
                new_is_bing = "bing.com/news/apiclick.aspx" in new_url
                old_is_google = "news.google.com/" in old_url
                if old_is_aggregator and new_is_direct:
                    for field in ("link_resultado", "publicado_em", "publicador", "fonte_busca", "descricao"):
                        if item.get(field):
                            existing[field] = item[field]
                elif old_is_google and new_is_bing:
                    for field in ("link_resultado", "publicado_em", "publicador", "fonte_busca", "descricao"):
                        if item.get(field):
                            existing[field] = item[field]
                elif len(item.get("descricao", "")) > len(existing.get("descricao", "")):
                    existing["descricao"] = item.get("descricao", "")
                continue
            item["consultas_encontradas"] = [item.get("consulta", "")]
            unique[key] = item

    # Se o agregador e uma fonte direta noticiam a mesma campanha, preferir o link direto.
    direct_items = [
        item for item in unique.values()
        if item.get("link_resultado")
        and "news.google.com/" not in item.get("link_resultado", "")
        and "bing.com/news/apiclick.aspx" not in item.get("link_resultado", "")
    ]
    partner_tokens = (
        ("livelo", ("livelo",)),
        ("itau", ("itau", "itaú", "credicard")),
        ("banestes", ("banestes",)),
        ("banco bv", ("banco bv", "bv")),
        ("c6", ("c6",)),
        ("banco do nordeste", ("banco do nordeste", "bnb")),
    )
    for item in unique.values():
        url = item.get("link_resultado", "")
        if "news.google.com/" not in url and "bing.com/news/apiclick.aspx" not in url:
            continue
        title = item.get("titulo", "").casefold()
        matched = []
        for direct in direct_items:
            direct_title = direct.get("titulo", "").casefold()
            common_partner = any(
                any(token in title for token in tokens) and any(token in direct_title for token in tokens)
                for _label, tokens in partner_tokens
            )
            common_program = (
                ("azul" in title and "azul" in direct_title)
                or ("latam" in title and "latam" in direct_title)
                or ("smiles" in title and "smiles" in direct_title)
            )
            if common_partner and common_program:
                matched.append(direct)
        if len(matched) == 1:
            direct = matched[0]
            item["link_agregador_original"] = url
            for field in ("link_resultado", "publicado_em", "publicador", "fonte_busca", "descricao"):
                if direct.get(field):
                    item[field] = direct[field]
            item["substituido_por_fonte_direta"] = True
            item["observacao_link"] = "Link do agregador substituído por artigo direto da mesma campanha."

    # A substituição de links por fonte direta pode tornar iguais URLs que tinham títulos diferentes.
    # Deduplicar de novo por URL canônica, mantendo títulos e consultas de origem.
    deduplicated = {}
    for item in unique.values():
        url = item.get('link_resultado', '')
        parsed_url = urlparse(url) if url else None
        if parsed_url and 'bing.com' in (parsed_url.netloc or '').casefold() and parsed_url.path.endswith('/news/apiclick.aspx'):
            target = parse_qs(parsed_url.query).get('url', [''])[0]
            if target.startswith(('https://', 'http://')):
                parsed_url = urlparse(target)
        if parsed_url and parsed_url.scheme in ('http', 'https') and parsed_url.netloc and 'news.google.com' not in parsed_url.netloc.casefold():
            canonical_key = 'url:' + parsed_url.netloc.casefold() + parsed_url.path.rstrip('/')
        else:
            canonical_key = 'title:' + normalize_title(item.get('titulo', ''))
        if canonical_key in deduplicated:
            rejected['duplicados'] += 1
            existing = deduplicated[canonical_key]
            queries = set(existing.get('consultas_encontradas', [existing.get('consulta', '')]))
            queries.update(item.get('consultas_encontradas', [item.get('consulta', '')]))
            existing['consultas_encontradas'] = sorted(q for q in queries if q)
            if len(item.get('descricao', '')) > len(existing.get('descricao', '')):
                existing.update({k: v for k, v in item.items() if v not in ('', None)})
        else:
            deduplicated[canonical_key] = item
    unique = deduplicated
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
        {'nome': 'Pontos pra Voar', 'url': 'https://pontospravoar.com'},
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
        "total_candidatos_triados": len(candidates) + len(likely_expired),
        "total_candidatos_para_validacao": len(needs_validation),
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
    print(f"Pistas brutas: {raw_count}; candidatos triados: {len(candidates) + len(likely_expired)}; pendentes de validação: {len(needs_validation)}; provavelmente encerrados: {len(likely_expired)}")
    print(f"Exclusões: {json.dumps(rejected, ensure_ascii=False)}")
    if payload["paginas_oficiais_ok"] == 0 and payload["buscas_publicas_ok"] == 0:
        raise SystemExit("Nenhuma fonte de consulta respondeu.")


if __name__ == "__main__":
    main()
