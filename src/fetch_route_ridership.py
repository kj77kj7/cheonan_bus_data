"""STCIS 노선별 이용량을 수집한다.

정류장별(fetch_ridership.py)과 엔드포인트도 응답 형태도 다르다.

    1단계 (routes)  busLineListAjax.do    -> 노선 목록 (HTML, 27쪽 페이징)
    2단계 (data)    indicatorPivotAjax.do -> 일자·시간대별 이용량 (JSON)

정류장은 승강장이 2,177개라 오래 걸리지만 노선은 260개 남짓이라 훨씬 싸다.
배차간격과 이용률의 관계는 노선 단위로 서므로 이쪽을 먼저 받는 편이 낫다.

노선 검색은 노선번호를 비워도 천안 전체가 나오는 대신 한 쪽에 10건씩만
준다. 쪽 번호를 넘기는 파라미터 이름이 요청 캡처에 안 잡혀서, 후보를
차례로 넣어보고 1쪽과 다른 결과가 오는 것을 골라 쓴다.

응답 JSON 은 값이 0 인 칸을 아예 빼고 준다 (실측: 13일×19시간대 = 247칸
중 236칸만 옴). 빠진 칸을 0 으로 채워야 표가 온전해진다.

사용법
    python src/fetch_route_ridership.py routes                      # 1단계
    python src/fetch_route_ridership.py data                        # 2단계, 기본 기간 전체
    python src/fetch_route_ridership.py data 2026-08-01 2026-08-14  # 특정 기간만
"""

import csv
import json
import os
import re
import sys
import time

from collect_realtime import Pacer
from config import DATA_DIR, LOGS_DIR
from fetch_ridership import (BASE, BLOCK_STREAK, COOLDOWNS, MAX_ATTEMPTS,
                             MIN_GAP, PERIODS, SessionExpired, load_cookie,
                             post, text_of)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SEARCH_URL = BASE + "/pivotIndi/busLineListAjax.do"
INDICATOR_URL = BASE + "/pivotIndi/indicatorPivotAjax.do"

INDI_CD = "Z01722"
ZONE_SD = "44"                       # 충청남도
ZONE_SGG = "44130_44131_44133"       # 천안시 (동남구·서북구)

ROUTE_IDS_CSV = os.path.join(DATA_DIR, "stcis_route_ids.csv")
OUT_DIR = os.path.join(DATA_DIR, "ridership")

ROUTE_ID_COLUMNS = ["route_id", "route_no", "tcbo_id", "excclc_area_cd",
                    "route_sd_cd", "route_type", "stg_arr_nma"]
DATA_COLUMNS = ["route_id", "route_no", "stg_arr_nma", "date", "hour", "use_cnt"]

HOURS = ["%02d" % h for h in range(24)]

# 쪽 번호 파라미터 이름이 캡처에 안 잡혔다. 흔한 것부터 넣어본다.
PAGE_PARAM_CANDIDATES = ["pageIndex", "pageNo", "currentPageNo", "page", "pageNum"]

# 차단은 시간이 지나면 풀리므로, 한 바퀴에 못 받은 것은 쉬었다 다시 돈다.
MAX_PASSES = 3

# 철도 노선이 목록에 섞여 나온다. 시내버스 분석 대상이 아니다.
NON_BUS_ROUTE_NOS = {"경부선", "장항선"}

CHECKBOX_RE = re.compile(r'name="chkBusLine"\s+value="([^"]*)"')
ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.S)
CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
TOTAL_RE = re.compile(r"총\s*:\s*(\d+)\s*건")
LASTPAGE_RE = re.compile(r"busLinePaging\((\d+)\)")


def search_params(page_param=None, page=1):
    params = [
        ("searchPopZoneSd", ZONE_SD), ("searchPopZoneSgg", ZONE_SGG),
        ("searchPopZoneEmd", ""),
        ("popupSearchRouteNo", ""),
        ("searchDateGubun", "3"),
        ("searchFromYear", "2025"), ("searchToYear", "2025"),
        ("searchFromMonth", PERIODS[-1][0][:7]),
        ("searchToMonth", PERIODS[-1][1][:7]),
        ("searchFromDay", PERIODS[-1][0]), ("searchToDay", PERIODS[-1][1]),
        ("indiCd", INDI_CD),
    ]
    if page_param:
        params.append((page_param, str(page)))
    return params


def indicator_params(route, from_day, to_day):
    params = [
        ("indiCd", INDI_CD), ("siteGb", "P"),
        ("searchDateGubun", "3"),
        ("searchFromYear", from_day[:4]), ("searchToYear", to_day[:4]),
        ("searchFromMonth", from_day[:7]), ("searchToMonth", to_day[:7]),
        ("searchFromDay", from_day), ("searchToDay", to_day),
        ("zoneSd", ""), ("zoneSgg", ""), ("zoneEmd", ""), ("zoneDstrct", ""),
        ("selectZoneSd", ""), ("selectZoneSgg", ""),
        ("tcboId", route["tcbo_id"]),
        ("excclcAreaCd", route["excclc_area_cd"]),
        ("routeId", route["route_id"]),
        ("routeSdCd", route["route_sd_cd"]), ("routeSggCd", "44"),
        ("daybyTblNm", "DM_RUTBY_USECNT_001"),
        ("mnbyTblNm", "DM_MMBY_RUTBY_USECNT_001"),
        ("yrbyTblNm", "DM_YRBY_RUTBY_USECNT_001"),
        ("dstrctTblNm", ""), ("mnbyDstrctTblNm", ""), ("yrbyDstrctTblNm", ""),
    ]
    # 피벗 차원. 같은 키가 여러 번 오고 TZON 은 실제로 두 번 온다.
    for option in ("ROUTE_NO", "STG_ARR_NMA", "YYYY", "YYYYMM",
                   "OPRAT_DATE", "TZON", "GIN_STF", "TZON"):
        params.append(("ddOption[]", option))
    return params


def parse_route_list(html):
    """검색 응답에서 노선 목록을 뽑는다. (총건수, 마지막쪽, 노선들)

    같은 노선이 한 쪽에 두 번 나오는 경우가 있어 routeId 로 중복을 없앤다.
    """
    if "chkBusLine" not in html and "총" not in html:
        raise SessionExpired("노선 검색 응답이 로그인 화면으로 보입니다")

    total_match = TOTAL_RE.search(html)
    total = int(total_match.group(1)) if total_match else 0
    last_page = max([int(p) for p in LASTPAGE_RE.findall(html)] or [1])

    routes = {}
    for row_html in ROW_RE.findall(html):
        checkbox = CHECKBOX_RE.search(row_html)
        if not checkbox:
            continue
        # 03||29001501|44|1_백석농공단지 - 백석농공단지
        parts = checkbox.group(1).split("|")
        if len(parts) < 5:
            continue
        cells = [text_of(c) for c in CELL_RE.findall(row_html)]
        route_id = parts[2]
        if route_id in routes:
            continue
        routes[route_id] = {
            "route_id": route_id,
            "route_no": cells[1] if len(cells) > 1 else parts[4].split("_")[0],
            "tcbo_id": parts[0],
            "excclc_area_cd": parts[1],
            "route_sd_cd": parts[3],
            "route_type": cells[2] if len(cells) > 2 else "",
            "stg_arr_nma": cells[3] if len(cells) > 3 else "",
        }
    return total, last_page, list(routes.values())


def parse_indicator(payload):
    """조회 JSON 에서 (일자, 시간대, 이용량) 을 뽑는다.

    값이 0 인 칸은 응답에 아예 없다. 부르는 쪽에서 채운다.
    """
    try:
        data = json.loads(payload)
    except ValueError:
        raise SessionExpired("조회 응답이 JSON 이 아닙니다")
    if "list" not in data:
        raise SessionExpired("조회 응답에 list 가 없습니다")

    rows = []
    for item in data["list"]:
        date = str(item.get("opratDate", ""))
        rows.append({
            "date": date.split("(")[0],
            "hour": str(item.get("tzon", "")).zfill(2),
            "use_cnt": int(item.get("ginStf", 0) or 0),
            "route_no": str(item.get("routeNo", "")),
            "stg_arr_nma": str(item.get("stgArrNma", "")),
        })
    return rows


def detect_page_param(cookie, pacer, first_ids):
    """2쪽을 요청해 1쪽과 다른 결과를 주는 파라미터 이름을 찾는다."""
    for name in PAGE_PARAM_CANDIDATES:
        try:
            html = post(SEARCH_URL, search_params(name, 2), cookie, pacer)
            _, _, routes = parse_route_list(html)
        except (IOError, ValueError):
            continue
        ids = {r["route_id"] for r in routes}
        if ids and ids - first_ids:
            return name
    return None


def stage_routes(cookie):
    pacer = Pacer(MIN_GAP)
    html = post(SEARCH_URL, search_params(), cookie, pacer)
    total, last_page, routes = parse_route_list(html)
    print("총 %d건 / %d쪽 / 1쪽에서 고유 노선 %d개" % (total, last_page, len(routes)))

    found = {r["route_id"]: r for r in routes}
    if last_page > 1:
        page_param = detect_page_param(cookie, pacer, set(found))
        if not page_param:
            print()
            print("[중단] 쪽 번호 파라미터 이름을 못 찾았습니다.")
            print("       후보: %s" % ", ".join(PAGE_PARAM_CANDIDATES))
            print("       STCIS 노선검색 팝업에서 '2' 를 눌러 그 요청을")
            print("       'Copy as cURL' 로 떠서 알려주십시오.")
            return 1
        print("쪽 번호 파라미터: %s" % page_param)

        for page in range(2, last_page + 1):
            try:
                html = post(SEARCH_URL, search_params(page_param, page),
                            cookie, pacer)
                _, _, routes = parse_route_list(html)
            except SessionExpired as e:
                print("[중단] 세션이 만료됐습니다 (%s)." % e)
                return 1
            except (IOError, ValueError) as e:
                print("  [실패] %d쪽: %s" % (page, e))
                continue
            for route in routes:
                found.setdefault(route["route_id"], route)
            if page % 5 == 0 or page == last_page:
                print("  %d/%d쪽  누적 고유 노선 %d개" % (page, last_page, len(found)))

    dropped = [r for r in found.values() if r["route_no"] in NON_BUS_ROUTE_NOS]
    for route in dropped:
        del found[route["route_id"]]
    ordered = sorted(found.values(), key=lambda r: (len(r["route_no"]), r["route_no"]))
    with open(ROUTE_IDS_CSV, "w", encoding="utf-8-sig", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=ROUTE_ID_COLUMNS,
                                extrasaction="ignore")
        writer.writeheader()
        writer.writerows(ordered)

    print()
    print("고유 노선 %d개를 %s 에 저장했습니다." % (len(ordered), ROUTE_IDS_CSV))
    if dropped:
        print("철도 노선 %d개는 뺐습니다: %s"
              % (len(dropped), ", ".join(r["route_no"] for r in dropped)))
    outside = [r for r in ordered if r["tcbo_id"] != "03"]
    if outside:
        print("[참고] 천안 운수사(03)가 아닌 노선 %d개가 섞여 있습니다: %s"
              % (len(outside), ", ".join(r["route_no"] for r in outside[:10])))
    return 0


def fetch_period(routes, path, from_day, to_day, cookie, pacer):
    """노선 목록을 한 바퀴 돈다.

    세션이 만료되면 None, 아니면 (받은 수, 실패한 수) 를 돌려준다.
    """
    is_new = not os.path.exists(path)
    got = failed = 0
    # 막혔다고 판단한 뒤에는 한 번만 찔러 본다. 어차피 TCP 가 안 열리는
    # 상태라, 3회씩 두드리면 노선 하나에 70초씩 버린다.
    streak = 0
    cool = 0
    with open(path, "a", encoding="utf-8-sig", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=DATA_COLUMNS,
                                extrasaction="ignore")
        if is_new:
            writer.writeheader()
        for i, route in enumerate(routes, 1):
            try:
                payload = post(INDICATOR_URL,
                               indicator_params(route, from_day, to_day),
                               cookie, pacer,
                               attempts=1 if streak else MAX_ATTEMPTS)
                rows = parse_indicator(payload)
            except SessionExpired as e:
                print()
                print("[중단] 세션이 만료됐습니다 (%s)." % e)
                print("       .env 의 STCIS_COOKIE 를 갱신하고 다시 실행하면")
                print("       받은 지점부터 이어서 받습니다.")
                return None
            except (IOError, ValueError) as e:
                print("  [실패] %s(%s): %s"
                      % (route["route_no"], route["route_id"], e))
                failed += 1
                streak += 1
                if streak >= BLOCK_STREAK:
                    nap = COOLDOWNS[min(cool, len(COOLDOWNS) - 1)]
                    print("  [대기] %d회 연속 실패. 막힌 것으로 보고 %d초 쉽니다."
                          % (streak, nap))
                    time.sleep(nap)
                    cool += 1
                    streak = 0
                continue
            streak = 0
            cool = 0

            # 응답에 없는 칸은 0 이다. 일자별로 24시간을 채워 표를 온전히 만든다.
            seen = {(r["date"], r["hour"]): r["use_cnt"] for r in rows}
            dates = sorted({r["date"] for r in rows})
            for date in dates:
                for hour in HOURS:
                    writer.writerow({
                        "route_id": route["route_id"],
                        "route_no": route["route_no"],
                        "stg_arr_nma": route["stg_arr_nma"],
                        "date": date, "hour": hour,
                        "use_cnt": seen.get((date, hour), 0),
                    })
            out.flush()
            got += 1
            if i % 25 == 0 or i == len(routes):
                print("  %d/%d  최근: %s번" % (i, len(routes), route["route_no"]))
    return got, failed


def remaining(routes, path):
    """CSV 에 아직 없는 노선을 돌려준다. 이어받기의 근거다."""
    done = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig", newline="") as f:
            done = {row["route_id"] for row in csv.DictReader(f)}
    return [r for r in routes if r["route_id"] not in done]


def stage_data(cookie, periods):
    if not os.path.exists(ROUTE_IDS_CSV):
        print("[오류] %s 가 없습니다." % ROUTE_IDS_CSV)
        print("       python src/fetch_route_ridership.py routes 를 먼저 실행하십시오.")
        return 1
    with open(ROUTE_IDS_CSV, encoding="utf-8-sig", newline="") as f:
        routes = list(csv.DictReader(f))
    print("대상 노선 %d개 / 기간 %d개" % (len(routes), len(periods)))

    os.makedirs(OUT_DIR, exist_ok=True)
    pacer = Pacer(MIN_GAP)

    for from_day, to_day in periods:
        path = os.path.join(OUT_DIR, "route_%s_%s.csv"
                            % (from_day.replace("-", ""), to_day.replace("-", "")))
        for pass_no in range(1, MAX_PASSES + 1):
            todo = remaining(routes, path)
            print()
            if not todo:
                if pass_no == 1:
                    print("[%s ~ %s] 받을 것이 없습니다." % (from_day, to_day))
                break
            print("[%s ~ %s] %d바퀴째 / 남은 노선 %d개"
                  % (from_day, to_day, pass_no, len(todo)))
            if pass_no > 1:
                # 앞 바퀴에서 막혔다는 뜻이다. 풀릴 시간을 준다.
                nap = COOLDOWNS[min(pass_no - 2, len(COOLDOWNS) - 1)]
                print("  %d초 쉬었다 시작합니다." % nap)
                time.sleep(nap)

            result = fetch_period(todo, path, from_day, to_day, cookie, pacer)
            if result is None:
                return 1
            got, failed = result
            print("  %d바퀴째 결과: %d개 받음, %d개 실패" % (pass_no, got, failed))
            if not failed:
                break

        print("  저장: %s" % path)
        left = remaining(routes, path)
        if left:
            print("  [주의] %d개 노선을 못 받았습니다: %s"
                  % (len(left), ", ".join(r["route_no"] for r in left[:10])))
            print("         같은 명령을 다시 실행하면 이것만 이어서 받습니다.")
    return 0


def main():
    stage = sys.argv[1] if len(sys.argv) > 1 else ""
    if stage not in ("routes", "data"):
        print(__doc__)
        return 1

    cookie = load_cookie()
    if cookie is None:
        return 1
    os.makedirs(LOGS_DIR, exist_ok=True)

    if stage == "routes":
        try:
            return stage_routes(cookie)
        except SessionExpired as e:
            print("[중단] 세션이 만료됐습니다 (%s)." % e)
            return 1
        except IOError as e:
            # 첫 검색 요청은 감싸는 곳이 없어서, 여기서 안 받으면
            # 안내 문구 대신 트레이스백이 그대로 튀어나온다.
            print("[중단] STCIS 에 닿지 못했습니다 (%s)." % e)
            print("       잠시 뒤 다시 실행하십시오. 그래도 안 되면")
            print("       .env 의 STCIS_COOKIE 가 살아 있는지 확인하십시오.")
            return 1

    if len(sys.argv) == 4:
        periods = [(sys.argv[2], sys.argv[3])]
    elif len(sys.argv) == 2:
        periods = PERIODS
    else:
        print("[오류] 기간은 시작일과 종료일을 함께 주십시오.")
        return 1
    return stage_data(cookie, periods)


if __name__ == "__main__":
    sys.exit(main())
