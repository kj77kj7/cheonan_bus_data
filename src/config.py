"""프로젝트 공통 설정: 경로, API 엔드포인트, .env 로더.

외부 패키지 없이 파이썬 표준 라이브러리만 사용한다.
"""

import os

# --- 경로 ---
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SRC_DIR)
ENV_PATH = os.path.join(PROJECT_ROOT, ".env")
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
DATA_RAW_DIR = os.path.join(DATA_DIR, "raw")
LOGS_DIR = os.path.join(PROJECT_ROOT, "logs")

# --- 공공데이터포털 TAGO 버스정보 서비스 엔드포인트 ---
# https 를 쓴다. 2026-08-02 무렵부터 평문 http(80) 가 응답하지 않는다.
# TCP 연결은 되고 응답만 오지 않아서, 서버 장애처럼 보이지만 실제로는
# 같은 요청이 https 로는 0.1초 만에 정상 응답한다.
BASE_ROUTE_INFO = "https://apis.data.go.kr/1613000/BusRouteInfoInqireService"
BASE_STATION_INFO = "https://apis.data.go.kr/1613000/BusSttnInfoInqireService"
BASE_LOCATION_INFO = "https://apis.data.go.kr/1613000/BusLcInfoInqireService"
BASE_ARRIVAL_INFO = "https://apis.data.go.kr/1613000/ArvlInfoInqireService"

# --- 천안시 도시코드 ---
CITY_CODE = "34010"


def load_env(path=ENV_PATH):
    """.env 파일을 읽어 dict 로 반환한다. (python-dotenv 미사용)"""
    env = {}
    if not os.path.exists(path):
        return env
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env
