from datetime import datetime, timezone
import pandas as pd
import requests
from briefing import cli, providers, calendar_data


def test_quote_clock_advances_during_collection(monkeypatch, tmp_path):
    start = datetime(2026, 10, 27, 12, 24, 22, tzinfo=timezone.utc)
    checked = datetime(2026, 10, 27, 12, 25, tzinfo=timezone.utc)
    monkeypatch.setattr(providers, 'listings', lambda *a: ({}, [], []))
    monkeypatch.setattr(providers, 'news', lambda *a: {'items': [], 'missing': []})
    monkeypatch.setattr(providers, 'official_filings', lambda *a: {'status': 'no_recent_filings', 'missing': []})
    monkeypatch.setattr(providers, 'economic_calendar', lambda *a: {'errors': []})
    # Exercise the call site with real daily fixtures from the existing tests.
    from test_core import bars
    idx = __import__('pandas_market_calendars').get_calendar('NYSE').valid_days('2025-01-01', '2026-10-26').tz_localize(None)
    f = bars(300)
    f.index = idx[-300:]
    monkeypatch.setattr(providers, 'identify', lambda *a: {'status': 'verified'})
    monkeypatch.setattr(providers, 'yahoo_daily', lambda *a: f)
    monkeypatch.setattr(providers, 'secondary_daily', lambda *a: (f, 'fixture'))
    intraday = pd.DataFrame({'Close': [396.41]}, index=pd.DatetimeIndex(['2026-10-27T08:24:29-04:00']))
    class Ticker:
        def history(self, **kwargs): return intraday
    monkeypatch.setattr(providers.yf, 'Ticker', lambda *a: Ticker())
    report = cli.build(start, 'premarket', tmp_path, clock=lambda: checked)
    q = report['symbols']['TSLA']['quote']
    assert q['status'] == 'indicative_unverified'
    assert q['age_minutes'] == round(31/60, 2)
    assert q['checked_at'] == checked.isoformat()
    assert report['generated_at_utc'] == start.isoformat()
    # Truly future/stale observations remain rejected.
    assert providers.quote('TSLA', start, 'premarket')['status'] == 'stale_or_wrong_session'
    assert providers.quote('TSLA', datetime(2026, 10, 27, 13, tzinfo=timezone.utc), 'premarket')['status'] == 'stale_or_wrong_session'


def test_bls_exhausted_fallbacks_retain_attempts(monkeypatch, tmp_path):
    now = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(calendar_data, 'SNAPSHOT', tmp_path/'missing')
    def down(url):
        response = requests.Response()
        response.status_code = 403
        raise requests.HTTPError(response=response)
    result = calendar_data.collect(now, down, lambda *a: [], tmp_path)
    assert result['errors']
    assert len(result['warnings']) == 3
    assert all(x['http_status'] == 403 for x in result['warnings'])


def test_quality_gate_and_safe_endpoint():
    row = {'scenario': {'stance': '중립'}, 'official_filings': {'status': 'unavailable', 'http_status': 403}, 'missing': []}
    result = {'symbols': {'TSLA': row}, 'calendar': {'errors': []}}
    assert cli.quality_failures(result)[0]['code'] == 'official_filings_unverified'
    row['official_filings'] = {'status': 'originals_verified'}
    assert cli.quality_failures(result) == []
    row['missing'] = ['뉴스 수집 실패: HTTPError']
    assert cli.quality_failures(result)[0]['code'] == 'collection_failure'
    response = requests.Response()
    response.url = 'https://data.sec.gov/submissions/CIK0001318605.json?contact=secret'
    exc = requests.HTTPError(response=response)
    assert cli.safe_endpoint(exc) == 'https://data.sec.gov/submissions/CIK0001318605.json'
