from datetime import datetime, timezone
import json
import pandas as pd
from briefing import cli
from briefing.providers import EXPECTED, identify, listings, economic_calendar
from briefing.report import write_report
from test_core import bars


def test_end_to_end_missing_and_verified(monkeypatch, tmp_path):
    now = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
    # A single test feed is reused as a fixture, never as a live quote.
    idx = __import__('pandas_market_calendars').get_calendar('NYSE').valid_days('2025-01-01', '2026-10-14').tz_localize(None)
    f = bars(len(idx))
    directory = {s: {'name': name, 'test_issue': 'N'} for s, name in EXPECTED.items()}
    directory['SPCX']['name'] = 'The SPAC and New Issue ETF'
    monkeypatch.setattr(cli.p, 'listings', lambda now: (directory, [], []))
    monkeypatch.setattr(cli.p, 'yahoo_daily', lambda symbol, cutoff: f)
    monkeypatch.setattr(cli.p, 'secondary_daily', lambda symbol, cutoff: (f, 'https://stooq.com'))
    monkeypatch.setattr(cli.p, 'news', lambda *a: {'items': [], 'missing': ['원문 미검증']})
    monkeypatch.setattr(cli.p, 'official_filings', lambda *a: {'items': [], 'missing': []})
    monkeypatch.setattr(cli.p, 'quote', lambda *a: (_ for _ in ()).throw(ValueError()))
    monkeypatch.setattr(cli.p, 'economic_calendar', lambda *a: {'events': [], 'sources': [], 'errors': [], 'missing': [], 'coverage': 'fixture'})
    result = cli.build(now, 'premarket', tmp_path/'state')
    assert result['status'] == 'degraded'
    assert result['symbols']['SPCX']['scenario']['stance'] == '판단 보류'
    assert result['symbols']['TSLA']['scenario']['stance'] == '중립'
    assert len(result['outcomes']) == 18  # 6 valid symbols × 3 horizons
    write_report(result, tmp_path/'output')
    assert 'NaN' not in (tmp_path/'output/briefing.json').read_text()
    assert '판단 보류' in (tmp_path/'output/briefing.md').read_text()
    json.loads((tmp_path/'output/briefing.json').read_text())


def test_scheduled_weekend_skips_network(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.p, 'listings', lambda *a: (_ for _ in ()).throw(AssertionError()))
    result = cli.build(datetime(2026, 10, 17, 12, tzinfo=timezone.utc), 'premarket', tmp_path, scheduled=True)
    assert result['status'].startswith('skipped')


def test_official_directory_freshness_and_name(monkeypatch):
    text = 'Symbol|Security Name|Test Issue\nTSLA|Tesla Inc.|N\nFile Creation Time: 1015202608:00|||'
    monkeypatch.setattr(cli.p, 'fetch', lambda url: text)
    records, sources, errors = listings(datetime(2026, 10, 15, 12, tzinfo=timezone.utc))
    assert not errors
    assert identify('TSLA', records, errors)['status'] == 'verified'
    assert sources[0]['file_created_at'] == '2026-10-15T08:00:00-04:00'
    records, _, errors = listings(datetime(2026, 10, 21, 12, tzinfo=timezone.utc))
    assert not records
    assert errors


def test_calendar_timezone_and_bounds(monkeypatch):
    text = '''BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
SUMMARY:CPI
DTSTART:20261015T123000Z
END:VEVENT
END:VCALENDAR'''
    monkeypatch.setattr(cli.p, 'fetch', lambda url: text)
    result = economic_calendar(datetime(2026, 10, 15, 12, tzinfo=timezone.utc))
    assert result['events'][0]['at_kst'] == '2026-10-15T21:30:00+09:00'
    assert result['events'][0]['actual'] is None


def test_schedule_contract():
    from pathlib import Path
    import yaml
    workflow = yaml.safe_load(Path('.github/workflows/market-indicators.yml').read_text())
    trigger = workflow.get('on', workflow.get(True))
    assert {x['cron'] for x in trigger['schedule']} == {'0 12 * * 1-5', '0 22 * * 1-5'}
    assert workflow['permissions'] == {'contents': 'read'}
    assert 'pull_request_target' not in trigger


def test_fomc_official_dates_without_invented_time():
    from briefing.providers import fomc_events
    html = '''<div class="panel"><div class="panel-heading">2026 FOMC Meetings</div>
    <div class="fomc-meeting"><div class="fomc-meeting__month">October</div>
    <div class="fomc-meeting__date">27-28*</div></div></div>'''
    result = fomc_events(html, datetime(2026, 10, 15, tzinfo=timezone.utc), 'https://www.federalreserve.gov')
    assert result[0]['date_et'] == '2026-10-28'
    assert result[0]['at_utc'] is None


def test_nasdaq_response_and_symbol_validation(monkeypatch):
    from briefing.providers import nasdaq_daily
    class Response:
        url = 'https://api.nasdaq.com/api/quote/TSLA/historical'
        def raise_for_status(self): pass
        def json(self): return {'data': {'symbol': 'TSLA', 'tradesTable': {'rows': [
            {'date': '10/14/2026', 'open': '$100.00', 'high': '$102.00', 'low': '$99.00', 'close': '$101.00', 'volume': '1,234'}]}}}
    monkeypatch.setattr(cli.p.requests, 'get', lambda *a, **kw: Response())
    frame, url = nasdaq_daily('TSLA', '2026-10-14')
    assert frame.iloc[0].Volume == 1234
    assert frame.iloc[0].Close == 101
    import pytest
    with pytest.raises(ValueError): nasdaq_daily('NVDA', '2026-10-14')


def test_all_sources_down_still_writes_report(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.p, 'listings', lambda now: ({}, [], [{'error': 'HTTPError'}]))
    def down(*args): raise ValueError('offline')
    monkeypatch.setattr(cli.p, 'news', down)
    monkeypatch.setattr(cli.p, 'official_filings', down)
    monkeypatch.setattr(cli.p, 'economic_calendar', lambda *a: {'events': [], 'sources': [], 'errors': [], 'missing': ['offline'], 'coverage': 'none'})
    result = cli.build(datetime(2026, 10, 15, 12, tzinfo=timezone.utc), 'premarket', tmp_path/'state')
    write_report(result, tmp_path/'output')
    assert result['status'] == 'degraded'
    assert all(x['scenario']['stance'] == '판단 보류' for x in result['symbols'].values())
    assert not list((tmp_path/'state').rglob('*.json'))
