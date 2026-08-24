"""route_stops.csv 에서 천안시 정류장 목록을 뽑아낸다.

STCIS 등 외부 조회 화면은 정류장명이나 ARS번호를 입력해야 조회가 된다.
단계 2 에서 이미 수집해 둔 route_stops.csv 에 그 값이 다 들어 있으므로,
중복을 걷어내고 붙여넣기 좋은 형태로 내보낸다.

천안시 노선은 아산·평택·안성·진천·세종까지 넘어가므로, 수집된 정류장에는
타 시군 정류장이 10% 가량 섞여 있다. nodeid 앞자리가 시군을 구분하는데,
ARS번호는 시군 단위로만 유일해서 시군이 다르면 같은 번호가 다른 정류장을
가리킨다(실측 49건). 시군구를 천안으로 걸어놓고 조회하는 화면에 이걸 섞어
넣으면 엉뚱한 정류장이 잡히므로, 천안과 타 시군을 갈라서 내보낸다.

경유 노선 수가 많은 정류장이 이용량도 많다. 조회를 나눠서 해야 할 때
앞에서부터 받으면 중요한 정류장을 먼저 확보하게 되므로 그 순서로 정렬한다.

산출물
    data/stop_list.csv         정류장 1개 = 1행 (시군 구분, 노선수, 좌표 포함)
    data/stop_names.txt        천안 정류소명만 한 줄씩
    data/stop_ars.txt          천안 ARS번호만 한 줄씩
    data/stop_names_other.txt  타 시군 정류소명 (별도 조회용)

사용법
    python src/export_stop_list.py
"""

import csv
import os
import sys

from config import DATA_DIR

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SOURCE_CSV = os.path.join(DATA_DIR, "route_stops.csv")
LIST_CSV = os.path.join(DATA_DIR, "stop_list.csv")
NAMES_TXT = os.path.join(DATA_DIR, "stop_names.txt")
ARS_TXT = os.path.join(DATA_DIR, "stop_ars.txt")
NAMES_OTHER_TXT = os.path.join(DATA_DIR, "stop_names_other.txt")

COLUMNS = ["nodeid", "nodenm", "nodeno", "region", "gpslati", "gpslong",
           "route_count", "routenos"]

# nodeid 앞 7자리가 시군을 가른다 (천안은 CAB2850). 수집 대상이 천안 노선이라
# 가장 많이 나오는 앞자리가 곧 천안이므로, 상수로 박지 않고 실행 시에 정한다.
PREFIX_LEN = 7


def load_stops(path):
    """nodeid 를 키로 정류장을 모으고, 경유 노선을 함께 쌓는다."""
    stops = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            nodeid = (row.get("nodeid") or "").strip()
            if not nodeid:
                continue
            stop = stops.get(nodeid)
            if stop is None:
                stop = {
                    "nodeid": nodeid,
                    "nodenm": (row.get("nodenm") or "").strip(),
                    "nodeno": (row.get("nodeno") or "").strip(),
                    "gpslati": (row.get("gpslati") or "").strip(),
                    "gpslong": (row.get("gpslong") or "").strip(),
                    "routes": set(),
                }
                stops[nodeid] = stop
            routeno = (row.get("routeno") or "").strip()
            if routeno:
                stop["routes"].add(routeno)
    return stops


def sort_key(stop):
    """노선 수 내림차순, 같으면 정류소명 순."""
    return (-len(stop["routes"]), stop["nodenm"])


def home_prefix(stops):
    """가장 많이 나오는 nodeid 앞자리 = 수집 대상 시군(천안)."""
    counts = {}
    for stop in stops.values():
        key = stop["nodeid"][:PREFIX_LEN]
        counts[key] = counts.get(key, 0) + 1
    return max(counts, key=counts.get)


def unique_names(stops):
    """정류소명 기준 중복 제거. 상·하행 승강장이 갈려도 이름은 하나다."""
    seen = set()
    names = []
    for stop in stops:
        name = stop["nodenm"]
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def write_lines(path, lines):
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def write_outputs(stops):
    ordered = sorted(stops.values(), key=sort_key)
    prefix = home_prefix(stops)
    home = [s for s in ordered if s["nodeid"].startswith(prefix)]
    other = [s for s in ordered if not s["nodeid"].startswith(prefix)]

    with open(LIST_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for stop in ordered:
            routes = sorted(stop["routes"])
            writer.writerow({
                "nodeid": stop["nodeid"],
                "nodenm": stop["nodenm"],
                "nodeno": stop["nodeno"],
                "region": "천안" if stop["nodeid"].startswith(prefix) else "타시군",
                "gpslati": stop["gpslati"],
                "gpslong": stop["gpslong"],
                "route_count": len(routes),
                "routenos": " ".join(routes),
            })

    names = unique_names(home)
    write_lines(NAMES_TXT, names)
    write_lines(NAMES_OTHER_TXT, unique_names(other))

    # ARS 가 비었거나 0 인 정류장이 있다. 그대로 넣으면 조회가 깨지므로 뺀다.
    ars = [s["nodeno"] for s in home if s["nodeno"] not in ("", "0")]
    write_lines(ARS_TXT, ars)

    return {"prefix": prefix, "ordered": ordered, "home": home,
            "other": other, "names": names, "ars": ars}


def main():
    if not os.path.exists(SOURCE_CSV):
        print("[오류] %s 가 없습니다." % SOURCE_CSV)
        print("       python src/fetch_route_stops.py 를 먼저 실행하십시오.")
        return 1

    stops = load_stops(SOURCE_CSV)
    if not stops:
        print("[오류] %s 에서 정류장을 하나도 읽지 못했습니다." % SOURCE_CSV)
        return 1

    out = write_outputs(stops)
    home, other = out["home"], out["other"]
    dropped = len(home) - len(out["ars"])

    print("전체 nodeid        : %5d" % len(out["ordered"]))
    print("  천안 (%s)   : %5d   정류소명 %d개 / ARS %d개"
          % (out["prefix"], len(home), len(out["names"]), len(out["ars"])))
    print("  타 시군          : %5d   정류소명 %d개"
          % (len(other), len(unique_names(other))))
    if dropped:
        print("  ARS 없어 제외      : %5d" % dropped)
    print()
    print("천안에서 경유 노선이 많은 정류장 10곳")
    for stop in home[:10]:
        print("  %-22s ARS %-7s %2d개 노선"
              % (stop["nodenm"], stop["nodeno"] or "-", len(stop["routes"])))
    print()
    print("저장: %s" % LIST_CSV)
    print("      %s  (천안 %d줄)" % (NAMES_TXT, len(out["names"])))
    print("      %s    (천안 %d줄)" % (ARS_TXT, len(out["ars"])))
    print("      %s  (타 시군 %d줄)" % (NAMES_OTHER_TXT, len(unique_names(other))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
