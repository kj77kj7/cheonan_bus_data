"""정책 시나리오 시뮬레이터.

"a 노선에 버스를 한 대 넣으면 무엇이 어떻게 바뀌는가"에 답한다.
증차·배차조정·정류장 추가 세 가지 개입을 넣고 배차, 대기시간, 이용객,
비용이 어디로 가는지 계산한다.

모델은 두 층이다. **이 구분이 이 모델의 전부**이고, 섞으면 안 된다.

  1층 공급 — 전부 실측에서 나온다. 추정이 아니다.

      한 바퀴 도는 데 T분 걸리는 노선을 h분 간격으로 굴리려면 N = T/h 대가
      있어야 한다. 이건 가정이 아니라 항등식이다. T 는 3주 관측에서 잰
      값이고 h 도 실측이므로, 지금 몇 대가 도는지(N = T/h)가 따라 나온다.
      여기에 ΔN 을 더하면 새 배차 h' = T/(N+ΔN) 이 나온다.

      대기시간도 마찬가지다. 시각표를 안 보고 정류장에 가면 평균 h/2 를
      기다린다. 최악(P90)은 그 노선이 실제로 얼마나 들쭉날쭉한지를 실측
      분포에서 가져와 곱한다.

  2층 수요 — 탄력성을 빌려 온다. 여기는 실측이 아니다.

      운행빈도 f = 1/h 가 늘면 이용객이 는다. 그 정도가 빈도탄력성 e 다.
      천안 자료로는 이 값을 추정할 수 없다. 추정하려면 같은 노선에서
      배차가 바뀐 기록과 그 전후 이용량이 있어야 하는데, 교통카드 자료
      (STCIS)가 2024년 4월부터 시작해 2024-01-27 노선개편 이전이 없다.

      그래서 문헌값을 파라미터로 두고 **구간으로 답한다.** 점추정 하나를
      내놓고 맞다고 하지 않는다. e 를 바꿔 가며 결론이 뒤집히는지 보는
      것이 이 층을 쓰는 올바른 방법이다.

      배차가 바뀌는 순간부터 수집기가 그 효과를 재기 시작하면, e 는
      천안 자체 값으로 대체된다. 그때 2층도 실측이 된다.

사용법
    python src/simulate.py 910 --buses 1        버스 1대 증차
    python src/simulate.py 910 --headway 35     공표 배차로 복원
    python src/simulate.py 910 --sweep          0~5대 표
    python src/simulate.py 910 --add-stops 2    정류장 2곳 추가 경유
    python src/simulate.py --all --restore      전 노선 공표배차 복원
    python src/simulate.py 910 --buses 1 --json 결과를 JSON 으로

산출물
    data/processed/scenarios.csv   --all 로 돌렸을 때 노선별 시나리오
"""

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime

from config import DATA_DIR

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BY_ROUTE_CSV = os.path.join(DATA_DIR, "processed", "headway_by_route.csv")
EVENTS_CSV = os.path.join(DATA_DIR, "interim", "stop_events.csv")
RIDERSHIP_CSV = os.path.join(DATA_DIR, "raw", "route_ridership.csv")
STOPS_CSV = os.path.join(DATA_DIR, "route_stops.csv")
OUT_CSV = os.path.join(DATA_DIR, "processed", "scenarios.csv")

# 천안시가 2024년 실적으로 밝힌 대당원가(1대·1일). 증차 비용은 전부
# 여기서 나온다. 우리가 지어낸 단가가 아니라 시 자신의 숫자다.
COST_PER_BUS_DAY = 682_945
COST_PER_BUS_YEAR = COST_PER_BUS_DAY * 365

# ── 2층 파라미터 ───────────────────────────────────────────────────────
# 버스 수요의 운행빈도 탄력성. 빈도가 1% 늘 때 이용객이 몇 % 느는가.
# 국제 문헌에서 단기 +0.4 안팎, 장기 +0.7 까지 보고된다(Balcombe et al.
# 2004; TCRP Report 95). 천안 값이 아니므로 기본값을 쓰되 반드시 구간과
# 함께 보고한다. 제출 전 인용 원문을 확인해 각주를 달 것.
ELASTICITY = 0.40
ELASTICITY_LOW = 0.20
ELASTICITY_HIGH = 0.60

# 통행시간 가치(원/시). 절감된 대기시간을 돈으로 환산할 때만 쓴다.
# 국토부 교통시설 투자평가지침의 업무 외 통행 시간가치대. 값을 바꿔도
# 순위는 안 바뀌므로 참고용으로만 낸다.
VALUE_OF_TIME_WON = 10_000

TRIPS_PER_DAY = 2          # 통근자 1인의 하루 왕복
COMMUTE_DAYS = 250
TRIPS_PER_PERSON = 2       # 이용량은 통행 건수라 왕복 2건이 1인

PAREN_RE = re.compile(r"\((상|하|[0-9]+)\)\s*$")


def norm_route(text):
    """STCIS 의 '10(상)' 과 TAGO 의 '10' 을 같은 노선으로 본다."""
    return PAREN_RE.sub("", str(text or "").strip()).strip()


def num(text, default=None):
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return default


def read_csv(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


# ── 자료 적재 ──────────────────────────────────────────────────────────

def load_trip_minutes():
    """노선별 한 운행 소요시간 T(분)의 중앙값.

    N = T/h 의 T 다. 관측에서 직접 잰다. 한 운행(trip_key)의 첫 통과와
    마지막 통과 사이가 그 운행의 소요시간이다.
    """
    rows = read_csv(EVENTS_CSV)
    if not rows:
        return {}
    spans = {}
    for row in rows:
        try:
            ts = datetime.fromisoformat(row["pass_ts"])
        except (KeyError, ValueError, TypeError):
            continue
        key = (norm_route(row.get("routeno")), row.get("trip_key"))
        first, last = spans.get(key, (ts, ts))
        spans[key] = (min(first, ts), max(last, ts))

    by_route = {}
    for (route_no, _), (first, last) in spans.items():
        minutes = (last - first).total_seconds() / 60.0
        if 5 <= minutes <= 240:            # 한 바퀴로 보기 어려운 것은 뺀다
            by_route.setdefault(route_no, []).append(minutes)
    return {k: sorted(v)[len(v) // 2] for k, v in by_route.items() if v}


def load_ridership():
    """노선별 하루 이용량(통행). STCIS 노선별 이용량에서 온다."""
    rows = read_csv(RIDERSHIP_CSV)
    if not rows:
        return {}
    total, days = {}, {}
    for row in rows:
        route = norm_route(row.get("route_no") or row.get("노선명")
                           or row.get("routeno"))
        value = None
        for key in ("riders", "이용건수", "승차인원", "total", "value"):
            value = num(row.get(key))
            if value is not None:
                break
        if not route or value is None:
            continue
        total[route] = total.get(route, 0.0) + value
        days.setdefault(route, set()).add(row.get("date") or row.get("일자"))
    return {r: total[r] / max(len(days[r]), 1) for r in total}


def load_stop_counts():
    """노선별 정류장 수. 정류장 추가 시나리오에서 T 증가분을 낸다."""
    rows = read_csv(STOPS_CSV)
    if not rows:
        return {}
    counts = {}
    for row in rows:
        route = norm_route(row.get("routeno"))
        if route:
            counts.setdefault(route, set()).add(row.get("nodeid"))
    return {k: len(v) for k, v in counts.items()}


def load_routes():
    """노선별로 실측·이용량·소요시간을 한 줄로 합친다.

    headway_by_route.csv 는 routeid 단위라 상·하행이 갈라져 있다. 같은
    번호는 표본이 큰 쪽을 대표로 둔다.
    """
    rows = read_csv(BY_ROUTE_CSV)
    if not rows:
        print("[오류] %s 가 없습니다." % BY_ROUTE_CSV)
        print("       python src/build_headway.py 를 먼저 실행하십시오.")
        return None

    trips = load_trip_minutes()
    riders = load_ridership()
    stops = load_stop_counts()

    best = {}
    for row in rows:
        route = norm_route(row.get("routeno"))
        n = num(row.get("n_headway"), 0) or 0
        if not route:
            continue
        if route in best and n <= best[route][0]:
            continue
        # 평시 값이 있으면 그것을 쓴다. 심야가 섞인 전체 P90 은 과장이다.
        median = num(row.get("median_day_min")) or num(row.get("median_min"))
        p90 = num(row.get("p90_day_min")) or num(row.get("p90_min"))
        best[route] = (n, {
            "route_no": route,
            "official_min": num(row.get("official_min")),
            "median_min": median,
            "p90_min": p90,
            "median_all_min": num(row.get("median_min")),
            "p90_all_min": num(row.get("p90_min")),
            "n_headway": int(n),
            "bunching_rate_obs": num(row.get("bunching_rate_obs")),
            "trip_min": trips.get(route),
            "daily_riders": riders.get(route),
            "n_stops": stops.get(route),
        })
    return {k: v[1] for k, v in best.items()}


# ── 1층: 공급 ──────────────────────────────────────────────────────────

def buses_now(route):
    """지금 이 노선을 굴리고 있는 대수. N = T/h 로 실측에서 되짚는다."""
    T, h = route["trip_min"], route["median_min"]
    if not T or not h:
        return None
    return T / h


def headway_from_buses(route, n_buses, trip_min=None):
    """N 대를 굴리면 배차가 몇 분이 되는가. h = T/N."""
    T = trip_min if trip_min is not None else route["trip_min"]
    if not T or not n_buses or n_buses <= 0:
        return None
    return T / n_buses


def wait_stats(route, headway):
    """그 배차에서 시민이 겪는 대기와, 열 번에 한 번 겪는 최악 배차.

    평균 대기는 h/2 다. 시각표를 안 보고 정류장에 가면 그렇게 된다.

    최악값은 그 노선이 실제로 얼마나 들쭉날쭉한지(P90/중앙값)를 그대로
    물려받는다고 본다. 증차하면 배차가 짧아질 뿐 아니라 흩어짐도 같은
    비율로 준다는 가정인데, 실제로는 대수가 늘수록 번칭이 더 빨리 잦아
    들어 이보다 좋아지는 편이다. 즉 보수적인 쪽으로 틀렸다.

    반환값은 (평균 대기, P90 배차)다. 뒤엣것은 대기가 아니라 배차라,
    그림 2 의 168분과 같은 축에서 읽을 수 있다.
    """
    if not headway:
        return None, None
    mean_wait = headway / 2.0
    base_h, base_p90 = route["median_min"], route["p90_min"]
    spread = (base_p90 / base_h) if (base_h and base_p90) else None
    worst_headway = headway * spread if spread else None
    return mean_wait, worst_headway


# ── 2층: 수요 ──────────────────────────────────────────────────────────

def demand_response(riders, h_old, h_new, elasticity):
    """배차가 h_old 에서 h_new 로 바뀌면 이용객이 얼마가 되는가.

    운행빈도 f = 1/h 의 불변탄력성 모형이다.
        Q' / Q = (f'/f)^e = (h/h')^e
    e 는 문헌값이고 천안 값이 아니다. 호출부에서 반드시 구간으로 낸다.
    """
    if not riders or not h_old or not h_new or h_new <= 0:
        return None
    return riders * (h_old / h_new) ** elasticity


# ── 시나리오 ───────────────────────────────────────────────────────────

def simulate(route, add_buses=None, target_headway=None, add_stops=0,
             elasticity=ELASTICITY):
    """개입 하나를 넣고 전후를 계산한다.

    add_buses 와 target_headway 는 같은 것을 양쪽에서 말하는 것이라
    (대수를 정하면 배차가 따라오고, 배차를 정하면 대수가 따라온다)
    둘 중 하나만 준다.
    """
    T, h0 = route["trip_min"], route["median_min"]
    if not T or not h0:
        return {"route_no": route["route_no"], "error": "실측 배차 또는 소요시간 없음"}

    n0 = buses_now(route)

    # 정류장을 더 들르게 하면 한 바퀴가 길어진다. 정류장당 평균 소요를
    # T/정류장수 로 보는 거친 근사다. 실제로는 정차시간과 주행시간이
    # 다르므로 상한으로 읽어야 한다.
    T_new = T
    if add_stops:
        per_stop = T / route["n_stops"] if route.get("n_stops") else None
        if per_stop is None:
            return {"route_no": route["route_no"], "error": "정류장 수 없음"}
        T_new = T + per_stop * add_stops

    if target_headway:
        h1 = float(target_headway)
        n1 = T_new / h1
    else:
        n1 = n0 + (add_buses or 0)
        h1 = headway_from_buses(route, n1, T_new)
    if not h1 or h1 <= 0:
        return {"route_no": route["route_no"], "error": "배차 계산 불가"}

    w0, worst0 = wait_stats(route, h0)
    w1, worst1 = wait_stats(route, h1)

    d_buses = n1 - n0
    cost = d_buses * COST_PER_BUS_YEAR

    riders0 = route.get("daily_riders")
    band = {}
    for name, e in (("low", ELASTICITY_LOW), ("mid", elasticity),
                    ("high", ELASTICITY_HIGH)):
        band[name] = demand_response(riders0, h0, h1, e)

    # 기존 이용객이 돌려받는 시간. 여기는 2층을 안 거치므로 실측 기반이다.
    saved_min_per_trip = (w0 - w1) if (w0 and w1) else None
    daily_saved_hours = (riders0 * saved_min_per_trip / 60.0
                         if riders0 and saved_min_per_trip else None)
    yearly_min_per_rider = (saved_min_per_trip * TRIPS_PER_DAY * COMMUTE_DAYS
                            if saved_min_per_trip else None)

    new_riders = (band["mid"] - riders0) if (band["mid"] and riders0) else None

    return {
        "route_no": route["route_no"],
        "trip_min": round(T, 1),
        "trip_min_new": round(T_new, 1),
        "n_stops": route.get("n_stops"),
        "buses_now": round(n0, 2),
        "buses_new": round(n1, 2),
        "buses_added": round(d_buses, 2),
        "headway_now": round(h0, 1),
        "headway_new": round(h1, 1),
        "official_min": route.get("official_min"),
        "wait_now": round(w0, 1) if w0 else None,
        "wait_new": round(w1, 1) if w1 else None,
        "worst_now": round(worst0, 1) if worst0 else None,
        "worst_new": round(worst1, 1) if worst1 else None,
        "riders_now": round(riders0) if riders0 else None,
        "riders_low": round(band["low"]) if band["low"] else None,
        "riders_mid": round(band["mid"]) if band["mid"] else None,
        "riders_high": round(band["high"]) if band["high"] else None,
        "new_riders": round(new_riders) if new_riders else None,
        "daily_saved_hours": round(daily_saved_hours, 1) if daily_saved_hours else None,
        "yearly_hours_per_rider": (round(yearly_min_per_rider / 60.0, 1)
                                   if yearly_min_per_rider else None),
        "annual_cost_won": int(cost),
        "cost_per_new_rider_won": (int(cost / (new_riders / TRIPS_PER_PERSON))
                                   if new_riders and new_riders > 0 else None),
        "time_benefit_won": (int(daily_saved_hours * 365 * VALUE_OF_TIME_WON)
                             if daily_saved_hours else None),
    }


def benefit_cost(result):
    """시간가치 편익 / 증차 비용. 참고 지표이지 결론이 아니다."""
    b, c = result.get("time_benefit_won"), result.get("annual_cost_won")
    if not b or not c or c <= 0:
        return None
    return b / c


# ── 출력 ───────────────────────────────────────────────────────────────

def fmt(value, unit="", nd=1):
    if value is None or value == "":
        return "—"
    if isinstance(value, float):
        return ("%%.%df%%s" % nd) % (value, unit)
    return "{:,}{}".format(value, unit)


def show(result):
    if result.get("error"):
        print("%s번 — %s" % (result["route_no"], result["error"]))
        return
    r = result
    print("=" * 62)
    print(" %s번  ·  한 바퀴 %s분  ·  정류장 %s곳"
          % (r["route_no"], fmt(r["trip_min"]), fmt(r["n_stops"])))
    print("=" * 62)
    print()
    print(" [1층 공급] 실측에서 그대로 따라 나온다")
    print("   투입 대수     %6s대   →  %6s대   (%+.2f대)"
          % (fmt(r["buses_now"], nd=2), fmt(r["buses_new"], nd=2),
             r["buses_added"]))
    print("   배차간격      %6s분   →  %6s분" % (fmt(r["headway_now"]), fmt(r["headway_new"])))
    print("   평균 대기     %6s분   →  %6s분" % (fmt(r["wait_now"]), fmt(r["wait_new"])))
    print("   최악 배차     %6s분   →  %6s분   (P90, 열 번에 한 번)"
          % (fmt(r["worst_now"]), fmt(r["worst_new"])))
    if r["official_min"]:
        print("   공표 배차     %6s분" % fmt(r["official_min"]))
    print()
    if r["yearly_hours_per_rider"]:
        print("   매일 타는 사람이 한 해에 돌려받는 시간  %s시간 (%.1f일)"
              % (fmt(r["yearly_hours_per_rider"]),
                 r["yearly_hours_per_rider"] / 24.0))
    if r["daily_saved_hours"]:
        print("   이 노선 전체가 하루에 돌려받는 시간      %s시간"
              % fmt(r["daily_saved_hours"]))
    print()
    if r["riders_now"]:
        print(" [2층 수요] 탄력성을 빌려 왔다 — 구간으로 읽을 것")
        print("   하루 이용     %6s통행  →  %6s통행"
              % (fmt(r["riders_now"]), fmt(r["riders_mid"])))
        lo, hi = sorted([r["riders_low"], r["riders_high"]])
        print("                 탄력성 %.1f~%.1f 구간  %s ~ %s통행"
              % (ELASTICITY_LOW, ELASTICITY_HIGH, fmt(lo), fmt(hi)))
        if r["new_riders"]:
            print("   순증           %s통행/일" % fmt(r["new_riders"]))
    else:
        print(" [2층 수요] 이 노선의 이용량 자료가 없어 건너뜁니다")
    print()
    print(" [비용] 천안시 2024년 실적 대당원가 %s원/대·일"
          % "{:,}".format(COST_PER_BUS_DAY))
    print("   연간 소요      %s원 (%.2f억)"
          % ("{:,}".format(r["annual_cost_won"]), r["annual_cost_won"] / 1e8))
    if r["cost_per_new_rider_won"]:
        print("   신규 1인당     %s원/년" % "{:,}".format(r["cost_per_new_rider_won"]))
    bc = benefit_cost(r)
    if bc:
        print("   시간편익/비용  %.2f  (시간가치 %s원/시 가정)"
              % (bc, "{:,}".format(VALUE_OF_TIME_WON)))
    print()


def sweep(route, max_buses=5, elasticity=ELASTICITY):
    """0대부터 한 대씩 넣으며 어디서 효과가 꺾이는지 본다.

    증차는 수익체감이다. h = T/N 이라 N 이 클수록 한 대가 줄이는 배차가
    작아진다. 어디까지 넣는 것이 합리적인지는 이 표가 보여준다.
    """
    print("=" * 74)
    print(" %s번 증차 시나리오" % route["route_no"])
    print("=" * 74)
    print(" %4s %9s %9s %11s %11s %12s"
          % ("증차", "배차(분)", "평균대기", "하루이용(통행)", "연비용(억)", "신규1인당(원)"))
    print("-" * 74)
    rows = []
    for k in range(0, max_buses + 1):
        r = simulate(route, add_buses=k, elasticity=elasticity)
        if r.get("error"):
            print(" %s" % r["error"])
            return []
        print(" %3d대 %9s %9s %11s %11s %12s"
              % (k, fmt(r["headway_new"]), fmt(r["wait_new"]),
                 fmt(r["riders_mid"]), fmt(r["annual_cost_won"] / 1e8, nd=2),
                 fmt(r["cost_per_new_rider_won"])))
        rows.append(r)
    print()
    return rows


SCENARIO_COLUMNS = ["route_no", "trip_min", "n_stops", "buses_now", "buses_new",
                    "buses_added", "headway_now", "headway_new", "official_min",
                    "wait_now", "wait_new", "worst_now", "worst_new",
                    "riders_now", "riders_low", "riders_mid", "riders_high",
                    "new_riders", "daily_saved_hours", "yearly_hours_per_rider",
                    "annual_cost_won", "cost_per_new_rider_won",
                    "time_benefit_won"]


def run_all(routes, target="official", add_buses=None, elasticity=ELASTICITY):
    """전 노선에 같은 개입을 넣고 표로 낸다.

    target='official' 은 "공표한 대로만 굴려 달라"는 시나리오다. 새로운
    요구가 아니라 이미 약속한 수준으로 되돌리는 것이라 방어하기 쉽다.
    """
    out = []
    for route in sorted(routes.values(), key=lambda r: r["route_no"]):
        if target == "official":
            official = route.get("official_min")
            if not official or not route.get("median_min"):
                continue
            if official >= route["median_min"]:      # 이미 지켜지는 노선
                continue
            r = simulate(route, target_headway=official, elasticity=elasticity)
        else:
            r = simulate(route, add_buses=add_buses, elasticity=elasticity)
        if not r.get("error"):
            out.append(r)

    out.sort(key=lambda r: -(r.get("daily_saved_hours") or 0))
    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SCENARIO_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(out)

    print("=" * 78)
    print(" 전 노선 공표배차 복원 시나리오" if target == "official"
          else " 전 노선 %+d대 증차 시나리오" % (add_buses or 0))
    print("=" * 78)
    print(" %6s %13s %8s %10s %12s %11s"
          % ("노선", "배차(분)", "증차", "연비용(억)", "하루절감(시간)", "신규(통행)"))
    print("-" * 78)
    for r in out[:20]:
        print(" %6s %5s → %-5s %7.1f대 %10.2f %12s %11s"
              % (r["route_no"], fmt(r["headway_now"]), fmt(r["headway_new"]),
                 r["buses_added"], r["annual_cost_won"] / 1e8,
                 fmt(r["daily_saved_hours"]), fmt(r["new_riders"])))
    print("-" * 78)
    total_cost = sum(r["annual_cost_won"] for r in out)
    total_hours = sum(r.get("daily_saved_hours") or 0 for r in out)
    total_new = sum(r.get("new_riders") or 0 for r in out)
    print(" 합계 %d개 노선 · 버스 %.1f대 · 연 %.1f억원"
          % (len(out), sum(r["buses_added"] for r in out), total_cost / 1e8))
    print("      하루 %s시간 절감 · 신규 %s통행/일 (탄력성 %.2f)"
          % (fmt(total_hours), fmt(round(total_new)), elasticity))
    print()
    print("[저장] %s" % OUT_CSV)
    return out


def main():
    ap = argparse.ArgumentParser(description="천안시 시내버스 정책 시나리오 시뮬레이터")
    ap.add_argument("route", nargs="?", help="노선번호 (예: 910)")
    ap.add_argument("--buses", type=float, help="증차 대수")
    ap.add_argument("--headway", type=float, help="목표 배차간격(분)")
    ap.add_argument("--add-stops", type=int, default=0, help="추가 경유 정류장 수")
    ap.add_argument("--sweep", action="store_true", help="0~5대 증차 표")
    ap.add_argument("--all", action="store_true", help="전 노선")
    ap.add_argument("--restore", action="store_true",
                    help="--all 과 함께: 공표 배차로 복원")
    ap.add_argument("--elasticity", type=float, default=ELASTICITY,
                    help="빈도탄력성 (기본 %.2f)" % ELASTICITY)
    ap.add_argument("--json", action="store_true", help="JSON 출력")
    args = ap.parse_args()

    routes = load_routes()
    if routes is None:
        return 1
    if not routes:
        print("[오류] 실측 배차가 있는 노선이 없습니다.")
        return 1

    if args.all:
        run_all(routes, target="official" if args.restore else "buses",
                add_buses=args.buses, elasticity=args.elasticity)
        return 0

    if not args.route:
        print("노선번호를 주십시오. 실측이 있는 노선:")
        print("  " + ", ".join(sorted(routes, key=lambda r: (len(r), r))))
        return 1

    key = norm_route(args.route)
    if key not in routes:
        print("[오류] %s번은 실측 배차가 없습니다." % key)
        print("  가능한 노선: " + ", ".join(sorted(routes, key=lambda r: (len(r), r))))
        return 1
    route = routes[key]

    if args.sweep:
        sweep(route, elasticity=args.elasticity)
        return 0

    if args.buses is None and args.headway is None and not args.add_stops:
        # 아무 개입도 안 주면 "공표대로 굴리기"를 기본 시나리오로 본다.
        args.headway = route.get("official_min")
        if not args.headway:
            print("[오류] 개입을 지정하십시오 (--buses / --headway / --add-stops).")
            return 1
        print("[기본] 공표 배차 %s분으로 복원하는 시나리오입니다.\n" % fmt(args.headway))

    result = simulate(route, add_buses=args.buses, target_headway=args.headway,
                      add_stops=args.add_stops, elasticity=args.elasticity)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        show(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
