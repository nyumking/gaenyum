"""Immutable forecast snapshots and no-lookahead, session-based outcome evaluation."""
import json
from pathlib import Path
import pandas as pd
import pandas_market_calendars as mcal


def save_snapshot(report, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    key = f"{report['context']['market_date']}-{report['mode']}"
    path = directory/f'{key}.json'
    # First successful decision for a slot is immutable; retries cannot rewrite it.
    if path.exists():
        return path
    decisions = {}
    for symbol, data in report['symbols'].items():
        stance = data.get('scenario', {}).get('stance')
        if stance in ['긍정', '부정', '중립']:
            decisions[symbol] = {'stance': stance, 'signal_session': report['context']['last_completed_session'],
                                 'signal_close': data['indicators']['close']}
    # Do not reserve an empty slot: a repaired run must be able to record its first decision.
    if not decisions:
        return None
    with path.open('x', encoding='utf-8') as handle:
        json.dump({'id': key, 'generated_at': report['generated_at_utc'],
                   'entry_rule': 'first regular-session open strictly after generation',
                   'decisions': decisions}, handle, ensure_ascii=False, indent=2)
    return path


def evaluate(snapshots, histories, asof):
    outcomes = []
    for path in sorted(Path(snapshots).glob('*.json')):
        snapshot = json.loads(path.read_text())
        generated = pd.Timestamp(snapshot['generated_at'])
        schedule = mcal.get_calendar('NYSE').schedule(start_date=generated.date(),
                                                       end_date=(generated+pd.Timedelta(days=80)).date())
        sessions = schedule[schedule.market_open > generated]
        if sessions.empty:
            continue
        entry_day = sessions.index[0]
        for symbol, decision in snapshot['decisions'].items():
            for horizon in [5, 10, 20]:
                target = sessions.index[horizon-1]
                base = {'forecast_id': snapshot['id'], 'symbol': symbol, 'stance': decision['stance'],
                        'entry_session': str(entry_day.date()), 'target_session': str(target.date()), 'horizon': horizon}
                if schedule.loc[target, 'market_close'] > pd.Timestamp(asof):
                    outcomes.append({**base, 'status': 'pending'})
                    continue
                stock, spy = histories.get(symbol), histories.get('SPY')
                try:
                    # All current adjusted values share the same corporate-action basis.
                    def total_return(frame):
                        if 'Adj Close' not in frame or frame.loc[entry_day, 'Open'] <= 0:
                            raise ValueError('adjusted return inputs missing')
                        ratio = frame.loc[entry_day, 'Adj Close']/frame.loc[entry_day, 'Close']
                        return float(frame.loc[target, 'Adj Close']/(frame.loc[entry_day, 'Open']*ratio)-1)
                    ret, benchmark = total_return(stock), total_return(spy)
                    if not all(__import__('math').isfinite(x) for x in [ret, benchmark]):
                        raise ValueError('nonfinite outcome')
                    direction = 1 if decision['stance'] == '긍정' else (-1 if decision['stance'] == '부정' else 0)
                    outcomes.append({**base, 'status': 'evaluated', 'return': ret,
                        'benchmark_return': benchmark, 'excess_return': ret-benchmark,
                        'direction_correct': bool(ret*direction > 0) if direction else None,
                        'note': '조정주가 기준 배당 포함 근사 수익률; 비용·슬리피지 제외. 실제 체결 아님.'})
                except (KeyError, TypeError, ValueError, ZeroDivisionError):
                    outcomes.append({**base, 'status': 'missing_data'})
    return outcomes


def summarize(outcomes):
    result = {}
    for h in [5, 10, 20]:
        rows = [x for x in outcomes if x['horizon'] == h]
        done = [x for x in rows if x['status'] == 'evaluated']
        directional = [x for x in done if x['direction_correct'] is not None]
        result[str(h)] = {'evaluated': len(done), 'pending': sum(x['status'] == 'pending' for x in rows),
            'missing_data': sum(x['status'] == 'missing_data' for x in rows),
            'directional_sample_size': len(directional),
            'direction_accuracy': sum(x['direction_correct'] for x in directional)/len(directional) if directional else None,
            'mean_excess_return': sum(x['excess_return'] for x in done)/len(done) if done else None}
    return result
