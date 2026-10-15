"""Public network sources. Errors are deliberately concise and never include credentials."""
import csv
import re
from bs4 import BeautifulSoup
import io
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import xml.etree.ElementTree as ET
import pandas as pd
import requests
import yfinance as yf
from icalendar import Calendar
from .core import EXPECTED

LIST_URLS = ['https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt',
             'https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt']
CALENDARS = {'BLS': 'https://www.bls.gov/schedule/news_release/bls.ics',
             'BEA': 'https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics'}

def fetch(url):
    response = requests.get(url, timeout=(10, 25), headers={'User-Agent': 'gaenyum-briefing/3.0 (public research)'})
    response.raise_for_status()
    return response.text

def listings(now):
    records, sources, errors = {}, [], []
    for url in LIST_URLS:
        try:
            text = fetch(url)
            # Nasdaq file timestamp is Eastern time, not a date inferred from download time.
            footer = next(line for line in text.splitlines() if line.startswith('File Creation Time:'))
            stamp = footer.split(': ', 1)[1].split('|')[0].strip()
            created = pd.Timestamp(datetime.strptime(stamp, '%m%d%Y%H:%M')).tz_localize('America/New_York')
            age = (pd.Timestamp(now)-created).total_seconds()/3600
            if age < -1 or age > 96:
                raise ValueError('directory timestamp outside 96h freshness limit')
            sources.append({'url': url, 'file_created_at': created.isoformat(), 'retrieved_at': now.isoformat()})
            for row in csv.DictReader(io.StringIO(text), delimiter='|'):
                symbol = row.get('Symbol') or row.get('ACT Symbol')
                if not symbol or symbol.startswith('File Creation'):
                    continue
                records[symbol] = {'name': row.get('Security Name'), 'exchange': row.get('Exchange', 'NASDAQ'),
                    'etf': row.get('ETF'), 'test_issue': row.get('Test Issue'),
                    'financial_status': row.get('Financial Status'), 'source': url,
                    'file_created_at': created.isoformat(), 'retrieved_at': now.isoformat()}
        except Exception as exc:
            errors.append({'url': url, 'error': type(exc).__name__})
    return records, sources, errors

def identify(symbol, records, errors):
    row = records.get(symbol)
    if row is None:
        return {'status': 'unknown' if errors else 'not_found', 'reason': '공식 디렉터리에서 확인 불가; 비상장/다른 상품 여부 추정 금지'}
    expected = EXPECTED.get(symbol)
    matches = expected and expected.casefold() in (row['name'] or '').casefold()
    # SPCX needs specific SpaceX identification, never merely the word "Space".
    if symbol == 'SPCX':
        matches = any(x in row['name'].casefold() for x in ['spacex', 'space exploration technologies'])
    return {**row, 'status': 'verified' if matches and row['test_issue'] == 'N' else 'identity_mismatch',
            'expected_name_fragment': expected,
            'caveat': '공식 디렉터리 등재 확인은 실시간 거래정지·상장 유지 보장이 아님'}

def yahoo_daily(symbol, cutoff):
    frame = yf.download(symbol, start=str(pd.Timestamp(cutoff).date()-timedelta(days=550)),
                        end=str(pd.Timestamp(cutoff).date()+timedelta(days=1)),
                        interval='1d', auto_adjust=False, actions=True, progress=False, threads=False)
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)
    frame.index = pd.to_datetime(frame.index).tz_localize(None).normalize()
    return frame.loc[frame.index <= pd.Timestamp(cutoff)]

def stooq_daily(symbol, cutoff):
    url = f'https://stooq.com/q/d/l/?s={symbol.lower()}.us&i=d&d1={(pd.Timestamp(cutoff)-timedelta(days=15)):%Y%m%d}&d2={pd.Timestamp(cutoff):%Y%m%d}'
    frame = pd.read_csv(io.StringIO(fetch(url)), parse_dates=['Date']).set_index('Date').sort_index()
    if frame.empty or not {'Close', 'Volume'}.issubset(frame):
        raise ValueError('secondary data missing')
    return frame, url

def quote(symbol, now, mode):
    """Separate actual extended-hours observations from completed daily candles."""
    frame = yf.Ticker(symbol).history(period='1d', interval='1m', prepost=True, auto_adjust=False)
    if frame.empty:
        raise ValueError('no intraday quote')
    row = frame.iloc[-1]
    stamp = pd.Timestamp(frame.index[-1])
    age = (pd.Timestamp(now)-stamp).total_seconds()/60
    et = stamp.tz_convert('America/New_York')
    session = 'premarket' if et.hour < 9 or (et.hour == 9 and et.minute < 30) else ('afterhours' if et.hour >= 16 else 'regular')
    expected = 'premarket' if mode == 'premarket' else 'afterhours'
    fresh = 0 <= age <= 30 and session == expected and et.date() == pd.Timestamp(now).tz_convert('America/New_York').date()
    return {'price': float(row.Close), 'observed_at': stamp.isoformat(), 'age_minutes': round(age, 2),
            'session': session, 'status': 'indicative_unverified' if fresh else 'stale_or_wrong_session',
            'source': 'Yahoo Finance 1m; indicative, not a consolidated real-time feed',
            'premarket_volume': None, 'premarket_same_time_rvol': None,
            'missing': ['동일 시각 독립 시세 검증', '전체 프리마켓 거래량 및 전일 동일 시간대 비교']}

def economic_calendar(now):
    events, sources, errors = [], [], []
    start, end = now-timedelta(hours=24), now+timedelta(days=30)
    for name, url in CALENDARS.items():
        try:
            cal = Calendar.from_ical(fetch(url))
            if not list(cal.walk('VEVENT')):
                raise ValueError('no VEVENT records')
            sources.append({'name': name, 'url': url, 'retrieved_at': now.isoformat()})
            for item in cal.walk('VEVENT'):
                date = item.decoded('dtstart')
                if not isinstance(date, datetime) or date.tzinfo is None:
                    continue
                if start <= date <= end:
                    events.append({'title': str(item.get('summary', '')), 'at_utc': date.astimezone(timezone.utc).isoformat(),
                                   'at_kst': date.astimezone(__import__('zoneinfo').ZoneInfo('Asia/Seoul')).isoformat(),
                                   'source': url, 'actual': None, 'consensus': None, 'previous': None})
        except Exception as exc:
            errors.append({'source': url, 'error': type(exc).__name__})
    fed_url = 'https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm'
    try:
        events.extend(fomc_events(fetch(fed_url), now, fed_url))
        sources.append({'name': 'Federal Reserve', 'url': fed_url, 'retrieved_at': now.isoformat()})
    except Exception as exc:
        errors.append({'source': fed_url, 'error': type(exc).__name__})
    return {'events': sorted(events, key=lambda x: x.get('at_utc') or x['date_et']), 'sources': sources, 'errors': errors,
            'missing': ['FOMC 정확한 발표 시각·소매판매·기업 실적 일정은 미수집', '발표 실제치·컨센서스·이전치 미수집'],
            'coverage': 'BLS·BEA 발표 일정 및 공식 FOMC 회의 날짜; 전체 경제 일정 아님'}

def news(symbol, now):
    url = f'https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US'
    root = ET.fromstring(fetch(url))
    items = []
    for item in root.findall('.//item'):
        stamp = parsedate_to_datetime(item.findtext('pubDate'))
        if now-timedelta(days=7) <= stamp <= now:
            items.append({'title': item.findtext('title'), 'url': item.findtext('link'), 'published_at': stamp.isoformat(),
                          'verification': 'headline_only_unverified',
                          'fundamental_impact': None, 'causal_claim': None})
    return {'source': url, 'retrieved_at': now.isoformat(), 'items': items[:8],
            'missing': ['원문·기업 공시 교차검증과 매출/이익 영향 수치 확인 전까지 투자 촉매로 확정하지 않음']}

# CIKs identify issuers, not exchange listing. SPCX is intentionally not guessed.
CIKS = {'HIMS': '0001773751', 'TSLA': '0001318605', 'NVDA': '0001045810',
        'AMD': '0000002488', 'MU': '0000723125'}

def official_filings(symbol, now):
    import os
    cik = CIKS.get(symbol)
    if not cik:
        return {'items': [], 'missing': ['공식 발행사 CIK 매핑 미확인; 다른 회사 공시로 대체하지 않음']}
    agent = os.environ.get('SEC_USER_AGENT')
    if not agent:
        return {'items': [], 'missing': ['SEC_USER_AGENT 미설정: SEC 정책에 맞는 연락처 설정 후 공시 원문 연결 가능']}
    url = f'https://data.sec.gov/submissions/CIK{cik}.json'
    r = requests.get(url, timeout=(10, 25), headers={'User-Agent': agent})
    r.raise_for_status()
    data = r.json()
    if symbol not in data.get('tickers', []):
        raise ValueError('SEC issuer ticker mismatch')
    recent = data['filings']['recent']
    items = []
    for i, day in enumerate(recent['filingDate']):
        if (now.date()-timedelta(days=30)).isoformat() <= day <= now.date().isoformat():
            accession = recent['accessionNumber'][i].replace('-', '')
            document = recent['primaryDocument'][i]
            items.append({'form': recent['form'][i], 'filed': day,
                'url': f'https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/{document}',
                'verification': 'official_filing_metadata', 'fundamental_impact': None})
    return {'source': url, 'retrieved_at': now.isoformat(), 'issuer': data['name'], 'items': items[:10],
            'missing': ['공시 존재만 검증; 원문 재무 영향은 미해석']}

def nasdaq_daily(symbol, cutoff):
    asset = 'etf' if symbol in ['SOXL', 'SPY', 'QQQ', 'SMH'] else 'stocks'
    url = f'https://api.nasdaq.com/api/quote/{symbol}/historical'
    response = requests.get(url, params={'assetclass': asset, 'limit': 20,
        'fromdate': str(pd.Timestamp(cutoff).date()-timedelta(days=15)), 'todate': str(cutoff)},
        headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json', 'Origin': 'https://www.nasdaq.com'},
        timeout=(10, 25))
    response.raise_for_status()
    data = response.json().get('data')
    if not data or data.get('symbol') != symbol:
        raise ValueError('Nasdaq missing data or symbol mismatch')
    rows = data['tradesTable']['rows']
    def number(value):
        return float(value.replace('$', '').replace(',', '').strip())
    frame = pd.DataFrame([{'Date': pd.to_datetime(r['date'], format='%m/%d/%Y'),
        **{col: number(r[col.lower()]) for col in ['Open', 'High', 'Low', 'Close', 'Volume']}} for r in rows]).set_index('Date').sort_index()
    return frame, response.url


def secondary_daily(symbol, cutoff):
    try:
        return nasdaq_daily(symbol, cutoff)
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return stooq_daily(symbol, cutoff)


def fomc_events(html, now, source):
    soup = BeautifulSoup(html, 'html.parser')
    result = []
    found = False
    for panel in soup.select('.panel'):
        heading = panel.select_one('.panel-heading')
        year_match = re.search(r'(20[0-9]{2}) FOMC Meetings', heading.get_text(' ', strip=True) if heading else '')
        if not year_match:
            continue
        found = True
        year = int(year_match[1])
        for meeting in panel.select('.fomc-meeting'):
            month = meeting.select_one('.fomc-meeting__month').get_text(strip=True).split('/')[-1]
            days = re.findall(r'\d+', meeting.select_one('.fomc-meeting__date').get_text(strip=True))
            try:
                day = datetime.strptime(f'{year} {month} {days[-1]}', '%Y %B %d').date()
            except ValueError:
                day = datetime.strptime(f'{year} {month} {days[-1]}', '%Y %b %d').date()
            if now.date()-timedelta(days=1) <= day <= now.date()+timedelta(days=30):
                result.append({'title': 'FOMC 회의 마지막 날 (결정 발표 시각 미확인)',
                    'date_et': day.isoformat(), 'at_utc': None,
                    'at_kst': f'{day.isoformat()} 미국 동부 날짜, KST 시각 미확인',
                    'source': source, 'actual': None, 'consensus': None, 'previous': None})
    if not found:
        raise ValueError('FOMC calendar structure unavailable')
    return result
