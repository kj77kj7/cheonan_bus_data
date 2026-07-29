"""단계 3 사전점검 - 실시간 버스위치 API 응답 필드 확인.

정류소 목록을 왜 미리 받아둬야 하는지 판단하려면, 실시간 API 가 이미
무엇을 주는지 알아야 한다. 운행 중인 버스가 잡힐 때까지 몇 개 노선을
차례로 조회한다. 호출량은 최대 몇 건이라 무시할 수준이다.
"""

import json
import os
import sys
import time
from urllib.parse import urlencode
from urllib.request import urlopen

from config import BASE_LOCATION_INFO, CITY_CODE, LOGS_DIR, ENV_PATH, load_env

OPERATION = "getRouteAcctoBusLcList"
# 배차가 짧아 운행 중일 가능성이 높은 노선부터
TRY_ROUTE_IDS = [
    ("400", "CAB285000142"),
    ("2", "CAB285000003"),
    ("11", "CAB285000006"),
    ("6", "CAB285000412"),
    ("90", "CAB285000310"),
    ("800", "CAB285000325"),
]
SAMPLE_PATH = os.path.join(LOGS_DIR, "sample_buslc.json")


def load_service_key():
    env = load_env(ENV_PATH)
    for name in ("SERVICE_KEY_DECODING", "SERVICE_KEY_ENCODING"):
        value = env.get(name, "").strip()
        if value and not value.startswith("여기에"):
            return value
    print("[중단] .env 에 인증키가 없습니다.")
    sys.exit(1)


def main():
    service_key = load_service_key()
    print("현재 시각: %s" % time.strftime("%Y-%m-%d %H:%M:%S"))

    for routeno, route_id in TRY_ROUTE_IDS:
        params = {
            "serviceKey": service_key,
            "_type": "json",
            "cityCode": CITY_CODE,
            "routeId": route_id,
            "numOfRows": 100,
            "pageNo": 1,
        }
        url = BASE_LOCATION_INFO + "/" + OPERATION + "?" + urlencode(params)
        try:
            with urlopen(url, timeout=20) as resp:
                raw = resp.read().decode("utf-8")
        except OSError as e:
            print("%s번: 호출 실패 %s" % (routeno, e))
            time.sleep(0.4)
            continue

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            print("%s번: JSON 아님. 앞부분: %s" % (routeno, raw[:200]))
            time.sleep(0.4)
            continue

        response = data.get("response", {})
        header = response.get("header", {})
        if header.get("resultCode") != "00":
            print("%s번: resultCode=%s msg=%s"
                  % (routeno, header.get("resultCode"), header.get("resultMsg")))
            time.sleep(0.4)
            continue

        body = response.get("body", {})
        total = body.get("totalCount", 0)
        items = body.get("items", "")
        print("%s번(%s): totalCount=%s" % (routeno, route_id, total))

        if items in ("", None):
            time.sleep(0.4)
            continue

        item = items.get("item")
        if isinstance(item, dict):
            item = [item]

        os.makedirs(LOGS_DIR, exist_ok=True)
        with open(SAMPLE_PATH, "w", encoding="utf-8") as f:
            f.write(raw)
        print("")
        print("운행 중 차량 %d대 포착 → %s 저장" % (len(item), SAMPLE_PATH))
        print("실제 필드명: %s" % ", ".join(item[0].keys()))
        print("")
        print("샘플 전량:")
        for row in item:
            print("  " + json.dumps(row, ensure_ascii=False))
        return

    print("")
    print("운행 중인 차량이 잡히지 않았습니다. (운행시간대 밖이거나 일시적 공백)")


if __name__ == "__main__":
    main()
