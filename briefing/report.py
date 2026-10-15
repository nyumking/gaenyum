"""Korean human-readable report; JSON retains complete provenance and all nulls."""
import json

def markdown(report):
    lines = [f"# 미국 증시 브리핑 3.1 — {report['mode']}", '',
        f"기준 시각(KST): {report['generated_at_kst']}",
        f"완료된 미국 거래일: {report['context']['last_completed_session']}",
        f"예정 세션 안에서 생성: {report['context']['scheduled_session_valid']} (False면 해당 슬롯 실행 검증 자료가 아님)",
        f"운영 상태: **{report['status']}** · 투자 관점: 5–20 거래일 (약 1주–1개월)", '',
        '일봉은 완료된 정규장만 사용합니다. 장전·시간외 관측가는 별도이며 실시간 검증 시세가 아닙니다.', '',
        '## 시장 환경', '', '| 종목 | 종가 | RSI14 | SPY 대비 20일 초과수익 | 검증 |', '|---|---:|---:|---:|---|']
    for symbol, row in report['market'].items():
        v = row.get('indicators') or {}
        lines.append(f"| {symbol} | {v.get('close')} | {v.get('rsi_14')} | {v.get('rs_20_vs_spy')} | {row.get('crosscheck', {}).get('status', 'missing')} |")
    lines += ['', '금리·VIX·선물·전체 업종 breadth는 미수집입니다. 시장 전체의 방향을 이 표만으로 확정하지 않습니다.']
    for symbol, row in report['symbols'].items():
        lines += ['', f'## {symbol}', '', f"공식 종목 식별: {row['listing']['status']} · {row['listing'].get('name', '확인 불가')}"]
        lines += [f"시세/거래량 교차검증: {row.get('crosscheck', {}).get('status', 'missing')}"]
        q = row.get('quote')
        if q:
            lines += [f"장전/시간외 관측가: {q['price']} · {q['observed_at']} · {q['status']} ({q['session']})"]
        lines += [f"**1주–1개월 판단: {row['scenario']['stance']}**"]
        if row.get('indicators'):
            lines += ['', '| 지표 | 값 |', '|---|---:|']
            lines += [f'| {k} | {v if v is not None else "확인 불가"} |' for k, v in row['indicators'].items()]
        for case in row['scenario'].get('cases', []):
            lines += [f"- {case['case']}: {case['condition']} · 참고 구간: {case['reference']}"]
        if row['scenario'].get('product_risk'):
            lines += [row['scenario']['product_risk']]
        filings = row.get('official_filings', {})
        lines += [f"SEC 확인 상태: {filings.get('status', 'unavailable')} · 원문 확인 {filings.get('originals_verified', 0)}건"]
        for item in row.get('news', {}).get('items', []):
            lines += [f"- 뉴스 [{item['title']}]({item['url']}) ({item['published_at']}, {item['verification']})"]
            original = item.get('original', {})
            if original.get('sha256'):
                lines += [f"  원문: {original['word_count']}단어 · 수집 {original['retrieved_at']} · SHA256 {original['sha256']}"]
            for claim in item.get('supported_claims', []):
                lines += [f"  근거 대조: {claim['metric']} USD {claim['official_value_usd']}, {claim['period_start']}~{claim['period_end']} ([공시]({claim['source']}))"]
        for item in row.get('official_filings', {}).get('items', []):
            lines += [f"- 공식 공시 [{item['form']}]({item['url']}) ({item['filed']}) · {item['verification']}"]
            original = item.get('original', {})
            if original.get('sha256'):
                lines += [f"  원문 SHA256 {original['sha256']} · 재무 사실 {len(original.get('facts', []))}개"]
        lines += ['기업가치·실적 영향: 원문 확인과 개별 수치 대조를 구분합니다. 확인된 공시 수치만 표시하며 가격 변동의 원인은 단정하지 않습니다.']
        lines += [f'- 데이터 누락/제약: {message}' for message in row['missing']]
    lines += ['', '## 경제 일정', '', report['calendar']['coverage']]
    for event in report['calendar']['events']:
        lines += [f"- {event['at_kst']} — {event['title']} ([공식 출처]({event['source']})); 실제치/예상치 미수집"]
    for source in report['calendar']['sources']:
        lines += [f"- 출처 상태: {source.get('name')} — {source.get('status', 'unknown')}, 확인 시각 {source.get('verified_at_utc', source.get('retrieved_at'))}"]
        if source.get('expires_at_utc'):
            lines += [f"  대체 사본 만료: {source['expires_at_utc']} · 실시간 조회 불가 상태"]
    lines += [f"- 원본 조회 경고: {x['source']} ({x['error']})" for x in report['calendar'].get('warnings', [])]
    lines += [f'- 누락: {x}' for x in report['calendar']['missing']]
    lines += [f"- 수집 실패: {x['source']} ({x['error']})" for x in report['calendar']['errors']]
    lines += ['', '## 5/10/20 거래일 사후 검증', '',
        '최초 보고서 생성 이후 첫 정규장 시가 진입, 5/10/20번째 거래일 종가 평가. SPY 초과수익도 기록합니다. 중립 판단은 적중률 계산에서 제외합니다.', '',
        '| 전망 | 종목 | 거래일 | 상태 | 수익률 | SPY 초과수익 | 방향 적중 |', '|---|---|---:|---|---:|---:|---|']
    for x in report['outcomes']:
        lines += [f"| {x['forecast_id']} | {x['symbol']} | {x['horizon']} | {x['status']} | {x.get('return')} | {x.get('excess_return')} | {x.get('direction_correct')} |"]
    for h, stats in report.get('outcome_summary', {}).items():
        lines += [f"- {h}거래일: 평가 {stats['evaluated']}건, 방향 판단 표본 {stats['directional_sample_size']}건, 방향 적중률 {stats['direction_accuracy']}, 평균 SPY 초과수익 {stats['mean_excess_return']}"]
    if not report['outcomes']:
        lines += ['', '평가할 누적 전망이 없습니다. 미래 성과를 계산하거나 적중률을 추정하지 않습니다.']
    lines += ['', '## 데이터 기준 및 한계', '',
        'RS는 20거래일 수익률의 차이, RVOL은 당일 거래량/직전 20일 평균, OBV는 조회 구간 첫날 0부터 계산합니다. 거래대금은 종가×거래량 근사치입니다.',
        '긍정/부정은 기술적 조건의 규칙 분류이며 수익률 확률 추정이 아닙니다. ATR 참고 구간은 매매 지시가 아닙니다.',
        '정규장 원시 OHLCV를 사용하며 최근 분할이 있으면 방향 판단을 보류합니다. 배당락 갭은 잔존할 수 있습니다.',
        '뉴스 원문·금리·선물·실적 일정·경제 실제치/컨센서스 등 확인되지 않은 값은 null 또는 누락으로 기록합니다.',
        '출처와 수집 시각, 오차, 상태 및 모든 누락은 같은 이름의 JSON에서 확인할 수 있습니다.', '',
        '기존 ChatGPT 예약 브리핑과 자동 연동하지 않습니다.']
    return '\n'.join(lines)+'\n'

def write_report(report, output):
    output.mkdir(parents=True, exist_ok=True)
    (output/'briefing.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    (output/'briefing.md').write_text(markdown(report), encoding='utf-8')
