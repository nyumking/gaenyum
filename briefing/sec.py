"""Declared, rate-limited SEC access with issuer identity and filing originals."""
import os
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
import requests
from .documents import get_html, filing_body
from .core import EXPECTED

KNOWN_CIKS = {'HIMS': '1773751', 'TSLA': '1318605', 'NVDA': '1045810', 'AMD': '2488', 'MU': '723125', 'SPCX': '1181412'}
_RATE_LAST = 0.0
_INDEX = {}


def user_agent():
    agent = os.environ.get('SEC_USER_AGENT', '').strip()
    if not re.search(r'[^\s@]+@[^\s@]+\.[^\s@]+', agent) or '\n' in agent or '\r' in agent:
        raise ValueError('SEC contact configuration missing')
    return agent


def pace():
    global _RATE_LAST
    delay = .25-(time.monotonic()-_RATE_LAST)
    if delay > 0: time.sleep(delay)
    _RATE_LAST = time.monotonic()


def sec_json(url, agent):
    pace()
    response = requests.get(url, headers={'User-Agent': agent, 'Accept': 'application/json', 'Accept-Encoding': 'gzip, deflate'}, timeout=(10, 25))
    response.raise_for_status()
    return response.json()


def issuer_cik(symbol, agent):
    # Refresh official mapping once per process. Never infer a CIK from a news headline.
    if 'mapping' not in _INDEX:
        try:
            data = sec_json('https://www.sec.gov/files/company_tickers.json', agent)
            _INDEX['mapping'] = {x['ticker']: str(x['cik_str']) for x in data.values()}
        except (requests.RequestException, ValueError, KeyError, TypeError):
            _INDEX['mapping'] = {}
    return _INDEX['mapping'].get(symbol) or KNOWN_CIKS.get(symbol)


def collect(symbol, now):
    try:
        agent = user_agent()
    except ValueError:
        return {'status': 'configuration_required', 'items': [], 'missing': ['SEC_USER_AGENT 연락처 Secret 미설정 또는 형식 오류']}
    if symbol == 'SOXL':
        return collect_fund(now, agent)
    cik = issuer_cik(symbol, agent)
    if not cik:
        return {'status': 'issuer_unresolved', 'items': [], 'missing': ['SEC 공식 CIK 매핑 미확인; 다른 발행사 공시 사용 금지']}
    url = f'https://data.sec.gov/submissions/CIK{int(cik):010d}.json'
    data = sec_json(url, agent)
    if symbol not in data.get('tickers', []):
        raise ValueError('SEC issuer ticker mismatch')
    # Current SEC submissions metadata is the issuer identity, not exchange listing proof.
    recent = data['filings']['recent']
    cutoff = now.date()-timedelta(days=30)
    items = []
    for i, day in enumerate(recent['filingDate']):
        if not cutoff.isoformat() <= day <= now.date().isoformat(): continue
        accepted = recent.get('acceptanceDateTime', [None]*len(recent['filingDate']))[i]
        if not accepted:
            continue  # Date-only records cannot establish what was knowable at report time.
        accepted_dt = datetime.fromisoformat(accepted.replace('Z', '+00:00'))
        if accepted_dt.tzinfo is None or accepted_dt > now:
            continue
        accession = recent['accessionNumber'][i]
        document = recent['primaryDocument'][i]
        if not re.fullmatch(r'\d{10}-\d{2}-\d{6}', accession) or not re.fullmatch(r'[A-Za-z0-9_.-]+', document):
            continue
        link = f'https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace("-", "")}/{document}'
        items.append({'form': recent['form'][i], 'filed': day, 'accepted_at': accepted_dt.isoformat(),
            'accession': accession, 'url': link, 'verification': 'official_filing_metadata',
            'description': recent.get('primaryDocDescription', ['']*len(recent['filingDate']))[i],
            'fundamental_impact': None})
    # Prioritize material disclosures; retain metadata for other filings without claiming body verification.
    priority = {'8-K': 0, '8-K/A': 1, '10-Q': 2, '10-K': 3, '6-K': 4, '20-F': 5}
    items = sorted(items, key=lambda x: (priority.get(x['form'], 99), -datetime.fromisoformat(x['accepted_at']).timestamp()))[:10]
    attempted = 0
    for item in items:
        if item['form'] not in priority or attempted >= 2:
            item['original'] = {'status': 'not_attempted_budget'}
            continue
        attempted += 1
        try:
            pace()
            html, metadata = get_html(item['url'], agent)
            parsed = urlparse(metadata['url'])
            if parsed.hostname not in ['www.sec.gov', 'sec.gov'] or not parsed.path.startswith(f'/Archives/edgar/data/{int(cik)}/'):
                raise ValueError('filing redirected outside verified issuer')
            if any(word in html.lower() for word in ['undeclared automated tool', 'your request originates from an undeclared']):
                raise ValueError('SEC access denied')
            item['original'] = filing_body(html, metadata)
            item['verification'] = 'official_original_retrieved'
        except (requests.RequestException, ValueError, KeyError) as exc:
            item['original'] = {'status': 'unavailable', 'error': type(exc).__name__,
                'http_status': getattr(getattr(exc, 'response', None), 'status_code', None)}
    count = sum(x.get('verification') == 'official_original_retrieved' for x in items)
    return {'status': 'originals_verified' if count else ('no_recent_filings' if not items else 'metadata_only'),
        'source': url, 'retrieved_at': datetime.now(timezone.utc).isoformat(), 'issuer': data['name'],
        'cik': str(int(cik)), 'items': items, 'originals_verified': count,
        'missing': ['원문 확인은 재무 전망/주가 인과관계 확정이 아님; 공시별 확인 상태 참조']}


def collect_fund(now, agent):
    """SOXL belongs to one trust series; trust-wide filings are not SOXL proof."""
    cik, series = '1424958', 'S000027920'
    source = f'https://data.sec.gov/submissions/CIK{int(cik):010d}.json'
    data = sec_json(source, agent)
    if data.get('name', '').casefold() != 'direxion shares etf trust':
        raise ValueError('fund trust identity mismatch')
    recent, items, attempts = data['filings']['recent'], [], 0
    for i, day in enumerate(recent['filingDate']):
        if not (now.date()-timedelta(days=30)).isoformat() <= day <= now.date().isoformat(): continue
        accepted = recent.get('acceptanceDateTime', [None]*len(recent['filingDate']))[i]
        if not accepted or datetime.fromisoformat(accepted.replace('Z', '+00:00')) > now: continue
        if recent['form'][i] not in ['497', '497K', 'N-CSR', 'N-CSRS', 'NPORT-P']: continue
        if attempts >= 5 or len(items) >= 2: break
        attempts += 1
        accession = recent['accessionNumber'][i]
        doc = recent['primaryDocument'][i]
        if not re.fullmatch(r'\d{10}-\d{2}-\d{6}', accession) or not re.fullmatch(r'[A-Za-z0-9_.-]+', doc): continue
        root = f'https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace("-", "")}/'
        try:
            pace()
            index_html, index_meta = get_html(root+accession+'-index.html', agent)
            # Require series and ticker inside the same Series block, not anywhere in a multi-fund index.
            if not fund_index_matches(index_html, series, 'SOXL'): continue
            item = {'form': recent['form'][i], 'filed': day, 'accepted_at': accepted, 'accession': accession,
                    'url': root+doc, 'series': series, 'index': index_meta, 'verification': 'official_series_index_verified', 'fundamental_impact': None}
            try:
                pace()
                html, meta = get_html(root+doc, agent)
                original = filing_body(html, meta)
                from bs4 import BeautifulSoup
                text = BeautifulSoup(html, 'html.parser').get_text(' ', strip=True)
                if not re.search(r'Direxion Daily Semiconductor Bull 3X (?:ETF|Shares)|S000027920', text, re.I):
                    raise ValueError('original does not identify SOXL series')
                item['original'], item['verification'] = original, 'official_original_retrieved'
            except (requests.RequestException, ValueError) as exc:
                item['original'] = {'status': 'unavailable', 'error': type(exc).__name__}
            items.append(item)
        except (requests.RequestException, ValueError): continue
    count = sum(x['verification'] == 'official_original_retrieved' for x in items)
    return {'status': 'originals_verified' if count else 'fund_series_unconfirmed',
        'cik': cik, 'series': series, 'issuer': data['name'], 'source': source, 'items': items, 'originals_verified': count,
        'missing': ['신탁 전체의 다른 펀드 공시를 SOXL 공시로 대체하지 않음; XML 원문은 미지원']}


def fund_index_matches(html, series, ticker):
    from bs4 import BeautifulSoup
    text = BeautifulSoup(html, 'html.parser').get_text(' ', strip=True)
    blocks = re.split(r'(?=Series\s+S\d{9})', text)
    return any(re.search(r'\b'+re.escape(series)+r'\b', block) and re.search(r'\b'+re.escape(ticker)+r'\b', block) for block in blocks if block.startswith('Series '))
