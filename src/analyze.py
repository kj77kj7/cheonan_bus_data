"""실측 배차·민원·이용량을 노선 단위로 잇고 개선 우선순위를 낸다.

세 자료가 서로 다른 노선 수를 덮는다.

    실측 배차·번칭   36개  (실시간 core 수집분)
    민원            193개  (정보공개청구)
    이용량          198개  (STCIS)

그래서 두 층으로 본다. 겹치는 36개에서 **민원이 실제 배차 불안정을 반영
하는지** 확인하고, 확인되면 민원을 대리지표 삼아 193개 전체로 넓힌다.
표본이 얇다는 약점을 감추지 않고 방법으로 다룬다.

민원 건수를 그대로 쓰면 안 된다. 사람이 많이 타는 노선일수록 민원도 많다.
이용 10만 통행당 건수로 바꿔야 노선끼리 견줄 수 있다.

우선순위는 탄력성 없이도 낼 수 있다. 공표보다 더 기다리는 시간에 그 노선
이용객 수를 곱하면 **하루에 시민이 더 쓴 시간**이 나온다. 증차 비용은
왕복 소요시간을 실측에서 얻어 계산한다 — 한 대가 한 바퀴 도는 데 T 분이
걸리면 배차 h 를 유지하는 데 T/h 대가 필요하다.

산출물
    data/processed/route_summary.csv    노선별 통합표
    data/processed/priority.csv         개선 우선순위

사용법
    python src/analyze.py
"""

import csv
import os
import re
import sys
from datetime import datetime

from config import DATA_DIR

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROC_DIR = os.path.join(DATA_DIR, "processed")
HEADWAY_CSV = os.path.join(PROC_DIR, "headway_by_route.csv")
COMPLAINTS_CSV = os.path.join(PROC_DIR, "complaints_by_route.csv")
EVENTS_CSV = os.path.join(DATA_DIR, "interim", "stop_events.csv")
RIDERSHIP_DIR = os.path.join(DATA_DIR, "ridership")
SUMMARY_CSV = os.path.join(PROC_DIR, "route_summary.csv")
PRIORITY_CSV = os.path.join(PROC_DIR, "priority.csv")

# 버스 한 대를 1년 더 굴리는 데 드는 원가. 2024년 실적원가의 대당원가
# 682,945원(1대·1일)에서 왔다. 검산: 원가총액 74,768,844,106 / 682,945 =
# 109,480 대·일 = 약 300대 × 365일. 대·일당 주행 179km 로 타당하다.
COST_PER_BUS_YEAR = 682_945 * 365

# 민원율의 분모. 10만 통행당 건수로 적으면 자릿수가 읽기 좋다.
RATE_BASE = 100_000

# 민원은 어느 해를 쓰느냐로 결과가 달라진다. 2025년은 온전한 해라 표본이
# 두텁지만 실측 배차(2026-08)와 한 해 차이가 난다. 그사이 배차가 조정됐다면
# 상관이 흐려진다. 2026년은 시점이 가깝지만 8월까지라 표본이 얇다.
# 둘 다 계산해 견준다. 기본은 시점이 가까운 쪽이다.
COMPLAINT_YEARS = [2026, 2025]

SUMMARY_COLUMNS = [
    "route_no", "n_headway", "official_min", "median_min", "p90_min",
    "gap_vs_official", "bunching_rate_obs", "trip_min",
    "complaints_total", "complaints_failure", "결행", "무정차",
    "daily_riders", "failure_per_100k", "has_headway",
]
PRIORITY_COLUMNS = [
    "route_no", "daily_riders", "official_min", "median_min",
    "excess_wait_min", "daily_lost_hours", "buses_needed",
    "annual_cost_won", "hours_saved_per_100m", "priority_rank",
]

PAREN_RE = re.compile(r"\((상|하|[0-9]+)\)\s*$")


def norm_route(text):
    """'10(상)' 과 '10' 을 같은 노선으로 본다.

    STCIS 는 상·하행을 노선번호에 괄호로 붙이고, TAGO 는 routeid 로 나눈다.
    민원 자료는 둘 다 없이 번호만 적는다. 셋을 이으려면 괄호를 떼야 한다.
    """
    return PAREN_RE.sub("", str(text or "").strip())


def read_csv(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def to_float(text, default=None):
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return default


def load_headway():
    rows = read_csv(HEADWAY_CSV)
    if rows is None:
        return {}
    out = {}
    for row in rows:
        key = norm_route(row.get("routeno"))
        n = to_float(row.get("n_headway"), 0)
        # 같은 번호가 상·하행으로 갈려 두 줄이면 표본이 큰 쪽을 대표로 둔다.
        if key in out and out[key]["n_headway"] >= n:
            continue
        out[key] = {
            "n_headway": n,
            "official_min": to_float(row.get("official_min")),
            "median_min": to_float(row.get("median_min")),
            "p90_min": to_float(row.get("p90_min")),
            "gap_vs_official": to_float(row.get("gap_vs_official")),
            "bunching_rate_obs": to_float(row.get("bunching_rate_obs")),
        }
    return out


def load_complaints(year):
    """그 해의 노선별 민원. 자료에 없는 노선은 부르는 쪽에서 0 으로 본다.

    민원이 한 건도 없는 노선을 빼면 안 된다. 0 건은 '자료 없음' 이 아니라
    '그 해에 신고가 없었다' 는 뜻이고, 서비스가 성했다는 정보다. 그걸
    버리면 나쁜 노선만 남아 상관이 사라진다.
    """
    rows = read_csv(COMPLAINTS_CSV)
    if rows is None:
        return {}, set()
    out = {}
    covered = set()
    for row in rows:
        key = norm_route(row.get("route_no"))
        covered.add(key)
        if int(to_float(row.get("year"), 0)) != year:
            continue
        acc = out.setdefault(key, {"total": 0, "failure": 0, "결행": 0, "무정차": 0})
        acc["total"] += int(to_float(row.get("total"), 0))
        acc["failure"] += int(to_float(row.get("service_failure"), 0))
        acc["결행"] += int(to_float(row.get("결행"), 0))
        acc["무정차"] += int(to_float(row.get("무정차"), 0))
    return out, covered


def load_ridership():
    """노선별 일평균 이용객. 여러 기간 파일이 있으면 가장 최근 것을 쓴다."""
    if not os.path.isdir(RIDERSHIP_DIR):
        return {}, None
    files = sorted(f for f in os.listdir(RIDERSHIP_DIR)
                   if f.startswith("route_") and f.endswith(".csv"))
    if not files:
        return {}, None
    latest = files[-1]
    totals = {}
    dates = set()
    with open(os.path.join(RIDERSHIP_DIR, latest), encoding="utf-8-sig",
              newline="") as f:
        for row in csv.DictReader(f):
            key = norm_route(row.get("route_no"))
            totals[key] = totals.get(key, 0) + to_float(row.get("use_cnt"), 0)
            dates.add(row.get("date"))
    days = max(1, len(dates))
    return {k: v / days for k, v in totals.items()}, latest


def load_trip_minutes():
    """노선별 한 운행의 소요시간 중앙값(분). 증차 대수 계산에 쓴다."""
    rows = read_csv(EVENTS_CSV)
    if rows is None:
        return {}
    spans = {}
    for row in rows:
        try:
            ts = datetime.fromisoformat(row["pass_ts"])
        except (KeyError, ValueError):
            continue
        key = (norm_route(row.get("routeno")), row.get("trip_key"))
        first, last = spans.get(key, (ts, ts))
        spans[key] = (min(first, ts), max(last, ts))

    by_route = {}
    for (route_no, _), (first, last) in spans.items():
        minutes = (last - first).total_seconds() / 60.0
        if 5 <= minutes <= 240:          # 한 바퀴로 보기 어려운 것은 뺀다
            by_route.setdefault(route_no, []).append(minutes)
    return {k: sorted(v)[len(v) // 2] for k, v in by_route.items() if v}


def spearman(pairs):
    """순위 상관. 표본이 얇고 분포가 치우쳐 있어 피어슨보다 안전하다."""
    pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
    n = len(pairs)
    if n < 5:
        return None, n

    def ranks(values):
        order = sorted(range(n), key=lambda i: values[i])
        out = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and values[order[j + 1]] == values[order[i]]:
                j += 1
            shared = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                out[order[k]] = shared
            i = j + 1
        return out

    rx = ranks([p[0] for p in pairs])
    ry = ranks([p[1] for p in pairs])
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return (num / den if den else None), n


def build_summary(headway, complaints, covered, riders, trips):
    routes = set(headway) | set(complaints) | set(riders)
    rows = []
    for route_no in sorted(routes, key=lambda r: (len(r), r)):
        h = headway.get(route_no, {})
        c = complaints.get(route_no, {})
        daily = riders.get(route_no)
        # 민원 자료가 덮는 노선인데 그 해 기록이 없으면 0 건이다.
        if c:
            failure = c.get("failure")
        elif route_no in covered:
            c = {"total": 0, "failure": 0, "결행": 0, "무정차": 0}
            failure = 0
        else:
            failure = None

        # 사람이 많이 타는 노선일수록 민원도 많다. 이용량으로 나눠야
        # 노선끼리 견줄 수 있다.
        rate = None
        if failure is not None and daily:
            rate = failure / (daily * 365.0) * RATE_BASE

        rows.append({
            "route_no": route_no,
            "n_headway": h.get("n_headway", 0) or 0,
            "official_min": h.get("official_min"),
            "median_min": h.get("median_min"),
            "p90_min": h.get("p90_min"),
            "gap_vs_official": h.get("gap_vs_official"),
            "bunching_rate_obs": h.get("bunching_rate_obs"),
            "trip_min": trips.get(route_no),
            "complaints_total": c.get("total"),
            "complaints_failure": failure,
            "결행": c.get("결행"),
            "무정차": c.get("무정차"),
            "daily_riders": round(daily) if daily else None,
            "failure_per_100k": round(rate, 1) if rate is not None else None,
            "has_headway": 1 if h else 0,
        })
    return rows


def build_priority(rows):
    """공표보다 더 기다리는 시간 × 이용객 = 하루에 시민이 더 쓴 시간.

    탄력성 없이도 낼 수 있는 값이다. 배차를 공표값까지 되돌리는 데 드는
    비용은 왕복 소요시간에서 구한다 — 한 바퀴 T 분인 노선이 배차 h 를
    유지하려면 T/h 대가 필요하다.
    """
    out = []
    for row in rows:
        gap = row["gap_vs_official"]
        daily = row["daily_riders"]
        official = row["official_min"]
        median = row["median_min"]
        trip = row["trip_min"]
        if not (gap and gap > 0 and daily and official and median and trip):
            continue

        # 배차가 h 면 평균 대기는 h/2 다. 초과 대기도 같은 비율로 본다.
        excess_wait = gap / 2.0
        lost_hours = daily * excess_wait / 60.0
        buses = trip / official - trip / median
        if buses <= 0:
            continue
        cost = buses * COST_PER_BUS_YEAR
        out.append({
            "route_no": row["route_no"],
            "daily_riders": daily,
            "official_min": official,
            "median_min": median,
            "excess_wait_min": round(excess_wait, 1),
            "daily_lost_hours": round(lost_hours, 1),
            "buses_needed": round(buses, 1),
            "annual_cost_won": int(cost),
            # 1억원을 들여 하루에 몇 시간을 돌려주는가
            "hours_saved_per_100m": round(lost_hours / (cost / 1e8), 1),
        })
    out.sort(key=lambda r: -r["hours_saved_per_100m"])
    for i, row in enumerate(out, 1):
        row["priority_rank"] = i
    return out


def layer1(rows, label):
    """민원이 실제 배차 불안정을 반영하는지 순위상관으로 본다."""
    deep = [r for r in rows if r["has_headway"] and r["failure_per_100k"] is not None]
    checks = [
        ("실측 배차 중앙값", "median_min"),
        ("P90 배차", "p90_min"),
        ("공표 대비 초과", "gap_vs_official"),
        ("번칭률", "bunching_rate_obs"),
    ]
    print("  [%s]  겹치는 노선 %d개" % (label, len(deep)))
    best = 0.0
    for name, field in checks:
        rho, n = spearman([(r["failure_per_100k"], r[field]) for r in deep])
        if rho is None:
            print("    %-16s 표본 부족 (%d개)" % (name, n))
            continue
        strength = ("뚜렷" if abs(rho) >= 0.5 else
                    "약함" if abs(rho) >= 0.3 else "없음")
        print("    %-16s rho %+.2f  (n=%d, %s)" % (name, rho, n, strength))
        best = max(best, abs(rho))
    return len(deep), best


def report(rows, priority, ridership_file, alt_rows):
    deep = [r for r in rows if r["has_headway"] and r["failure_per_100k"] is not None]
    wide = [r for r in rows if r["failure_per_100k"] is not None]

    print("노선 %d개를 이었다 (실측 배차 있는 노선 %d개)"
          % (len(rows), sum(1 for r in rows if r["has_headway"])))
    if ridership_file:
        print("이용량 출처: %s" % ridership_file)
    print()

    print("=" * 62)
    print("1층 — 민원이 실제 배차 불안정을 반영하는가")
    print("=" * 62)
    n_main, best_main = layer1(rows, "민원 %d년" % COMPLAINT_YEARS[0])
    print()
    n_alt, best_alt = layer1(alt_rows, "민원 %d년" % COMPLAINT_YEARS[1])
    print()
    print("  양수면 민원이 많은 노선일수록 배차가 실제로 나쁘다는 뜻이다.")
    print("  실측 배차는 2026-08 관측이라, 민원도 가까운 해를 쓰는 쪽이 맞다.")
    print("  두 해가 크게 다르면 그사이 배차가 조정됐다는 뜻이다.")
    print()
    if max(best_main, best_alt) < 0.3:
        print("  >> 어느 쪽도 뚜렷하지 않다. 민원을 배차 불안정의 대리지표로")
        print("     쓸 수 없다는 뜻이므로, 2층은 '민원 자체의 분포' 로만 읽고")
        print("     결론은 실측이 있는 노선으로 한정해야 한다.")
        print()

    print("=" * 62)
    print("2층 — 서비스 불안정 노선 (전체 %d개, 이용 10만 통행당 결행·무정차)" % len(wide))
    print("=" * 62)
    ranked = sorted([r for r in wide if r["daily_riders"] and r["daily_riders"] >= 100],
                    key=lambda r: -r["failure_per_100k"])
    print("  %-8s %10s %10s %10s" % ("노선", "일평균이용", "불이행민원", "10만당"))
    for r in ranked[:12]:
        print("  %-8s %10s %10s %10.1f"
              % (r["route_no"], "{:,}".format(r["daily_riders"]),
                 r["complaints_failure"], r["failure_per_100k"]))
    print()

    if not priority:
        print("[주의] 우선순위를 낼 노선이 없습니다.")
        print("       공표 배차·실측 배차·이용량·왕복 소요시간이 모두 있어야 합니다.")
        return

    print("=" * 62)
    print("3층 — 개선 우선순위 (증차 1억원당 하루에 돌려주는 시간)")
    print("=" * 62)
    print("  %-6s %9s %7s %7s %9s %7s %9s"
          % ("노선", "일이용객", "공표", "실측", "손실시간", "증차", "억원당"))
    for r in priority[:12]:
        print("  %-6s %9s %6.0f분 %6.1f분 %8.0fh %6.1f대 %8.1fh"
              % (r["route_no"], "{:,}".format(r["daily_riders"]),
                 r["official_min"], r["median_min"], r["daily_lost_hours"],
                 r["buses_needed"], r["hours_saved_per_100m"]))
    print()
    total_hours = sum(r["daily_lost_hours"] for r in priority)
    total_cost = sum(r["annual_cost_won"] for r in priority)
    print("  이 %d개 노선에서 시민이 하루에 더 쓰는 시간 {:,.0f} 시간"
          .format(total_hours) % len(priority))
    print("  전부 공표 배차까지 되돌리는 비용 연 %.1f억원 (버스 %.0f대)"
          % (total_cost / 1e8, sum(r["buses_needed"] for r in priority)))


def main():
    headway = load_headway()
    complaints, covered = load_complaints(COMPLAINT_YEARS[0])
    alt_complaints, _ = load_complaints(COMPLAINT_YEARS[1])
    riders, ridership_file = load_ridership()
    trips = load_trip_minutes()

    missing = []
    if not headway:
        missing.append("%s (build_headway.py)" % HEADWAY_CSV)
    if not complaints:
        missing.append("%s (build_complaints.py)" % COMPLAINTS_CSV)
    if not riders:
        missing.append("%s (fetch_route_ridership.py data)" % RIDERSHIP_DIR)
    if missing:
        print("[오류] 필요한 자료가 없습니다.")
        for item in missing:
            print("  - %s" % item)
        return 1

    rows = build_summary(headway, complaints, covered, riders, trips)
    alt_rows = build_summary(headway, alt_complaints, covered, riders, trips)
    priority = build_priority(rows)

    os.makedirs(PROC_DIR, exist_ok=True)
    with open(SUMMARY_CSV, "w", encoding="utf-8-sig", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=SUMMARY_COLUMNS,
                                extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    with open(PRIORITY_CSV, "w", encoding="utf-8-sig", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=PRIORITY_COLUMNS,
                                extrasaction="ignore")
        writer.writeheader()
        writer.writerows(priority)

    report(rows, priority, ridership_file, alt_rows)
    print()
    print("저장: %s" % SUMMARY_CSV)
    print("      %s" % PRIORITY_CSV)
    return 0


if __name__ == "__main__":
    sys.exit(main())
