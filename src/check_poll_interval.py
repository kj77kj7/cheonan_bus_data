"""수집 주기 산정 근거 - 정류장 간 이동시간 추정.

수집 주기가 정류장 간 이동시간보다 길면 버스가 정류장 몇 개를 건너뛴 채
관측되어, '정류장 통과시각'을 프로브로 삼는 구간속도 산출이 무너진다.
실제 수집한 좌표로 정류장 간 거리를 재서 적정 주기의 상한을 계산한다.
"""

import csv
import io
import math
import os
import statistics
from collections import defaultdict

from config import DATA_DIR, LOGS_DIR

STOPS_CSV = os.path.join(DATA_DIR, "route_stops.csv")
OUT_PATH = os.path.join(LOGS_DIR, "poll_interval_basis.md")

CORE = {
    "처치군": ["5", "405", "80", "85", "88"],
    "도심 간선": ["6", "400", "11", "2", "3", "121", "90", "800", "7"],
    "중간층": ["900", "910", "140"],
}
OUTER = ["402", "414", "82", "221", "601"]

# 시내버스 표정속도(정차·신호 포함). 국내 시내버스는 대체로 15~20km/h 대다.
SPEEDS_KMH = [12, 15, 18, 22]


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def main():
    with open(STOPS_CSV, encoding="utf-8-sig") as f:
        stops = list(csv.DictReader(f))

    by_route = defaultdict(list)
    for s in stops:
        by_route[s["routeid"]].append(s)

    routeno_of = {rid: rows[0]["routeno"] for rid, rows in by_route.items()}

    core_routes = set()
    for routes in CORE.values():
        core_routes.update(routes)

    def gaps_for(routenos):
        gaps = []
        for rid, rows in by_route.items():
            if routeno_of[rid] not in routenos:
                continue
            rows = sorted(rows, key=lambda r: int(r["nodeord"]))
            for a, b in zip(rows, rows[1:]):
                d = haversine_m(float(a["gpslati"]), float(a["gpslong"]),
                                float(b["gpslati"]), float(b["gpslong"]))
                if d > 0:
                    gaps.append(d)
        return sorted(gaps)

    lines = []

    def out(t=""):
        lines.append(t)

    out("# 수집 주기 산정 근거")
    out("")
    out("수집 주기가 정류장 간 이동시간보다 길면 버스가 정류장을 건너뛴 채 관측된다.")
    out("그러면 '정류장 통과시각 차이 → 구간속도' 라는 프로브 방식 자체가 성립하지 않는다.")
    out("")

    for label, routenos in (("핵심 27개 routeId (처치군+간선+중간층)", core_routes),
                            ("외곽 10개 routeId", set(OUTER))):
        gaps = gaps_for(routenos)
        if not gaps:
            continue
        out("## %s" % label)
        out("")
        p25 = gaps[int(len(gaps) * 0.25)]
        p50 = statistics.median(gaps)
        p75 = gaps[int(len(gaps) * 0.75)]
        out("- 정류장 간 거리: 25%% %.0fm / 중앙값 **%.0fm** / 75%% %.0fm / 평균 %.0fm"
            % (p25, p50, p75, statistics.mean(gaps)))
        out("- 구간 수: %d개" % len(gaps))
        out("")
        out("| 표정속도 | 중앙 구간 소요 | 25% 구간 소요 | 주기 150초면 건너뛰는 정류장 | 주기 300초면 |")
        out("|---|---|---|---|---|")
        for kmh in SPEEDS_KMH:
            mps = kmh * 1000.0 / 3600.0
            t50 = p50 / mps
            t25 = p25 / mps
            skip150 = 150.0 / t50
            skip300 = 300.0 / t50
            out("| %dkm/h | %.0f초 | %.0f초 | %.1f개 | %.1f개 |"
                % (kmh, t50, t25, skip150, skip300))
        out("")

    # 예산 시나리오
    out("## 호출 예산 시나리오 (일 한도 10,000건)")
    out("")
    core_ids = sum(1 for rid in by_route if routeno_of[rid] in core_routes)
    outer_ids = sum(1 for rid in by_route if routeno_of[rid] in set(OUTER))
    out("- 핵심 routeId %d개 / 외곽 routeId %d개 / 합계 %d개"
        % (core_ids, outer_ids, core_ids + outer_ids))
    out("")

    def cycles(minutes, interval_sec):
        return int(minutes * 60 / interval_sec)

    out("### 시나리오 1 — 전 노선 균일 주기")
    out("")
    out("| 주기 | 05:30~23:00 순회 | 일 호출량 | 한도 대비 |")
    out("|---|---|---|---|")
    for iv in (150, 180, 234, 300):
        c = cycles(1050, iv)
        total = (core_ids + outer_ids) * c
        out("| %d초 | %d회 | %s건 | %s |"
            % (iv, c, format(total, ","),
               "초과 %.2f배" % (total / 10000.0) if total > 10000 else "여유 %s건" % format(10000 - total, ",")))
    out("")
    out("균일 주기로는 234초가 상한이다. 위 표대로면 정류장을 2~4개씩 건너뛴다.")
    out("")

    out("### 시나리오 2 — 첨두 집중 + 외곽 분리 (권장)")
    out("")
    plan_core = [
        ("07:00~09:00 오전 첨두", 120, 60),
        ("17:00~19:00 오후 첨두", 120, 60),
        ("09:00~17:00 주간 비첨두", 480, 600),
        ("05:30~07:00, 19:00~23:00 주변시간", 330, 600),
    ]
    out("**핵심 %d개 routeId**" % core_ids)
    out("")
    out("| 시간대 | 길이(분) | 주기 | 순회 | 호출량 |")
    out("|---|---|---|---|---|")
    core_total = 0
    for label, minutes, iv in plan_core:
        c = cycles(minutes, iv)
        calls = c * core_ids
        core_total += calls
        out("| %s | %d | %d초 | %d회 | %s건 |" % (label, minutes, iv, c, format(calls, ",")))
    out("")
    out("- 핵심 소계: **%s건**" % format(core_total, ","))
    out("")

    out("**외곽 %d개 routeId** (하루 2~3회만 운행 — 대부분의 조회가 빈 응답)" % outer_ids)
    out("")
    outer_c = cycles(840, 900)  # 06:00~20:00, 15분
    outer_total = outer_c * outer_ids
    out("- 06:00~20:00 을 900초(15분) 주기로 순회: %d회 × %d개 = **%s건**"
        % (outer_c, outer_ids, format(outer_total, ",")))
    out("")

    grand = core_total + outer_total
    out("- **합계 %s건 / 한도 10,000건 — 재시도 여유 %s건 (%.1f%%)**"
        % (format(grand, ","), format(10000 - grand, ","), 100.0 * (10000 - grand) / 10000))
    out("")
    out("첨두시간 주기가 60초로 내려가 기존 계획(균일 150초)보다 오히려 2.5배 조밀해진다.")
    out("지연 예측 모델이 실제로 필요로 하는 시간대에 해상도를 몰아주는 방식이다.")
    out("")

    os.makedirs(LOGS_DIR, exist_ok=True)
    with io.open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("저장: %s" % OUT_PATH)


if __name__ == "__main__":
    main()
