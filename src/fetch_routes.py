"""단계 1 - 천안시 전체 시내버스 노선 목록 조회 후 CSV 저장.

- 오퍼레이션: getRouteNoList (버스노선정보 서비스)
- cityCode: 34010 (천안시)
- 페이지네이션 처리, 결과 1건일 때 dict 로 오는 경우 처리
- 오류 시 추측으로 우회하지 않고 원인을 출력한 뒤 중단
"""

import csv
import json
import os
import sys
from urllib.parse import urlencode
from urllib.request import urlopen
from urllib.error import URLError, HTTPError

from config import BASE_ROUTE_INFO, CITY_CODE, PROJECT_ROOT, ENV_PATH, load_env

OPERATION = "getRouteNoList"
NUM_OF_ROWS = 100
OUTPUT_CSV = os.path.join(PROJECT_ROOT, "data", "routes_34010.csv")


def build_url(service_key, page_no):
    params = {
        "serviceKey": service_key,  # 디코딩 키. urlencode 가 퍼센트 인코딩한다.
        "pageNo": page_no,
        "numOfRows": NUM_OF_ROWS,
        "_type": "json",
        "cityCode": CITY_CODE,
    }
    return BASE_ROUTE_INFO + "/" + OPERATION + "?" + urlencode(params)


def fetch_page(service_key, page_no):
    url = build_url(service_key, page_no)
    with urlopen(url, timeout=20) as resp:
        raw = resp.read().decode("utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # 인증 오류 등은 JSON 이 아니라 XML 로 반환되는 경우가 많다.
        raise RuntimeError(
            "JSON 파싱 실패 - 서버가 오류(XML)를 반환했을 수 있습니다.\n"
            "원본 응답 앞부분:\n" + raw[:500]
        )


def normalize_items(body):
    """body.items 를 항상 리스트로 정규화한다.

    - 결과 0건: items 가 "" (빈 문자열) 로 온다
    - 결과 1건: items.item 이 dict 로 온다
    - 결과 여러 건: items.item 이 list 로 온다
    """
    items = body.get("items", "")
    if items in ("", None):
        return []
    item = items.get("item", [])
    if isinstance(item, dict):
        return [item]
    if isinstance(item, list):
        return item
    return []


def main():
    env = load_env(ENV_PATH)

    def is_filled(value):
        return value and not value.startswith("여기에")

    # 디코딩 키를 우선 사용하고, 없으면 인코딩 키를 사용한다.
    # (공공데이터포털이 키를 한 종류만 발급하는 경우가 있다)
    service_key = ""
    for name in ("SERVICE_KEY_DECODING", "SERVICE_KEY_ENCODING"):
        value = env.get(name, "").strip()
        if is_filled(value):
            service_key = value
            break

    if not service_key:
        print("[중단] .env 에 인증키가 없습니다.")
        print("       SERVICE_KEY_DECODING 또는 SERVICE_KEY_ENCODING 에 키를 입력한 뒤 다시 실행하십시오.")
        print("       경로: " + ENV_PATH)
        sys.exit(1)

    all_rows = []
    page_no = 1
    total_count = None

    while True:
        try:
            data = fetch_page(service_key, page_no)
        except (URLError, HTTPError) as e:
            print("[중단] 네트워크 오류: " + str(e))
            sys.exit(1)
        except RuntimeError as e:
            print("[중단] " + str(e))
            sys.exit(1)

        response = data.get("response", {})
        header = response.get("header", {})
        code = header.get("resultCode")
        msg = header.get("resultMsg")
        if code != "00":
            print("[중단] API 오류 응답: resultCode=" + str(code) + ", resultMsg=" + str(msg))
            print("       오퍼레이션명 또는 인증키를 확인해야 할 수 있습니다.")
            sys.exit(1)

        body = response.get("body", {})
        if total_count is None:
            total_count = int(body.get("totalCount", 0))
            print("총 노선 수(totalCount): " + str(total_count))
            if total_count == 0:
                print("[중단] 조회 결과가 0건입니다. cityCode(34010) 또는 오퍼레이션명을 확인하십시오.")
                sys.exit(1)

        rows = normalize_items(body)
        all_rows.extend(rows)
        print("페이지 " + str(page_no) + " 수집: " + str(len(rows)) + "건 (누적 " + str(len(all_rows)) + ")")

        if len(all_rows) >= total_count or not rows:
            break
        page_no += 1

    # 컬럼: 모든 레코드 키의 합집합 (등장 순서 유지)
    columns = []
    for row in all_rows:
        for key in row.keys():
            if key not in columns:
                columns.append(key)

    os.makedirs(os.path.dirname(OUTPUT_CSV), exist_ok=True)
    with open(OUTPUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in all_rows:
            writer.writerow(row)

    print("")
    print("=== 단계 1 결과 ===")
    print("총 건수: " + str(len(all_rows)))
    print("컬럼 목록(" + str(len(columns)) + "개): " + ", ".join(columns))
    print("저장 위치: " + OUTPUT_CSV)
    print("")
    print("샘플 3건:")
    for row in all_rows[:3]:
        print("  " + json.dumps(row, ensure_ascii=False))


if __name__ == "__main__":
    main()
