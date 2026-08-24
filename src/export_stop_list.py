"""route_stops.csv 에서 천안시 정류장 목록을 뽑아낸다.

STCIS 등 외부 조회 화면은 정류장명이나 ARS번호를 입력해야 조회가 된다.
단계 2 에서 이미 수집해 둔 route_stops.csv 에 그 값이 다 들어 있으므로,
중복을 걷어내고 붙여넣기 좋은 형태로 내보낸다.

경유 노선 수가 많은 정류장이 이용량도 많다. 조회를 나눠서 해야 할 때
앞에서부터 받으면 중요한 정류장을 먼저 확보하게 되므로 그 순서로 정렬한다.

산출물
    data/stop_list.csv   정류장 1개 = 1행 (노선수, 좌표 포함)
    data/stop_names.txt  정류소명만 한 줄씩
    data/stop_ars.txt    ARS번호만 한 줄씩

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

COLUMNS = ["nodeid", "nodenm", "nodeno", "gpslati", "gpslong", "route_count", "routenos"]


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


def write_outputs(stops):
    ordered = sorted(stops.values(), key=sort_key)

    with open(LIST_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for stop in ordered:
            routes = sorted(stop["routes"])
            writer.writerow({
                "nodeid": stop["nodeid"],
                "nodenm": stop["nodenm"],
                "nodeno": stop["nodeno"],
                "gpslati": stop["gpslati"],
                "gpslong": stop["gpslong"],
                "route_count": len(routes),
                "routenos": " ".join(routes),
            })

    # 정류소명은 승강장이 나뉘어도 이름이 같으므로 여기서 한 번 더 중복을 없앤다.
    seen = set()
    names = []
    for stop in ordered:
        name = stop["nodenm"]
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    with open(NAMES_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(names) + "\n")

    ars = [s["nodeno"] for s in ordered if s["nodeno"]]
    with open(ARS_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(ars) + "\n")

    return ordered, names, ars


def main():
    if not os.path.exists(SOURCE_CSV):
        print("[오류] %s 가 없습니다." % SOURCE_CSV)
        print("       python src/fetch_route_stops.py 를 먼저 실행하십시오.")
        return 1

    stops = load_stops(SOURCE_CSV)
    if not stops:
        print("[오류] %s 에서 정류장을 하나도 읽지 못했습니다." % SOURCE_CSV)
        return 1

    ordered, names, ars = write_outputs(stops)

    print("고유 nodeid   : %5d" % len(ordered))
    print("고유 정류소명 : %5d" % len(names))
    print("ARS번호 보유  : %5d" % len(ars))
    print()
    print("경유 노선이 많은 정류장 10곳")
    for stop in ordered[:10]:
        print("  %-20s ARS %-8s %2d개 노선  %s"
              % (stop["nodenm"], stop["nodeno"] or "-",
                 len(stop["routes"]), " ".join(sorted(stop["routes"])[:8])))
    print()
    print("저장: %s" % LIST_CSV)
    print("      %s" % NAMES_TXT)
    print("      %s" % ARS_TXT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
