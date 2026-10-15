# 구축 및 검증 기록

2026-10-16 KST. 이 기록은 코드 구축, 시험 실행, 서비스 운영을 구분합니다.

## 구현됨

- 7종목 및 SPY/QQQ/SMH, 공식 상장 식별, 완료 거래일 OHLCV 검증, 독립 공급자 종가·거래량 대조.
- RSI/MACD/ATR/상대강도/OBV와 거래량 지표, 조건부 5–20거래일 기술적 시나리오.
- 경제 일정 및 뉴스 연결, 출처·시각·누락을 보존하는 Markdown/JSON.
- 최초 전망 보관 및 다음 정규장 시가를 기준으로 하는 5/10/20거래일 사후 평가와 표본 수·적중률·초과수익 집계.
- KST 21:00/07:00 GitHub Actions 정의, 휴장일 스킵, 오류 보고서 artifact 업로드, 별도 브랜치 원본 전망 보관.

## 로컬 검증

Python 3.12 환경에서 `python -m pytest -q`: **23 passed**. GitHub CI는 Python 3.11로 별도 검증합니다.

테스트는 알려진 수열에 대한 지표 수치, Wilder 초기값, 상승/하락/횡보 RSI, 현재일 제외 RVOL, 누락·중복·NaN·잘못된 OHLC, 시세/거래량 오차, 휴일·DST·조기 폐장·UTC 요일, SPCX 오인 방지, 공식 디렉터리 신선도, 경제 일정 시간대, Nasdaq 응답 파싱, 실제 네트워크 없는 전체 파이프라인, 전체 공급자 실패 시 보고서 생성, 전망 재실행 불변성, 다음 거래일 진입·미래 성과 보류·중립 제외를 검증합니다.

실제 네트워크 수집 시험:

- Nasdaq 공식 디렉터리와 파일 생성시각 조회 성공. 현재 SPCX의 공식 이름은 `Space Exploration Technologies Corp. - Class A Common Stock`으로 확인됨. 코드에 상장 상태를 하드코딩하지 않음.
- Yahoo 일봉: 7종목 수집 성공. SPCX는 약 86거래일로 SMA200은 null. 다른 대상은 약 378일 수집됨.
- Stooq는 브라우저 인증 HTML을 반환하여 사용 불가. 이를 정상 시세로 처리하지 않았음.
- Nasdaq 공식 과거 시세 경로를 추가한 뒤 7종목 및 SPY/QQQ/SMH의 동일 거래일 종가와 거래량 교차검증 통과.
- BEA 경제 일정 수집 성공. BLS는 HTTP 오류로 수집 실패하여 degraded 처리.
- Yahoo RSS 헤드라인 수집은 원문 미검증 상태. SEC_USER_AGENT 미설정으로 SEC 공시 조회 생략. SPCX·SOXL의 SEC 발행사 CIK 매핑은 추정하지 않음.
- 실제 시험 시각이 미국 정규장 중이므로 장전/시간외 시세로 인정하지 않았으며, 신규 전망 원본도 저장하지 않았음. 정해진 슬롯에서 실제로 거래된 장전/시간외 시세를 수집한 검증은 별도로 필요함.

## 운영 판정

실제 피드 시험의 상태는 **degraded**이며, 완전한 정상 운영이라고 선언하지 않습니다. 핵심 시세 교차검증은 통과했지만 BLS 조회, 공식 공시 설정, 뉴스 원문 재무 영향 검증, 정해진 슬롯에서의 운영 확인이 남아 있습니다. FOMC는 공식 회의 날짜만 수집하며 발표 시각을 추정하지 않습니다. 소매판매·기업 실적 일정·경제 실제치/예상치·선물/금리/VIX 등은 미수집으로 명시됩니다.

5/10/20거래일 성과는 시간이 지나야 검증할 수 있습니다. 테스트 통과를 실전 적중률로 제시하지 않습니다. 원본 전망 ledger 저장도 실제 유효 슬롯에서 검증된 판단이 생긴 뒤 처음 생성됩니다.

GitHub 예약 실행은 기본 브랜치에 반영된 워크플로에만 적용됩니다. PR CI 결과, 기본 브랜치 반영과 실제 Actions 실행 결과는 GitHub Actions 기록을 확인해야 하며 이 문서의 로컬 결과와 구분합니다.

## 보안 및 기존 예약

비밀키가 필요 없는 기본 수집기이며 실제 키나 연락처를 코드에 넣지 않았습니다. 선택 SEC 연락처는 Actions Secrets에서만 읽고 출력하지 않습니다. 공개 보고서에는 개인 데이터나 비밀키를 넣지 않습니다.

기존 ChatGPT 예약 브리핑 두 개는 수정·삭제하지 않았고 GitHub 출력과 연결하지 않았습니다. 검증 완료 후 연동 여부를 결정합니다.

## GitHub 실제 결과

- [구현 PR #1](https://github.com/nyumking/gaenyum/pull/1): 병합 완료. 기본 브랜치 반영 커밋 `badbd56498af2bdc1e1be79b6666f572eff3262a`.
- [PR CI](https://github.com/nyumking/gaenyum/actions/runs/38763560190): 성공. Python 3.11 테스트 통과.
- [기본 브랜치 실제 수집](https://github.com/nyumking/gaenyum/actions/runs/38763700292): 테스트 성공, 실제 수집 및 보고서 업로드 완료. BLS HTTP 오류 때문에 `degraded` / 종료 코드 2를 기록하여 수집 작업은 최종 실패 상태로 표시됨.
- [실행 보고서 artifact](https://github.com/nyumking/gaenyum/actions/runs/38763700292/artifacts/11986812729): Markdown/JSON 두 파일 업로드 확인. 다운로드한 JSON에서도 `core_price_volume_verified: true`, 7종목 모두 `crosscheck.status: verified`, BLS HTTP 오류를 확인함.
- 기본 브랜치 워크플로의 21:00/07:00 KST cron 정의는 반영됨. 예약 시각 도달로 시작된 새 버전 실행은 아직 관측하지 않았음. 이번 push 실행은 미국 정규장 중이므로 최초 전망 ledger 저장 단계는 건너뜀. 이는 실제 예약 시각 실행 및 ledger 생성 성공을 의미하지 않음.

따라서 **코드 구축·기본 브랜치 반영·테스트·실제 수집/보고서 업로드는 확인**, **완전 정상 운영·정각 전달·원본 ledger의 실전 첫 저장·실전 성과는 미검증**입니다.

## 3.1 원문·저장 검증 보완 (진행 기록)

2026-10-16 KST:

- SEC 연락처를 사용자가 제공했고 `SEC_USER_AGENT` 저장소 Secret 등록 완료 메시지와 이름을 GitHub UI에서 확인함. 연락처는 공개 코드에 포함하지 않음.
- BLS 직접 ICS 및 월별 페이지가 실행 환경에서 HTTP 403을 반환함. 직접 조회 경로의 접근 거부는 해결됐다고 주장하지 않음. 공식 10월·11월 목록을 검토해 48시간만 유효한 초기 사본과 라이브 재조회/실패/만료 판정을 추가함. 로컬 보고서에서 BLS fallback, BEA/Fed live와 일정 20건, 누락 오류 0건을 확인함. 사본 경고는 유지됨.
- 로컬에서 뉴스 원문 13개 확인: HIMS/TSLA/NVDA/AMD/MU 각 2개와 SPCX 1개. SEC 원문에 의한 수치 대조 성공은 0개이므로 `original_retrieved_unsubstantiated`로 표시함. 접근 실패와 동영상은 별도 미검증 상태임.
- 로컬 SEC 요청은 실제 연락처를 사용해도 HTTP 403. Secret 저장 성공과 SEC 수집 성공을 구분함. GitHub 실행 환경에서 별도 확인 필요.
- SPCX CIK 1181412는 SEC 공식 8-K 색인 https://www.sec.gov/Archives/edgar/data/1181412/000162828026056945/0001628280-26-056945-index.html 에서 확인함. SOXL은 CIK 1424958 / Series S000027920 / Class C000084845를 SEC https://www.sec.gov/Archives/edgar/data/1424958/000119312526287433/0001193125-26-287433-index-headers.html 에서 확인하고, 현재 개별 공시 색인의 동일 series 블록과 ticker를 재대조하도록 구현함.
- Markdown/JSON 파일 해시·GitHub 실행 ID·event·cron·실행 시각이 담긴 receipt와 보고서 불변 보관 경로를 추가함. 예약 성공 판단은 `event=schedule`, 올바른 모드·세션·3시간 이내 지연, 실제 artifact/보관 파일의 일치 여부로 확인해야 함.
