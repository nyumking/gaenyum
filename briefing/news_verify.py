"""Verify original article availability and narrow, attributable primary-source evidence."""
import re
from datetime import datetime
from decimal import Decimal
from urllib.parse import urlparse
import requests
from .documents import get_html, article

ALIASES = {'HIMS': ['hims', 'hims & hers'], 'TSLA': ['tesla'],
           'SPCX': ['spacex', 'space exploration technologies'],
           'SOXL': ['soxl', 'direxion daily semiconductor bull'],
           'NVDA': ['nvidia'], 'AMD': ['advanced micro devices', 'amd'], 'MU': ['micron']}


def matches_company(text, symbol):
    return any(re.search(r'(?<![a-z0-9])'+re.escape(x)+r'(?![a-z0-9])', text, re.I) for x in ALIASES[symbol])


def supported_claims(text, filings):
    """Exact period, metric, USD amount match; source retrieval alone is not truth validation."""
    patterns = {'revenue': r'\brevenues?\b', 'net_income': r'\bnet (?:income|profit)\b', 'operating_income': r'\boperating (?:income|profit)\b'}
    results = []
    facts = [(item, fact) for item in filings.get('items', [])
             if item.get('verification') == 'official_original_retrieved'
             for fact in item.get('original', {}).get('facts', [])]
    for sentence in re.split(r'(?<=[.!?])\s+', text):
        for metric, pattern in patterns.items():
            if not re.search(pattern, sentence, re.I): continue
            amounts = list(re.finditer(r'\$\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(billion|million|thousand)?', sentence, re.I))
            # Ambiguous multi-value sentences may compare estimates, prior periods or guidance.
            if len(amounts) != 1 or re.search(r'guidance|forecast|expected|estimate|consensus|outlook', sentence, re.I):
                continue
            m = amounts[0]
            multiplier = {'billion': Decimal(10)**9, 'million': Decimal(10)**6, 'thousand': Decimal(1000), None: Decimal(1)}[m[2].lower() if m[2] else None]
            amount = Decimal(m[1].replace(',', ''))*multiplier
            for item, fact in facts:
                context = fact.get('context', {})
                end = context.get('enddate')
                start = context.get('startdate')
                if fact['metric'] != metric or not end or not start: continue
                # Require explicit start AND end dates so quarter and YTD cannot be confused.
                if not (start in sentence and end in sentence): continue
                primary = Decimal(fact['value'])
                if abs(amount-primary) > max(Decimal('.01'), abs(primary)*Decimal('.001')): continue
                results.append({'metric': metric, 'article_value_usd': str(amount), 'official_value_usd': str(primary),
                    'period_start': start, 'period_end': end, 'source': item['url'], 'accession': item['accession'],
                    'status': 'numeric_period_match', 'note': '문장에 명시된 기간·항목·금액만 대조; 기사 전체 사실 확인 아님'})
    return results[:5]


def verify_item(item, symbol, filings, now, cache):
    base = dict(item)
    url = item['url']
    if '/video/' in urlparse(url).path:
        return {**base, 'verification': 'video_transcript_unavailable', 'original': {'status': 'unsupported_video'}}
    try:
        if url not in cache:
            html, metadata = get_html(url)
            cache[url] = article(html, metadata)
        body = cache[url]
        stamp = datetime.fromisoformat(body['published_at'])
        rss = datetime.fromisoformat(item['published_at'])
        if stamp > now or abs((stamp-rss).total_seconds()) > 172800:
            raise ValueError('article/RSS timestamp mismatch')
        content = body['_text']
        if not matches_company(content+' '+body['title'], symbol):
            return {**base, 'verification': 'issuer_not_confirmed_in_original',
                    'original': {k: v for k, v in body.items() if k not in ['_text', 'sec_links']}}
        shared = set(re.findall(r'[a-z0-9]+', item['title'].lower())) & set(re.findall(r'[a-z0-9]+', body['title'].lower()))
        if len(shared) < 3:
            raise ValueError('article/RSS title mismatch')
        claims = supported_claims(content, filings)
        verified_links = [x['url'] for x in filings.get('items', []) if x.get('verification') == 'official_original_retrieved' and x['url'] in body['sec_links']]
        return {**base, 'verification': 'primary_numeric_claims_matched' if claims else 'original_retrieved_unsubstantiated',
            'original': {k: v for k, v in body.items() if k not in ['_text', 'sec_links']},
            'verified_official_links': verified_links, 'supported_claims': claims,
            'fundamental_impact': None, 'causal_claim': None,
            'reason': '원문 조회·발행시각·종목 관련성 확인. 공식 자료와 수치/기간이 일치한 주장만 별도 표시; 가격 인과관계 미확정.'}
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        return {**base, 'verification': 'original_unavailable', 'original': {'status': 'unavailable',
            'error': type(exc).__name__, 'http_status': getattr(getattr(exc, 'response', None), 'status_code', None)}}


def verify_feed(feed, symbol, filings, now, cache):
    items, attempted = [], 0
    for item in feed.get('items', []):
        if attempted >= 2:
            items.append({**item, 'verification': 'not_attempted_budget'})
            continue
        verified = verify_item(item, symbol, filings, now, cache)
        items.append(verified)
        # Video does not consume the HTML verification budget.
        if verified['verification'] != 'video_transcript_unavailable': attempted += 1
    return {**feed, 'items': items,
        'verification_summary': {status: sum(x['verification'] == status for x in items) for status in sorted({x['verification'] for x in items})},
        'missing': ['확인 불가·유료·동영상·예산 초과 기사는 미검증 유지. 원문 접근 성공과 사실 대조 성공은 구분.']}
