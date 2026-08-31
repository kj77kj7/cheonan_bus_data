"""천안 자체 자료로 빈도탄력성을 추정한다.

simulate.py 의 2층은 지금 문헌값 0.4 를 쓴다. 그것을 천안에서 실제로
일어난 배차 조정으로 갈아 끼우는 것이 이 파일의 일이다.

천안시 교통정보센터는 노선 조정을 시행 전에 공고한다. 그 시행일 앞뒤로
교통카드 이용량이 어떻게 달라졌는지 보면 "배차를 줄이면 이용객이 얼마나
느는가"가 나온다.

전후 비교만으로는 안 된다
--------------------------
461번이 8월 15일에 증차됐고 9월 이용객이 7월보다 늘었다고 하자. 그것이
증차 덕인지 개학 때문인지 구분이 안 된다. 그래서 **그 기간에 아무 조정도
없었던 노선들의 변화를 빼준다.** 계절·경기·요금처럼 모든 노선에 똑같이
걸리는 것은 이 뺄셈에서 사라진다. 이중차분(DID)이다.

    효과 = (처치노선 이후 − 처치노선 이전)
         − (대조노선 이후 − 대조노선 이전)          ... 로그 이용량 기준

    탄력성 = 효과 / Δln(운행빈도)

로그로 재는 이유는 탄력성이 비율 대 비율이기 때문이다. 이용량이 8% 늘고
빈도가 20% 늘었으면 탄력성은 0.4 다. 절대값으로 재면 노선 크기에 휘둘린다.

무엇을 채워 넣어야 하나
------------------------
data/changes.csv 를 손으로 채운다. 공고 첨부의 표에서 옮기면 된다.

    effective_date,route_no,headway_before,headway_after,trips_before,trips_after,source
    2025-08-15,461,,,18,24,2025.08.11 461번 운행 조정 안내
    2025-10-01,3,40,30,,,2025.09.12 운행계통 조정 안내

    - effective_date : 시행일. 공고일이 아니다
    - route_no       : 바뀐 노선. 한 공고에 여럿이면 여러 줄로 적는다
    - headway_*      : 배차간격(분). 빈도 f = 1/headway
    - trips_*        : 하루 운행횟수. 이쪽이 있으면 이쪽을 먼저 쓴다
                       (f 에 그대로 비례하고 반올림 오차가 없다)
    - source         : 근거 공고. 나중에 각주로 쓴다

headway 든 trips 든 **둘 중 하나는 숫자로 있어야** 탄력성이 나온다. 없으면
"이용량이 몇 % 달라졌다"까지만 나오고 그것도 결과에 같이 적는다.

이용량은 data/ridership/route_<시작>_<끝>.csv 를 월별로 여러 개 받아 둔다.
사건 하나당 시행일 직전 달과 직후 달이 있어야 한다. 시행일이 낀 달은
전후가 섞이므로 자동으로 건너뛴다.

사용법
    python src/estimate_elasticity.py
    python src/estimate_elasticity.py --window 2      전후 각 2개월씩 묶기
    python src/estimate_elasticity.py --json

산출물
    data/processed/elasticity.csv       사건별 추정치
    data/processed/elasticity.json      요약 (simulate.py 가 읽는다)
"""

import argparse
import csv
import json
import math
import os
import re
import sys
from datetime import date, datetime, timedelta

from config import DATA_DIR

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RIDERSHIP_DIR = os.path.join(DATA_DIR, "ridership")
CHANGES_CSV = os.path.join(DATA_DIR, "changes.csv")
OUT_DIR = os.path.join(DATA_DIR, "processed")
OUT_CSV = os.path.join(OUT_DIR, "elasticity.csv")
OUT_JSON = os.path.join(OUT_DIR, "elasticity.json")

# 시행일 앞뒤로 이만큼 안에 다른 조정이 있는 노선은 대조군에서 뺀다.
# 대조군이 조용해야 뺄셈이 의미가 있다.
QUIET_DAYS = 45

# 이용량이 너무 적은 노선은 로그 차이가 크게 튄다. 하루 평균 이만큼은
# 넘어야 표본에 넣는다.
MIN_DAILY = 30.0

PERIOD_RE = re.compile(r"route_(\d{8})_(\d{8})\.csv$")
PAREN_RE = re.compile(r"\((상|하|[0-9]+)\)\s*$")

CSV_COLUMNS = ["effective_date", "route_no", "source",
               "pre_period", "post_period",
               "pre_daily", "post_daily", "d_ln_riders",
               "control_n", "control_d_ln", "did",
               "d_ln_freq", "elasticity", "note"]


def norm_route(text):
    return PAREN_RE.sub("", str(text or "").strip()).strip()


def num(text, default=None):
    try:
        value = float(str(text).strip())
    except (TypeError, ValueError):
        return default
    return value


# ── 이용량 패널 ────────────────────────────────────────────────────────

def load_panel():
    """기간 파일들을 읽어 {기간: {노선: 일평균 이용량}} 으로 만든다.

    기간은 파일명에서 온다. route_20250701_20250731.csv → 2025-07-01~07-31.
    """
    if not os.path.isdir(RIDERSHIP_DIR):
        return {}
    panel = {}
    for name in sorted(os.listdir(RIDERSHIP_DIR)):
        m = PERIOD_RE.search(name)
        if not m:
            continue
        start = datetime.strptime(m.group(1), "%Y%m%d").date()
        end = datetime.strptime(m.group(2), "%Y%m%d").date()
        totals, dates = {}, set()
        with open(os.path.join(RIDERSHIP_DIR, name), encoding="utf-8-sig",
                  newline="") as f:
            for row in csv.DictReader(f):
                route = norm_route(row.get("route_no"))
                if not route:
                    continue
                totals[route] = totals.get(route, 0.0) + (num(row.get("use_cnt"), 0) or 0)
                dates.add(row.get("date"))
        days = max(1, len(dates))
        if not totals:
            continue
        panel[(start, end)] = {k: v / days for k, v in totals.items()}
    return panel


def load_changes():
    """조정 사건 목록. 시행일별로 묶는다."""
    if not os.path.exists(CHANGES_CSV):
        return {}
    events = {}
    with open(CHANGES_CSV, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            raw = str(row.get("effective_date", "")).strip()
            try:
                eff = datetime.strptime(raw[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
            route = norm_route(row.get("route_no"))
            if not route:
                continue
            events.setdefault(eff, []).append({
                "route_no": route,
                "headway_before": num(row.get("headway_before")),
                "headway_after": num(row.get("headway_after")),
                "trips_before": num(row.get("trips_before")),
                "trips_after": num(row.get("trips_after")),
                "source": str(row.get("source", "")).strip(),
            })
    return events


def d_ln_frequency(change):
    """운행빈도의 로그 변화량. 탄력성의 분모다.

    운행횟수가 있으면 그것을 쓴다. f 에 그대로 비례하고, 배차간격은
    시간대마다 달라 대표값 하나로 적히면 반올림이 섞이기 때문이다.
    배차간격만 있으면 f = 1/h 로 뒤집는다.
    """
    tb, ta = change["trips_before"], change["trips_after"]
    if tb and ta and tb > 0 and ta > 0:
        return math.log(ta / tb), "운행횟수"
    hb, ha = change["headway_before"], change["headway_after"]
    if hb and ha and hb > 0 and ha > 0:
        return math.log(hb / ha), "배차간격"
    return None, None


# ── 기간 고르기 ────────────────────────────────────────────────────────

def pick_periods(panel, effective, window):
    """시행일 앞뒤로 쓸 기간을 고른다.

    시행일이 걸친 기간은 전후가 섞여 있어 못 쓴다. 온전히 이전인 것과
    온전히 이후인 것만 고르고, 시행일에서 가까운 순으로 window 개씩.
    """
    before = sorted((p for p in panel if p[1] < effective),
                    key=lambda p: p[1], reverse=True)[:window]
    after = sorted((p for p in panel if p[0] > effective),
                   key=lambda p: p[0])[:window]
    return before, after


def mean_daily(panel, periods, route):
    values = [panel[p][route] for p in periods if route in panel[p]]
    return sum(values) / len(values) if values else None


def treated_near(events, effective, days=QUIET_DAYS):
    """시행일 주변에서 조정이 있었던 모든 노선. 대조군에서 뺀다."""
    out = set()
    for eff, changes in events.items():
        if abs((eff - effective).days) <= days:
            out.update(c["route_no"] for c in changes)
    return out


# ── 추정 ───────────────────────────────────────────────────────────────

def estimate_event(panel, events, effective, window):
    """한 사건에서 노선별 이중차분과 탄력성을 낸다."""
    before, after = pick_periods(panel, effective, window)
    if not before or not after:
        return [], "시행일 전후 기간 자료 없음"

    noisy = treated_near(events, effective)
    # 대조군: 이 시기에 아무 조정도 없었고, 전후 모두 자료가 있는 노선
    controls = []
    for route in panel[before[0]]:
        if route in noisy:
            continue
        pre = mean_daily(panel, before, route)
        post = mean_daily(panel, after, route)
        if not pre or not post or pre < MIN_DAILY or post < MIN_DAILY:
            continue
        controls.append(math.log(post / pre))
    if len(controls) < 10:
        return [], "대조군이 %d개뿐 (10개 미만)" % len(controls)
    control_mean = sum(controls) / len(controls)

    label_pre = "%s~%s" % (before[-1][0], before[0][1])
    label_post = "%s~%s" % (after[0][0], after[-1][1])

    rows = []
    for change in events[effective]:
        route = change["route_no"]
        pre = mean_daily(panel, before, route)
        post = mean_daily(panel, after, route)
        if not pre or not post or pre < MIN_DAILY or post < MIN_DAILY:
            rows.append({
                "effective_date": effective.isoformat(), "route_no": route,
                "source": change["source"], "note": "이용량 자료 부족",
            })
            continue
        d_ln = math.log(post / pre)
        did = d_ln - control_mean
        d_freq, basis = d_ln_frequency(change)
        elasticity = (did / d_freq) if (d_freq and abs(d_freq) > 1e-6) else None
        rows.append({
            "effective_date": effective.isoformat(), "route_no": route,
            "source": change["source"],
            "pre_period": label_pre, "post_period": label_post,
            "pre_daily": round(pre, 1), "post_daily": round(post, 1),
            "d_ln_riders": round(d_ln, 4),
            "control_n": len(controls), "control_d_ln": round(control_mean, 4),
            "did": round(did, 4),
            "d_ln_freq": round(d_freq, 4) if d_freq else "",
            "elasticity": round(elasticity, 3) if elasticity is not None else "",
            "note": ("%s 기준" % basis) if basis else "빈도 변화량 없음 — 효과만",
        })
    return rows, None


def summarize(rows):
    """사건들을 묶어 하나의 탄력성으로 만든다.

    사건마다 노선이 여럿이라 노선을 그냥 다 세면 노선 많은 공고가 결과를
    끌고 간다. 사건 안에서 먼저 중앙값을 내고, 사건끼리 다시 중앙값을
    낸다. 표본이 얇을 때 평균보다 덜 흔들린다.
    """
    per_event = {}
    for row in rows:
        e = row.get("elasticity")
        if e == "" or e is None:
            continue
        per_event.setdefault(row["effective_date"], []).append(float(e))
    if not per_event:
        return None

    event_medians = []
    for eff in sorted(per_event):
        v = sorted(per_event[eff])
        event_medians.append((eff, v[len(v) // 2], len(v)))

    values = sorted(m for _, m, _ in event_medians)
    n = len(values)
    median = values[n // 2]
    lo, hi = values[0], values[-1]
    if n >= 4:
        lo, hi = values[int(n * 0.25)], values[int(n * 0.75)]
    return {
        "n_events": n,
        "n_routes": sum(c for _, _, c in event_medians),
        "elasticity": round(median, 3),
        "low": round(lo, 3),
        "high": round(hi, 3),
        "per_event": [{"effective_date": e, "elasticity": round(m, 3),
                       "n_routes": c} for e, m, c in event_medians],
    }


def main():
    ap = argparse.ArgumentParser(description="천안 자료로 빈도탄력성을 추정한다")
    ap.add_argument("--window", type=int, default=1,
                    help="시행일 전후로 묶을 기간 수 (기본 1개월씩)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    panel = load_panel()
    if not panel:
        print("[오류] %s 에 route_<시작>_<끝>.csv 가 없습니다." % RIDERSHIP_DIR)
        print("       fetch_route_ridership.py 로 월별 이용량을 먼저 받으십시오.")
        return 1

    events = load_changes()
    if not events:
        print("[오류] %s 가 없습니다." % CHANGES_CSV)
        print()
        print("공고 첨부의 표를 아래 형식으로 옮겨 만드십시오.")
        print("  effective_date,route_no,headway_before,headway_after,"
              "trips_before,trips_after,source")
        print("  2025-08-15,461,,,18,24,2025.08.11 461번 운행 조정 안내")
        return 1

    print("이용량 기간 %d개:" % len(panel))
    for (s, e) in sorted(panel):
        print("  %s ~ %s   노선 %d개" % (s, e, len(panel[(s, e)])))
    print()

    all_rows = []
    for effective in sorted(events):
        rows, skip = estimate_event(panel, events, effective, args.window)
        if skip:
            print("[건너뜀] %s — %s" % (effective, skip))
            continue
        all_rows.extend(rows)

    if not all_rows:
        print()
        print("[결과 없음] 추정된 사건이 없습니다.")
        print("  시행일 앞뒤로 온전한 기간이 각각 하나씩은 있어야 합니다.")
        return 1

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_rows)

    print("=" * 78)
    print(" 사건별 추정")
    print("=" * 78)
    print(" %-12s %6s %10s %10s %10s %9s"
          % ("시행일", "노선", "이용Δln", "대조Δln", "이중차분", "탄력성"))
    print("-" * 78)
    for row in all_rows:
        if not row.get("pre_period"):
            print(" %-12s %6s   %s" % (row["effective_date"], row["route_no"],
                                       row.get("note", "")))
            continue
        print(" %-12s %6s %10.4f %10.4f %10.4f %9s"
              % (row["effective_date"], row["route_no"], row["d_ln_riders"],
                 row["control_d_ln"], row["did"],
                 row["elasticity"] if row["elasticity"] != "" else "—"))
    print()

    summary = summarize(all_rows)
    if summary:
        print("=" * 78)
        print(" 천안 빈도탄력성  ε = %.3f   (사건 %d건 · 노선 %d개)"
              % (summary["elasticity"], summary["n_events"], summary["n_routes"]))
        print("   사건 간 범위  %.3f ~ %.3f" % (summary["low"], summary["high"]))
        print("=" * 78)
        print()
        print(" 문헌값은 0.4 안팎이다. 이 값이 그 근처면 서로를 뒷받침하고,")
        print(" 크게 벗어나면 천안의 특성이므로 천안 값을 쓰는 편이 맞다.")
        print()
        print(" simulate.py 에 반영:")
        print("   python src/simulate.py 910 --buses 1 --elasticity %.3f"
              % summary["elasticity"])
        with open(OUT_JSON, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print()
        print("[저장] %s" % OUT_CSV)
        print("[저장] %s" % OUT_JSON)
    else:
        print("[주의] 빈도 변화량(운행횟수·배차간격)이 없어 탄력성은 못 냈습니다.")
        print("       이용량 변화(이중차분)까지는 %s 에 있습니다." % OUT_CSV)
        print("       공고 첨부에서 변경 전후 운행횟수를 찾아 넣으십시오.")

    if args.json and summary:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
