# cheonan-bus

천안시 시내버스 데이터 수집 파이프라인.

2026년 천안시 AI·데이터 기반 정책 아이디어 경진대회 출품용. 노선별 실측
배차간격·정시성 산출과, 버스 도착 지연 예측·정류장 수요 예측 모델 개발을
최종 목표로 한다. 실시간 데이터는 소급 수집이 불가능하므로 수집 착수가 최우선.

## 데이터 출처

공공데이터포털(data.go.kr) TAGO 버스정보 서비스. 천안시 도시코드 34010.

## 사전 준비

1. `.env.example` 를 참고해 `.env` 를 만들고 인증키를 입력한다.
   ```
   SERVICE_KEY_DECODING=디코딩_키
   SERVICE_KEY_ENCODING=인코딩_키
   ```
   - 이 프로젝트는 `urllib.parse.urlencode` 로 파라미터를 조립하므로
     **디코딩 키**를 사용한다.
   - 외부 패키지 없이 파이썬 표준 라이브러리만 사용한다.

## 단계 1 - 노선 목록 조회

천안시 전체 시내버스 노선 목록을 조회해 CSV 로 저장한다.

```
python src/fetch_routes.py
```

- 결과: `data/routes_34010.csv`
- 실행 후 총 건수, 컬럼 목록, 샘플 3건을 콘솔에 출력한다.

## 폴더 구조

```
cheonan-bus/
  .env            # 인증키 (git 제외)
  .env.example    # 인증키 템플릿
  .gitignore
  README.md
  src/
    config.py     # 경로 / 엔드포인트 / .env 로더
    fetch_routes.py  # 단계 1 노선 목록 수집
  data/           # 수집 결과 (git 제외)
  logs/           # 수집 로그 (git 제외)
```

## 이후 단계 (승인 후 진행)

- 단계 2: 선정 노선의 경유 정류소·좌표 수집
- 단계 3: 실시간 위치 수집기 (핵심)
- 단계 4: 기상청 ASOS 시간자료 수집
