# 인수인계 — 새 세션에서 이어받을 때 읽는 문서

> 최종 갱신: 2026-08-26
> 이 문서만 읽으면 프로젝트를 이어받을 수 있도록 쓴다.

---

## 1. 30초 요약

**천안시 시내버스의 배차를 줄이거나 노선을 조정하면 이용객이 얼마나 늘어나는지**를
실측으로 추정하고, 개선 시나리오별 증가량과 우선순위를 내는 프로젝트.
2026년 천안시 AI·데이터 기반 정책 아이디어 경진대회 출품용.

**지금 상태**

- 실시간 버스위치 수집기 2개가 무인으로 돌고 있다
- 교통카드 승하차(STCIS) 수집기를 만들어 검증까지 끝냈다
- **다음 관문은 정보공개청구 하나다** — 배차간격 변경 이력 (7장)

---

## 2. 목표가 한 번 바뀌었다 — 반드시 읽을 것

초기 문서는 **버스 도착 지연 예측**을 본체로 잡았고, 2024-01-27 노선 개편을
처치로 삼는 **DID** 를 정책 근거로 두었다. **둘 다 바뀌었다.**

| | 전 | 후 |
|---|---|---|
| 본체 | 지연 예측 | **배차 → 이용률 탄력성 추정** |
| 인과 설계 | 개편 전후 DID | **노선 고정효과 패널** |
| 지연 예측 | 본체 | 서비스 품질 변수 (투입) |

**DID 를 폐기한 이유** — STCIS 승하차 데이터가 **2024년 4~5월부터** 시작한다.
개편 시행일(2024-01-27)보다 늦어서 '개편 전' 칸이 비고, 실시간 데이터는 2026년
7월부터라 그쪽도 못 채운다. 2023 년 여섯 구간을 직접 찔러 전부 0건인 것을 확인했다
(같은 파라미터로 2024-05 는 260건이 나온다. 요청 문제가 아니다).

`rank_control_candidates.py` 의 DID 대조군 산출물은 **지우지 않았다.** 노선 유사도는
다른 데 쓸 데가 있다.

자세한 것은 [DESIGN.md](DESIGN.md) 3·4장.

---

## 3. 지금 돌고 있는 것

Windows 작업 스케줄러에 등록된 두 작업이 파이썬 수집기를 상시 실행 중이다.

```
작업 스케줄러 라이브러리 > CheonanBus >
  collect_core      37 routeId,  60초 주기   (표본 22개 노선, 정밀)
  collect_network  265 routeId, 600초 주기   (전수, 광역)
```

- 실행 계정 **S4U** — 로그인 안 해도 돌아간다
- 트리거 3개 — 시스템 시작 시(+1분) / 매일 05:25 / **워치독 10분 반복**
- `MultipleInstances=IgnoreNew` — 이미 돌고 있으면 무시된다
  (작업 상태의 `LastTaskResult=0x800710E0` 이 그 코드다. **정상이다**)
- 수집 시간대 **05:30 ~ 다음날 01:00**. 01:00~05:30 은 대기 (정상)

### 확인

```bash
python C:\Users\kj77k\Downloads\cheonan_bus_data\src\check_health.py
```

정상 판정: **경보 없음 / 저장 실패 없음 / 순회 간격 60·600초 / 빈 구간 없음**
`logs/ALERT_*.txt` 가 생기면 그 안에 원인과 조치가 적혀 있다.

### 재시작 (코드를 고쳤을 때만)

**관리자 PowerShell**에서:

```bash
powershell -ExecutionPolicy Bypass -File C:\Users\kj77k\Downloads\cheonan_bus_data\scripts\restart_tasks.ps1
```

> `Stop-ScheduledTask` 만 쓰면 안 된다. 런처(cmd)만 죽고 파이썬이 **고아 프로세스로
> 살아남아** 옛 코드가 계속 돈다. `restart_tasks.ps1` 은 프로세스가 사라진 것을
> 확인한 뒤에만 시작한다. 수집기가 `RunLevel=Highest` 로 떠서 **일반 권한으로는
> 종료할 수 없다.**

완전 중지는 `unregister_tasks.ps1`.

---

## 4. 파일 지도

```
cheonan_bus_data/
  .env                    인증키·STCIS 쿠키 (git 제외)
  docs/
    HANDOFF.md            ← 지금 이 문서
    DESIGN.md             분석·모델 설계 (탄력성 패널, 데이터 모델)
    DATA_SOURCES.md       추가로 필요한 데이터와 확보 경로
    DELIVERABLES.md       완성 시 결과물
  src/
    config.py             경로·엔드포인트·.env 로더
    collect_realtime.py   ★ 실시간 수집기 (Pacer 가 여기 있다)
    check_health.py       ★ 건강 점검
    fetch_routes.py / fetch_route_detail.py / fetch_route_stops.py
                          단계1~2 정적 수집
    export_stop_list.py   route_stops.csv → 조회용 정류장 목록
    make_query_batches.py 정류장 목록을 붙여넣기 묶음으로 자르기
    probe_stcis.py        STCIS 응답 형태 사전탐색
    fetch_ridership.py       ★ STCIS 정류장별 이용량
    fetch_route_ridership.py ★ STCIS 노선별 이용량
    rank_control_candidates.py    (DID 폐기로 현재 미사용)
    check_nodeid_fragmentation.py / check_poll_interval.py / verify_stage2.py
    measure_latency.py / probe_*.py   API 사전탐색 (역할 끝남)
    make_thumbnail.py     포털 등록용 썸네일
  scripts/                스케줄러 등록/재시작/해제 (PowerShell, 관리자)
  data/                   git 제외
    routes_34010.csv           265행
    cheonan_routes_detail.csv  265행 (+ 공표 배차)
    route_stops.csv          9,098행
    stop_names.txt / stop_ars.txt / stop_list.csv   조회 입력용
    stcis_route_ids.csv        198행 (STCIS 노선 ID)
    realtime/                  실시간 원본 (일자별)
    ridership/                 STCIS 이용량
  logs/                   git 제외
```

---

## 5. STCIS 사용법 — 교통카드 승하차

공개 API 가 아니라 **로그인 세션으로 도는 화면**이다.

### 쿠키 넣기

STCIS 에 로그인 → F12 → Network → 아무 조회 → 요청 우클릭 →
**Copy as cURL** → `-b '...'` 안의 문자열을 `.env` 에 넣는다.

```
STCIS_COOKIE=JSESSIONID=...; WMONID=...
```

만료되면 401/403 이 오고 수집기가 즉시 멈춘다. 쿠키만 갱신하고 다시 돌리면
**받은 지점부터 이어받는다.**

### 실행

```bash
python src\fetch_route_ridership.py routes                      # 노선 ID 목록 (1회)
python src\fetch_route_ridership.py data 2026-08-01 2026-08-14  # 한 구간
python src\fetch_route_ridership.py data                        # 6개 구간 전체

python src\export_stop_list.py                                  # 정류장 목록
python src\fetch_ridership.py stops                             # 정류장 ID (1회)
python src\fetch_ridership.py data 2026-08-01 2026-08-14
```

### ⚠ 반드시 지킬 것

- **하루 총 요청 수에 상한이 있다.** 1.5초 간격으로 수백 건을 보냈더니 STCIS 가
  그 PC 를 통째로 막았다. 루트 페이지까지 20초 타임아웃으로 죽는다.
  `MIN_GAP` 은 **3초**로 두었고, **기간을 나눠 여러 날에 걸쳐** 받는다.
- **수집 중에 다른 확인용 요청을 병렬로 보내지 말 것.** 경합으로 타임아웃이 난다.
- 정류장별은 승강장이 2,177개라 노선별(198개)보다 요청이 11배다. **노선별을
  먼저** 끝내고 정류장별은 여유 있을 때.

---

## 6. 이미 겪은 함정 — 다시 밟지 말 것

전부 수정됐고 커밋에 기록돼 있다.

| # | 사고 | 원인 | 조치 |
|---|---|---|---|
| 1 | 08-01 하루치 전량 유실 | `Writer.close()` 가 `self.day` 를 안 비워, 01:00에 닫힌 writer 를 05:30에 재사용 | `af57250` |
| 2 | 08-02~03 대량 실패 | 포털이 **평문 HTTP(80) 차단**. TCP 는 연결되고 응답만 안 와서 서버 장애로 오인 | `f3a041f` — 전 엔드포인트 `https://` |
| 3 | HTTPS 전환 직후 429 폭증 | 응답이 4.5초→0.1초로 빨라져 순간 속도가 9건/초까지. **429는 인증키 단위** | `630d56a` — `Pacer` |
| 4 | 1번이 경보에 안 걸림 | `sweep()` 이 쓰기 예외를 삼켜 "실패 0건"으로 보고 | `f3a041f` |
| 5 | 점검이 정상을 이상으로 보고 | 순회가 길어지자 CSV 타임스탬프 묶기가 순회를 쪼갬 | `818c902` |
| 6 | 복구 후에도 경보가 안 지워짐 | 해제 조건이 streak 기반인데 재시작하면 streak 이 0 | `818c902` |
| 7 | **읽기 타임아웃에 재시도가 안 걸림** | `response.read()` 의 `socket.timeout` 은 `URLError` 가 아니라 두 except 를 비껴감 | `ae60ab8` — `OSError` 로 받는다 |
| 8 | STCIS PC 단위 차단 | 1.5초 간격으로 수백 건 | `ae60ab8` — `MIN_GAP=3` |

### 규칙

- **모든 공공데이터포털 API 는 `https://`.**
- **인증키는 디코딩 키.** `urlencode` 가 자동으로 퍼센트 인코딩한다.
- **새 수집기는 `Pacer` 를 재사용한다.** 429는 키 단위라 다른 프로세스까지 죽인다.
- **재시도 루프는 `OSError` 로 받는다.** `URLError` 만으로는 새어 나간다.
- **PowerShell 스크립트는 UTF-8 BOM 으로 저장한다.** BOM 이 없으면 PS 5.1 이
  cp949 로 읽어 한글 주석에서 파싱이 깨진다.
- **작업 스케줄러 반복 트리거의 Duration 은 비운다.** `[TimeSpan]::MaxValue` 는
  `P99999999DT23H59M59S` 로 직렬화돼 등록이 거부된다.

---

## 7. 다음에 할 일

### 지금 당장 — 정보공개청구 하나

**[DATA_SOURCES.md](DATA_SOURCES.md) A-1.** 이게 모형의 생사를 쥔다.

> 열린정부(www.open.go.kr) → 천안시청 대중교통과
> **2024년 1월 ~ 2026년 8월 노선별 배차간격(운행횟수) 변경 이력**
> 변경 시행일, 노선번호, 변경 전·후 배차간격 또는 1일 운행횟수

배차가 변한 기록이 없으면 [DESIGN.md](DESIGN.md) 4장의 β 를 추정할 수 없다.
행정 문서라 회신 가능성이 높지만 대기가 길다. **먼저 걸어두는 것이 맞다.**

### 그다음 (병행 가능)

| 순서 | 작업 | 비고 |
|---|---|---|
| 1 | STCIS 노선별 6개 구간 수집 | 하루 한두 구간씩 |
| 2 | `build_stop_events.py` | 실시간 → 통과 사건 |
| 3 | `build_headway.py` — 실측 배차·번칭 | **산출물 A 핵심** |
| 4 | SGIS 권역 인구·종사자 | 정류장 좌표 이미 있음 |
| 5 | `fetch_weather.py` — ASOS | 소급 가능 |
| 6 | `build_route_panel.py` → `estimate_elasticity.py` | **본체** |

변환 규칙과 이상치 처리는 [DESIGN.md](DESIGN.md) 5장.
특히 **회차 분리**(`nodeord` 감소를 경계로)를 빠뜨리면 서로 다른 운행이 한
시계열로 섞인다.

---

## 8. 수집 결손일 — 분석에서 제외할 날

**모든 유실은 PC 종료 때문이다. 수집기 소프트웨어는 08-04 이후 무결점이다.**

| 날짜 | core 행 | 상태 | 원인 |
|---|---|---|---|
| 07-31 | 31,931 | 부분 | 14:28 수집 시작 |
| 08-01 | **0** | 유실 | Writer 버그 (함정 1) |
| 08-02 | 28,332 | 부분 | HTTP 차단 (함정 2) |
| 08-03 | 30,976 | 부분 | HTTP 차단 / 429 |
| 08-05 | 55,621 | 부분 | PC 종료 |
| 08-15 | 20,989 | 부분 | PC 종료 |
| 08-17 | **1** | 유실 | PC 종료 |
| 08-19 | 부분 | 부분 | PC 종료 |

**온전한 12일 (분석에 쓸 날)**

```
20260804 20260806 20260807 20260808 20260809 20260810
20260811 20260812 20260813 20260814 20260816 20260818
```

> 이 목록을 분석 코드에 **화이트리스트로 박아둘 것.** 결손일이 섞이면 통계가
> 왜곡된다. 8/19 이후로 수집이 이어졌다면 `check_health.py all` 로 갱신한다.

요일 분포가 고르지 않다 — 화 3일, 목·금·일 2일, **월·수·토 각 1일**.
요일 효과를 변수로 쓸 때 주의한다. 수집 기간의 유일한 공휴일(8/15 광복절)이
결손일이라 **공휴일 효과는 이 데이터로 못 본다.**

---

## 9. 운영 상 알아둘 것

- **PC 를 계속 켜둬야 한다.** 수집 시간대(05:30~01:00)에 꺼져 있으면 그만큼
  유실된다. 01:00~05:30 은 꺼도 무방하지만 05:30 전에 켜야 한다.
- 재부팅은 자유롭다. BootTrigger 가 1분 안에 복구한다 (여러 차례 검증).
- 자원 사용은 **CPU 0%, 메모리 30MB**. 다른 작업에 영향 없다.
- 호출량은 하루 core 약 45,000 / network 약 33,000. **일 한도 500,000건** 대비
  여유가 크다.
- 디스크는 하루 약 7 MB.
