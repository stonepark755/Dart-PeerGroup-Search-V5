# HANDOFF — DART 재무실적 조회 에이전트 v5.1 (4개 분기 합산 구간 고정 + 정정공시 접수본 선택 + 한국시간)

## 0-00. v5.4 (2026-11-16) — 당일 공시 처리
- 사례: HB솔루션(297890) 3분기보고서 20261116000323 이 검색 당일 접수 → 13:09 검색 시 재무정보 API 013, document.xml 014(파일 없음). 몇 분 뒤 /api/diag 에선 재무정보 API 000. 그런데 013 을 1시간 재사용해 계속 실패. 2025 4Q 순이익도 2025 3분기보고서에 지배기업 귀속 누적이 없어 2026 3분기보고서의 전기 비교정보에 의존 → 함께 실패.
- `dart.is_recent_filing(rcept_no)` : 접수일이 `RECENT_FILING_DAYS`(3)일 이내. `statement()`에서 최근 공시면 '데이터 없음' 재사용 60초(그 외 3600초), `probe_report()`는 600초.
- `dart.opendart_error(body)` : OpenDART 오류 응답(XML·JSON) → (status, message). document.xml 오류는 XML 이라 기존엔 원문 일부(깨진 글자)가 그대로 노출됐음.
- 최근 공시 실패 시 메시지에 "…접수된 최근 공시라 OpenDART 재무정보·원문 제공 전일 수 있음. 잠시 후 다시 검색하세요" 추가.
- 테스트: `test_same_day_filing_recovers_within_minutes`, `test_recent_filing_and_error_parsing`. 전체 62개.

## 0-0. v5.3 (2026-11-16) — 공시목록 누락 보완 + 진단 페이지
- 배포(v5.2) 후에도 한화에어로스페이스 2026 3Q 가 '보고서 미공시(최근 공시: 2026년 2Q)' → 재무정보 API 이전 단계인 **공시목록(list.json)에서 3분기보고서를 못 받아온 것**. 코드상 원인 미확인(기간·페이지·보고서명 해석 모두 정상 경로, 같은 목록에서 1Q·2Q는 인식).
- `engine.CompanyRun.probe_missing(keys)` : 구간 분기(+4Q면 같은 해 3Q) 중 목록에 없고 분기말이 지난 것을 `client.probe_report()`로 확인해 `self.reports`에 추가(`found_via="재무정보 API"`). `probe_notes()`로 주석 기록. WebClient 에는 probe_report 가 없어 웹 모드는 영향 없음.
- `dart.DartClient.probe_report(corp_code, bsns_year, reprt_code)` : 재무정보 API(CFS→OFS) 행의 `rcept_no` 반환.
- 진단 `GET /api/diag?code=012450` (상세조회 화면에 링크) : 캐시 미사용 OpenDART 원응답 — `list_5y`(5년 범위), `list_recent`(전년 1/1~), `list_5y_server_cache`(서버 캐시본), `recognized`(인식된 보고서), `fnltt`(최근 6개 분기 CFS/OFS 상태·행수·rcept_no). `DartClient.list_raw/list_cached/statement_raw` 추가.
- **다음 단계: 배포 후 /api/diag?code=012450 결과로 원인 확정** — list_5y·list_recent 에 '분기보고서 (2026.09)' 유무, 서버 캐시본과 차이, fnltt 2026년 3Q 상태.
- 테스트 `tests/test_probe_diag.py`(4개). 전체 60개 통과.

## 0-1. v5.2 추가 수정 (2026-11-16) — API 모드 원문 폴백
- 배경: 배포본은 `DATA_SOURCE=api`. 한화에어로스페이스 2026 3분기보고서(20261112000647, 11/12 공시)가 DART엔 있으나 재무정보 API가 수치를 주지 않아 '작년 4Q~금년 3Q' 산출 불가(사용자 보고). 실제 API 응답은 미확인(작업공간에서 OpenDART 접속 불가).
- `dart.py` `DartClient`
  - `api_statement(corp_code, bsns_year, reprt_code, fs_div) -> list|None` : 기존 재무정보 API 호출. `_json(..., empty_ttl=3600)`으로 '013 데이터 없음'은 1시간만 재사용.
  - `statement(..., rcept_no)` : API 우선 → API에 연결·별도 모두 없으면 `doc_statements(rcept_no)[fs]` / API에 별도만 있으면 `doc_has_consolidated()`가 참일 때만 원문 CFS / 원문에서도 못 찾으면 `DocParseError`(엔진이 같은 기간 다른 접수본으로 재시도).
  - `document_zip(rcept_no)` : document.xml 원본을 최근 6건 메모리 보관(연결 여부 확인과 추출이 같은 원본 공유). `doc_statements(rcept_no)` : 추출 결과 영구 캐시(`doc_statements:v1:` 키, 성공 시만).
- `dart_web.py` : `decode_xml`, `dart_xml_to_html`(TE/TU→TD, TITLE→P, SECTION-n→DIV, &cr;), `xml_toc`(TITLE ATOC="Y" 기준 구간), `parse_document_xml`, `parse_document_zip`(본문 XML 우선, 없으면 첨부). 표 해석은 기존 `pick_statement_nodes`·`extract_statements` 재사용, 행에 `origin: "document"`.
- `engine.py` : 기준 판정 근거에 "공시원문에서 연결 손익계산서 직접 추출", `origin_notes()`로 구간 주석·FY 메시지에 원문 추출 내역 기록.
- 테스트: `tests/test_api_document_fallback.py`(7개, 실제 DartClient + 가짜 `_http`), 픽스처 `tests/fixture_dart_xml.py`(요약재무정보·주석 부문손익 함정 포함).
- ⚠️ 픽스처는 DART XML 형식을 본뜬 것. **실제 document.xml로 한화에어로스페이스 2026 3Q 추출값을 공시 화면과 대조 확인 필요** (연결 포괄손익계산서 3개월 매출액·영업이익·지배기업 귀속 순이익).

## 0. v5.1 추가 수정 (2026-11-16)
### (1) 검색 시점·검색일을 한국시간으로
- 원인: `datetime.now()`·`date.today()`가 서버 시스템 시간대를 사용 → 해외 리전(UTC) 배포 시 9시간 늦게 표기, 자정~09시엔 '검색일'도 하루 전.
- `dart.py`: `KST = timezone(timedelta(hours=9))`, `now_kst()`, `today_kst()` 추가. slim 이미지에 tzdata가 없어도 되도록 **고정 오프셋**(ZoneInfo 미사용).
- `server.py`: 검색 시점 `now_kst().strftime("%Y-%m-%dT%H:%M:%S")`(오프셋 없이 저장), 검색일·기준연도 `today_kst()`. `diagnose.py`도 `today_kst()`.
- 화면·엑셀: "검색 시점 … (한국시간)" 표기. 엑셀 파일명은 브라우저 시계가 아니라 서버 `searched_at`(한국시간) 사용. `THIS_YEAR`는 `Intl.DateTimeFormat(timeZone:"Asia/Seoul")`.
### (2) 한화에어로스페이스(012450) '작년 4Q~금년 3Q' 산출 불가
- 원인: 2025 사업보고서 접수본이 `사업보고서 (2025.12)` 20260316001112 와 `[첨부정정]사업보고서 (2025.12)` 20260319000633 두 개.
  `build_reports()`가 접수번호가 큰 [첨부정정]본을 골랐는데, 첨부정정은 첨부서류만 고친 공시라 본문 손익계산서를 못 읽음 → 2025 4Q(연간−3Q누적) 실패 → 4Q가 들어간 구간·FY 모두 실패.
  (DART 공시검색으로 접수본 구성은 확인. 공시뷰어 본문은 접근 제한으로 직접 열람하지 못함 → 실환경 확인 필요)
- `engine.build_reports(rows, fye)`: 기간별 후보를 모두 모아 **본문 있는 접수본(원본·[기재정정]) 중 최신 → [첨부정정]/[첨부추가]** 순으로 정렬. 1순위가 `best[k]`, 나머지는 `rep.alternates`.
  `Report`에 `attach_only`, `alternates`, `replaced` 필드 추가(`public()`에 `replaced` 포함). `ATTACH_ONLY_RE = r"\[\s*첨부\s*(정정|추가)\s*\]"`.
- `engine.CompanyRun.stmt(rep, fs)`: `DocParseError` 시 `_use_alternate(rep)`로 같은 기간 다음 접수본으로 **rep를 제자리 교체**(접수번호·보고서명·접수일, 해당 기간 `_stmt`/`_basis` 캐시 폐기) 후 재시도. 접수본별 실패 사유는 `self._errs[(bsns,q)]`에 누적.
- `replacement_notes(reps=None)`: 대체 사용 내역을 구간 주석(`windows[].notes`)과 FY 항목 `message`에 기록.
- 테스트: `tests/test_filings.py`(7개) — 첨부정정 우선순위, 기재정정 우선 유지, 한화 재현(첨부정정이 파싱 실패/엉뚱한 표 두 경우), 기재정정 실패 시 원본 대체, 전부 실패 시 사유 누적, UTC 서버에서 한국시간 표기.
- 실환경 확인: 한화에어로스페이스 `TTM_Q3` → 2025년 4Q~2026년 3Q 값 산출, 분기별 상세 2025년 4Q 출처가 접수번호 20260316001112 인지. `FY`도 동일 접수본.


> Claude Code에게: 이 문서는 claude.ai 대화에서 진행한 작업을 이어받기 위한 인계서입니다.
> 먼저 이 문서 → `README.md` → `engine.py`의 `CompanyRun.run()` 순서로 읽고 시작하세요.

## 1. 프로젝트 개요
- 한국 상장종목(KOSPI·KOSDAQ)을 여러 개 추가해 **매출액·영업이익·당기순이익**을 조회기간별로 한 번에 검색하고 엑셀로 내려받는 웹 에이전트.
- 순수 Python 표준 라이브러리 HTTP 서버(`server.py`) + 단일 HTML(`static/index.html`) + 자체 xlsx 작성기(`xlsx.py`).
- 데이터 소스: `DATA_SOURCE=web`(기본, DART 공시원문 스크래핑 `dart_web.py`) / `api`(OpenDART, `dart.py`).
- 실행: `python server.py` (포트 `PORT`, 기본 8000). API 키는 `DART_API_KEY` 환경변수 또는 `.env`.
- 테스트: `python -m unittest discover -s tests` → **현재 62개 전부 통과** (ResourceWarning은 기존 test_web의 SQLite 미종료로, 무해).

## 2. 이번 작업(v4 → v5)의 요구사항
**문제:** 조회기간 `최근 4개 분기`가 회사별 *최신 공시 보고서*(`max(self.reports)`) 기준이어서,
검색 시점에 3분기보고서를 낸 회사(예: 비에이치아이 → 2025 4Q~2026 3Q)와 안 낸 회사(예: 한텍 → 2025 3Q~2026 2Q)의
**합산 기간이 서로 달라짐**.

**요구:** `최근 4개 분기` 선택지를 아래 3개로 대체하고, 검색 시 선택한 구간으로 합산.

| 코드 | 화면 표기 | 기준연도 2026일 때 |
|---|---|---|
| `FY` | 최근 사업연도 | (기존과 동일) |
| `TTM_Q1` | 4개분기(작년 2Q~금년 1Q) | 2025 2Q ~ 2026 1Q |
| `TTM_Q2` | 4개분기(작년 3Q~금년 2Q) | 2025 3Q ~ 2026 2Q |
| `TTM_Q3` | 4개분기(작년 4Q~금년 3Q) | 2025 4Q ~ 2026 3Q |

## 3. 확정된 설계 결정 (변경 시 사용자 확인 필요)
1. **구간 고정:** 모든 종목이 같은 구간을 합산. 구간 내 분기 보고서가 미공시면 **다른 분기로 당기지 않고** 해당 분기 `미공시`, 합계 `산출 불가`.
2. **기준연도(금년):** 기본값 = 검색일의 연도. UI에서 올해/전년 선택 가능(1~5월에 1Q 미공시 구간을 피하기 위함 — 사용자 요청 외 추가분).
3. **비12월 결산법인:** 같은 **달력 분기말**에 해당하는 회계분기로 매핑(`month_end(base_year, 3*n)` → `period_info(end, fye)`). 결산월이 3·6·9·12월이 아니면 해당 구간 `산출 불가`.
4. **항목별 다른 구간 허용:** 예) 매출액 `TTM_Q3`, 순이익 `TTM_Q2`. 구간마다 별도 계산(`windows` 리스트).
5. **구버전 호환:** API의 `periods` 값 `TTM`(회사별 최신 4개 분기)은 서버·엔진에서 계속 허용. 화면에서는 제거.
6. **분기 상세 4Q 행 기간 표기 수정:** 기존엔 사업보고서 연간(1/1~12/31)으로 표기 → 3개월(10/1~12/31)로 수정. 값은 원래부터 4Q 단독(연간−3Q누적)이었음. 연간(FY) 조회의 기간 표기는 그대로.

## 4. 변경 파일·함수

### `engine.py`
- 상수 추가
  ```python
  TTM_WINDOWS = {"TTM_Q1": (1, "4개분기(작년 2Q~금년 1Q)"),
                 "TTM_Q2": (2, "4개분기(작년 3Q~금년 2Q)"),
                 "TTM_Q3": (3, "4개분기(작년 4Q~금년 3Q)")}   # 코드: (종료 분기, 표기)
  LEGACY_TTM = "TTM"
  PERIOD_CODES = ("FY", LEGACY_TTM) + tuple(TTM_WINDOWS)
  def is_ttm(code) -> bool
  ```
- `quarter_end(bsns, q, fye) -> date` : `period_info`의 역함수(사업연도·분기 → 분기 말일).
- `CompanyRun.window_end(code, base_year) -> ((bsns, q) | None, err | None)` : 구간 마지막 분기. `TTM`이면 `max(self.reports)`.
- `CompanyRun.quarter_keys(end_key)` *(staticmethod로 변경, 인자 추가)* : end_key 포함 4개 분기 키.
- `CompanyRun.compute_quarters(end_key)` *(인자 추가)* : 미공시 분기에도 `period_start/end`를 채우고 `unfiled: bool` 플래그 추가(분기말 > 최근 공시 보고서 기말).
- `CompanyRun.compute_ttm(quarters, code=LEGACY_TTM)` : 결과에 `window`, `window_label` 추가. 미공시만으로 실패하면 메시지 `"{분기} 보고서 미공시(검색일 … 기준) — 구간을 바꿔 합산하지 않으므로 4개 분기 합계 미확정"`.
- `CompanyRun._quarter_item` : 보고서 없음 사유를 `미공시(검색일 기준 아직 제출되지 않음, 최근 공시: …)` vs `정기보고서가 DART에서 확인되지 않음`으로 구분.
- `CompanyRun.window_label(code)` *(staticmethod)*, `CompanyRun.compute_window(code, base_year, items) -> dict`
- `CompanyRun.run(periods, base_year=None)` *(인자 추가)* — 결과 dict 신규/변경 필드:
  - `base_year: int`
  - `windows: [ {code, label, base_year, items:[항목키], period_label, quarters, ttm:{항목: …}, basis_changes, notes, error} ]` — 화면 항목 순서대로 중복 제거
  - `quarters/ttm/basis_changes/notes/has_ttm` : **첫 번째 유효 구간**의 값(구버전 호환용)
  - `basis_display` : 첫 구간에서 재무구분이 판정된 마지막 분기 기준(미공시 분기 건너뜀)
  - `items[i]` : TTM 항목이면 `windows[periods[i]].ttm[i]`

### `server.py`
- `norm_base_year(v) -> int` : 2000~올해 범위 밖/비정수면 올해.
- `run_one(item, searched_at, base_year)` *(인자 추가)* : `periods` 값은 `PERIOD_CODES` 중 하나, 아니면 `FY`.
  SQLite 저장 시 구간이 여러 개면 **분기는 (사업연도, 분기) 중복 제거**, TTM은 각 항목이 쓰는 구간 값만 저장.
- `run_query(payload)` : `payload["base_year"]` 처리, 응답에 `base_year` 포함.

### `static/index.html`
- `WINDOWS`, `PERIOD`(드롭다운 4종), `THIS_YEAR`, `state.baseYear`.
- 조회조건 패널에 `#baseyear` 선택(올해/전년) + `#winhint`(구간 실제 연도 안내, `renderYearHint()`).
- `/api/query` 요청 body에 `base_year` 포함. 결과 메타에 기준연도 표기.
- 결과 셀: 산출 불가여도 `period_label`(구간) 표시.
- `renderDetail(i)` → 구간별 블록 반복, 실제 표는 `renderWindow(w)`가 반환(HTML 문자열). 미공시 분기 태그 `미공시`(`.tag.warn`).
- 상세조회 버튼 조건: `r.windows.length || r.has_ttm`.

### `xlsx.py`
- 결과 시트 헤더에 `기준연도(금년): YYYY년`. `조회기간` 열은 실패 시에도 구간 표기.
- `분기별 상세` 시트: 종목 → **구간별** (구간명, 실제 기간, 적용 항목) + 분기표 + `4개 분기 합계` + 주석.
- `계산과정` 시트: 구간이 겹쳐도 같은 분기는 종목당 1회만 출력.

### `tests/test_windows.py` (신규, 7개)
- 한텍형(3Q 미공시)·비에이치아이형(3Q 공시)이 `TTM_Q3`에서 같은 구간 → 한텍형 `산출 불가`+`미공시`, `unfiled` 플래그, 4Q 기간 `2025-10-01` 시작
- `TTM_Q2` 두 회사 동일 값, `TTM_Q1` 값, 항목별 다른 구간, 기준연도 2025, 3월·11월 결산 매핑, 다중 구간 엑셀 생성
- 픽스처는 `tests/test_scenarios.py`의 `Fake`, `rows`, `TODAY(2026-11-13 가정)` 재사용

## 5. 검증 완료 / 미검증
- ✅ 단위테스트 38개, 가짜 클라이언트로 서버 기동 후 화면(조건·결과·상세) 스크린샷 확인, `/api/export` 엑셀을 openpyxl로 열어 구간별 출력 확인
- ⚠️ **실제 DART(웹 스크래핑/OpenDART) 대상 실검색은 하지 않음.** 이번 변경은 기간 산정 로직만이고 파싱 코드는 미변경. 실제 환경에서 한텍(098070)·비에이치아이(083650)로 확인 권장:
  1. `TTM_Q3` → 두 종목 모두 `2025년 4Q~2026년 3Q` 표기, 한텍이 3Q 미공시면 `산출 불가`
  2. `TTM_Q2` → 두 종목 모두 `2025년 3Q~2026년 2Q`로 값 산출
  3. 엑셀 내보내기 → 결과·분기별 상세·계산과정 시트 확인

## 6. 주의사항 / 후속 후보
- `TODAY` 고정 테스트: 실제 `date.today()`는 `server.py`에서만 주입. 엔진 테스트는 `CompanyRun(..., today)`로 날짜 고정.
- 데모 서버 실행 시 `data/dart_agent.sqlite`가 생성됨 — `.gitignore` 처리돼 있으나 배포 zip에 넣지 말 것.
- 후속 후보(사용자 미요청, 진행 전 확인): ① 드롭다운 표기에 실제 연도 병기, ② 신규 종목 추가 시 기본 조회기간을 직전 "전체 적용" 값으로, ③ 미공시 종목만 이전 구간으로 자동 재조회하는 옵션(설계 원칙 1과 충돌하므로 별도 토글로만).
