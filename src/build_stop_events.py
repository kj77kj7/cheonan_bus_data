"""실시간 버스위치 관측을 '정류장 통과 사건' 으로 바꾼다.

수집한 한 행은 "그 순간 이 버스가 이 정류장에 있(었)다" 는 **상태**다.
분석에 필요한 것은 "이 버스가 이 정류장을 **언제 지나갔는가**" 라는
**사건**이다. 같은 정류장이 연속으로 관측된 묶음을 하나로 접는다.

변환 규칙

1. (routeid, vehicleno) 로 묶고 ts 순으로 정렬한다
2. nodeord 가 같은 값으로 연속되면 한 정류장 체류로 묶고, 통과 시각은
   그 묶음의 **마지막** 관측으로 본다 (출발에 가장 가깝다)
3. 회차가 바뀌면 운행을 나눈다. 안 나누면 같은 차량의 서로 다른 운행이
   한 시계열로 섞여 배차간격이 엉킨다
4. nodeord 가 2 이상 건너뛰면 그 사이는 관측 실패다. 사건은 남기되
   gap_before 에 몇 칸을 건너뛰었는지 적어, 구간 표본에서 뺄 수 있게 한다

산출물
    data/interim/stop_events.csv

사용법
    python src/build_stop_events.py                 # 온전한 날만, core
    python src/build_stop_events.py all             # 모든 날
    python src/build_stop_events.py all network     # 모드 지정
"""

import csv
import os
import sys
from datetime import datetime, timedelta

from config import DATA_DIR

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REALTIME_DIR = os.path.join(DATA_DIR, "realtime")
OUT_DIR = os.path.join(DATA_DIR, "interim")
OUT_CSV = os.path.join(OUT_DIR, "stop_events.csv")

COLUMNS = ["trip_key", "routeid", "routeno", "vehicleno", "trip_seq",
           "nodeord", "nodeid", "nodenm", "pass_ts", "obs_count", "gap_before"]

# PC 종료로 하루가 통째로 또는 크게 빈 날들이 있다. 섞이면 배차·이용 통계가
# 왜곡되므로 기본값은 온전한 날만 쓴다. 근거는 docs/HANDOFF.md 8장.
COMPLETE_DAYS = [
    "20260804", "20260806", "20260807", "20260808", "20260809", "20260810",
    "20260811", "20260812", "20260813", "20260814", "20260816", "20260818",
]

# 회차 판정. nodeord 가 줄었다고 무조건 회차로 보면 GPS 튐 한 번에 운행이
# 쪼개진다. 크게 뒤로 갔거나 노선 앞머리로 돌아온 경우만 회차로 본다.
TURNAROUND_DROP = 3
TURNAROUND_HEAD = 2

# 같은 차량이라도 이만큼 관측이 끊기면 다른 운행으로 본다 (차고지 대기 등).
MAX_TRIP_GAP = timedelta(minutes=60)


def parse_ts(text):
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def load_day(path):
    """한 날짜 파일을 읽어 (routeid, vehicleno) 별로 관측을 모은다.

    수집기가 BOM 을 붙여 쓰므로 utf-8-sig 로 읽어야 한다. utf-8 로 읽으면
    첫 컬럼명이 '\ufeffts' 가 되어 row.get("ts") 가 늘 None 이 되고, 데이터가
    아무리 많아도 한 건도 안 남는다. 오류는 안 나고 결과만 비는 종류라
    알아채기 어렵다.
    """
    trips = {}
    bad = 0
    total = 0
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            total += 1
            ts = parse_ts((row.get("ts") or "").strip())
            vehicle = (row.get("vehicleno") or "").strip()
            ordtext = (row.get("nodeord") or "").strip()
            if ts is None or not vehicle or not ordtext.isdigit():
                bad += 1
                continue
            key = ((row.get("routeid") or "").strip(), vehicle)
            trips.setdefault(key, []).append({
                "ts": ts,
                "nodeord": int(ordtext),
                "nodeid": (row.get("nodeid") or "").strip(),
                "nodenm": (row.get("nodenm") or "").strip(),
                "routeno": (row.get("routeno") or "").strip(),
            })
    # 한 건도 못 건졌으면 컬럼 이름이 어긋난 것이지 데이터가 나쁜 게 아니다.
    # 조용히 빈 결과를 내보내면 뒤 단계까지 헛돌므로 여기서 멈춘다.
    if total and not trips:
        raise ValueError("%d행을 읽었으나 쓸 수 있는 행이 하나도 없습니다. "
                         "컬럼 이름을 확인하십시오 (기대: %s)"
                         % (total, ", ".join(["ts", "vehicleno", "nodeord"])))
    return trips, bad


def is_turnaround(prev_ord, cur_ord):
    if cur_ord >= prev_ord:
        return False
    return (prev_ord - cur_ord) >= TURNAROUND_DROP or cur_ord <= TURNAROUND_HEAD


def build_events(observations):
    """한 (노선, 차량) 의 관측을 사건 목록으로 접는다."""
    observations.sort(key=lambda o: o["ts"])
    events = []
    trip_seq = 1
    group = None      # 지금 묶고 있는 정류장 체류
    prev_ord = None

    def flush(gap):
        if group is not None:
            events.append({
                "trip_seq": trip_seq,
                "nodeord": group["nodeord"],
                "nodeid": group["nodeid"],
                "nodenm": group["nodenm"],
                "routeno": group["routeno"],
                "pass_ts": group["last_ts"],
                "obs_count": group["count"],
                "gap_before": gap,
            })

    pending_gap = 0
    for obs in observations:
        if group is None:
            group = {"nodeord": obs["nodeord"], "nodeid": obs["nodeid"],
                     "nodenm": obs["nodenm"], "routeno": obs["routeno"],
                     "last_ts": obs["ts"], "count": 1}
            prev_ord = obs["nodeord"]
            continue

        if obs["nodeord"] == group["nodeord"]:
            group["last_ts"] = obs["ts"]
            group["count"] += 1
            continue

        long_pause = obs["ts"] - group["last_ts"] > MAX_TRIP_GAP
        turned = is_turnaround(prev_ord, obs["nodeord"])

        flush(pending_gap)
        if turned or long_pause:
            trip_seq += 1
            pending_gap = 0
        else:
            # 2 이상 건너뛰었으면 그 사이 정류장은 관측하지 못한 것이다.
            pending_gap = max(0, obs["nodeord"] - prev_ord - 1)

        group = {"nodeord": obs["nodeord"], "nodeid": obs["nodeid"],
                 "nodenm": obs["nodenm"], "routeno": obs["routeno"],
                 "last_ts": obs["ts"], "count": 1}
        prev_ord = obs["nodeord"]

    flush(pending_gap)
    return events


def main():
    args = sys.argv[1:]
    days_arg = args[0] if args else ""
    mode = args[1] if len(args) > 1 else "core"

    if not os.path.isdir(REALTIME_DIR):
        print("[오류] %s 가 없습니다." % REALTIME_DIR)
        return 1

    available = sorted(f for f in os.listdir(REALTIME_DIR)
                       if f.startswith(mode + "_") and f.endswith(".csv"))
    if days_arg == "all":
        files = available
    else:
        wanted = {"%s_%s.csv" % (mode, d) for d in COMPLETE_DAYS}
        files = [f for f in available if f in wanted]
    if not files:
        print("[오류] %s 모드의 대상 파일이 없습니다. (전체 %d개)"
              % (mode, len(available)))
        return 1

    os.makedirs(OUT_DIR, exist_ok=True)
    totals = {"obs": 0, "events": 0, "bad": 0, "trips": 0, "gapped": 0}
    per_day = []

    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()

        for filename in files:
            day = filename.split("_")[1].split(".")[0]
            try:
                trips, bad = load_day(os.path.join(REALTIME_DIR, filename))
            except ValueError as e:
                print()
                print("[중단] %s: %s" % (filename, e))
                print("       수집기가 쓴 파일이 맞는지 확인하십시오.")
                return 1
            obs = sum(len(v) for v in trips.values())
            events_today = 0
            trips_today = 0

            for (routeid, vehicle), observations in sorted(trips.items()):
                events = build_events(observations)
                trips_today += len({e["trip_seq"] for e in events})
                for event in events:
                    writer.writerow({
                        "trip_key": "%s_%s_%d" % (routeid, vehicle, event["trip_seq"]),
                        "routeid": routeid,
                        "routeno": event["routeno"],
                        "vehicleno": vehicle,
                        "trip_seq": event["trip_seq"],
                        "nodeord": event["nodeord"],
                        "nodeid": event["nodeid"],
                        "nodenm": event["nodenm"],
                        "pass_ts": event["pass_ts"].isoformat(timespec="seconds"),
                        "obs_count": event["obs_count"],
                        "gap_before": event["gap_before"],
                    })
                    events_today += 1
                    if event["gap_before"]:
                        totals["gapped"] += 1

            totals["obs"] += obs
            totals["events"] += events_today
            totals["bad"] += bad
            totals["trips"] += trips_today
            per_day.append((day, len(trips), obs, events_today, trips_today))
            print("  %s  차량 %3d  관측 %6d -> 사건 %5d  운행 %4d"
                  % (day, len(trips), obs, events_today, trips_today))

    print()
    print("파일 %d개 / 관측 %d행 -> 사건 %d건"
          % (len(files), totals["obs"], totals["events"]))
    if totals["bad"]:
        print("(ts·차량번호·nodeord 가 비어 건너뛴 행 %d개)" % totals["bad"])
    print("운행 %d회, 사건당 평균 관측 %.1f회"
          % (totals["trips"], totals["obs"] / max(1, totals["events"])))
    print("앞 정류장을 건너뛴 사건 %d건 (%.1f%%) — 구간 표본에서 뺄 것"
          % (totals["gapped"], 100.0 * totals["gapped"] / max(1, totals["events"])))
    if per_day:
        per_vehicle = [t / max(1, v) for _, v, _, _, t in per_day]
        print("차량 1대의 하루 운행 횟수 평균 %.1f회 (최소 %.1f / 최대 %.1f)"
              % (sum(per_vehicle) / len(per_vehicle),
                 min(per_vehicle), max(per_vehicle)))
        print("  이 값이 상식 밖이면(예: 40회) 회차 판정이 틀린 것이다.")
    print()
    print("저장: %s" % OUT_CSV)
    return 0


if __name__ == "__main__":
    sys.exit(main())
