"""STCIS 노선·정류장 지표 응답 형태를 확인한다 (수집기 작성 전 사전탐색).

정류장별 이용량은 두 단계로 나온다.

    1) sttnListAjax.do    정류장명 -> 정류장 목록 (내부 sttnId 를 준다)
    2) indicatorAjax.do   sttnId   -> 일자·시간대별 승하차

화면에는 ARS번호가 '~' 로만 보이지만 내부적으로는 sttnId 로 정류장을
구분한다. 1) 의 응답에서 그 값을 어떻게 꺼내는지 확인해야 수집기를 쓸 수
있으므로, 먼저 원본 응답을 그대로 받아 저장한다.

로그인 세션이 필요하다. 브라우저에서 STCIS 에 로그인한 뒤 개발자도구
Network 에서 요청 하나를 'Copy as cURL' 해서 -b 뒤의 쿠키 문자열을
.env 에 넣는다.

    STCIS_COOKIE=JSESSIONID=...; WMONID=...

사용법
    python src/probe_stcis.py                  # 방죽안오거리로 두 단계 다
    python src/probe_stcis.py 종합터미널        # 다른 정류장으로
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
SEARCH_URL = BASE + "/pivotIndi/sttnListAjax.do"
INDICATOR_URL = BASE + "/pivotIndi/indicatorAjax.do"
REFERER = BASE + "/pivotIndi/wpsPivotIndicator.do?siteGb=P&indiClss=IC03&indiSel=IC0308"

# 정류장별 이용량 지표. 노선별 이용량은 코드가 다르므로 따로 떠야 한다.
INDI_CD = "Z01723"
INDI_NM = "노선·정류장 지표(정류장별 이용량)"

ZONE_SD = "44"                       # 충청남도
ZONE_SGG = "44130_44131_44133"       # 천안시 (동남구·서북구)

# 확인용 기본값. 실제 수집에서는 기간을 돌려가며 쓴다.
FROM_DAY = "2026-08-01"
TO_DAY = "2026-08-11"
SAMPLE_STTN_ID = "2921386"           # 방죽안오거리 (캡처에서 확인된 값)

TIMEOUT = 30


def load_cookie():
    env = load_env(ENV_PATH)
    cookie = env.get("STCIS_COOKIE", "").strip()
    if not cookie or cookie.startswith("여기에"):
        print("[오류] .env 에 STCIS_COOKIE 가 없습니다.")
        print("       브라우저에서 STCIS 에 로그인한 뒤, 개발자도구 Network 에서")
        print("       요청을 'Copy as cURL' 해 -b 뒤의 쿠키 문자열을 넣으십시오.")
        print()
        print("       STCIS_COOKIE=JSESSIONID=...; WMONID=...")
        return None
    return cookie


def post(url, params, cookie):
    body = urlencode(params, encoding="utf-8").encode("utf-8")
    request = Request(url, data=body, method="POST")
    request.add_header("Content-Type",
                       "application/x-www-form-urlencoded; charset=UTF-8")
    request.add_header("X-Requested-With", "XMLHttpRequest")
    request.add_header("Origin", BASE)
    request.add_header("Referer", REFERER)
    request.add_header("Accept", "*/*")
    request.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
    request.add_header("Cookie", cookie)
    with urlopen(request, timeout=TIMEOUT) as response:
        raw = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
        return response.status, raw.decode(charset, errors="replace")


def search_params(name):
    return {
        "searchDateGubun": "3",
        "searchFromMonth": "2026-07",
        "searchFromDay": FROM_DAY,
        "searchPopSttnZoneSd": ZONE_SD,
        "searchPopSttnZoneSgg": ZONE_SGG,
        "searchPopSttnZoneEmd": "",
        "popupSearchSttnNma": name,
        "popupSearchSttnArsno": "",
        "searchFromYear": "2025",
        "searchToYear": "2025",
        "searchToMonth": "2026-07",
        "searchToDay": TO_DAY,
        "indiCd": INDI_CD,
    }


def indicator_params(sttn_id):
    """캡처한 요청을 그대로 옮긴다. 쓰임을 모르는 빈 값도 함께 보낸다."""
    return {
        "indiCd": INDI_CD, "siteGb": "P", "indiNm": INDI_NM,
        "searchDateGubun": "3",
        "searchFromYear": "2025", "searchToYear": "2025",
        "searchFromMonth": "2026-07", "searchToMonth": "2026-07",
        "searchFromDay": FROM_DAY,
        "searchFromDayDD": FROM_DAY.replace("-", ""),
        "searchToDay": TO_DAY,
        "zoneSd": "", "zoneSgg": "", "zoneEmd": "", "zoneDstrct": "",
        "selectZoneSd": "", "selectZoneSgg": "",
        "tcboId": "", "excclcAreaCd": "",
        "routeId": "", "routeSdCd": "", "routeSggCd": "",
        "tcboIdSttn": "03", "excclcAreaCdSttn": "MM10144000",
        "sttnId": sttn_id, "sttnIdGrp": "",
        "sttnSdCd": "", "sttnSggCd": "44131",
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


def probe(label, url, params, cookie):
    print("=" * 60)
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
    if stripped.startswith(("{", "[")):
        shape = "JSON"
    elif stripped.startswith("<"):
        shape = "HTML/XML"
    else:
        shape = "알 수 없음"

    print("  HTTP %s / %s / %d자" % (status, shape, len(text)))
    print("  저장: %s" % path)
    print("  --- 앞 1200자 ---")
    print(text[:1200])
    print()
    return text


def main():
    cookie = load_cookie()
    if cookie is None:
        return 1

    name = sys.argv[1] if len(sys.argv) > 1 else "방죽안오거리"
    print("정류장명: %s" % name)
    print()

    if probe("search", SEARCH_URL, search_params(name), cookie) is None:
        return 1
    probe("indicator", INDICATOR_URL, indicator_params(SAMPLE_STTN_ID), cookie)

    print("=" * 60)
    print("logs/stcis_probe_search.txt 와 logs/stcis_probe_indicator.txt 를")
    print("그대로 올려주시면 파서를 붙여 수집기를 만듭니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
