"""단계 2 - 노선별 경유 정류소 전수 수집 (1회성).

routes_34010.csv 의 모든 routeId 에 대해 경유 정류소 목록 조회
(getRouteAcctoThrghSttnList) 를 호출해 정류소 ID·명칭·좌표·경유순번을
data/route_stops.csv 로 저장한다.

- 재개 가능: 이미 수집된 routeId 는 건너뛰고 CSV 에 이어쓴다
- 재시도: 지수 백오프로 3회. 최종 실패는 logs/failed_routes.txt 에 기록하고 계속 진행
- 레이트 리밋: 호출 간 0.4초

주의: 이 API 응답에는 updowncd 필드가 없다 (상·하행은 routeId 로 이미 분리됨).
컬럼은 유지하되 빈 값으로 채운다.
"""

import csv
import json
import os
import sys
import time
from datetime import datetime
from urllib.parse import urlencode
from urllib.request import urlopen

from config import BASE_ROUTE_INFO, CITY_CODE, DATA_DIR, LOGS_DIR, ENV_PATH, load_env

OPERATION = "getRouteAcctoThrghSttnList"
NUM_OF_ROWS = 200
SLEEP_SEC = 0.4
MAX_ATTEMPTS = 3

ROUTES_CSV = os.path.join(DATA_DIR, "routes_34010.csv")
DETAIL_CSV = os.path.join(DATA_DIR, "cheonan_routes_detail.csv")
OUTPUT_CSV = os.path.join(DATA_DIR, "route_stops.csv")
FAILED_PATH = os.path.join(LOGS_DIR, "failed_routes.txt")

COLUMNS = [
    "routeid", "routeno", "nodeord", "nodeid", "nodenm", "nodeno",
    "gpslati", "gpslong", "updowncd", "fetched_at",
]


def load_service_key():
    env = load_env(ENV_PATH)
    decoding = env.get("SERVICE_KEY_DECODING", "").strip()
    if decoding and not decoding.startswith("여기에"):
        return "SERVICE_KEY_DECODING", decoding

    encoding = env.get("SERVICE_KEY_ENCODING", "").strip()
    if encoding and not encoding.startswith("여기에"):
        print("[안내] SERVICE_KEY_DECODING 이 비어 있어 ENCODING 키로 폴백합니다.")
        print("       공공데이터포털의 디코딩 키를 .env 에 채워두는 편이 정석입니다: " + ENV_PATH)
        return "SERVICE_KEY_ENCODING", encoding

    print("[중단] .env 에 인증키가 없습니다: " + ENV_PATH)
    sys.exit(1)


def load_routeno_map():
    """routeid -> routeno 대응표. 상세 CSV 우선, 없으면 원본 목록."""
    mapping = {}
    for path in (ROUTES_CSV, DETAIL_CSV):
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                rid = row.get("routeid")
                if rid:
                    mapping[rid] = row.get("routeno", "")
    return mapping


def fetch_page(service_key, route_id, page_no):
    params = {
        "serviceKey": service_key,
        "_type": "json",
        "cityCode": CITY_CODE,
        "routeId": route_id,
        "numOfRows": NUM_OF_ROWS,
        "pageNo": page_no,
    }
    url = BASE_ROUTE_INFO + "/" + OPERATION + "?" + urlencode(params)
    with urlopen(url, timeout=20) as resp:
        raw = resp.read().decode("utf-8")

    data = json.loads(raw)  # 인증 오류 등은 XML 이라 여기서 예외 → 호출측 재시도
    response = data.get("response", {})
    header = response.get("header", {})
    if header.get("resultCode") != "00":
        raise RuntimeError("resultCode=%s msg=%s"
                           % (header.get("resultCode"), header.get("resultMsg")))

    body = response.get("body", {})
    total_count = int(body.get("totalCount", 0) or 0)
    items = body.get("items", "")
    if items in ("", None):
        return [], total_count
    item = items.get("item", [])
    if isinstance(item, dict):
        item = [item]
    return item, total_count


def fetch_route_stops(service_key, route_id):
    """한 노선의 전체 정류소를 페이지네이션까지 처리해 반환한다."""
    rows = []
    page_no = 1
    while True:
        page_rows, total_count = fetch_page(service_key, route_id, page_no)
        rows.extend(page_rows)
        if not page_rows or len(rows) >= total_count:
            break
        page_no += 1
    return rows


def load_done_routeids():
    """이미 수집이 끝난 routeId 집합. (재개용)"""
    if not os.path.exists(OUTPUT_CSV):
        return set()
    done = set()
    with open(OUTPUT_CSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            rid = row.get("routeid")
            if rid:
                done.add(rid)
    return done


def main():
    key_name, service_key = load_service_key()
    print("사용 인증키: %s" % key_name)

    routeno_map = load_routeno_map()
    route_ids = []
    with open(ROUTES_CSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            route_ids.append(row["routeid"])

    done = load_done_routeids()
    todo = [r for r in route_ids if r not in done]

    print("전체 routeId: %d개 | 기수집 %d개 | 이번 대상 %d개"
          % (len(route_ids), len(done), len(todo)))
    if not todo:
        print("수집할 노선이 없습니다. 이미 전부 완료된 상태입니다.")
        return

    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(LOGS_DIR, exist_ok=True)

    is_new_file = not os.path.exists(OUTPUT_CSV)
    started = time.time()
    failures = []
    call_count = 0
    stop_total = 0

    with open(OUTPUT_CSV, "a", encoding="utf-8-sig", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=COLUMNS, extrasaction="ignore")
        if is_new_file:
            writer.writeheader()

        for idx, route_id in enumerate(todo, 1):
            stops = None
            last_err = None
            for attempt in range(MAX_ATTEMPTS):
                call_count += 1
                try:
                    stops = fetch_route_stops(service_key, route_id)
                    break
                except (OSError, RuntimeError, ValueError) as e:
                    # OSError 가 URLError·HTTPError·timeout 을 포함한다
                    last_err = str(e)
                    time.sleep(0.5 * (2 ** attempt))  # 지수 백오프

            if stops is None:
                failures.append((route_id, last_err))
                print("  [실패] %s : %s" % (route_id, last_err))
            else:
                fetched_at = datetime.now().isoformat(timespec="seconds")
                routeno = routeno_map.get(route_id, "")
                for s in stops:
                    writer.writerow({
                        "routeid": s.get("routeid", route_id),
                        "routeno": routeno,
                        "nodeord": s.get("nodeord", ""),
                        "nodeid": s.get("nodeid", ""),
                        "nodenm": s.get("nodenm", ""),
                        "nodeno": s.get("nodeno", ""),
                        "gpslati": s.get("gpslati", ""),
                        "gpslong": s.get("gpslong", ""),
                        "updowncd": s.get("updowncd", ""),  # 이 API 는 미제공
                        "fetched_at": fetched_at,
                    })
                out.flush()  # 중간에 죽어도 여기까지는 남는다
                stop_total += len(stops)

            if idx % 20 == 0 or idx == len(todo):
                elapsed = time.time() - started
                print("  진행 %d/%d | 누적 정류장 %d개 | 실패 %d건 | 경과 %.1f초"
                      % (idx, len(todo), stop_total, len(failures), elapsed))

            time.sleep(SLEEP_SEC)

    if failures:
        with open(FAILED_PATH, "w", encoding="utf-8") as f:
            for rid, msg in failures:
                f.write("%s\t%s\n" % (rid, msg))
    elif os.path.exists(FAILED_PATH):
        os.remove(FAILED_PATH)  # 재실행으로 전부 복구된 경우

    elapsed = time.time() - started
    print("")
    print("=== 단계 2 수집 결과 ===")
    print("총 API 호출: %d회 (재시도 포함)" % call_count)
    print("성공 노선: %d개 | 실패 노선: %d개" % (len(todo) - len(failures), len(failures)))
    print("수집 정류장 행: %d개" % stop_total)
    print("소요 시간: %.1f초" % elapsed)
    print("저장 위치: %s" % OUTPUT_CSV)
    if failures:
        print("실패 목록: %s (재실행하면 실패분만 다시 시도합니다)" % FAILED_PATH)


if __name__ == "__main__":
    main()
