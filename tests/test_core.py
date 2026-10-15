from datetime import datetime, timezone
import numpy as np
import pandas as pd
import pandas_market_calendars as mcal
import pytest
from briefing.core import indicators, validate_bars, crosscheck, session_context, scenarios, wilder
from briefing.providers import identify


def bars(n=260, flat=False):
    idx = mcal.get_calendar('NYSE').valid_days('2025-01-01', '2026-10-15').tz_localize(None)[:n]
    c = np.ones(n)*100 if flat else np.arange(n, dtype=float)+100
    return pd.DataFrame({'Open': c, 'High': c+2, 'Low': c-2, 'Close': c,
                         'Adj Close': c, 'Volume': np.ones(n)*1000}, index=idx)


def test_numeric_reference():
    f = bars()
    x = indicators(f, f)
    assert x['rsi_14'] == 100
    assert x['atr_14'] == 4
    assert x['macd'] == pytest.approx(7, abs=.0001)
    assert x['macd_histogram'] == pytest.approx(0, abs=.0001)
    assert x['rs_20_vs_spy'] == 0
    assert x['rvol_20'] == 1
    assert x['obv'] == 259000
    assert x['obv_change_5'] == 5000
    assert x['sma_20'] == 349.5


def test_wilder_seed_and_flat_rsi():
    s = pd.Series([np.nan]+[1.]*14+[15.])
    assert wilder(s).iloc[14] == 1
    assert wilder(s).iloc[15] == 2
    f = bars(flat=True)
    assert indicators(f, f)['rsi_14'] == 50
    f = bars()
    f['Close'] = 500-f.Close
    f['Open'] = f.Close
    f['High'], f['Low'] = f.Close+2, f.Close-2
    assert indicators(f, f)['rsi_14'] == 0


def test_rvol_excludes_current_day():
    f = bars()
    f.iloc[-1, f.columns.get_loc('Volume')] = 5000
    assert indicators(f, f)['rvol_20'] == 5

@pytest.mark.parametrize('mutation', ['stale', 'gap', 'duplicate', 'negative', 'nan', 'ohlc'])
def test_invalid_feed_rejected(mutation):
    f = bars()
    expected = f.index[-1].date()
    if mutation == 'stale': f = f.iloc[:-1]
    if mutation == 'gap': f = f.drop(f.index[30])
    if mutation == 'duplicate': f = pd.concat([f, f.iloc[-1:]])
    if mutation == 'negative': f.iloc[-1, f.columns.get_loc('Volume')] = -1
    if mutation == 'nan': f.iloc[-1, f.columns.get_loc('Close')] = np.nan
    if mutation == 'ohlc': f.iloc[-1, f.columns.get_loc('High')] = 1
    with pytest.raises(ValueError): validate_bars(f, expected)


def test_crosscheck_price_and_volume():
    f, g = bars(), bars()
    assert crosscheck(f, g)['status'] == 'verified'
    g.iloc[-1, g.columns.get_loc('Volume')] = 2000
    assert crosscheck(f, g)['status'] == 'mismatch'
    assert crosscheck(f, g.iloc[:-1])['status'] == 'mismatch'


def test_holiday_dst_and_correct_utc_weekday():
    # Mon 22 UTC belongs to Tue 07 KST, should use Monday close.
    x = session_context(datetime(2026, 10, 12, 22, tzinfo=timezone.utc), 'close')
    assert x['last_completed_session'] == '2026-10-12'
    assert x['scheduled_session_valid']
    # Independence Day observation: no trading.
    assert not session_context(datetime(2026, 7, 3, 12, tzinfo=timezone.utc), 'premarket')['scheduled_session_valid']
    # Before DST 12 UTC is 07 ET, after DST 08 ET.
    for day in [6, 9]:
        assert session_context(datetime(2026, 3, day, 12, tzinfo=timezone.utc), 'premarket')['scheduled_session_valid']
    # Early close Friday after Thanksgiving.
    x = session_context(datetime(2026, 11, 27, 19, tzinfo=timezone.utc), 'close')
    assert x['last_completed_session'] == '2026-11-27'
    assert x['scheduled_session_valid']


def test_identity_no_spcx_guess():
    row = {'name': 'The SPAC and New Issue ETF', 'test_issue': 'N'}
    assert identify('SPCX', {'SPCX': row}, [])['status'] == 'identity_mismatch'
    assert identify('SPCX', {}, [])['status'] == 'not_found'
    assert identify('SPCX', {}, [{}])['status'] == 'unknown'
    assert scenarios(None, False, 'SPCX')['cases'] == []
