# US Market Briefing Data Engine

Automated OHLCV and technical indicator collection for HIMS, TSLA, SPCX (SpaceX), and SOXL.

## Setup

```bash
pip install -r requirements.txt
python market_analysis.py
```

The script writes `output/indicators.json` and `output/indicators.md`, including data source, latest candle timestamp, and daily/4h RSI, MACD, SMA, volume and support/resistance. It fails visibly when market data is unavailable or stale rather than inventing values.

## Automation

The GitHub Actions workflow runs 22:00 UTC Tuesday–Saturday (07:00 Asia/Seoul), after the regular US trading session, with a US market-calendar check to skip non-trading days. Trigger it manually via Actions → Market Indicators → Run workflow.

Yahoo Finance (via yfinance) is a convenience data source, not a guaranteed exchange-grade feed. Always cross-check material prices before trading. The 4h timeframe uses regular-session 1h bars grouped into 4-hour chunks anchored at 09:30 ET; since the US regular session is 6.5 hours, its final 4h candle is shorter. All calculations use available completed candles and are marked with timestamps.
