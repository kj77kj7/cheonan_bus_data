"""단계 2 사전점검 - 경유 정류소 API 응답 필드명 확인.

routeId 하나로 시험 호출해 raw JSON 을 logs/sample_response.json 에 저장하고
실제 키 이름을 출력한다. 본 수집 루프를 돌리기 전에 1회만 실행한다.
"""

import json
import os
import sys
from urllib.parse import urlencode
from urllib.request import urlopen

from config import BASE_ROUTE_INFO, CITY_CODE, LOGS_DIR, ENV_PATH, load_env

OPERATION = "getRouteAcctoThrghSttnList"
SAMPLE_ROUTE_ID = "CAB285000385"  # 5번 (처치군)
SAMPLE_PATH = os.path.join(LOGS_DIR, "sample_response.json")


def load_service_key():
    env = load_env(ENV_PATH)
    for name in ("SERVICE_KEY_DECODING", "SERVICE_KEY_ENCODING"):
        value = env.get(name, "").strip()
        if value and not value.startswith("여기에"):
            return name, value
    print("[중단] .env 에 인증키가 없습니다: " + ENV_PATH)
    sys.exit(1)


def main():
    key_name, service_key = load_service_key()
    print("사용 인증키: %s" % key_name)

    params = {
        "serviceKey": service_key,
        "_type": "json",
        "cityCode": CITY_CODE,
        "routeId": SAMPLE_ROUTE_ID,
        "numOfRows": 200,
        "pageNo": 1,
    }
    url = BASE_ROUTE_INFO + "/" + OPERATION + "?" + urlencode(params)

    with urlopen(url, timeout=20) as resp:
        raw = resp.read().decode("utf-8")

    os.makedirs(LOGS_DIR, exist_ok=True)
    with open(SAMPLE_PATH, "w", encoding="utf-8") as f:
        f.write(raw)
    print("원본 응답 저장: %s" % SAMPLE_PATH)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # 인증 오류 등은 XML 로 반환된다.
        print("[중단] JSON 파싱 실패. 응답 앞부분:")
        print(raw[:800])
        sys.exit(1)

    response = data.get("response", {})
    header = response.get("header", {})
    print("resultCode=%s resultMsg=%s" % (header.get("resultCode"), header.get("resultMsg")))
    if header.get("resultCode") != "00":
        sys.exit(1)

    body = response.get("body", {})
    print("totalCount=%s numOfRows=%s pageNo=%s"
          % (body.get("totalCount"), body.get("numOfRows"), body.get("pageNo")))

    items = body.get("items", "")
    if items in ("", None):
        print("[경고] items 가 비어 있습니다.")
        sys.exit(1)
    item = items.get("item")
    if isinstance(item, dict):
        item = [item]

    print("수집 건수: %d" % len(item))
    print("실제 필드명: %s" % ", ".join(item[0].keys()))
    print("")
    print("샘플 3건:")
    for row in item[:3]:
        print("  " + json.dumps(row, ensure_ascii=False))


if __name__ == "__main__":
    main()
