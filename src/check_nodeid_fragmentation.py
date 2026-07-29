"""정류소 ID 파편화 점검.

같은 정류소명이 여러 nodeid 로 쪼개져 있는지 확인한다. 파편화가 심하면
nodeid 교집합으로 계산한 '권역 중복'이 과소평가되므로, 대조군 선정 판정을
정류소명·좌표 기준으로 보정해야 한다.
"""

import csv
import io
import os
from collections import defaultdict

from config import DATA_DIR, LOGS_DIR

STOPS_CSV = os.path.join(DATA_DIR, "route_stops.csv")
OUT_PATH = os.path.join(LOGS_DIR, "nodeid_fragmentation.md")

TREATMENT = ["5", "405", "80", "85", "88"]
CANDIDATES = ["6", "400", "90", "82"]


def main():
    with open(STOPS_CSV, encoding="utf-8-sig") as f:
        stops = list(csv.DictReader(f))

    # 정류소명 -> nodeid 집합
    name_to_ids = defaultdict(set)
    id_to_name = {}
    for s in stops:
        name_to_ids[s["nodenm"]].add(s["nodeid"])
        id_to_name[s["nodeid"]] = s["nodenm"]

    lines = []

    def out(t=""):
        lines.append(t)

    out("# 정류소 ID 파편화 점검")
    out("")
    unique_names = len(name_to_ids)
    unique_ids = len(id_to_name)
    out("- 고유 정류소명: %d개" % unique_names)
    out("- 고유 nodeid: %d개" % unique_ids)
    out("- 이름당 평균 nodeid: %.2f개" % (unique_ids / float(unique_names)))
    out("")

    multi = {n: ids for n, ids in name_to_ids.items() if len(ids) > 1}
    out("- 2개 이상 nodeid 를 가진 정류소명: **%d개** (전체의 %.1f%%)"
        % (len(multi), 100.0 * len(multi) / unique_names))
    out("")
    out("보통 상·하행 양쪽 승강장이 별도 ID 를 갖기 때문에 2개까지는 정상이다.")
    out("3개 이상이면 회차지·환승센터처럼 승강장이 여러 개인 거점일 가능성이 크다.")
    out("")

    heavy = sorted(((n, ids) for n, ids in multi.items() if len(ids) >= 3),
                   key=lambda x: -len(x[1]))
    out("### nodeid 3개 이상인 정류소 (상위 20개)")
    out("")
    out("| 정류소명 | nodeid 수 | nodeid |")
    out("|---|---|---|")
    for n, ids in heavy[:20]:
        out("| %s | %d | %s |" % (n, len(ids), ", ".join(sorted(ids))))
    out("")

    # 노선번호 -> 정류소명 집합 / nodeid 집합
    by_no_names = defaultdict(set)
    by_no_ids = defaultdict(set)
    for s in stops:
        by_no_names[s["routeno"]].add(s["nodenm"])
        by_no_ids[s["routeno"]].add(s["nodeid"])

    treat_names = set()
    treat_ids = set()
    for no in TREATMENT:
        treat_names |= by_no_names.get(no, set())
        treat_ids |= by_no_ids.get(no, set())

    out("## 대조군 후보 재판정 — nodeid 기준 vs 정류소명 기준")
    out("")
    out("| 후보 | 정류소명 수 | nodeid 공유 | 이름 공유 | nodeid 공유율 | 이름 공유율 |")
    out("|---|---|---|---|---|---|")
    result = {}
    for no in CANDIDATES:
        names = by_no_names.get(no, set())
        ids = by_no_ids.get(no, set())
        shared_names = names & treat_names
        shared_ids = ids & treat_ids
        rate_id = 100.0 * len(shared_ids) / len(ids) if ids else 0.0
        rate_nm = 100.0 * len(shared_names) / len(names) if names else 0.0
        result[no] = (shared_names, rate_nm)
        out("| %s | %d | %d개 | **%d개** | %.1f%% | **%.1f%%** |"
            % (no, len(names), len(shared_ids), len(shared_names), rate_id, rate_nm))
    out("")

    out("### 이름 기준으로 새로 드러난 공유 정류소")
    out("")
    for no in CANDIDATES:
        shared_names, rate = result[no]
        ids = by_no_ids.get(no, set())
        shared_ids_names = {id_to_name[i] for i in (ids & treat_ids)}
        newly = sorted(shared_names - shared_ids_names)
        out("**%s번** — 이름 공유 %d개 중 nodeid 로는 안 잡히던 것 %d개"
            % (no, len(shared_names), len(newly)))
        out("")
        if newly:
            for n in newly[:25]:
                out("- %s" % n)
            if len(newly) > 25:
                out("- ... 외 %d개" % (len(newly) - 25))
        else:
            out("- 없음")
        out("")

    os.makedirs(LOGS_DIR, exist_ok=True)
    with io.open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("저장: %s" % OUT_PATH)


if __name__ == "__main__":
    main()
