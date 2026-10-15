"""Run a real public-source collection; all failures are visible in output."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
from . import providers as p
from .core import SYMBOLS, BENCHMARKS, session_context, validate_bars, indicators, crosscheck, scenarios
from .outcomes import save_snapshot, evaluate, summarize
from .report import write_report


def build(now, mode, state, scheduled=False):
    context = session_context(now, mode)
    result = {'schema_version': '3.0', 'generated_at_utc': now.isoformat(),
        'generated_at_kst': now.astimezone(ZoneInfo('Asia/Seoul')).isoformat(),
        'mode': mode, 'context': context, 'status': 'collecting', 'symbols': {}, 'market': {},
        'provenance': {'daily': 'Yahoo Finance via yfinance; raw OHLCV + adjusted close for outcomes',
                       'secondary': 'Nasdaq historical endpoint; Stooq fallback; daily close and volume',
                       'retrieved_at': now.isoformat()},
        'calendar': {'events': [], 'sources': [], 'errors': [], 'missing': [], 'coverage': 'not collected'},
        'outcomes': []}
    if scheduled and not context['scheduled_session_valid']:
        result['status'] = 'skipped_nontrading_or_wrong_window'
        return result
    records, sources, errors = p.listings(now)
    result['listing_sources'], result['listing_errors'] = sources, errors
    histories, rows = {}, {}
    last = context['last_completed_session']
    for symbol in BENCHMARKS+SYMBOLS:
        row = {'listing': p.identify(symbol, records, errors), 'missing': [],
               'indicators': None, 'quote': None, 'crosscheck': {'status': 'missing'}}
        rows[symbol] = row
        if row['listing']['status'] != 'verified':
            row['missing'].append('공식 종목 식별 미검증: 시세 분석 보류')
            continue
        try:
            frame = validate_bars(p.yahoo_daily(symbol, last), last)
            histories[symbol] = frame
            row['daily_as_of'] = str(frame.index[-1].date())
            row['daily_rows'] = len(frame)
            if 'Stock Splits' in frame and frame['Stock Splits'].tail(220).ne(0).any():
                row['missing'].append('최근 220거래일 주식분할: 원시 가격 지표 왜곡 가능, 판단 보류')
            try:
                second, url = p.secondary_daily(symbol, last)
                validate_bars(second, last) if len(second) >= 60 else _validate_secondary(second)
                row['crosscheck'] = {**crosscheck(frame, second), 'source': url, 'retrieved_at': now.isoformat()}
            except Exception as exc:
                row['missing'].append(f'독립 시세/거래량 수집 실패: {type(exc).__name__}')
        except Exception as exc:
            row['missing'].append(f'일봉 검증 실패: {type(exc).__name__}: {str(exc)[:120] if isinstance(exc, ValueError) else "provider unavailable"}')
    spy = histories.get('SPY')
    for symbol, row in rows.items():
        if symbol in histories and spy is not None:
            row['indicators'] = indicators(histories[symbol], spy)
        elif symbol in histories:
            row['missing'].append('SPY 누락으로 상대강도와 방향 판단 불가')
        if row['crosscheck']['status'] != 'verified':
            row['missing'].append('동일 거래일 종가/거래량 독립 교차검증 미통과')
        if symbol in BENCHMARKS:
            result['market'][symbol] = row
            continue
        try:
            row['news'] = p.news(symbol, now)
            row['missing'] += row['news']['missing']
        except Exception as exc:
            row['missing'].append(f'뉴스 수집 실패: {type(exc).__name__}')
        try:
            row['official_filings'] = p.official_filings(symbol, now)
            row['missing'] += row['official_filings']['missing']
        except Exception as exc:
            row['missing'].append(f'공식 공시 수집 실패: {type(exc).__name__}')
        if symbol in histories:
            try:
                row['quote'] = p.quote(symbol, now, mode)
                row['missing'] += row['quote']['missing']
                if row['quote']['status'] != 'indicative_unverified':
                    row['missing'].append('장전/시간외 관측 시세 지연 또는 세션 불일치')
            except Exception as exc:
                row['missing'].append(f'장전/시간외 시세 실패: {type(exc).__name__}')
        v = row['indicators']
        eligible = bool(v and all(v.get(k) is not None for k in ['atr_14', 'rs_20_vs_spy', 'macd_histogram', 'sma_20'])
            and row['listing']['status'] == 'verified' and row['crosscheck']['status'] == 'verified'
            and rows['SPY']['crosscheck']['status'] == 'verified'
            and not any('주식분할' in x for x in row['missing']))
        row['scenario'] = scenarios(v, eligible, symbol)
        row['scenario']['basis'] = '완료된 정규장 일봉만 사용; 장전 뉴스·시세·매크로가 검증된 종합 투자판단은 아님'
        result['symbols'][symbol] = row
    result['calendar'] = p.economic_calendar(now)
    degraded = (any(r['scenario']['stance'] == '판단 보류' for r in result['symbols'].values())
                or bool(result['calendar']['errors'])
                or any(any('수집 실패' in m or '시세 실패' in m for m in r['missing']) for r in result['symbols'].values()))
    result['status'] = 'degraded' if degraded else 'partial'
    result['core_price_volume_verified'] = all(r['crosscheck']['status'] == 'verified' for r in rows.values())
    if context['scheduled_session_valid']:
        save_snapshot(result, Path(state)/'forecasts')
    result['outcomes'] = evaluate(Path(state)/'forecasts', histories, now)
    result['outcome_summary'] = summarize(result['outcomes'])
    return result


def _validate_secondary(frame):
    import numpy as np
    if frame.empty or not frame.index.is_monotonic_increasing or frame.index.has_duplicates:
        raise ValueError('invalid secondary index')
    if not np.isfinite(frame[['Close', 'Volume']].to_numpy()).all() or (frame.Close <= 0).any() or (frame.Volume < 0).any():
        raise ValueError('invalid secondary close/volume')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['premarket', 'close'], default='close')
    parser.add_argument('--output', type=Path, default=Path('output'))
    parser.add_argument('--state', type=Path, default=Path('state'))
    parser.add_argument('--scheduled', action='store_true')
    args = parser.parse_args()
    result = build(datetime.now(timezone.utc), args.mode, args.state, args.scheduled)
    write_report(result, args.output)
    print(f"Briefing status: {result['status']}; output: {args.output}")
    # Data degradation is a visible job failure while artifacts are still uploaded.
    return 2 if result['status'] == 'degraded' else 0

if __name__ == '__main__':
    raise SystemExit(main())
