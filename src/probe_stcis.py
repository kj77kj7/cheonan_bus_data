"""STCIS 노선·정류장 지표 응답 형태를 확인한다 (수집기 작성 전 사전탐색).

정류장과 노선이 서로 다른 엔드포인트를 쓴다.

    정류장  sttnListAjax.do    -> indicatorAjax.do        (화면조각 HTML)
    노선    busLineListAjax.do -> indicatorPivotAjax.do   (형태 미확인)

정류장 검색은 이름을 반드시 넣어야 결과가 나오는데, 노선 검색은 캡처에서
popupSearchRouteNo 가 비어 있는 채로 조회됐다. 노선번호 없이 천안 전체
노선이 한 번에 나온다면 1단계가 265회에서 1회로 줄어들므로, 그것부터
확인한다.

로그인 세션이 필요하다. 브라우저에서 STCIS 에 로그인한 뒤 개발자도구
Network 에서 요청 하나를 'Copy as cURL' 해서 -b 뒤의 쿠키 문자열을
.env 에 넣는다.

    STCIS_COOKIE=JSESSIONID=...; WMONID=...

사용법
    python src/probe_stcis.py            # 정류장 (방죽안오거리)
    python src/probe_stcis.py 종합터미널   # 정류장, 다른 이름으로
    python src/probe_stcis.py route      # 노선
"""

import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from config import ENV_PATH, LOGS_DIR, load_env

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "https://stcis.go.kr"
REFERER = BASE + "/pivotIndi/wpsPivotIndicator.do?siteGb=P&indiClss=IC03&indiSel=IC0308"

STTN_SEARCH_URL = BASE + "/pivotIndi/sttnListAjax.do"
STTN_INDICATOR_URL = BASE + "/pivotIndi/indicatorAjax.do"
ROUTE_SEARCH_URL = BASE + "/pivotIndi/busLineListAjax.do"
ROUTE_INDICATOR_URL = BASE + "/pivotIndi/indicatorPivotAjax.do"

ZONE_SD = "44"                       # 충청남도
ZONE_SGG = "44130_44131_44133"       # 천안시 (동남구·서북구)

# 확인용 기본값. 실제 수집에서는 기간을 돌려가며 쓴다.
FROM_DAY = "2026-08-01"
TO_DAY = "2026-08-13"
SAMPLE_STTN_ID = "2921386"           # 방죽안오거리
SAMPLE_ROUTE_ID = "29001501"         # 캡처에서 확인된 노선

TIMEOUT = 40


def load_cookie():
    cookie = load_env(ENV_PATH).get("STCIS_COOKIE", "").strip()
    if not cookie or cookie.startswith("여기에"):
        print("[오류] .env 에 STCIS_COOKIE 가 없습니다.")
        print("       브라우저에서 STCIS 에 로그인한 뒤, 개발자도구 Network 에서")
        print("       요청을 'Copy as cURL' 해 -b 뒤의 쿠키 문자열을 넣으십시오.")
        print()
        print("       STCIS_COOKIE=JSESSIONID=...; WMONID=...")
        return None
    return cookie


def post(url, params, cookie):
    """params 는 (키, 값) 목록. ddOption[] 처럼 같은 키가 여러 번 온다."""
    body = urlencode(params, encoding="utf-8").encode("utf-8")
    request = Request(url, data=body, method="POST")
    request.add_header("Content-Type",
                       "application/x-www-form-urlencoded; charset=UTF-8")
    request.add_header("X-Requested-With", "XMLHttpRequest")
    request.add_header("Origin", BASE)
    request.add_header("Referer", REFERER)
    request.add_header("Accept", "application/json, text/javascript, */*; q=0.01")
    request.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
    request.add_header("Cookie", cookie)
    with urlopen(request, timeout=TIMEOUT) as response:
        raw = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
        return response.status, raw.decode(charset, errors="replace")


def sttn_search_params(name):
    return [
        ("searchDateGubun", "3"),
        ("searchFromMonth", FROM_DAY[:7]), ("searchFromDay", FROM_DAY),
        ("searchPopSttnZoneSd", ZONE_SD), ("searchPopSttnZoneSgg", ZONE_SGG),
        ("searchPopSttnZoneEmd", ""),
        ("popupSearchSttnNma", name), ("popupSearchSttnArsno", ""),
        ("searchFromYear", "2025"), ("searchToYear", "2025"),
        ("searchToMonth", TO_DAY[:7]), ("searchToDay", TO_DAY),
        ("indiCd", "Z01723"),
    ]


def sttn_indicator_params(sttn_id):
    return [
        ("indiCd", "Z01723"), ("siteGb", "P"),
        ("indiNm", "노선·정류장 지표(정류장별 이용량)"),
        ("searchDateGubun", "3"),
        ("searchFromYear", "2025"), ("searchToYear", "2025"),
        ("searchFromMonth", FROM_DAY[:7]), ("searchToMonth", TO_DAY[:7]),
        ("searchFromDay", FROM_DAY),
        ("searchFromDayDD", FROM_DAY.replace("-", "")),
        ("searchToDay", TO_DAY),
        ("zoneSd", ""), ("zoneSgg", ""), ("zoneEmd", ""), ("zoneDstrct", ""),
        ("selectZoneSd", ""), ("selectZoneSgg", ""),
        ("tcboId", ""), ("excclcAreaCd", ""),
        ("routeId", ""), ("routeSdCd", ""), ("routeSggCd", ""),
        ("tcboIdSttn", "03"), ("excclcAreaCdSttn", "MM10144000"),
        ("sttnId", sttn_id), ("sttnIdGrp", ""),
        ("sttnSdCd", ""), ("sttnSggCd", "44131"),
        ("searchODAreaGubun", ""), ("searchODAreaGubun_2", ""),
        ("rdStgptSel", "Y"),
        ("searchStgptZoneSd", ""), ("searchStgptZoneSgg", ""),
        ("searchStgptZoneEmd", ""),
        ("rdAlocSel", "Y"),
        ("searchAlocZoneSd", ""), ("searchAlocZoneSgg", ""),
        ("searchAlocZoneEmd", ""),
        ("pgngYn", "N"),
        ("daybyTblNm", "DM_STTNBY_USECNT_001"),
        ("mnbyTblNm", "DM_MMBY_STTNBY_USECNT_001"),
        ("yrbyTblNm", ""), ("dstrctTblNm", ""),
        ("mnbyDstrctTblNm", ""), ("yrbyDstrctTblNm", ""),
    ]


def route_search_params(route_no=""):
    """route_no 를 비우면 천안 전체 노선이 나오는지 확인한다."""
    return [
        ("searchPopZoneSd", ZONE_SD), ("searchPopZoneSgg", ZONE_SGG),
        ("searchPopZoneEmd", ""),
        ("popupSearchRouteNo", route_no),
        ("searchDateGubun", "3"),
        ("searchFromYear", "2025"), ("searchToYear", "2025"),
        ("searchFromMonth", FROM_DAY[:7]), ("searchToMonth", TO_DAY[:7]),
        ("searchFromDay", FROM_DAY), ("searchToDay", TO_DAY),
        ("indiCd", "Z01722"),
    ]


def route_indicator_params(route_id):
    params = [
        ("indiCd", "Z01722"), ("siteGb", "P"),
        ("searchDateGubun", "3"),
        ("searchFromYear", "2025"), ("searchToYear", "2025"),
        ("searchFromMonth", FROM_DAY[:7]), ("searchToMonth", TO_DAY[:7]),
        ("searchFromDay", FROM_DAY), ("searchToDay", TO_DAY),
        ("zoneSd", ""), ("zoneSgg", ""), ("zoneEmd", ""), ("zoneDstrct", ""),
        ("selectZoneSd", ""), ("selectZoneSgg", ""),
        ("tcboId", "03"), ("excclcAreaCd", ""),
        ("routeId", route_id), ("routeSdCd", "44"), ("routeSggCd", "44"),
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


def probe(label, url, params, cookie):
    print("=" * 64)
    print("[%s] %s" % (label, url))
    try:
        status, text = post(url, params, cookie)
    except HTTPError as e:
        print("  HTTP %s %s" % (e.code, e.reason))
        if e.code in (401, 403):
            print("  세션이 만료된 것 같습니다. 쿠키를 다시 떠서 .env 를 갱신하십시오.")
        return None
    except URLError as e:
        print("  연결 실패: %s" % e.reason)
        return None

    os.makedirs(LOGS_DIR, exist_ok=True)
    path = os.path.join(LOGS_DIR, "stcis_probe_%s.txt" % label)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)

    stripped = text.lstrip()
    shape = "JSON" if stripped.startswith(("{", "[")) else (
        "HTML/XML" if stripped.startswith("<") else "알 수 없음")
    print("  HTTP %s / %s / %d자" % (status, shape, len(text)))
    print("  저장: %s" % path)
    print("  --- 앞 1500자 ---")
    print(text[:1500])
    print()
    return text


def main():
    cookie = load_cookie()
    if cookie is None:
        return 1

    arg = sys.argv[1] if len(sys.argv) > 1 else ""

    if arg == "route":
        print("노선 지표 (indiCd=Z01722)")
        print("노선번호를 비운 채로 천안 전체가 나오는지부터 확인합니다.")
        print()
        probe("route_search", ROUTE_SEARCH_URL, route_search_params(), cookie)
        probe("route_indicator", ROUTE_INDICATOR_URL,
              route_indicator_params(SAMPLE_ROUTE_ID), cookie)
        tail = ("logs/stcis_probe_route_search.txt 와 "
                "logs/stcis_probe_route_indicator.txt")
    else:
        name = arg or "방죽안오거리"
        print("정류장 지표 (indiCd=Z01723) — %s" % name)
        print()
        if probe("search", STTN_SEARCH_URL, sttn_search_params(name), cookie) is None:
            return 1
        probe("indicator", STTN_INDICATOR_URL,
              sttn_indicator_params(SAMPLE_STTN_ID), cookie)
        tail = "logs/stcis_probe_search.txt 와 logs/stcis_probe_indicator.txt"

    print("=" * 64)
    print("%s 를" % tail)
    print("그대로 올려주시면 파서를 붙여 수집기를 만듭니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
