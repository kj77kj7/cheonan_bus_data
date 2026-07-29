"""단계 1-3 - 노선 상세정보 보강.

routes_34010.csv 의 각 routeid 에 대해 노선정보 항목 조회
(getRouteInfoIem) 를 호출해 상세정보를 받아 하나의 CSV 로 합친다.

- 호출 사이 0.3초 간격
- 실패한 노선은 따로 모아 마지막에 보고, 진행은 계속
- 총 호출 건수 카운트
- 결과: data/cheonan_routes_detail.csv
"""

import csv
import json
import os
import sys
import time
from urllib.parse import urlencode
from urllib.request import urlopen

from config import BASE_ROUTE_INFO, CITY_CODE, PROJECT_ROOT, ENV_PATH, load_env

OPERATION = "getRouteInfoIem"
INPUT_CSV = os.path.join(PROJECT_ROOT, "data", "routes_34010.csv")
OUTPUT_CSV = os.path.join(PROJECT_ROOT, "data", "cheonan_routes_detail.csv")
SLEEP_SEC = 0.3

# 보고용 컬럼 우선순위 (있으면 이 순서대로 앞에 배치)
PREFERRED = [
    "routeid", "routeno", "routetp",
    "startnodenm", "endnodenm",
    "startvehicletime", "endvehicletime",
    "intervaltime", "intervalsattime", "intervalsunttime",
]


def load_service_key():
    env = load_env(ENV_PATH)
    for name in ("SERVICE_KEY_DECODING", "SERVICE_KEY_ENCODING"):
        value = env.get(name, "").strip()
        if value and not value.startswith("여기에"):
            return value
    print("[중단] .env 에 인증키가 없습니다.")
    sys.exit(1)


def fetch_detail(service_key, route_id):
    params = {
        "serviceKey": service_key,
        "_type": "json",
        "cityCode": CITY_CODE,
        "routeId": route_id,
    }
    url = BASE_ROUTE_INFO + "/" + OPERATION + "?" + urlencode(params)
    with urlopen(url, timeout=20) as resp:
        raw = resp.read().decode("utf-8")
    data = json.loads(raw)  # 오류 시 예외 → 호출측에서 실패 처리
    response = data.get("response", {})
    header = response.get("header", {})
    if header.get("resultCode") != "00":
        raise RuntimeError("resultCode=%s msg=%s" % (header.get("resultCode"), header.get("resultMsg")))
    items = response.get("body", {}).get("items", "")
    if items in ("", None):
        raise RuntimeError("빈 응답(items 없음)")
    item = items.get("item")
    if isinstance(item, list):
        item = item[0] if item else None
    if not isinstance(item, dict):
        raise RuntimeError("item 형식 예외")
    return item


def main():
    service_key = load_service_key()
    route_ids = []
    with open(INPUT_CSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            route_ids.append(row["routeid"])

    print("대상 노선 ID: %d개" % len(route_ids))

    results = []
    failures = []
    call_count = 0

    for idx, route_id in enumerate(route_ids, 1):
        # 일시적 오류(타임아웃 등)는 최대 3회 재시도, 그래도 실패하면 목록에 남기고 진행
        item = None
        last_err = None
        for attempt in range(3):
            call_count += 1
            try:
                item = fetch_detail(service_key, route_id)
                break
            except (OSError, RuntimeError, json.JSONDecodeError) as e:
                # OSError 가 URLError·HTTPError·socket.timeout 을 모두 포함한다
                last_err = str(e)
                time.sleep(0.5)
        if item is not None:
            results.append(item)
        else:
            failures.append((route_id, last_err))
        if idx % 50 == 0:
            print("  진행 %d/%d (성공 %d, 실패 %d)" % (idx, len(route_ids), len(results), len(failures)))
        time.sleep(SLEEP_SEC)

    # 컬럼: 선호 컬럼 우선 + 나머지 등장순
    columns = [c for c in PREFERRED]
    for row in results:
        for key in row.keys():
            if key not in columns:
                columns.append(key)

    os.makedirs(os.path.dirname(OUTPUT_CSV), exist_ok=True)
    with open(OUTPUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in results:
            writer.writerow(row)

    print("")
    print("=== 단계 1-3 결과 ===")
    print("총 호출 건수: %d" % call_count)
    print("성공: %d건 | 실패: %d건" % (len(results), len(failures)))
    print("저장 위치: %s" % OUTPUT_CSV)
    print("컬럼(%d개): %s" % (len(columns), ", ".join(columns)))
    if failures:
        print("\n실패 노선 목록:")
        for rid, msg in failures:
            print("  %s : %s" % (rid, msg))
    if results:
        print("\n샘플 2건:")
        for row in results[:2]:
            print("  " + json.dumps(row, ensure_ascii=False))


if __name__ == "__main__":
    main()
