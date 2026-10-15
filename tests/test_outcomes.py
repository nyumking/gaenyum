import json
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import pandas_market_calendars as mcal
from briefing.outcomes import evaluate, save_snapshot


def test_no_lookahead_and_trading_days(tmp_path):
    # Friday after close; next session is Tuesday after Memorial Day.
    snapshot = {'id': 'test', 'generated_at': '2026-05-22T22:00:00+00:00',
                'decisions': {'TSLA': {'stance': '긍정'}}}
    (tmp_path/'test.json').write_text(json.dumps(snapshot))
    idx = mcal.get_calendar('NYSE').valid_days('2026-05-26', '2026-07-01').tz_localize(None)
    frame = pd.DataFrame({'Open': 100., 'Close': 110., 'Adj Close': 110.}, index=idx)
    frame.loc['2026-06-01', 'Close'] = 120.
    frame.loc['2026-06-01', 'Adj Close'] = 120.
    h = {'TSLA': frame, 'SPY': frame}
    x = evaluate(tmp_path, h, datetime(2026, 5, 29, 22, tzinfo=timezone.utc))
    assert all(row['status'] == 'pending' for row in x)
    assert x[0]['entry_session'] == '2026-05-26'
    assert x[0]['target_session'] == '2026-06-01'
    x = evaluate(tmp_path, h, datetime(2026, 6, 1, 22, tzinfo=timezone.utc))
    assert abs(x[0]['return']-.2) < 1e-8
    assert x[0]['excess_return'] == 0
    assert x[0]['direction_correct'] is True
    assert x[1]['status'] == 'pending'
    assert evaluate(tmp_path, {}, datetime(2026, 6, 1, 22, tzinfo=timezone.utc))[0]['status'] == 'missing_data'


def test_first_snapshot_immutable_and_empty_not_reserved(tmp_path):
    report = {'context': {'market_date': '2026-10-15', 'last_completed_session': '2026-10-14'},
              'generated_at_utc': '2026-10-15T12:00:00+00:00', 'mode': 'premarket', 'symbols': {}}
    assert save_snapshot(report, tmp_path) is None
    report['symbols'] = {'TSLA': {'scenario': {'stance': '긍정'}, 'indicators': {'close': 100}}}
    path = save_snapshot(report, tmp_path)
    initial = path.read_text()
    report['symbols']['TSLA']['scenario']['stance'] = '부정'
    save_snapshot(report, tmp_path)
    assert path.read_text() == initial


def test_summary_excludes_neutral_and_pending():
    from briefing.outcomes import summarize
    rows = [
        {'horizon': 5, 'status': 'evaluated', 'direction_correct': True, 'excess_return': .1},
        {'horizon': 5, 'status': 'evaluated', 'direction_correct': None, 'excess_return': 0},
        {'horizon': 5, 'status': 'pending'}]
    s = summarize(rows)
    assert s['5']['direction_accuracy'] == 1
    assert s['5']['directional_sample_size'] == 1
    assert s['5']['mean_excess_return'] == .05
    assert s['10']['direction_accuracy'] is None
