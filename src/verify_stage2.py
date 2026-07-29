"""단계 2 검증 - 수집 결과 품질 점검 및 표본 확정.

A. 데이터 품질 (수집률·좌표 결측·범위 이탈·nodeord 정합·정류장 수 분포)
B. 실시간 표본 22개 노선의 routeId 합계 확인 (단계 3 호출량 산정의 전제)
C. 극단 배차 노선(402 등)의 배차값 진위 확인
D. DID 대조군 후보와 처치군의 권역 중복(공유 정류소) 확인

결과는 콘솔과 logs/stage2_report.md 에 동시 출력한다.
"""

import csv
import os
import statistics
import sys
from collections import defaultdict

from config import DATA_DIR, LOGS_DIR

# 윈도우 콘솔(cp949)이 일부 문자를 못 찍어 죽는 것을 막는다. 리포트 파일은 UTF-8.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

STOPS_CSV = os.path.join(DATA_DIR, "route_stops.csv")
ROUTES_CSV = os.path.join(DATA_DIR, "routes_34010.csv")
DETAIL_CSV = os.path.join(DATA_DIR, "cheonan_routes_detail.csv")
REPORT_PATH = os.path.join(LOGS_DIR, "stage2_report.md")

# 천안시 대략 좌표 범위
LAT_MIN, LAT_MAX = 36.6, 37.05
LON_MIN, LON_MAX = 127.0, 127.5

SAMPLE_GROUPS = [
    ("처치군", ["5", "405", "80", "85", "88"]),
    ("도심 간선", ["6", "400", "11", "2", "3", "121", "90", "800", "7"]),
    ("중간층", ["900", "910", "140"]),
    ("외곽", ["402", "414", "82", "221", "601"]),
]
EXPECTED_SAMPLE_ROUTEIDS = 43  # 사전 가정값. 실제와 다르면 경고한다.

EXTREME_ROUTES = ["402", "414", "82", "221", "601"]
CONTROL_CANDIDATES = ["6", "400", "90", "82"]
TREATMENT_ROUTES = ["5", "405", "80", "85", "88"]

_lines = []


def out(text=""):
    print(text)
    _lines.append(text)


def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def hhmm_to_min(value):
    """'0605' -> 365. 파싱 불가면 None."""
    v = (value or "").strip()
    if len(v) != 4 or not v.isdigit():
        return None
    return int(v[:2]) * 60 + int(v[2:])


def load_stops():
    with open(STOPS_CSV, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load_detail():
    with open(DETAIL_CSV, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def section_a(stops, all_route_ids):
    out("## A. 데이터 품질")
    out("")

    by_route = defaultdict(list)
    for r in stops:
        by_route[r["routeid"]].append(r)

    collected = set(by_route.keys())
    out("### A-1. 수집률")
    out("")
    out("- 수집 성공 routeId: **%d / %d개** (%.1f%%)"
        % (len(collected), len(all_route_ids), 100.0 * len(collected) / len(all_route_ids)))
    out("- 총 정류장 행: %d개" % len(stops))
    missing_routes = [r for r in all_route_ids if r not in collected]
    if missing_routes:
        out("- 미수집 routeId %d개: %s" % (len(missing_routes), ", ".join(missing_routes)))
    else:
        out("- 미수집 routeId: 없음")
    out("")

    out("### A-2. 좌표 결측")
    out("")
    missing_coord = []
    for r in stops:
        lat, lon = to_float(r["gpslati"]), to_float(r["gpslong"])
        if lat is None or lon is None or lat == 0 or lon == 0:
            missing_coord.append(r)
    out("- 좌표 결측 또는 0인 행: **%d개**" % len(missing_coord))
    for r in missing_coord[:10]:
        out("  - %s / %s (nodeord %s, nodeid %s) lat=%r lon=%r"
            % (r["routeno"], r["nodenm"], r["nodeord"], r["nodeid"], r["gpslati"], r["gpslong"]))
    if len(missing_coord) > 10:
        out("  - ... 외 %d건" % (len(missing_coord) - 10))
    out("")

    out("### A-3. 좌표 범위 이탈")
    out("")
    out("판정 범위: 위도 %.2f~%.2f, 경도 %.1f~%.1f" % (LAT_MIN, LAT_MAX, LON_MIN, LON_MAX))
    out("")
    outliers = []
    for r in stops:
        lat, lon = to_float(r["gpslati"]), to_float(r["gpslong"])
        if lat is None or lon is None or lat == 0 or lon == 0:
            continue  # A-2 에서 이미 집계
        if not (LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX):
            outliers.append((r, lat, lon))
    out("- 범위 이탈 행: **%d개**" % len(outliers))
    # 이탈이 몰린 노선을 보여주는 편이 원인 파악에 유용하다
    by_routeno = defaultdict(int)
    for r, lat, lon in outliers:
        by_routeno[r["routeno"]] += 1
    if by_routeno:
        out("- 노선별 이탈 건수: " + ", ".join(
            "%s(%d건)" % (no, n) for no, n in sorted(by_routeno.items(), key=lambda x: -x[1])))
        out("")
        out("| 노선 | 정류소 | 위도 | 경도 |")
        out("|---|---|---|---|")
        for r, lat, lon in outliers[:15]:
            out("| %s | %s | %.5f | %.5f |" % (r["routeno"], r["nodenm"], lat, lon))
        if len(outliers) > 15:
            out("")
            out("... 외 %d건" % (len(outliers) - 15))
    out("")

    out("### A-4. nodeord 결번·중복")
    out("")
    ord_problems = []
    for rid, rows in by_route.items():
        ords = [to_int(r["nodeord"]) for r in rows]
        ords = [o for o in ords if o is not None]
        if not ords:
            ord_problems.append((rid, rows[0]["routeno"], "nodeord 전부 파싱 불가"))
            continue
        dups = len(ords) - len(set(ords))
        expected = set(range(min(ords), max(ords) + 1))
        gaps = sorted(expected - set(ords))
        notes = []
        if dups:
            notes.append("중복 %d건" % dups)
        if gaps:
            notes.append("결번 %d개(%s%s)"
                         % (len(gaps), ", ".join(str(g) for g in gaps[:5]),
                            " ..." if len(gaps) > 5 else ""))
        if min(ords) != 1:
            notes.append("시작번호 %d" % min(ords))
        if notes:
            ord_problems.append((rid, rows[0]["routeno"], "; ".join(notes)))

    if ord_problems:
        out("- 문제 있는 routeId: **%d개**" % len(ord_problems))
        out("")
        out("| routeId | 노선 | 내용 |")
        out("|---|---|---|")
        for rid, no, note in ord_problems[:25]:
            out("| %s | %s | %s |" % (rid, no, note))
        if len(ord_problems) > 25:
            out("")
            out("... 외 %d개" % (len(ord_problems) - 25))
    else:
        out("- 결번·중복 없음 (모든 노선이 1부터 연속)")
    out("")

    out("### A-5. 노선당 정류장 수 분포")
    out("")
    counts = sorted(len(v) for v in by_route.values())
    out("- 최소 %d개 / 중앙값 %.1f개 / 최대 %d개 / 평균 %.1f개"
        % (counts[0], statistics.median(counts), counts[-1], statistics.mean(counts)))
    tiny = [(rid, rows[0]["routeno"], len(rows))
            for rid, rows in by_route.items() if len(rows) < 5]
    if tiny:
        out("- 정류장 5개 미만 이상 노선: **%d개**" % len(tiny))
        for rid, no, n in sorted(tiny, key=lambda x: x[2]):
            out("  - %s (%s번): %d개" % (rid, no, n))
    else:
        out("- 정류장 5개 미만 노선: 없음")
    out("")

    return by_route


def section_b(detail, by_route):
    out("## B. 실시간 표본 22개 노선 routeId 합계")
    out("")

    detail_by_no = defaultdict(list)
    for r in detail:
        detail_by_no[r["routeno"]].append(r["routeid"])

    out("| 그룹 | 노선 | routeId 수 | 수집 완료 | routeId |")
    out("|---|---|---|---|---|")
    total = 0
    total_routes = 0
    sample_route_ids = []
    for group, routes in SAMPLE_GROUPS:
        for no in routes:
            rids = detail_by_no.get(no, [])
            done = sum(1 for rid in rids if rid in by_route)
            total += len(rids)
            total_routes += 1
            sample_route_ids.extend(rids)
            out("| %s | %s | %d | %d | %s |"
                % (group, no, len(rids), done, ", ".join(rids) if rids else "**없음**"))
    out("")
    out("- 표본 노선번호: **%d개**" % total_routes)
    out("- 표본 routeId 합계: **%d개** (사전 가정 %d개)" % (total, EXPECTED_SAMPLE_ROUTEIDS))
    out("")

    if total != EXPECTED_SAMPLE_ROUTEIDS:
        out("> **경고**: 사전 가정 %d개와 실제 %d개가 다릅니다. 단계 3 일일 호출량을"
            % (EXPECTED_SAMPLE_ROUTEIDS, total))
        out("> 실제값으로 재산정해야 합니다.")
        out("")

    # 단계 3 일일 호출량 재산정: 05:30~23:00 을 150초 주기로 순회
    window_min = (23 * 60) - (5 * 60 + 30)  # 1,050분
    cycles = int(window_min * 60 / 150)      # 420회
    out("### 단계 3 일일 호출량 재산정")
    out("")
    out("- 수집 시간대: 05:30~23:00 (%d분)" % window_min)
    out("- 150초 주기 순회 횟수: %d회/일" % cycles)
    out("- **노선 단위 조회 시 일일 호출량 = %d개 × %d회 = %s건**"
        % (total, cycles, format(total * cycles, ",")))
    quota = 10000
    if total * cycles > quota:
        need = (total * cycles) / float(quota)
        safe_interval = 150 * need
        out("- 개발계정 한도 %s건 **초과** (%.2f배). 주기를 %.0f초 이상으로 늘리거나"
            % (format(quota, ","), need, safe_interval))
        out("  운영계정 상향 신청이 필요합니다.")
        # 한도 내에 들어오는 주기를 안내
        for cand in (180, 200, 240, 300):
            c = int(window_min * 60 / cand)
            out("  - 주기 %d초 → %s건/일 %s"
                % (cand, format(total * c, ","), "(한도 내)" if total * c <= quota else "(여전히 초과)"))
    else:
        out("- 개발계정 한도 %s건 대비 여유 %s건 (%.1f%% 사용)"
            % (format(quota, ","), format(quota - total * cycles, ","),
               100.0 * total * cycles / quota))
    out("")
    return sample_route_ids


def section_c(detail, by_route):
    out("## C. 극단 배차 노선 진위 확인")
    out("")
    out("운행 시간대 폭 ÷ 배차간격 + 1 = 이론상 일일 운행 횟수. 2~3회로 나오면")
    out("하루 몇 번만 다니는 벽지 노선이 맞고 데이터 오류가 아니다.")
    out("")
    out("| 노선 | routeId | 첫차 | 막차 | 운행폭(분) | 배차(분) | 이론 운행횟수 | 정류장 수 | 판정 |")
    out("|---|---|---|---|---|---|---|---|---|")

    verdicts = {}
    for no in EXTREME_ROUTES:
        rows = [r for r in detail if r["routeno"] == no]
        for r in rows:
            rid = r["routeid"]
            start = hhmm_to_min(r["startvehicletime"])
            end = hhmm_to_min(r["endvehicletime"])
            interval = to_int(r["intervaltime"])
            n_stops = len(by_route.get(rid, []))

            if start is None or end is None or not interval:
                out("| %s | %s | %s | %s | ? | %s | ? | %d | 판정불가 |"
                    % (no, rid, r["startvehicletime"], r["endvehicletime"],
                       r["intervaltime"], n_stops))
                continue

            span = end - start
            if span < 0:
                span += 24 * 60  # 자정 넘김
            trips = span / float(interval) + 1

            if trips <= 3.5 and span >= interval * 0.9:
                verdict = "벽지 노선 확정 — 표본 유지"
            elif span < interval * 0.9:
                verdict = "**데이터 오류 의심** — 운행폭 < 배차"
            else:
                verdict = "재확인 필요"
            verdicts.setdefault(no, []).append(verdict)

            out("| %s | %s | %s | %s | %d | %d | %.1f회 | %d | %s |"
                % (no, rid, r["startvehicletime"], r["endvehicletime"],
                   span, interval, trips, n_stops, verdict))
    out("")

    suspect = [no for no, vs in verdicts.items() if any("오류" in v for v in vs)]
    if suspect:
        out("> **조치 필요**: %s번은 데이터 오류가 의심되므로 표본에서 제외하고" % ", ".join(suspect))
        out("> 대체 노선을 선정해야 합니다.")
    else:
        out("- 판정: 전 노선 벽지 노선으로 확인. H2(공급 부족) 가설의 극단 사례로 표본 유지.")
    out("")


def section_d(detail, by_route):
    out("## D. DID 대조군 후보 권역 확인")
    out("")

    detail_by_no = defaultdict(list)
    for r in detail:
        detail_by_no[r["routeno"]].append(r)

    def stops_of(routeno):
        """노선번호 기준 전 방향 정류소 nodeid 집합과 이름 대응표."""
        node_ids = set()
        names = {}
        for r in detail_by_no.get(routeno, []):
            for s in by_route.get(r["routeid"], []):
                node_ids.add(s["nodeid"])
                names[s["nodeid"]] = s["nodenm"]
        return node_ids, names

    out("### D-1. 기점·종점")
    out("")
    out("| 구분 | 노선 | routeId | 기점 | 종점 |")
    out("|---|---|---|---|---|")
    for label, routes in (("처치군", TREATMENT_ROUTES), ("대조군 후보", CONTROL_CANDIDATES)):
        for no in routes:
            for r in detail_by_no.get(no, []):
                out("| %s | %s | %s | %s | %s |"
                    % (label, no, r["routeid"], r["startnodenm"], r["endnodenm"]))
    out("")

    # 처치군 전체 정류소 합집합
    treat_ids = set()
    treat_names = {}
    for no in TREATMENT_ROUTES:
        ids, names = stops_of(no)
        treat_ids |= ids
        treat_names.update(names)
    out("- 처치군 5개 노선의 고유 정류소: **%d개**" % len(treat_ids))
    out("")

    out("### D-2. 대조군 후보별 처치군과 공유 정류소")
    out("")
    out("| 후보 | 자체 정류소 | 처치군과 공유 | 공유율 | 대조군 적합성 |")
    out("|---|---|---|---|---|")
    shared_detail = []
    for no in CONTROL_CANDIDATES:
        ids, names = stops_of(no)
        shared = ids & treat_ids
        rate = 100.0 * len(shared) / len(ids) if ids else 0.0
        if len(shared) == 0:
            fit = "**부적합 — 교체 필요**"
        elif rate >= 30:
            fit = "적합 (권역 강하게 중복)"
        elif rate >= 10:
            fit = "보통 (부분 중복)"
        else:
            fit = "약함 (중복 미미)"
        out("| %s | %d개 | **%d개** | %.1f%% | %s |" % (no, len(ids), len(shared), rate, fit))
        shared_detail.append((no, shared, names))
    out("")

    out("### D-3. 공유 정류소 목록")
    out("")
    for no, shared, names in shared_detail:
        out("**%s번** — 공유 %d개" % (no, len(shared)))
        out("")
        if not shared:
            out("- 없음")
        else:
            listed = sorted(shared, key=lambda nid: names.get(nid, ""))
            for nid in listed[:30]:
                out("- %s (%s)" % (names.get(nid, "?"), nid))
            if len(listed) > 30:
                out("- ... 외 %d개" % (len(listed) - 30))
        out("")

    unfit = [no for no, shared, _ in shared_detail if not shared]
    if unfit:
        out("> **조치 필요**: %s번은 처치군과 공유 정류소가 없어 대조군에서 제외하고"
            % ", ".join(unfit))
        out("> 다른 노선으로 교체해야 합니다.")
        out("")


def main():
    if not os.path.exists(STOPS_CSV):
        print("[중단] %s 가 없습니다. fetch_route_stops.py 를 먼저 실행하십시오." % STOPS_CSV)
        return

    stops = load_stops()
    detail = load_detail()

    all_route_ids = []
    with open(ROUTES_CSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            all_route_ids.append(row["routeid"])

    fetched = sorted({r["fetched_at"] for r in stops if r.get("fetched_at")})

    out("# 단계 2 검증 리포트")
    out("")
    out("- 대상 파일: `data/route_stops.csv`")
    out("- 수집 시각 범위: %s ~ %s" % (fetched[0] if fetched else "?", fetched[-1] if fetched else "?"))
    out("- 도시코드: 34010 (천안시)")
    out("- 참고: 이 API(`getRouteAcctoThrghSttnList`)는 `updowncd` 를 제공하지 않는다.")
    out("  상·하행은 routeId 가 이미 분리하고 있어 정보 손실은 없다.")
    out("")

    by_route = section_a(stops, all_route_ids)
    section_b(detail, by_route)
    section_c(detail, by_route)
    section_d(detail, by_route)

    os.makedirs(LOGS_DIR, exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(_lines) + "\n")
    print("")
    print("리포트 저장: %s" % REPORT_PATH)


if __name__ == "__main__":
    main()
