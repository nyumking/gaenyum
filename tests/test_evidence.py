import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from briefing import calendar_data as c, sec
from briefing.documents import article, inline_facts, public_url
from briefing.news_verify import verify_item, supported_claims
from briefing.audit import write_receipt, verify_receipt
from briefing.archive import stage_archive

NOW = datetime(2026, 10, 15, 18, tzinfo=timezone.utc)
META = {'url': 'https://publisher.example/article', 'sha256': 'abc', 'retrieved_at': NOW.isoformat()}


def test_bls_snapshot_expires_and_does_not_refresh_itself(tmp_path, monkeypatch):
    d = {'verified_at_utc': (NOW-timedelta(hours=47)).isoformat(), 'coverage_start': '2026-10-01',
         'coverage_end': '2026-11-30', 'verification_method': 'official_test_snapshot',
         'events': [c.event('CPI', NOW+timedelta(days=1), 'https://www.bls.gov/schedule/2026/10_sched_list.htm')]}
    path = tmp_path/'bls.json'
    path.write_text(json.dumps(d))
    monkeypatch.setattr(c, 'SNAPSHOT', tmp_path/'absent.json')
    def down(url): raise ValueError('offline')
    _, source, warnings = c.bls_calendar(NOW, down, path)
    assert source['status'] == 'official_snapshot_fallback'
    assert source['verified_at_utc'] == d['verified_at_utc']
    assert json.loads(path.read_text()) == d
    assert warnings
    with pytest.raises(ValueError): c.bls_calendar(NOW+timedelta(hours=2), down, path)


def test_bls_html_and_dst():
    html = '<table><tr><td>Tuesday, November 10, 2026</td><td>08:30 AM</td><td>CPI</td></tr></table>'
    data = c.parse_bls_html(html, 'https://www.bls.gov/schedule/2026/11_sched_list.htm')
    assert data[0]['at_kst'] == '2026-11-10T22:30:00+09:00'
    assert data[0]['actual'] is None


def test_live_calendar_caches_and_rejects_obsolete_schedule(tmp_path):
    ics = 'BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nDTSTART:20261130T133000Z\nSUMMARY:CPI\nEND:VEVENT\nEND:VCALENDAR'
    _, source, attempts = c.bls_calendar(NOW, lambda u: ics, tmp_path/'bls.json')
    assert source['status'] == 'live'
    assert not attempts
    with pytest.raises(ValueError): c.parse_ics(ics.replace('20261130', '20251001'), c.BLS, NOW)


def news_html(text=None, paywall=False):
    text = text or ('Tesla published its quarterly report. '*40)
    return '<script type="application/ld+json">'+json.dumps({'@type':'NewsArticle','headline':'Tesla quarterly report update',
        'datePublished':'2026-10-15T14:00:00Z','articleBody':text,'isAccessibleForFree':not paywall})+'</script>'


def test_original_article_is_not_claim_verification(monkeypatch):
    import briefing.news_verify as n
    monkeypatch.setattr(n, 'get_html', lambda url: (news_html(), META))
    x = verify_item({'title':'Tesla quarterly report update', 'url':META['url'], 'published_at':'2026-10-15T14:00:00+00:00'}, 'TSLA', {}, NOW, {})
    assert x['verification'] == 'original_retrieved_unsubstantiated'
    assert x['supported_claims'] == []
    assert '_text' not in json.dumps(x)
    assert x['causal_claim'] is None


def test_paywall_and_missing_body_never_verified():
    with pytest.raises(ValueError): article(news_html(paywall=True), META)
    with pytest.raises(ValueError): article(news_html('A short preview only.'), META)


def test_news_wrong_company_and_future_timestamp(monkeypatch):
    import briefing.news_verify as n
    monkeypatch.setattr(n, 'get_html', lambda url: (news_html(), META))
    item = {'title':'Tesla quarterly report update', 'url':META['url'], 'published_at':'2026-10-15T14:00:00+00:00'}
    assert verify_item(item, 'MU', {}, NOW, {})['verification'] == 'issuer_not_confirmed_in_original'
    assert verify_item(item, 'TSLA', {}, NOW-timedelta(days=2), {})['verification'] == 'original_unavailable'


def test_inline_fact_scale_and_period():
    html = '''<xbrli:context id="q"><xbrli:period><xbrli:startDate>2026-07-01</xbrli:startDate><xbrli:endDate>2026-09-30</xbrli:endDate></xbrli:period></xbrli:context>
    <xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
    <ix:nonFraction name="us-gaap:Revenues" contextRef="q" unitRef="usd" scale="6" format="ixt:num-dot-decimal">2,000</ix:nonFraction>'''
    f = inline_facts(html)[0]
    assert f['value'] == '2000000000'
    assert f['context']['enddate'] == '2026-09-30'
    filings = {'items':[{'verification':'official_original_retrieved','url':'https://www.sec.gov/a', 'accession':'test','original':{'facts':[f]}}]}
    assert supported_claims('Revenue was $2 billion for 2026-07-01 through 2026-09-30.', filings)
    assert not supported_claims('Revenue was $2 billion in 2026.', filings)
    assert not supported_claims('Expected revenue is $2 billion for 2026-07-01 through 2026-09-30.', filings)


def test_sec_missing_configuration_does_not_request(monkeypatch):
    monkeypatch.delenv('SEC_USER_AGENT', raising=False)
    monkeypatch.setattr(sec, 'sec_json', lambda *a: (_ for _ in ()).throw(AssertionError()))
    assert sec.collect('TSLA', NOW)['status'] == 'configuration_required'


def test_sec_identity_and_future_filing_rejected(monkeypatch):
    monkeypatch.setenv('SEC_USER_AGENT', 'test research@test.example')
    monkeypatch.setattr(sec, 'issuer_cik', lambda *a: '1318605')
    monkeypatch.setattr(sec, 'sec_json', lambda *a: {'tickers':['OTHER']})
    with pytest.raises(ValueError): sec.collect('TSLA', NOW)
    data = {'tickers':['TSLA'], 'name':'Tesla', 'filings':{'recent':{'filingDate':['2026-10-15'],
            'acceptanceDateTime':['2026-10-15T23:00:00Z']}}}
    monkeypatch.setattr(sec, 'sec_json', lambda *a: data)
    assert sec.collect('TSLA', NOW)['status'] == 'no_recent_filings'


@pytest.mark.parametrize('url',['http://example.com','https://127.0.0.1/a','https://user:password@example.com/a','https://example.com:8443/a'])
def test_nonpublic_or_credential_url_rejected(url):
    with pytest.raises(ValueError): public_url(url)


def test_receipt_cannot_turn_push_into_schedule_and_archive_is_immutable(monkeypatch, tmp_path):
    out = tmp_path/'output'
    out.mkdir()
    for f in ['briefing.md','briefing.json']: (out/f).write_text('report')
    report = {'mode':'close','generated_at_utc':'2026-10-15T22:10:00+00:00',
              'generated_at_kst':'2026-10-16T07:10:00+09:00','status':'partial','context':{'scheduled_session_valid':True}}
    monkeypatch.setenv('GITHUB_EVENT_NAME','push')
    monkeypatch.setenv('BRIEFING_SCHEDULE','0 22 * * 1-5')
    monkeypatch.setenv('GITHUB_RUN_ID','123')
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT','1')
    assert not write_receipt(report,out)['scheduled_slot_valid']
    monkeypatch.setenv('GITHUB_EVENT_NAME','schedule')
    assert write_receipt(report,out)['scheduled_slot_valid']
    target = stage_archive(out, tmp_path/'state', tmp_path/'history')
    assert verify_receipt(target)['run_id'] == '123'
    assert (tmp_path/'history/state/.keep').exists()
    (out/'briefing.md').write_text('tampered')
    with pytest.raises(ValueError): verify_receipt(out)


def test_fund_series_cannot_match_ticker_in_another_series():
    from briefing.sec import fund_index_matches
    html = '<p>Series S000027920 Semiconductor fund</p><p>Series S000027921 Another fund SOXL</p>'
    assert not fund_index_matches(html, 'S000027920', 'SOXL')
    assert fund_index_matches('<p>Series S000027920 Semiconductor SOXL</p>', 'S000027920', 'SOXL')
