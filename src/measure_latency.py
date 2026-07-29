"""수집 주기 설계용 - 버스위치 API 응답시간 실측.

수집 주기의 하한은 호출 한도가 아니라 '한 바퀴 도는 데 걸리는 시간'이 정한다.
순차 호출과 병렬 호출의 실측치를 비교해 동시 요청 수를 정한다.
"""

import csv
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode
from urllib.request import urlopen

from config import BASE_LOCATION_INFO, CITY_CODE, DATA_DIR, ENV_PATH, load_env

OPERATION = "getRouteAcctoBusLcList"
DETAIL_CSV = os.path.join(DATA_DIR, "cheonan_routes_detail.csv")
SAMPLE_N = 20
WORKER_LEVELS = [1, 4, 8, 16]


def load_service_key():
    env = load_env(ENV_PATH)
    for name in ("SERVICE_KEY_DECODING", "SERVICE_KEY_ENCODING"):
        v = env.get(name, "").strip()
        if v and not v.startswith("여기에"):
            return v
    sys.exit("[중단] .env 에 인증키가 없습니다.")


def call(service_key, route_id):
    params = {
        "serviceKey": service_key,
        "_type": "json",
        "cityCode": CITY_CODE,
        "routeId": route_id,
        "numOfRows": 100,
        "pageNo": 1,
    }
    url = BASE_LOCATION_INFO + "/" + OPERATION + "?" + urlencode(params)
    t0 = time.time()
    try:
        with urlopen(url, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
        json.loads(raw)
        return time.time() - t0, True
    except Exception:
        return time.time() - t0, False


def main():
    service_key = load_service_key()

    route_ids = []
    with open(DETAIL_CSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            route_ids.append(row["routeid"])
    sample = route_ids[:SAMPLE_N]

    print("표본 %d개 노선으로 측정합니다. (총 %d개 중)" % (len(sample), len(route_ids)))
    print("")

    results = {}
    for workers in WORKER_LEVELS:
        t0 = time.time()
        if workers == 1:
            out = [call(service_key, rid) for rid in sample]
        else:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                out = list(ex.map(lambda rid: call(service_key, rid), sample))
        elapsed = time.time() - t0

        lat = [d for d, ok in out]
        fails = sum(1 for _, ok in out if not ok)
        per_call = elapsed / len(sample)
        results[workers] = per_call

        print("동시 %2d개 | 전체 %.1f초 | 노선당 %.3f초 | 개별응답 중앙값 %.2f초 | 실패 %d건"
              % (workers, elapsed, per_call, statistics.median(lat), fails))
        time.sleep(1.0)  # 측정 간 간격

    print("")
    print("=== 전체 노선 1회 순회 예상 시간 ===")
    print("")
    print("| 동시 요청 | 37개 노선 | 265개 노선 |")
    print("|---|---|---|")
    for workers in WORKER_LEVELS:
        per = results[workers]
        print("| %d개 | %.0f초 | %.0f초 |" % (workers, per * 37, per * 265))

    print("")
    print("=== 일일 호출량 (05:30~23:00, 1,050분) ===")
    print("")
    print("| 주기 | 순회 | 37개 노선 | 265개 노선 |")
    print("|---|---|---|---|")
    for iv in (30, 60, 90, 120):
        c = int(1050 * 60 / iv)
        print("| %d초 | %d회 | %s건 | %s건 |"
              % (iv, c, format(37 * c, ","), format(265 * c, ",")))
    print("")
    print("일일 한도 500,000건")


if __name__ == "__main__":
    main()
