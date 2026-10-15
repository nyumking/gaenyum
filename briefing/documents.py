"""Bounded public HTML retrieval and evidence extraction, without saving news bodies."""
import hashlib
import ipaddress
import json
import re
import socket
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
import requests

MAX_BYTES = 8_000_000
UA = 'gaenyum-briefing/3.1 (+https://github.com/nyumking/gaenyum)'


def public_url(url):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in [None, 443]:
        raise ValueError('only public HTTPS document URLs supported')
    for item in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM):
        if not ipaddress.ip_address(item[4][0]).is_global:
            raise ValueError('nonpublic destination refused')
    return url


def get_html(url, user_agent=UA):
    for _ in range(6):
        public_url(url)
        with requests.get(url, headers={'User-Agent': user_agent, 'Accept': 'text/html,application/xhtml+xml'},
                          allow_redirects=False, stream=True, timeout=(10, 25)) as response:
            if response.status_code in [301, 302, 303, 307, 308]:
                url = urljoin(url, response.headers['Location'])
                continue
            response.raise_for_status()
            if not any(x in response.headers.get('Content-Type', '').lower() for x in ['text/html', 'application/xhtml+xml', 'text/plain']):
                raise ValueError('document is not supported HTML/text')
            parts, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError('document too large')
                parts.append(chunk)
            raw = b''.join(parts)
            text = raw.decode(response.encoding or 'utf-8', errors='replace')
            return text, {'url': url, 'retrieved_at': datetime.now(timezone.utc).isoformat(),
                          'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': size}
    raise ValueError('too many redirects')


def article(html, metadata):
    soup = BeautifulSoup(html, 'html.parser')
    structured = []
    def walk(obj):
        if isinstance(obj, dict):
            kind = obj.get('@type', [])
            kind = [kind] if isinstance(kind, str) else kind
            if any(x in ['NewsArticle', 'Article', 'ReportageNewsArticle'] for x in kind):
                structured.append(obj)
            for value in obj.values(): walk(value)
        elif isinstance(obj, list):
            for value in obj: walk(value)
    for tag in soup.find_all('script', type='application/ld+json'):
        try: walk(json.loads(tag.string or tag.get_text()))
        except (ValueError, TypeError): continue
    node = max(structured, key=lambda x: len(x.get('articleBody') or ''), default={})
    if node.get('isAccessibleForFree') in [False, 'false']:
        raise ValueError('paywall declared')
    published = node.get('datePublished')
    if not published:
        tag = soup.find('meta', attrs={'property': 'article:published_time'})
        published = tag.get('content') if tag else None
    title = node.get('headline') or (soup.find('h1').get_text(' ', strip=True) if soup.find('h1') else '')
    text = node.get('articleBody')
    if not text:
        main = soup.find('article') or soup.select_one('.caas-body')
        if main:
            for unwanted in main.select('script, style, nav, aside, footer, form'): unwanted.decompose()
            text = ' '.join(p.get_text(' ', strip=True) for p in main.find_all('p'))
    text = ' '.join((text or '').split())
    if len(text.split()) < 100 or not title:
        raise ValueError('article body missing or incomplete')
    stamp = datetime.fromisoformat(str(published).replace('Z', '+00:00')) if published else None
    if stamp is None or stamp.tzinfo is None:
        raise ValueError('publication time unverified')
    # Keep the body in memory only. Reports retain digest, metadata, and short evidence.
    return {**metadata, 'title': title, 'published_at': stamp.astimezone(timezone.utc).isoformat(),
            'word_count': len(text.split()), '_text': text,
            'sec_links': [urljoin(metadata['url'], a['href']) for a in soup.find_all('a', href=True)
                          if '/Archives/edgar/data/' in a['href'] and urlparse(urljoin(metadata['url'], a['href'])).hostname in ['sec.gov', 'www.sec.gov']]}


METRICS = {
    'revenue': ['RevenueFromContractWithCustomerExcludingAssessedTax', 'Revenues', 'SalesRevenueNet'],
    'net_income': ['NetIncomeLoss', 'ProfitLoss'],
    'operating_income': ['OperatingIncomeLoss'],
}


def inline_facts(html):
    soup = BeautifulSoup(html, 'html.parser')
    contexts = {}
    dimensioned = set()
    for tag in soup.find_all(lambda t: t.name and t.name.lower().split(':')[-1] == 'context'):
        dates = {}
        for date in tag.find_all(lambda t: t.name and t.name.lower().split(':')[-1] in ['startdate', 'enddate', 'instant']):
            dates[date.name.lower().split(':')[-1]] = date.get_text(strip=True)
        contexts[tag.get('id')] = dates
        if tag.find(lambda t: t.name and t.name.lower().split(':')[-1] in ['explicitmember', 'typedmember']):
            dimensioned.add(tag.get('id'))
    units = {}
    for tag in soup.find_all(lambda t: t.name and t.name.lower().split(':')[-1] == 'unit'):
        units[tag.get('id')] = tag.get_text(' ', strip=True)
    facts = []
    for tag in soup.find_all(lambda t: t.name and t.name.lower().split(':')[-1] == 'nonfraction'):
        if tag.get('contextref') in dimensioned: continue
        name = tag.get('name', '')
        metric = next((k for k, names in METRICS.items() if name.split(':')[-1] in names and name.startswith('us-gaap:')), None)
        if not metric or 'USD' not in units.get(tag.get('unitref'), ''):
            continue
        # Only ordinary decimal SEC formats are supported. Do not guess locale transforms.
        if tag.get('format', '').split(':')[-1] not in ['', 'num-dot-decimal', 'numdotdecimal', 'numcommadecimal']:
            continue
        if tag.get('format', '').endswith('numcommadecimal'):
            continue
        text = tag.get_text('', strip=True).replace(',', '').replace('$', '')
        if not re.fullmatch(r'\(?-?[0-9]+(?:\.[0-9]+)?\)?', text):
            continue
        try:
            number = Decimal(text.strip('()'))*(Decimal(10)**int(tag.get('scale', 0)))
            if tag.get('sign') == '-' or text.startswith('('): number = -abs(number)
        except (InvalidOperation, ValueError): continue
        facts.append({'metric': metric, 'tag': name, 'value': str(number), 'unit': 'USD',
                      'context': contexts.get(tag.get('contextref'), {}), 'context_id': tag.get('contextref')})
    return facts[:100]


def filing_body(html, metadata):
    soup = BeautifulSoup(html, 'html.parser')
    facts = inline_facts(html)
    for node in soup.select('script, style'): node.decompose()
    text = soup.get_text(' ', strip=True)
    if len(text.split()) < 100:
        raise ValueError('filing body incomplete')
    return {**metadata, 'word_count': len(text.split()), 'facts': facts,
            'evidence_excerpt': ' '.join(text.split()[:20]), 'status': 'official_original_retrieved'}
