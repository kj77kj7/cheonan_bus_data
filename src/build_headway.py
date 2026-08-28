"""정류장 통과 사건에서 실측 배차간격과 번칭을 산출한다.

천안시가 공표하는 배차는 계획값이고, 시민이 겪는 것은 실제값이다. 그
간극을 잰 자료가 지금 없다. 3주간 60초 간격으로 관측한 덕에 여기서 처음
나온다.

배차간격은 **같은 노선·같은 정류장을 연속으로 지나간 두 운행의 시간차**다.
같은 차량의 다음 회차가 아니라 뒤따라오는 다른 운행이어야 하므로, 운행
단위(trip_key)로 접은 뒤 정류장별로 다시 늘어놓는다.

번칭(bunching)은 배차가 공표값의 30% 미만으로 붙은 사건이다. 두 대가 붙어
오고 다음이 한참 안 오는 현상이라, 평균 배차가 지켜져도 체감은 나빠진다.
'배차 10분' 이라 안내하면서 실제로는 20분을 기다리게 만드는 주범이다.

산출물
    data/processed/headway.csv           배차 사건 1건 = 1행
    data/processed/headway_by_route.csv  노선별 공표 대비 실측 요약

사용법
    python src/build_headway.py
"""

import csv
import os
import sys
from datetime import datetime

from config import DATA_DIR

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

EVENTS_CSV = os.path.join(DATA_DIR, "interim", "stop_events.csv")
DETAIL_CSV = os.path.join(DATA_DIR, "cheonan_routes_detail.csv")
OUT_DIR = os.path.join(DATA_DIR, "processed")
HEADWAY_CSV = os.path.join(OUT_DIR, "headway.csv")
BY_ROUTE_CSV = os.path.join(OUT_DIR, "headway_by_route.csv")

HEADWAY_COLUMNS = ["routeid", "routeno", "nodeord", "nodeid", "nodenm",
                   "prev_ts", "pass_ts", "headway_sec", "date", "dow", "hour",
                   "official_min", "ratio", "is_bunching"]
BY_ROUTE_COLUMNS = ["routeid", "routeno", "official_min", "n_headway",
                    "median_min", "p90_min", "iqr_min", "cv",
                    "bunching_rate", "long_wait_rate", "gap_vs_official"]

# 이 밖은 배차로 보지 않는다. 너무 짧으면 같은 운행이 두 번 잡힌 것이고,
# 너무 길면 운행이 끊긴 구간(점심 공백·막차 이후)이다.
MIN_HEADWAY_SEC = 60
MAX_HEADWAY_SEC = 3 * 3600

BUNCHING_RATIO = 0.30      # 공표값의 30% 미만이면 붙어 온 것으로 본다
LONG_WAIT_RATIO = 2.0      # 공표값의 2배를 넘으면 하염없이 기다린 것

DOW = ["월", "화", "수", "목", "금", "토", "일"]


def percentile(values, q):
    """정렬된 값에서 q 분위(0~1). 표본이 적어 보간 없이 가까운 쪽을 쓴다."""
    if not values:
        return None
    index = int(round(q * (len(values) - 1)))
    return values[index]


def load_official():
    """공표 배차간격(분). 평일 값을 쓴다."""
    official = {}
    if not os.path.exists(DETAIL_CSV):
        return official
    with open(DETAIL_CSV, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            text = (row.get("intervaltime") or "").strip()
            digits = "".join(c for c in text if c.isdigit())
            if digits:
                official[(row.get("routeid") or "").strip()] = int(digits)
    return official


def load_events():
    """정류장별로 통과 사건을 모은다. 운행마다 한 번씩만 센다."""
    stops = {}
    seen = set()
    with open(EVENTS_CSV, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            try:
                ts = datetime.fromisoformat(row["pass_ts"])
                nodeord = int(row["nodeord"])
            except (KeyError, ValueError):
                continue
            # 한 운행이 같은 정류장을 두 번 지나면(순환노선) 앞의 것만 쓴다.
            trip_stop = (row["trip_key"], nodeord)
            if trip_stop in seen:
                continue
            seen.add(trip_stop)
            key = (row["routeid"], nodeord)
            stops.setdefault(key, []).append({
                "ts": ts, "routeno": row["routeno"],
                "nodeid": row["nodeid"], "nodenm": row["nodenm"],
            })
    return stops


def main():
    if not os.path.exists(EVENTS_CSV):
        print("[오류] %s 가 없습니다." % EVENTS_CSV)
        print("       python src/build_stop_events.py 를 먼저 실행하십시오.")
        return 1

    official = load_official()
    if not official:
        print("[주의] %s 를 못 읽어 공표 배차 비교를 건너뜁니다." % DETAIL_CSV)

    stops = load_events()
    os.makedirs(OUT_DIR, exist_ok=True)

    by_route = {}
    written = dropped_short = dropped_long = 0

    with open(HEADWAY_CSV, "w", encoding="utf-8-sig", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=HEADWAY_COLUMNS,
                                extrasaction="ignore")
        writer.writeheader()

        for (routeid, nodeord), passes in sorted(stops.items()):
            passes.sort(key=lambda p: p["ts"])
            official_min = official.get(routeid)
            for prev, cur in zip(passes, passes[1:]):
                gap = (cur["ts"] - prev["ts"]).total_seconds()
                if gap < MIN_HEADWAY_SEC:
                    dropped_short += 1
                    continue
                if gap > MAX_HEADWAY_SEC:
                    dropped_long += 1
                    continue

                ratio = (gap / 60.0 / official_min) if official_min else ""
                bunching = 1 if (ratio != "" and ratio < BUNCHING_RATIO) else 0
                writer.writerow({
                    "routeid": routeid, "routeno": cur["routeno"],
                    "nodeord": nodeord, "nodeid": cur["nodeid"],
                    "nodenm": cur["nodenm"],
                    "prev_ts": prev["ts"].isoformat(timespec="seconds"),
                    "pass_ts": cur["ts"].isoformat(timespec="seconds"),
                    "headway_sec": int(gap),
                    "date": cur["ts"].date().isoformat(),
                    "dow": DOW[cur["ts"].weekday()],
                    "hour": "%02d" % cur["ts"].hour,
                    "official_min": official_min if official_min else "",
                    "ratio": "%.3f" % ratio if ratio != "" else "",
                    "is_bunching": bunching,
                })
                written += 1

                summary = by_route.setdefault(routeid, {
                    "routeid": routeid, "routeno": cur["routeno"],
                    "official_min": official_min if official_min else "",
                    "gaps": [], "bunching": 0, "long_wait": 0,
                })
                summary["gaps"].append(gap)
                summary["bunching"] += bunching
                if ratio != "" and ratio > LONG_WAIT_RATIO:
                    summary["long_wait"] += 1

    rows = []
    for summary in by_route.values():
        gaps = sorted(summary["gaps"])
        n = len(gaps)
        median = percentile(gaps, 0.5)
        q1, q3 = percentile(gaps, 0.25), percentile(gaps, 0.75)
        mean = sum(gaps) / n
        var = sum((g - mean) ** 2 for g in gaps) / n
        official_min = summary["official_min"]
        rows.append({
            "routeid": summary["routeid"], "routeno": summary["routeno"],
            "official_min": official_min,
            "n_headway": n,
            "median_min": round(median / 60.0, 1),
            "p90_min": round(percentile(gaps, 0.9) / 60.0, 1),
            "iqr_min": round((q3 - q1) / 60.0, 1),
            "cv": round(var ** 0.5 / mean, 2) if mean else "",
            "bunching_rate": round(100.0 * summary["bunching"] / n, 1),
            "long_wait_rate": round(100.0 * summary["long_wait"] / n, 1),
            "gap_vs_official": (round(median / 60.0 - official_min, 1)
                                if official_min else ""),
        })
    rows.sort(key=lambda r: -r["bunching_rate"])

    with open(BY_ROUTE_CSV, "w", encoding="utf-8-sig", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=BY_ROUTE_COLUMNS,
                                extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print("배차 사건 %d건 / 노선 %d개" % (written, len(rows)))
    print("(%d초 미만 %d건, %d시간 초과 %d건 제외)"
          % (MIN_HEADWAY_SEC, dropped_short, MAX_HEADWAY_SEC // 3600, dropped_long))
    print()

    with_official = [r for r in rows if r["official_min"] != ""]
    if with_official:
        worse = [r for r in with_official if r["gap_vs_official"] > 0]
        print("공표 배차가 있는 노선 %d개 중 실측 중앙값이 더 긴 노선 %d개 (%.0f%%)"
              % (len(with_official), len(worse),
                 100.0 * len(worse) / len(with_official)))
        print()
        print("번칭이 잦은 노선 10개")
        print("  %-6s %6s %8s %8s %8s %7s"
              % ("노선", "공표", "실측중앙", "P90", "번칭률", "표본"))
        for r in with_official[:10]:
            print("  %-6s %5s분 %7.1f분 %7.1f분 %7.1f%% %7d"
                  % (r["routeno"], r["official_min"], r["median_min"],
                     r["p90_min"], r["bunching_rate"], r["n_headway"]))
    print()
    print("저장: %s" % HEADWAY_CSV)
    print("      %s" % BY_ROUTE_CSV)
    return 0


if __name__ == "__main__":
    sys.exit(main())
