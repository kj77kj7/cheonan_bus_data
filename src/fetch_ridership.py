"""STCIS 정류장별 이용량을 수집한다.

교통카드 승하차는 노선·정류장 지표 화면에서만 나오고, 화면이 한 번에
정류장 하나씩만 조회한다. 손으로는 감당이 안 되므로 두 단계로 자동화한다.

    1단계 (stops)  정류장명 -> sttnListAjax.do -> 내부 sttnId
    2단계 (data)   sttnId  -> indicatorAjax.do -> 일자·시간대별 승하차

화면에 ARS번호가 '~' 로만 보여서 우리 nodeid 와 이을 수가 없는데, 검색
응답의 체크박스 value 에 조회에 필요한 값이 파이프로 묶여 들어 있다.

    03|MM10144000|2921386|44131|방죽안오거리|~|1
    │  │          │       │
    │  │          │       └ sttnSggCd
    │  │          └──────── sttnId
    │  └─────────────────── excclcAreaCdSttn
    └────────────────────── tcboIdSttn

공개 API 가 아니라 로그인 세션으로 도는 화면이므로, 쿠키를 .env 에서
읽고 만료되면 즉시 멈춘다. 밤새 돌다가 빈 파일만 쌓이는 것을 막는다.

사용법
    python src/fetch_ridership.py stops                        # 1단계
    python src/fetch_ridership.py data                         # 2단계, 기본 기간 전체
    python src/fetch_ridership.py data 2026-08-01 2026-08-14   # 특정 기간만
"""

import csv
import os
import re
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from collect_realtime import Pacer
from config import DATA_DIR, ENV_PATH, LOGS_DIR, load_env

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "https://stcis.go.kr"
SEARCH_URL = BASE + "/pivotIndi/sttnListAjax.do"
INDICATOR_URL = BASE + "/pivotIndi/indicatorAjax.do"
REFERER = BASE + "/pivotIndi/wpsPivotIndicator.do?siteGb=P&indiClss=IC03&indiSel=IC0308"

INDI_CD = "Z01723"
INDI_NM = "노선·정류장 지표(정류장별 이용량)"
ZONE_SD = "44"                       # 충청남도
ZONE_SGG = "44130_44131_44133"       # 천안시 (동남구·서북구)

NAMES_TXT = os.path.join(DATA_DIR, "stop_names.txt")
STOP_IDS_CSV = os.path.join(DATA_DIR, "stcis_stop_ids.csv")
OUT_DIR = os.path.join(DATA_DIR, "ridership")

STOP_ID_COLUMNS = ["query_nm", "sttn_id", "tcbo_id", "excclc_area_cd",
                   "sgg_cd", "sttn_nm", "ars", "sd", "sgg", "emd"]

# 표는 04시부터 시작해 자정을 넘어 03시로 끝난다. 시간당 승차·하차 두 칸.
HOURS = ["%02d" % h for h in range(4, 24)] + ["00", "01", "02", "03"]
VALUES_PER_ROW = len(HOURS) * 2

# 해마다 같은 달을 골라 계절과 학사일정을 맞춘다. 마지막 구간은 실시간
# 수집분과 겹치도록 잡았다.
#
# 2023 년은 뺐다. STCIS 에 그 시기 데이터가 아예 없다 — 2023-05·09·11·12
# 와 2024-01·02·03 이 전부 0건인데 2024-05 는 같은 파라미터로 260건이
# 나왔다. 요청이 잘못된 게 아니라 데이터가 없는 것이다. 시작은 2024-03-14
# 이후 ~ 2024-05-06 이전 어딘가다. 노선 개편 시행일(2024-01-27)보다 늦어
# STCIS 승하차만으로는 개편 전후 비교가 성립하지 않는다.
PERIODS = [
    ("2024-05-06", "2024-05-19"),
    ("2024-09-02", "2024-09-15"),
    ("2025-05-12", "2025-05-25"),
    ("2025-09-01", "2025-09-14"),
    ("2026-05-11", "2026-05-24"),
    ("2026-08-01", "2026-08-14"),
]

TIMEOUT = 40
MAX_ATTEMPTS = 3
MIN_GAP = 2.0        # 요청 시작 사이 최소 간격(초). 공공 시스템이라 넉넉히 둔다.

# STCIS 는 요청이 몰리면 TCP 핸드셰이크 자체를 끊는다. HTTP 429 가 아니라
# 연결이 안 되는 것이라, 애플리케이션에서는 WinError 10060 으로 보인다.
# 실측: 6분쯤 막혔다가 저절로 풀렸고, 90초를 쉰 뒤에는 곧바로 응답했다.
# 막힌 동안 계속 두드려 봐야 시간만 버리므로, 연속 실패가 쌓이면 물러선다.
BLOCK_STREAK = 3               # 이만큼 연속 실패하면 막힌 것으로 본다
COOLDOWNS = [120, 300, 600]    # 물러서는 시간(초). 반복될수록 길게 쉰다.

CHECKBOX_RE = re.compile(r'name="chkSttn"\s+value="([^"]*)"')
ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.S)
CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
TAG_RE = re.compile(r"<[^>]+>")
TOTAL_RE = re.compile(r"총\s*:\s*(\d+)\s*건")
PAGE_RE = re.compile(r'<li[^>]*><a href="#">(\d+)</a></li>')
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\([월화수목금토일]\)")
DATA_ROW_RE = re.compile(r"id='firstTr'(.*?)</tr>", re.S)
DATA_CELL_RE = re.compile(r"<td class='td_right'><div>(-?\d+)</div></td>")


def text_of(html):
    return TAG_RE.sub("", html).replace("&nbsp;", " ").strip()


def load_cookie():
    cookie = load_env(ENV_PATH).get("STCIS_COOKIE", "").strip()
    if not cookie or cookie.startswith("여기에"):
        print("[오류] .env 에 STCIS_COOKIE 가 없습니다.")
        print("       STCIS 에 로그인한 뒤 개발자도구 Network 에서 요청을")
        print("       'Copy as cURL' 해 -b 뒤의 쿠키 문자열을 넣으십시오.")
        return None
    return cookie


class SessionExpired(Exception):
    pass


def post(url, params, cookie, pacer, attempts=MAX_ATTEMPTS):
    body = urlencode(params, encoding="utf-8").encode("utf-8")
    last = None
    for attempt in range(1, attempts + 1):
        pacer.wait()
        request = Request(url, data=body, method="POST")
        request.add_header("Content-Type",
                           "application/x-www-form-urlencoded; charset=UTF-8")
        request.add_header("X-Requested-With", "XMLHttpRequest")
        request.add_header("Origin", BASE)
        request.add_header("Referer", REFERER)
        request.add_header("Accept", "*/*")
        request.add_header("User-Agent",
                           "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
        request.add_header("Cookie", cookie)
        try:
            with urlopen(request, timeout=TIMEOUT) as response:
                raw = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                return raw.decode(charset, errors="replace")
        except HTTPError as e:
            if e.code in (401, 403):
                raise SessionExpired("HTTP %s" % e.code)
            last = e
        # URLError 만 잡으면 안 된다. 연결은 됐는데 본문이 안 오는 경우
        # response.read() 가 socket.timeout 을 그대로 올리고, 그것은
        # URLError 가 아니라서 재시도 없이 통과해 버린다. STCIS 가 느려질
        # 때 실제로 이쪽으로 터졌다. 둘의 공통 조상인 OSError 로 받는다.
        except OSError as e:
            last = e
        if attempt < attempts:
            time.sleep(2.0 * attempt)
    raise IOError("요청 실패: %s" % last)


def parse_stop_list(html):
    """검색 응답에서 정류장 목록을 뽑는다. (건수, 페이지수, 정류장들)"""
    if "chkSttn" not in html and "총" not in html:
        raise SessionExpired("검색 응답이 로그인 화면으로 보입니다")

    total_match = TOTAL_RE.search(html)
    total = int(total_match.group(1)) if total_match else 0
    pages = max([int(p) for p in PAGE_RE.findall(html)] or [1])

    stops = []
    for row_html in ROW_RE.findall(html):
        checkbox = CHECKBOX_RE.search(row_html)
        if not checkbox:
            continue
        parts = checkbox.group(1).split("|")
        if len(parts) < 6:
            continue
        cells = [text_of(c) for c in CELL_RE.findall(row_html)]
        # 셀 구성: 체크박스, 구분, 시도, 시군구, 읍면동, ARS, 정류장명
        stops.append({
            "tcbo_id": parts[0],
            "excclc_area_cd": parts[1],
            "sttn_id": parts[2],
            "sgg_cd": parts[3],
            "sttn_nm": parts[4],
            "ars": parts[5],
            "sd": cells[2] if len(cells) > 2 else "",
            "sgg": cells[3] if len(cells) > 3 else "",
            "emd": cells[4] if len(cells) > 4 else "",
        })
    return total, pages, stops


def parse_indicator(html):
    """조회 응답에서 (일자, 값 48개) 목록을 뽑는다.

    왼쪽 표에 일자가, 오른쪽 표에 값이 같은 순서로 들어 있어 위치로 맞춘다.
    """
    if "td_right" not in html:
        raise SessionExpired("조회 응답에 데이터 표가 없습니다")

    dates = DATE_RE.findall(html)
    rows = []
    for row_html in DATA_ROW_RE.findall(html):
        values = [int(v) for v in DATA_CELL_RE.findall(row_html)]
        if values:
            rows.append(values)

    if len(dates) != len(rows):
        raise ValueError("일자 %d개와 값 행 %d개가 맞지 않습니다"
                         % (len(dates), len(rows)))
    for values in rows:
        if len(values) != VALUES_PER_ROW:
            raise ValueError("한 행의 값이 %d개입니다 (%d개여야 함)"
                             % (len(values), VALUES_PER_ROW))
    return list(zip(dates, rows))


def search_params(name, from_day, to_day):
    return {
        "searchDateGubun": "3",
        "searchFromMonth": from_day[:7],
        "searchFromDay": from_day,
        "searchPopSttnZoneSd": ZONE_SD,
        "searchPopSttnZoneSgg": ZONE_SGG,
        "searchPopSttnZoneEmd": "",
        "popupSearchSttnNma": name,
        "popupSearchSttnArsno": "",
        "searchFromYear": from_day[:4],
        "searchToYear": to_day[:4],
        "searchToMonth": to_day[:7],
        "searchToDay": to_day,
        "indiCd": INDI_CD,
    }


def indicator_params(stop, from_day, to_day):
    return {
        "indiCd": INDI_CD, "siteGb": "P", "indiNm": INDI_NM,
        "searchDateGubun": "3",
        "searchFromYear": from_day[:4], "searchToYear": to_day[:4],
        "searchFromMonth": from_day[:7], "searchToMonth": to_day[:7],
        "searchFromDay": from_day,
        "searchFromDayDD": from_day.replace("-", ""),
        "searchToDay": to_day,
        "zoneSd": "", "zoneSgg": "", "zoneEmd": "", "zoneDstrct": "",
        "selectZoneSd": "", "selectZoneSgg": "",
        "tcboId": "", "excclcAreaCd": "",
        "routeId": "", "routeSdCd": "", "routeSggCd": "",
        "tcboIdSttn": stop["tcbo_id"],
        "excclcAreaCdSttn": stop["excclc_area_cd"],
        "sttnId": stop["sttn_id"], "sttnIdGrp": "",
        "sttnSdCd": "", "sttnSggCd": stop["sgg_cd"],
        "searchODAreaGubun": "", "searchODAreaGubun_2": "",
        "rdStgptSel": "Y",
        "searchStgptZoneSd": "", "searchStgptZoneSgg": "", "searchStgptZoneEmd": "",
        "rdAlocSel": "Y",
        "searchAlocZoneSd": "", "searchAlocZoneSgg": "", "searchAlocZoneEmd": "",
        "pgngYn": "N",
        "daybyTblNm": "DM_STTNBY_USECNT_001",
        "mnbyTblNm": "DM_MMBY_STTNBY_USECNT_001",
        "yrbyTblNm": "", "dstrctTblNm": "",
        "mnbyDstrctTblNm": "", "yrbyDstrctTblNm": "",
    }


def data_columns():
    cols = ["sttn_id", "sttn_nm", "sgg", "emd", "ars", "date"]
    for hour in HOURS:
        cols.append("board_%s" % hour)
        cols.append("alight_%s" % hour)
    return cols


def stage_stops(cookie):
    if not os.path.exists(NAMES_TXT):
        print("[오류] %s 가 없습니다." % NAMES_TXT)
        print("       python src/export_stop_list.py 를 먼저 실행하십시오.")
        return 1
    with open(NAMES_TXT, encoding="utf-8") as f:
        names = [line.strip() for line in f if line.strip()]

    done = set()
    if os.path.exists(STOP_IDS_CSV):
        with open(STOP_IDS_CSV, encoding="utf-8-sig", newline="") as f:
            done = {row["query_nm"] for row in csv.DictReader(f)}
        print("이미 받은 정류소명 %d개는 건너뜁니다." % len(done))

    todo = [n for n in names if n not in done]
    if not todo:
        print("받을 것이 없습니다.")
        return 0

    pacer = Pacer(MIN_GAP)
    multipage = []
    written = 0
    is_new = not os.path.exists(STOP_IDS_CSV)
    with open(STOP_IDS_CSV, "a", encoding="utf-8-sig", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=STOP_ID_COLUMNS,
                                extrasaction="ignore")
        if is_new:
            writer.writeheader()
        for i, name in enumerate(todo, 1):
            try:
                html = post(SEARCH_URL, search_params(name, *PERIODS[-1]),
                            cookie, pacer)
                total, pages, stops = parse_stop_list(html)
            except SessionExpired as e:
                print()
                print("[중단] 세션이 만료됐습니다 (%s)." % e)
                print("       .env 의 STCIS_COOKIE 를 갱신하고 다시 실행하십시오.")
                print("       여기까지 받은 %d개는 저장돼 있습니다." % written)
                return 1
            except (IOError, ValueError) as e:
                print("  [실패] %s: %s" % (name, e))
                continue

            if pages > 1:
                multipage.append(name)
            for stop in stops:
                stop["query_nm"] = name
                writer.writerow(stop)
                written += 1
            out.flush()
            if i % 25 == 0 or i == len(todo):
                print("  %d/%d  최근: %s (%d건)" % (i, len(todo), name, total))

    print()
    print("정류장 %d건을 %s 에 저장했습니다." % (written, STOP_IDS_CSV))
    if multipage:
        print("[주의] 검색 결과가 2쪽 이상인 정류소명 %d개 — 일부만 받았습니다:"
              % len(multipage))
        print("       %s" % ", ".join(multipage[:10]))
    return 0


def stage_data(cookie, periods):
    if not os.path.exists(STOP_IDS_CSV):
        print("[오류] %s 가 없습니다." % STOP_IDS_CSV)
        print("       python src/fetch_ridership.py stops 를 먼저 실행하십시오.")
        return 1
    with open(STOP_IDS_CSV, encoding="utf-8-sig", newline="") as f:
        stops = list(csv.DictReader(f))
    # 같은 sttnId 가 여러 정류소명 검색에 걸릴 수 있다.
    unique = {}
    for stop in stops:
        unique.setdefault(stop["sttn_id"], stop)
    stops = list(unique.values())
    print("대상 정류장 %d개 / 기간 %d개" % (len(stops), len(periods)))

    os.makedirs(OUT_DIR, exist_ok=True)
    columns = data_columns()
    pacer = Pacer(MIN_GAP)

    for from_day, to_day in periods:
        path = os.path.join(OUT_DIR, "stop_%s_%s.csv"
                            % (from_day.replace("-", ""), to_day.replace("-", "")))
        done = set()
        if os.path.exists(path):
            with open(path, encoding="utf-8-sig", newline="") as f:
                done = {row["sttn_id"] for row in csv.DictReader(f)}
        todo = [s for s in stops if s["sttn_id"] not in done]
        print()
        print("[%s ~ %s] 남은 정류장 %d개" % (from_day, to_day, len(todo)))
        if not todo:
            continue

        is_new = not os.path.exists(path)
        with open(path, "a", encoding="utf-8-sig", newline="") as out:
            writer = csv.DictWriter(out, fieldnames=columns, extrasaction="ignore")
            if is_new:
                writer.writeheader()
            for i, stop in enumerate(todo, 1):
                try:
                    html = post(INDICATOR_URL,
                                indicator_params(stop, from_day, to_day),
                                cookie, pacer)
                    rows = parse_indicator(html)
                except SessionExpired as e:
                    print()
                    print("[중단] 세션이 만료됐습니다 (%s)." % e)
                    print("       .env 의 STCIS_COOKIE 를 갱신하고 다시 실행하면")
                    print("       받은 지점부터 이어서 받습니다.")
                    return 1
                except (IOError, ValueError) as e:
                    print("  [실패] %s(%s): %s"
                          % (stop["sttn_nm"], stop["sttn_id"], e))
                    continue

                for date, values in rows:
                    row = {"sttn_id": stop["sttn_id"], "sttn_nm": stop["sttn_nm"],
                           "sgg": stop["sgg"], "emd": stop["emd"],
                           "ars": stop["ars"], "date": date}
                    for j, hour in enumerate(HOURS):
                        row["board_%s" % hour] = values[j * 2]
                        row["alight_%s" % hour] = values[j * 2 + 1]
                    writer.writerow(row)
                out.flush()
                if i % 25 == 0 or i == len(todo):
                    print("  %d/%d  최근: %s" % (i, len(todo), stop["sttn_nm"]))
        print("  저장: %s" % path)
    return 0


def main():
    stage = sys.argv[1] if len(sys.argv) > 1 else ""
    if stage not in ("stops", "data"):
        print(__doc__)
        return 1

    cookie = load_cookie()
    if cookie is None:
        return 1
    os.makedirs(LOGS_DIR, exist_ok=True)

    if stage == "stops":
        return stage_stops(cookie)

    if len(sys.argv) == 4:
        periods = [(sys.argv[2], sys.argv[3])]
    elif len(sys.argv) == 2:
        periods = PERIODS
    else:
        print("[오류] 기간은 시작일과 종료일을 함께 주십시오.")
        print("       python src/fetch_ridership.py data 2026-08-01 2026-08-14")
        return 1
    return stage_data(cookie, periods)


if __name__ == "__main__":
    sys.exit(main())
