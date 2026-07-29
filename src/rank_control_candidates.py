"""DID 대조군 후보 순위화.

표본 22개 노선 중 처치군을 제외한 전 노선에 대해, 처치군과의 권역 중복도를
정류소명 기준으로 계산해 순위를 매긴다.

주의: 여기서 재는 것은 '같은 수요 권역을 다투는가' 뿐이다. 대조군의 나머지
요건인 '2024.1 개편으로 변경되지 않았을 것'은 정보공개청구 회신(개편 전후
노선대응표)이 와야 확정할 수 있다. 이 순위는 회신 전 잠정안이다.
"""

import csv
import io
import os
from collections import defaultdict

from config import DATA_DIR, LOGS_DIR

STOPS_CSV = os.path.join(DATA_DIR, "route_stops.csv")
OUT_PATH = os.path.join(LOGS_DIR, "control_candidates_rank.md")

TREATMENT = ["5", "405", "80", "85", "88"]
SAMPLE_NON_TREATMENT = [
    "6", "400", "11", "2", "3", "121", "90", "800", "7",   # 도심 간선
    "900", "910", "140",                                    # 중간층
    "402", "414", "82", "221", "601",                       # 외곽
]


def main():
    with open(STOPS_CSV, encoding="utf-8-sig") as f:
        stops = list(csv.DictReader(f))

    by_no_names = defaultdict(set)
    for s in stops:
        by_no_names[s["routeno"]].add(s["nodenm"])

    # 처치군은 개별 노선과 합집합 양쪽 모두 필요하다
    treat_all = set()
    for no in TREATMENT:
        treat_all |= by_no_names.get(no, set())

    lines = []

    def out(t=""):
        lines.append(t)

    out("# DID 대조군 후보 순위 (잠정)")
    out("")
    out("- 판정 기준: 정류소**명** 기준 공유율 (nodeid 파편화 보정)")
    out("- 처치군: %s번 / 고유 정류소명 %d개" % (", ".join(TREATMENT), len(treat_all)))
    out("")
    out("> 이 순위는 '같은 수요 권역인가'만 잰 것이다. 대조군의 또 다른 필수 요건인")
    out("> '2024.1 개편으로 변경되지 않았을 것'은 정보공개청구 회신(개편 전후 노선")
    out("> 대응표)으로만 확정할 수 있다. 회신 전까지는 잠정안으로만 쓴다.")
    out("")

    rows = []
    for no in SAMPLE_NON_TREATMENT:
        names = by_no_names.get(no, set())
        if not names:
            continue
        shared = names & treat_all
        # Jaccard 는 노선 길이 차이에 덜 휘둘린다
        union = names | treat_all
        jaccard = 100.0 * len(shared) / len(union) if union else 0.0
        rate = 100.0 * len(shared) / len(names)
        rows.append((no, len(names), len(shared), rate, jaccard))

    rows.sort(key=lambda x: -x[3])

    out("## 공유율 순위")
    out("")
    out("| 순위 | 노선 | 정류소 수 | 처치군과 공유 | 공유율 | 자카드 | 판정 |")
    out("|---|---|---|---|---|---|---|")
    for i, (no, n, sh, rate, jac) in enumerate(rows, 1):
        if rate >= 30:
            fit = "**적합**"
        elif rate >= 15:
            fit = "보통"
        elif rate >= 5:
            fit = "약함"
        else:
            fit = "부적합"
        out("| %d | %s | %d | %d개 | **%.1f%%** | %.1f%% | %s |"
            % (i, no, n, sh, rate, jac, fit))
    out("")

    # 처치군 개별 노선별로 가장 잘 맞는 대조군을 찾는 편이 실무적으로 유용하다
    out("## 처치군 노선별 최적 대조군")
    out("")
    out("개별 처치 노선마다 권역이 다르므로, 노선별로 짝을 지어야 DID 가 성립한다.")
    out("")
    out("| 처치 노선 | 정류소 수 | 1순위 대조군 | 공유율 | 2순위 | 공유율 |")
    out("|---|---|---|---|---|---|")
    for t in TREATMENT:
        t_names = by_no_names.get(t, set())
        if not t_names:
            continue
        scored = []
        for no in SAMPLE_NON_TREATMENT:
            c_names = by_no_names.get(no, set())
            if not c_names:
                continue
            shared = t_names & c_names
            rate = 100.0 * len(shared) / len(t_names)
            scored.append((no, rate, len(shared)))
        scored.sort(key=lambda x: -x[1])
        first = scored[0] if scored else ("-", 0.0, 0)
        second = scored[1] if len(scored) > 1 else ("-", 0.0, 0)
        out("| %s | %d | **%s** | %.1f%% (%d개) | %s | %.1f%% (%d개) |"
            % (t, len(t_names), first[0], first[1], first[2],
               second[0], second[1], second[2]))
    out("")

    os.makedirs(LOGS_DIR, exist_ok=True)
    with io.open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("저장: %s" % OUT_PATH)


if __name__ == "__main__":
    main()
