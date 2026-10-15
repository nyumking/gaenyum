"""Live official calendar with bounded, transparently labelled official snapshots."""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
from icalendar import Calendar

BLS = 'https://www.bls.gov/schedule/news_release/bls.ics'
BEA = 'https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics'
SNAPSHOT = Path(__file__).with_name('data')/'bls-calendar.json'
TTL = timedelta(hours=48)


def event(title, stamp, source):
    if stamp.tzinfo is None:
        raise ValueError('calendar event missing timezone')
    return {'title': title, 'at_utc': stamp.astimezone(timezone.utc).isoformat(),
            'at_kst': stamp.astimezone(ZoneInfo('Asia/Seoul')).isoformat(),
            'source': source, 'actual': None, 'consensus': None, 'previous': None}


def parse_ics(text, source, now):
    cal = Calendar.from_ical(text)
    entries = list(cal.walk('VEVENT'))
    if not entries:
        raise ValueError('empty ICS calendar')
    result = []
    for item in entries:
        if item.get('RRULE'):
            raise ValueError('recurring events need explicit expansion')
        if str(item.get('status', '')).upper() == 'CANCELLED':
            continue
        stamp = item.decoded('dtstart')
        if not isinstance(stamp, datetime) or stamp.tzinfo is None:
            raise ValueError('event has no verified time')
        result.append(event(str(item.get('summary', '')), stamp, source))
    if not result or max(x['at_utc'] for x in result) < now.astimezone(timezone.utc).isoformat():
        raise ValueError('calendar has no future coverage')
    return result


def parse_bls_html(html, source):
    soup = BeautifulSoup(html, 'html.parser')
    result = []
    for row in soup.select('tr'):
        cells = row.find_all(['td', 'th'], recursive=False)
        values = [x.get_text(' ', strip=True) for x in cells]
        if len(values) != 3:
            continue
        date, time, title = values
        if not re.search(r'\d{4}', date) or not re.search(r'\d{1,2}:\d{2}\s*[AP]M', time):
            continue  # Holidays have no release time.
        stamp = datetime.strptime(f'{date} {time}', '%A, %B %d, %Y %I:%M %p').replace(tzinfo=ZoneInfo('America/New_York'))
        result.append(event(title, stamp, source))
    if not result:
        raise ValueError('BLS list structure missing')
    return result


def validate_snapshot(data, now):
    reviewed = datetime.fromisoformat(data['verified_at_utc'])
    age = now-reviewed
    if age < timedelta(0) or age > TTL:
        raise ValueError('official snapshot expired (48h maximum)')
    if data['coverage_start'] > (now-timedelta(hours=24)).date().isoformat() or data['coverage_end'] < (now+timedelta(days=30)).date().isoformat():
        raise ValueError('snapshot does not cover report window')
    for x in data['events']:
        if not x['source'].startswith('https://www.bls.gov/schedule/'):
            raise ValueError('snapshot source not official BLS')
        if datetime.fromisoformat(x['at_utc']).tzinfo is None:
            raise ValueError('snapshot event timezone missing')
    return {**data, 'age_hours': round(age.total_seconds()/3600, 2)}


def bls_calendar(now, fetcher, cache_path):
    failures = []
    try:
        text = fetcher(BLS)
        events = parse_ics(text, BLS, now)
        snapshot = {'verified_at_utc': now.isoformat(), 'verification_method': 'direct_official_ics',
            'coverage_start': (now-timedelta(days=1)).date().isoformat(),
            'coverage_end': max(x['at_utc'][:10] for x in events), 'events': events,
            'source_sha256': hashlib.sha256(text.encode()).hexdigest()}
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2))
        return events, {'name': 'BLS', 'url': BLS, 'status': 'live', 'retrieved_at': now.isoformat()}, failures
    except Exception as exc:
        failures.append({'source': BLS, 'error': type(exc).__name__, 'http_status': getattr(getattr(exc, 'response', None), 'status_code', None)})
    try:
        months = sorted({(now-timedelta(days=1)).strftime('%Y/%m'), now.strftime('%Y/%m'), (now+timedelta(days=30)).strftime('%Y/%m')})
        events = []
        for month in months:
            url = f'https://www.bls.gov/schedule/{month}_sched_list.htm'
            events.extend(parse_bls_html(fetcher(url), url))
        snapshot = {'verified_at_utc': now.isoformat(), 'verification_method': 'direct_official_monthly_html',
            'coverage_start': (now-timedelta(days=1)).date().isoformat(),
            'coverage_end': (now+timedelta(days=30)).date().isoformat(), 'events': events}
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2))
        return events, {'name': 'BLS', 'url': BLS, 'status': 'live_html_fallback', 'retrieved_at': now.isoformat()}, failures
    except Exception as exc:
        failures.append({'source': 'BLS monthly HTML', 'error': type(exc).__name__, 'http_status': getattr(getattr(exc, 'response', None), 'status_code', None)})
    # Prefer the newest valid copy. Invalid copies cannot reset their age on reuse.
    valid = []
    for path in [cache_path, SNAPSHOT]:
        try:
            valid.append(validate_snapshot(json.loads(path.read_text()), now))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    if not valid:
        raise ValueError('BLS live sources unavailable; no unexpired official snapshot')
    data = max(valid, key=lambda x: x['verified_at_utc'])
    return data['events'], {'name': 'BLS', 'url': BLS, 'status': 'official_snapshot_fallback',
        'verified_at_utc': data['verified_at_utc'], 'age_hours': data['age_hours'],
        'expires_at_utc': (datetime.fromisoformat(data['verified_at_utc'])+TTL).isoformat(),
        'verification_method': data['verification_method'], 'live_access': 'unavailable'}, failures


def collect(now, fetcher, fomc_parser, state):
    events, sources, errors, warnings = [], [], [], []
    try:
        bls, source, attempts = bls_calendar(now, fetcher, Path(state)/'calendar'/'bls.json')
        events.extend(bls)
        sources.append(source)
        warnings.extend(attempts)
    except Exception as exc:
        errors.append({'source': BLS, 'error': type(exc).__name__, 'reason': 'live unavailable and snapshot missing/expired'})
    try:
        events.extend(parse_ics(fetcher(BEA), BEA, now))
        sources.append({'name': 'BEA', 'url': BEA, 'status': 'live', 'retrieved_at': now.isoformat()})
    except Exception as exc:
        errors.append({'source': BEA, 'error': type(exc).__name__})
    fed = 'https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm'
    try:
        events.extend(fomc_parser(fetcher(fed), now, fed))
        sources.append({'name': 'Federal Reserve', 'url': fed, 'status': 'live', 'retrieved_at': now.isoformat()})
    except Exception as exc:
        errors.append({'source': fed, 'error': type(exc).__name__})
    start, end = now-timedelta(hours=24), now+timedelta(days=30)
    events = [x for x in events if start.date().isoformat() <= (x.get('at_utc') or x['date_et'])[:10] <= end.date().isoformat()]
    unique = {(x['title'], x.get('at_utc') or x.get('date_et'), x['source']): x for x in events}
    return {'events': sorted(unique.values(), key=lambda x: x.get('at_utc') or x['date_et']),
        'sources': sources, 'errors': errors, 'warnings': warnings,
        'missing': ['FOMC 정확한 발표 시각·소매판매·기업 실적 일정 미수집', '발표 실제치·컨센서스·이전치 미수집'],
        'coverage': 'BLS·BEA 발표 일정 및 FOMC 날짜. BLS 대체 사본은 최대 48시간 유효하며 실시간 조회와 구분.'}
