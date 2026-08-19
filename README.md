# cheonan-bus

천안시 시내버스 실시간 운행 데이터 수집·분석 파이프라인.

2026년 천안시 AI·데이터 기반 정책 아이디어 경진대회 출품용. 노선별 실측
배차간격·정시성 산출과, 버스 도착 지연 예측·정류장 수요 예측 모델 개발을
최종 목표로 한다. 실시간 데이터는 소급 수집이 불가능하므로 수집 착수가 최우선.

외부 패키지 없이 **파이썬 표준 라이브러리만** 사용한다.

## 문서

기획·설계 문서는 [`docs/`](docs/) 에 있다. **처음 보는 사람은 [docs/HANDOFF.md](docs/HANDOFF.md) 부터.**

| 문서 | 내용 |
|---|---|
| [docs/HANDOFF.md](docs/HANDOFF.md) | 현재 상태, 운영 방법, 겪은 함정, 결정 대기 항목 |
| [docs/DESIGN.md](docs/DESIGN.md) | 데이터 모델, 지표 정의, DID 설계, 진행 순서 |
| [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) | 추가로 필요한 데이터와 확보 경로 |
| [docs/DELIVERABLES.md](docs/DELIVERABLES.md) | 완성 시 결과물 |

## 데이터 출처

공공데이터포털(data.go.kr) TAGO 버스정보 서비스. 천안시 도시코드 34010.

> **엔드포인트는 반드시 `https://`.** 평문 HTTP(80)는 2026-08-02 무렵부터
> 응답하지 않는다. TCP 연결은 되고 응답만 오지 않아 서버 장애로 오인하기 쉽다.

## 사전 준비

`.env.example` 를 참고해 `.env` 를 만들고 인증키를 입력한다.

```
SERVICE_KEY_DECODING=디코딩_키
SERVICE_KEY_ENCODING=인코딩_키
```

`urllib.parse.urlencode` 로 파라미터를 조립하므로 **디코딩 키**를 사용한다.

## 수집 파이프라인

### 단계 1 — 노선 목록·상세

```
python src/fetch_routes.py         # -> data/routes_34010.csv        (265행)
python src/fetch_route_detail.py   # -> data/cheonan_routes_detail.csv (265행, 배차간격 포함)
```

### 단계 2 — 경유 정류소

```
python src/fetch_route_stops.py    # -> data/route_stops.csv          (9,098행)
python src/verify_stage2.py        # 품질 검증 -> logs/stage2_report.md
```

### 단계 3 — 실시간 위치 (핵심)

두 모드를 별도 프로세스로 동시에 돌린다. 하나가 죽어도 다른 하나는 살아남는다.

```
python src/collect_realtime.py core      # 표본 22개 노선(37 routeId) / 60초
python src/collect_realtime.py network   # 전체 265 routeId / 600초
```

- 수집 시간대 **05:30 ~ 다음날 01:00**
- 요청 간격을 강제해 인증키 단위 429 를 피한다 (`Pacer`)
- 결과: `data/realtime/{mode}_{YYYYMMDD}.csv`

## 무인 운영

Windows 작업 스케줄러에 등록하면 로그인 없이, 재부팅 후에도 자동으로 돌아간다.
**관리자 PowerShell**에서 실행한다.

```
powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1     # 등록
powershell -ExecutionPolicy Bypass -File scripts\restart_tasks.ps1      # 재시작 (코드 수정 후)
powershell -ExecutionPolicy Bypass -File scripts\unregister_tasks.ps1   # 해제
```

> 재시작은 반드시 `restart_tasks.ps1` 로. `Stop-ScheduledTask` 만 쓰면 런처(cmd)만
> 죽고 파이썬이 고아 프로세스로 남아 옛 코드가 계속 돈다.

## 상태 점검

```
python src/check_health.py          # 오늘
python src/check_health.py 20260813 # 특정 날짜
python src/check_health.py all      # 전체 기간 요약
```

정상 판정: **경보 없음 / 저장 실패 없음 / 순회 간격 60·600초 / 빈 구간 없음**.
문제가 생기면 `logs/ALERT_*.txt` 가 만들어지고, 그 안에 원인과 조치가 적힌다.

## 폴더 구조

```
cheonan-bus/
  .env / .env.example    인증키 (.env 는 git 제외)
  README.md
  docs/                  기획·설계 문서
  src/
    config.py            경로·엔드포인트·.env 로더
    collect_realtime.py  실시간 수집기
    check_health.py      건강 점검
    fetch_*.py           단계 1~2 수집
    check_*.py / verify_stage2.py / rank_control_candidates.py
                         품질 검증·분석 보조
  scripts/               작업 스케줄러 등록/재시작/해제 (PowerShell)
  data/                  수집 결과 (git 제외)
    realtime/            실시간 원본 (일자별)
  logs/                  수집 로그·경보·리포트 (git 제외)
```

## 진행 상황

- 단계 1~2 **완료**
- 단계 3 **가동 중** — 2026-07-31 시작, 20일차 기준 core 925,602행 / network 287,837행
- 단계 4 (기상청 ASOS) — 미착수. [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) A-1 참고
- 분석 단계 — [docs/DESIGN.md](docs/DESIGN.md) 8장의 순서를 따른다
