"""Pure calculations and validation. No network calls or trade execution."""
from datetime import timedelta
import numpy as np
import pandas as pd
import pandas_market_calendars as mcal

SYMBOLS = ['HIMS', 'TSLA', 'SPCX', 'SOXL', 'NVDA', 'AMD', 'MU']
EXPECTED = {'HIMS': 'Hims', 'TSLA': 'Tesla', 'SPCX': 'Space',
            'SOXL': 'Daily Semiconductor Bull', 'NVDA': 'NVIDIA',
            'AMD': 'Advanced Micro Devices', 'MU': 'Micron',
            'SPY': 'S&P 500', 'QQQ': 'Invesco QQQ', 'SMH': 'Semiconductor'}
BENCHMARKS = ['SPY', 'QQQ', 'SMH']

def session_context(now, mode):
    now = pd.Timestamp(now)
    if now.tzinfo is None:
        raise ValueError('Timezone-aware timestamp required')
    schedule = mcal.get_calendar('NYSE').schedule(
        start_date=(now-timedelta(days=20)).date(), end_date=(now+timedelta(days=3)).date())
    completed = schedule[schedule.market_close <= now]
    last = completed.index[-1].date()
    ny_day = now.tz_convert('America/New_York').date()
    active = ny_day in schedule.index.date
    if mode == 'premarket':
        active = active and now < schedule.loc[str(ny_day), 'market_open']
    else:
        active = active and now >= schedule.loc[str(ny_day), 'market_close']
    return {'last_completed_session': str(last), 'market_date': str(ny_day),
            'scheduled_session_valid': bool(active)}

def validate_bars(frame, expected_day):
    if frame.empty:
        raise ValueError('empty daily feed')
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError('duplicate or unsorted sessions')
    required = ['Open', 'High', 'Low', 'Close', 'Volume']
    if not all(c in frame for c in required):
        raise ValueError('missing OHLCV columns')
    if not np.isfinite(frame[required].to_numpy(dtype=float)).all():
        raise ValueError('nonfinite or missing OHLCV')
    if (frame[['Open', 'High', 'Low', 'Close']] <= 0).any().any():
        raise ValueError('nonpositive price')
    if ((frame.High < frame[['Open', 'Close', 'Low']].max(axis=1)) |
        (frame.Low > frame[['Open', 'Close', 'High']].min(axis=1)) |
        (frame.Volume < 0)).any():
        raise ValueError('invalid OHLCV relationship')
    if str(frame.index[-1].date()) != str(expected_day):
        raise ValueError(f'stale daily session {frame.index[-1].date()}; expected {expected_day}')
    expected = mcal.get_calendar('NYSE').valid_days(frame.index[0].date(), expected_day).date
    if set(expected) != set(frame.index.date):
        raise ValueError('missing or nontrading sessions inside daily history')
    if len(frame) < 60:
        raise ValueError('insufficient history: need at least 60 sessions')
    return frame

def wilder(series, n=14):
    """Wilder RMA, seeded with the first n valid observations' SMA."""
    out = pd.Series(np.nan, index=series.index, dtype=float)
    valid = series.dropna()
    if len(valid) >= n:
        seed = valid.index[n-1]
        out.loc[seed] = valid.iloc[:n].mean()
        pos = series.index.get_loc(seed)
        for i in range(pos+1, len(series)):
            out.iloc[i] = (out.iloc[i-1]*(n-1)+series.iloc[i])/n
    return out

def indicators(frame, benchmark):
    c, v = frame.Close, frame.Volume
    d = c.diff()
    gain, loss = wilder(d.clip(lower=0)), wilder(-d.clip(upper=0))
    rsi = 100-100/(1+gain/loss.replace(0, np.nan))
    rsi = rsi.mask((loss == 0) & (gain > 0), 100).mask((loss == 0) & (gain == 0), 50)
    macd = c.ewm(span=12, adjust=False, min_periods=26).mean()-c.ewm(span=26, adjust=False, min_periods=26).mean()
    signal = macd.ewm(span=9, adjust=False, min_periods=9).mean()
    tr = pd.concat([frame.High-frame.Low, (frame.High-c.shift()).abs(),
                    (frame.Low-c.shift()).abs()], axis=1).max(axis=1)
    obv = (np.sign(d).fillna(0)*v).cumsum()
    prior20 = v.shift().rolling(20).mean()
    aligned = pd.concat([c.rename('stock'), benchmark.Close.rename('benchmark')], axis=1).dropna()
    rs = None
    if len(aligned) >= 21 and aligned.index[-1] == frame.index[-1]:
        rs = (aligned.stock.iloc[-1]/aligned.stock.iloc[-21]-1) - (aligned.benchmark.iloc[-1]/aligned.benchmark.iloc[-21]-1)
    def number(x):
        return round(float(x), 6) if x is not None and np.isfinite(x) else None
    out = {'close': c.iloc[-1], 'rsi_14': rsi.iloc[-1], 'macd': macd.iloc[-1],
           'macd_signal': signal.iloc[-1], 'macd_histogram': (macd-signal).iloc[-1],
           'atr_14': wilder(tr).iloc[-1], 'volume': v.iloc[-1],
           'prior_volume_avg_20': prior20.iloc[-1], 'rvol_20': v.iloc[-1]/prior20.iloc[-1] if prior20.iloc[-1] else None,
           'volume_5_vs_prior5': v.tail(5).mean()/v.iloc[-10:-5].mean()-1 if v.iloc[-10:-5].mean() else None,
           'obv': obv.iloc[-1], 'obv_change_5': obv.iloc[-1]-obv.iloc[-6],
           'dollar_volume': c.iloc[-1]*v.iloc[-1], 'rs_20_vs_spy': rs,
           'support_20': frame.Low.tail(20).min(), 'resistance_20': frame.High.tail(20).max()}
    for n in [5, 10, 20, 50, 200]:
        out[f'sma_{n}'] = c.rolling(n).mean().iloc[-1]
    return {k: number(x) for k, x in out.items()}

def crosscheck(primary, secondary):
    """Same session, raw/as-traded OHLCV; mismatch is never silently averaged."""
    a, b = primary.iloc[-1], secondary.iloc[-1]
    if primary.index[-1].date() != secondary.index[-1].date():
        return {'status': 'mismatch', 'reason': 'secondary session differs'}
    price = abs(a.Close/b.Close-1)
    volume = abs(a.Volume/b.Volume-1) if b.Volume > 0 else None
    return {'status': 'verified' if price <= .01 and volume is not None and volume <= .10 else 'mismatch',
            'close_relative_difference': float(price), 'volume_relative_difference': float(volume) if volume is not None else None,
            'secondary_close': float(b.Close), 'secondary_volume': float(b.Volume),
            'thresholds': {'close': .01, 'volume': .10},
            'caveat': 'Providers may use different split/volume conventions; mismatch blocks direction.'}

def scenarios(values, eligible, symbol):
    if not eligible:
        return {'stance': '판단 보류', 'horizon': '5–20 거래일 (약 1주–1개월)',
                'reason': '상장 식별·데이터·교차검증 중 미충족 항목 존재', 'cases': []}
    c, a, s, r = [values[k] for k in ['close', 'atr_14', 'support_20', 'resistance_20']]
    score = sum([c > values['sma_20'], values['macd_histogram'] > 0,
                 values['rs_20_vs_spy'] > 0, values['obv_change_5'] > 0])
    stance = '긍정' if score >= 3 else ('부정' if score <= 1 else '중립')
    return {'stance': stance, 'horizon': '5–20 거래일 (약 1주–1개월)',
            'method': '종가>SMA20, MACD histogram>0, SPY 대비 RS20>0, OBV5>0: 3–4 긍정 / 0–1 부정 / 2 중립',
            'cases': [
                {'case': '상승', 'condition': f'완료 종가가 20일 저항 {r:.2f} 돌파, RVOL≥1.2 확인', 'reference': round(r+2*a, 2), 'invalidated_below': round(r-a, 2)},
                {'case': '기본', 'condition': f'20일 지지 {s:.2f}~저항 {r:.2f} 범위 유지', 'reference': None},
                {'case': '하락', 'condition': f'완료 종가가 20일 지지 {s:.2f} 이탈', 'reference': round(max(.01, s-2*a), 2), 'invalidated_above': round(s+a, 2)}],
            'risk': 'ATR 배수는 조건부 참고 구간이며 확률·목표수익 보장이 아님. 뉴스/경제 발표에 따라 무효화 가능.',
            'product_risk': 'SOXL은 일일 3배 레버리지 ETF: 복리·경로 의존으로 여러 날 수익률은 단순 3배가 아님.' if symbol == 'SOXL' else None}
