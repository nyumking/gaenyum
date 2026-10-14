"""Reproducible, timestamped market indicators; no fabricated quotes."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pandas_market_calendars as mcal
import yfinance as yf

SYMBOLS = ["HIMS", "TSLA", "SPCX", "SOXL"]
NY = ZoneInfo("America/New_York")
OUT = Path("output")


def completed_trading_day():
    now = datetime.now(timezone.utc)
    cal = mcal.get_calendar("NYSE")
    schedule = cal.schedule(start_date=(now-timedelta(days=10)).date(),
                            end_date=now.date())
    completed = schedule[schedule.market_close <= pd.Timestamp(now)]
    if completed.empty:
        raise RuntimeError("No completed NYSE trading session")
    return completed.index[-1].date()


def get_prices(ticker, interval, period):
    frame = yf.download(ticker, interval=interval, period=period, auto_adjust=True,
                        actions=False, progress=False, threads=False)
    if frame.empty:
        raise ValueError(f"{ticker}: empty {interval} feed")
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)
    required = ["Open", "High", "Low", "Close", "Volume"]
    frame = frame[required].dropna(subset=["Open", "High", "Low", "Close"])
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    if (frame["Close"] <= 0).any() or (frame["High"] < frame["Low"]).any():
        raise ValueError(f"{ticker}: invalid OHLC")
    return frame


def four_hour_from_hourly(hourly):
    """NY regular hours only, anchored at 09:30; excludes partial current session."""
    h = hourly.copy()
    h.index = h.index.tz_convert(NY)
    h = h.between_time("09:30", "15:59")
    parts = []
    for day, group in h.groupby(h.index.date):
        anchor = pd.Timestamp(day, tz=NY) + pd.Timedelta(hours=9, minutes=30)
        buckets = ((group.index - anchor).total_seconds() // (4*3600)).astype(int)
        for bucket, rows in group.groupby(buckets):
            parts.append((anchor + pd.Timedelta(hours=int(bucket)*4),
                          {"Open": rows.Open.iloc[0], "High": rows.High.max(),
                           "Low": rows.Low.min(), "Close": rows.Close.iloc[-1],
                           "Volume": rows.Volume.sum()}))
    if not parts:
        raise ValueError("No US regular-session intraday bars")
    result = pd.DataFrame([v for _, v in parts], index=pd.DatetimeIndex([k for k,_ in parts]))
    result.index.name = "Date"
    return result


def calc(df):
    close = df.Close.astype(float)
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    rs = up / down.replace(0, np.nan)
    rsi = (100 - 100 / (1+rs)).where(down.ne(0), 100.0)
    macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    signal = macd.ewm(span=9, adjust=False).mean()
    def val(x):
        x = float(x)
        return round(x, 4) if np.isfinite(x) else None
    return {
        "as_of": df.index[-1].isoformat(),
        "bars": len(df),
        "close": val(close.iloc[-1]),
        "rsi_14": val(rsi.iloc[-1]),
        "macd_12_26": val(macd.iloc[-1]),
        "macd_signal_9": val(signal.iloc[-1]),
        "macd_histogram": val((macd-signal).iloc[-1]),
        "sma_20": val(close.rolling(20).mean().iloc[-1]),
        "sma_50": val(close.rolling(50).mean().iloc[-1]),
        "sma_200": val(close.rolling(200).mean().iloc[-1]),
        "volume": int(df.Volume.iloc[-1]),
        "volume_avg_20": val(df.Volume.rolling(20).mean().iloc[-1]),
        "support_20bar": val(df.Low.tail(20).min()),
        "resistance_20bar": val(df.High.tail(20).max()),
    }


def main():
    last_day = completed_trading_day()
    result = {"generated_at_utc": datetime.now(timezone.utc).isoformat(),
              "last_completed_us_session": last_day.isoformat(),
              "price_source": "Yahoo Finance via yfinance; adjusted OHLC",
              "caveats": ["Not exchange-verified or investment advice",
                          "4h bars are synthetic, regular-session anchored at 09:30 ET",
                          "Missing indicators remain null; never estimated"],
              "symbols": {}, "errors": {}}
    for sym in SYMBOLS:
        try:
            daily = get_prices(sym, "1d", "2y")
            if daily.index[-1].date() < last_day:
                raise ValueError(f"stale daily data: {daily.index[-1].date()} < {last_day}")
            hourly = get_prices(sym, "60m", "60d")
            candles4h = four_hour_from_hourly(hourly)
            result["symbols"][sym] = {"daily": calc(daily), "four_hour": calc(candles4h)}
        except Exception as exc:
            result["errors"][sym] = str(exc)
    OUT.mkdir(exist_ok=True)
    (OUT/"indicators.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"# US market technicals — {last_day}", "",
             f"Generated UTC: {result['generated_at_utc']}", "",
             "Source: Yahoo Finance / yfinance, adjusted OHLC. Indicative only.", ""]
    for sym in SYMBOLS:
        lines.append(f"## {sym}")
        if sym in result["errors"]:
            lines.append(f"DATA ERROR: {result['errors'][sym]}\n")
            continue
        for tf, values in result["symbols"][sym].items():
            lines.append(f"### {tf}")
            lines.extend(f"- {key}: {value}" for key, value in values.items())
        lines.append("")
    (OUT/"indicators.md").write_text("\n".join(lines), encoding="utf-8")
    if result["errors"]:
        raise RuntimeError(f"Market data failures: {result['errors']}")


if __name__ == "__main__":
    main()
