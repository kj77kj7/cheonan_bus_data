"""정류장 통과 사건에서 실측 배차간격과 번칭을 산출한다.

천안시가 공표하는 배차는 계획값이고, 시민이 겪는 것은 실제값이다. 그
간극을 잰 자료가 지금 없다. 3주간 60초 간격으로 관측한 덕에 여기서 처음
나온다.

배차간격은 **같은 노선·같은 정류장을 연속으로 지나간 두 운행의 시간차**다.
같은 차량의 다음 회차가 아니라 뒤따라오는 다른 운행이어야 하므로, 운행
단위(trip_key)로 접은 뒤 정류장별로 다시 늘어놓는다.

번칭(bunching)은 배차가 붙어 버린 사건이다. 두 대가 몰려 오고 다음이 한참
안 오는 현상이라, 평균 배차가 지켜져도 체감은 나빠진다. 두 가지로 잰다.

- 공표 대비: 공표 배차의 30% 미만. 계획과 실제의 괴리를 곧바로 보여준다
- 실측 대비: 그 노선 실측 중앙값의 30% 미만. 공표값이 없거나 못 믿을 때도
  쓸 수 있어 전 노선에 적용된다

공표값을 그대로 믿으면 안 된다. TAGO 의 intervaltime 은 노선마다 의미가
섞여 있어서, 402번은 685분(운행시간대 전체와 같다), 82번은 460분으로
들어와 있다. 배차간격이 아니라 다른 값이다. 이런 노선을 그대로 두면 실측
59분이 전부 번칭으로 찍혀 번칭률 100% 가 나온다.

산출물
    data/processed/headway.csv           배차 사건 1건 = 1행
    data/processed/headway_by_route.csv  노선별 공표 대비 실측 요약

사용법
    python src/build_headway.py
"""

import bisect
import csv
import os
import sys
from datetime import datetime, timedelta

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
                   "official_min", "ratio", "is_bunching",
                   "ratio_obs", "is_bunching_obs", "missed_between"]
BY_ROUTE_COLUMNS = ["routeid", "routeno", "official_min", "official_dropped",
                    "n_headway", "n_excluded", "median_min", "p90_min",
                    "n_day", "median_day_min", "p90_day_min",
                    "n_peak", "median_peak_min", "p90_peak_min",
                    "iqr_min", "cv",
                    "bunching_rate", "bunching_rate_obs", "long_wait_rate",
                    "gap_vs_official"]

# 이 밖은 배차로 보지 않는다. 너무 짧으면 같은 운행이 두 번 잡힌 것이고,
# 너무 길면 운행이 끊긴 구간(점심 공백·막차 이후)이다.
MIN_HEADWAY_SEC = 60
MAX_HEADWAY_SEC = 3 * 3600

BUNCHING_RATIO = 0.30      # 이 비율 미만이면 붙어 온 것으로 본다

# 어떤 운행이 정류장을 통째로 놓치면 그 정류장의 배차 계열에서 그 운행이
# 빠져, 앞뒤 간격이 실제의 두 배로 잡힌다. 놓친 운행은 사건 자체가 없어
# gap_before 로는 안 잡히지만, 그 운행이 앞뒤 정류장은 관측했으므로 통과
# 시각을 보간해 복원할 수 있다. 그 시각이 두 배차 사이에 끼면 오염이다.
EXCLUDE_MISSED = True

# 표본이 두 자릿수인 노선은 순위에 올리지 않는다. 18건짜리 노선이 번칭률
# 1위로 올라오면 표가 오해를 부른다.
MIN_SAMPLE_FOR_RANK = 100
LONG_WAIT_RATIO = 2.0      # 공표값의 2배를 넘으면 하염없이 기다린 것

# 공표 배차가 운행시간대의 1/3 을 넘으면 하루 세 번도 안 다닌다는 뜻이다.
# 시내버스로는 있을 수 없으므로 배차간격이 아닌 다른 값으로 본다.
MAX_OFFICIAL_SHARE = 1.0 / 3
# 첫차·막차를 모르는 노선에 쓸 절대 상한(분).
MAX_OFFICIAL_MIN = 180

# 시간대를 안 가리고 P90 을 내면 첫차 전후와 막차 무렵의 긴 간격이 그대로
# 섞인다. 심야에 40분이 뜨는 것은 정상 운영이지 문제가 아닌데, 그것이
# "열 번에 한 번은 168분"으로 읽히면 과장이다. 시민이 실제로 버스를 쓰는
# 시간대만 따로 잰 값을 나란히 낸다.
DAY_START, DAY_END = 7, 21        # 평시 07:00~20:59
PEAK_HOURS = {7, 8, 17, 18}       # 출퇴근 첨두

DOW = ["월", "화", "수", "목", "금", "토", "일"]


def percentile(values, q):
    """정렬된 값에서 q 분위(0~1). 표본이 적어 보간 없이 가까운 쪽을 쓴다."""
    if not values:
        return None
    return values[int(round(q * (len(values) - 1)))]


def mins(seconds):
    """초를 분으로. 표본이 비면 빈칸."""
    return round(seconds / 60.0, 1) if seconds is not None else ""


def to_minutes(text):
    """'0610' 을 분으로. 못 읽으면 None."""
    digits = "".join(c for c in str(text or "") if c.isdigit())
    if len(digits) != 4:
        return None
    hour, minute = int(digits[:2]), int(digits[2:])
    return hour * 60 + minute if hour < 24 and minute < 60 else None


def load_official():
    """공표 배차간격(분). 배차로 볼 수 없는 값은 걸러낸다.

    돌려주는 것은 {routeid: (분 또는 None, 걸러낸 이유)}.
    """
    official = {}
    if not os.path.exists(DETAIL_CSV):
        return official
    with open(DETAIL_CSV, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            routeid = (row.get("routeid") or "").strip()
            digits = "".join(c for c in (row.get("intervaltime") or "")
                             if c.isdigit())
            if not digits:
                official[routeid] = (None, "값 없음")
                continue
            minutes = int(digits)

            start = to_minutes(row.get("startvehicletime"))
            end = to_minutes(row.get("endvehicletime"))
            span = None
            if start is not None and end is not None:
                span = end - start
                if span < 0:          # 막차가 자정을 넘는 노선
                    span += 24 * 60

            if span and minutes > span * MAX_OFFICIAL_SHARE:
                official[routeid] = (None, "운행시간대 %d분의 1/3 초과" % span)
            elif span is None and minutes > MAX_OFFICIAL_MIN:
                official[routeid] = (None, "%d분 초과" % MAX_OFFICIAL_MIN)
            else:
                official[routeid] = (minutes, "")
    return official


def load_events():
    """정류장별 통과 사건과, 그 정류장을 놓친 운행의 보간 통과시각을 모은다.

    돌려주는 것은 (stops, missed).
      stops  {(routeid, nodeord): [통과 사건]}
      missed {(routeid, nodeord): [정렬된 보간 시각]}

    어떤 운행이 nodeord 5 를 보고 7 을 봤다면 6 은 놓친 것이다. 5 와 7 의
    관측 시각을 순번으로 나눠 6 의 통과 시각을 추정한다. 정류장 간격이
    고르다는 가정이라 정확하진 않지만, 그 시각이 두 배차 사이에 드는지만
    보면 되므로 이 정도로 충분하다.
    """
    trips = {}
    with open(EVENTS_CSV, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            try:
                ts = datetime.fromisoformat(row["pass_ts"])
                nodeord = int(row["nodeord"])
                gap_before = int(row.get("gap_before") or 0)
            except (KeyError, ValueError):
                continue
            trips.setdefault(row["trip_key"], []).append({
                "ts": ts, "nodeord": nodeord, "gap_before": gap_before,
                "routeid": row["routeid"], "routeno": row["routeno"],
                "nodeid": row["nodeid"], "nodenm": row["nodenm"],
            })

    stops = {}
    missed = {}
    for events in trips.values():
        events.sort(key=lambda e: e["nodeord"])
        seen = set()
        for event in events:
            # 한 운행이 같은 정류장을 두 번 지나면(순환노선) 앞의 것만 쓴다.
            if event["nodeord"] in seen:
                continue
            seen.add(event["nodeord"])
            stops.setdefault((event["routeid"], event["nodeord"]), []).append(event)

        for prev, cur in zip(events, events[1:]):
            if cur["gap_before"] <= 0:
                continue
            span_ord = cur["nodeord"] - prev["nodeord"]
            span_sec = (cur["ts"] - prev["ts"]).total_seconds()
            if span_ord <= 1:
                continue
            for missing in range(prev["nodeord"] + 1, cur["nodeord"]):
                share = (missing - prev["nodeord"]) / float(span_ord)
                when = prev["ts"] + timedelta(seconds=span_sec * share)
                missed.setdefault((cur["routeid"], missing), []).append(when)

    for key in missed:
        missed[key].sort()
    return stops, missed


def has_missed_between(times, start, end):
    """start 와 end 사이에 놓친 운행의 통과 시각이 있는가."""
    if not times:
        return False
    index = bisect.bisect_right(times, start)
    return index < len(times) and times[index] < end


def collect_headways(stops, missed):
    """배차 사건을 모은다. 노선별 실측 중앙값을 알아야 번칭을 매길 수 있어
    한 번에 다 모은 뒤 두 번째 바퀴에서 값을 채운다."""
    events = []
    short = long = contaminated = 0
    for (routeid, nodeord), passes in sorted(stops.items()):
        passes.sort(key=lambda p: p["ts"])
        gaps = missed.get((routeid, nodeord), [])
        for prev, cur in zip(passes, passes[1:]):
            gap = (cur["ts"] - prev["ts"]).total_seconds()
            if gap < MIN_HEADWAY_SEC:
                short += 1
                continue
            if gap > MAX_HEADWAY_SEC:
                long += 1
                continue
            dirty = has_missed_between(gaps, prev["ts"], cur["ts"])
            if dirty:
                contaminated += 1
            events.append({
                "routeid": routeid, "routeno": cur["routeno"],
                "nodeord": nodeord, "nodeid": cur["nodeid"],
                "nodenm": cur["nodenm"],
                "prev_ts": prev["ts"], "pass_ts": cur["ts"], "gap": gap,
                "missed": 1 if dirty else 0,
            })
    return events, short, long, contaminated


def main():
    if not os.path.exists(EVENTS_CSV):
        print("[오류] %s 가 없습니다." % EVENTS_CSV)
        print("       python src/build_stop_events.py 를 먼저 실행하십시오.")
        return 1

    official = load_official()
    if not official:
        print("[주의] %s 를 못 읽어 공표 배차 비교를 건너뜁니다." % DETAIL_CSV)

    stops, missed = load_events()
    events, short, long, contaminated = collect_headways(stops, missed)
    if not events:
        print("[오류] 배차 사건이 하나도 없습니다.")
        print("       같은 정류장을 두 번 이상 지난 기록이 있어야 합니다.")
        return 1

    clean = [e for e in events if not e["missed"]] if EXCLUDE_MISSED else events

    # 노선별 실측 중앙값. 공표값을 못 믿는 노선의 번칭 기준이 된다.
    gaps_by_route = {}
    for event in clean:
        gaps_by_route.setdefault(event["routeid"], []).append(event["gap"])
    median_by_route = {rid: percentile(sorted(g), 0.5)
                       for rid, g in gaps_by_route.items()}

    os.makedirs(OUT_DIR, exist_ok=True)
    by_route = {}

    with open(HEADWAY_CSV, "w", encoding="utf-8-sig", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=HEADWAY_COLUMNS,
                                extrasaction="ignore")
        writer.writeheader()
        for event in events:
            routeid = event["routeid"]
            gap = event["gap"]
            official_min, reason = official.get(routeid, (None, "노선 정보 없음"))
            obs_median = median_by_route[routeid]

            ratio = (gap / 60.0 / official_min) if official_min else None
            ratio_obs = gap / obs_median if obs_median else None
            bunching = 1 if (ratio is not None and ratio < BUNCHING_RATIO) else 0
            bunching_obs = (1 if (ratio_obs is not None
                                  and ratio_obs < BUNCHING_RATIO) else 0)

            writer.writerow({
                "routeid": routeid, "routeno": event["routeno"],
                "nodeord": event["nodeord"], "nodeid": event["nodeid"],
                "nodenm": event["nodenm"],
                "prev_ts": event["prev_ts"].isoformat(timespec="seconds"),
                "pass_ts": event["pass_ts"].isoformat(timespec="seconds"),
                "headway_sec": int(gap),
                "date": event["pass_ts"].date().isoformat(),
                "dow": DOW[event["pass_ts"].weekday()],
                "hour": "%02d" % event["pass_ts"].hour,
                "official_min": official_min if official_min else "",
                "ratio": "%.3f" % ratio if ratio is not None else "",
                "is_bunching": bunching if ratio is not None else "",
                "ratio_obs": "%.3f" % ratio_obs if ratio_obs is not None else "",
                "is_bunching_obs": bunching_obs,
                "missed_between": event["missed"],
            })

            summary = by_route.setdefault(routeid, {
                "routeid": routeid, "routeno": event["routeno"],
                "official_min": official_min if official_min else "",
                "official_dropped": reason,
                "gaps": [], "gaps_day": [], "gaps_peak": [],
                "bunching": 0, "bunching_obs": 0, "long_wait": 0,
                "excluded": 0,
            })
            if EXCLUDE_MISSED and event["missed"]:
                summary["excluded"] += 1
                continue
            summary["gaps"].append(gap)
            # 시간대는 뒤 차의 통과 시각으로 매긴다. 앞 차 기준으로 하면
            # 막차 직전에 시작해 다음 날 첫차로 끝나는 간격이 평시로 샌다.
            hour = event["pass_ts"].hour
            if DAY_START <= hour < DAY_END:
                summary["gaps_day"].append(gap)
            if hour in PEAK_HOURS:
                summary["gaps_peak"].append(gap)
            summary["bunching"] += bunching
            summary["bunching_obs"] += bunching_obs
            if ratio is not None and ratio > LONG_WAIT_RATIO:
                summary["long_wait"] += 1

    rows = []
    for summary in by_route.values():
        gaps = sorted(summary["gaps"])
        day = sorted(summary["gaps_day"])
        peak = sorted(summary["gaps_peak"])
        n = len(gaps)
        if not n:
            continue
        median = percentile(gaps, 0.5)
        q1, q3 = percentile(gaps, 0.25), percentile(gaps, 0.75)
        mean = sum(gaps) / n
        var = sum((g - mean) ** 2 for g in gaps) / n
        official_min = summary["official_min"]
        rows.append({
            "routeid": summary["routeid"], "routeno": summary["routeno"],
            "official_min": official_min,
            "official_dropped": summary["official_dropped"],
            "n_headway": n,
            "n_excluded": summary["excluded"],
            "median_min": round(median / 60.0, 1),
            "p90_min": round(percentile(gaps, 0.9) / 60.0, 1),
            "n_day": len(day),
            "median_day_min": mins(percentile(day, 0.5)),
            "p90_day_min": mins(percentile(day, 0.9)),
            "n_peak": len(peak),
            "median_peak_min": mins(percentile(peak, 0.5)),
            "p90_peak_min": mins(percentile(peak, 0.9)),
            "iqr_min": round((q3 - q1) / 60.0, 1),
            "cv": round(var ** 0.5 / mean, 2) if mean else "",
            "bunching_rate": (round(100.0 * summary["bunching"] / n, 1)
                              if official_min else ""),
            "bunching_rate_obs": round(100.0 * summary["bunching_obs"] / n, 1),
            "long_wait_rate": (round(100.0 * summary["long_wait"] / n, 1)
                               if official_min else ""),
            "gap_vs_official": (round(median / 60.0 - official_min, 1)
                                if official_min else ""),
        })
    rows.sort(key=lambda r: -r["bunching_rate_obs"])
    # 표본이 얇은 노선은 CSV 에는 남기되 순위표에서는 뺀다.
    ranked = [r for r in rows if r["n_headway"] >= MIN_SAMPLE_FOR_RANK]
    thin = [r for r in rows if r["n_headway"] < MIN_SAMPLE_FOR_RANK]

    with open(BY_ROUTE_CSV, "w", encoding="utf-8-sig", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=BY_ROUTE_COLUMNS,
                                extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    valid = [r for r in ranked if r["official_min"] != ""]
    dropped = [r for r in ranked if r["official_min"] == ""]

    print("배차 사건 %d건 / 노선 %d개" % (len(events), len(rows)))
    print("(%d초 미만 %d건, %d시간 초과 %d건 제외)"
          % (MIN_HEADWAY_SEC, short, MAX_HEADWAY_SEC // 3600, long))
    print()

    if contaminated:
        share = 100.0 * contaminated / len(events)
        print("관측 실패가 낀 배차 사건 %d건 (%.1f%%)" % (contaminated, share))
        print("  그 사이를 지났을 운행을 못 봐서 간격이 부풀려진 것들이다.")
        if EXCLUDE_MISSED:
            dirty_median = percentile(sorted(e["gap"] for e in events), 0.5)
            clean_median = percentile(sorted(e["gap"] for e in clean), 0.5)
            print("  집계에서 뺐다. 전체 중앙값 %.1f분 -> 정제 후 %.1f분"
                  % (dirty_median / 60.0, clean_median / 60.0))
        print()

    if thin:
        print("표본 %d건 미만이라 순위에서 뺀 노선 %d개: %s"
              % (MIN_SAMPLE_FOR_RANK, len(thin),
                 ", ".join("%s(%d건)" % (r["routeno"], r["n_headway"])
                           for r in thin[:8])))
        print("  (CSV 에는 남아 있다)")
        print()

    if dropped:
        print("공표 배차를 못 믿어 비운 노선 %d개 — 배차간격이 아닌 값이 들어 있다"
              % len(dropped))
        for r in dropped[:8]:
            print("  %-6s 실측 중앙 %5.1f분   사유: %s"
                  % (r["routeno"], r["median_min"], r["official_dropped"]))
        print()

    if valid:
        worse = [r for r in valid if r["gap_vs_official"] > 0]
        print("공표 배차가 성한 노선 %d개 중 실측 중앙값이 더 긴 노선 %d개 (%.0f%%)"
              % (len(valid), len(worse), 100.0 * len(worse) / len(valid)))
        print()
        print("공표 대비 실측이 나쁜 노선 10개")
        print("  %-6s %6s %8s %8s %8s %8s %7s"
              % ("노선", "공표", "실측중앙", "P90", "차이", "번칭률", "표본"))
        for r in sorted(valid, key=lambda x: -x["gap_vs_official"])[:10]:
            print("  %-6s %5s분 %7.1f분 %7.1f분 %+7.1f분 %7.1f%% %7d"
                  % (r["routeno"], r["official_min"], r["median_min"],
                     r["p90_min"], r["gap_vs_official"], r["bunching_rate"],
                     r["n_headway"]))
        print()

    print("실측 중앙값 대비 번칭이 잦은 노선 10개 (공표값과 무관)")
    print("  %-6s %8s %8s %8s %7s" % ("노선", "실측중앙", "P90", "번칭률", "표본"))
    for r in ranked[:10]:
        print("  %-6s %7.1f분 %7.1f분 %7.1f%% %7d"
              % (r["routeno"], r["median_min"], r["p90_min"],
                 r["bunching_rate_obs"], r["n_headway"]))
    print()
    print("저장: %s" % HEADWAY_CSV)
    print("      %s" % BY_ROUTE_CSV)
    return 0


if __name__ == "__main__":
    sys.exit(main())
