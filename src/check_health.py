"""수집 건강 점검 - 언제든 실행해 수집이 제대로 되고 있는지 본다.

3주짜리 상시 수집에서 가장 무서운 건 '조용히 멈춰 있는' 상태다.
로그를 눈으로 훑는 대신 이걸 돌려 빈 구간과 실패율을 확인한다.

    python src/check_health.py          # 오늘
    python src/check_health.py 20260731 # 특정 날짜
    python src/check_health.py all      # 전체 기간 요약
"""

import csv
import glob
import io
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

from config import DATA_DIR, LOGS_DIR

REALTIME_DIR = os.path.join(DATA_DIR, "realtime")
MODES = {"core": 60, "network": 600}   # 모드별 기대 주기(초)
DAY_START, DAY_END = "05:30", "01:00"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_lines = []


def out(text=""):
    print(text)
    _lines.append(text)


def hhmm(t):
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def load_day(mode, day):
    path = os.path.join(REALTIME_DIR, "%s_%s.csv" % (mode, day))
    if not os.path.exists(path):
        return None, path
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f)), path


def analyse(mode, day, interval):
    rows, path = load_day(mode, day)
    out("### %s 모드 — %s" % (mode, day))
    out("")
    if rows is None:
        out("- 파일 없음: `%s`" % os.path.basename(path))
        out("")
        return
    if not rows:
        out("- 파일은 있으나 데이터가 없습니다.")
        out("")
        return

    stamps = sorted({r["ts"] for r in rows})
    times = [datetime.fromisoformat(s) for s in stamps]

    out("- 총 행: %s개 | 고유 차량 %d대 | 노선 %d개"
        % (format(len(rows), ","),
           len({r["vehicleno"] for r in rows}),
           len({r["routeno"] for r in rows})))
    out("- 관측 시각: %s ~ %s" % (times[0].strftime("%H:%M:%S"), times[-1].strftime("%H:%M:%S")))

    # 순회 사이 간격이 기대 주기보다 크게 벌어진 구간을 찾는다.
    # 한 순회 안에서도 호출별로 시각이 다르므로, 주기의 절반으로 묶어 순회 단위로 본다
    bucket = max(interval // 2, 5)
    sweeps = []
    for t in times:
        if not sweeps or (t - sweeps[-1]).total_seconds() > bucket:
            sweeps.append(t)

    gaps = []
    for a, b in zip(sweeps, sweeps[1:]):
        gap = (b - a).total_seconds()
        if gap > interval * 2.5:
            gaps.append((a, b, gap))

    out("- 순회 횟수: %d회" % len(sweeps))
    if len(sweeps) > 1:
        spans = [(b - a).total_seconds() for a, b in zip(sweeps, sweeps[1:])]
        spans.sort()
        out("- 순회 간격: 중앙값 %.0f초 (기대 %d초)" % (spans[len(spans) // 2], interval))

    if gaps:
        total_lost = sum(g for _, _, g in gaps)
        out("- **빈 구간 %d곳, 합계 %.0f분**" % (len(gaps), total_lost / 60))
        out("")
        out("| 시작 | 끝 | 공백 |")
        out("|---|---|---|")
        for a, b, g in gaps[:15]:
            out("| %s | %s | %.0f분 |"
                % (a.strftime("%H:%M:%S"), b.strftime("%H:%M:%S"), g / 60))
        if len(gaps) > 15:
            out("")
            out("... 외 %d곳" % (len(gaps) - 15))
    else:
        out("- 빈 구간 없음")

    # 수집 시간대 대비 커버리지
    expected_min = (24 * 60 - hhmm(DAY_START)) + hhmm(DAY_END)
    covered = (times[-1] - times[0]).total_seconds() / 60
    lost = sum(g for _, _, g in gaps) / 60
    out("- 수집 시간대 %d분 중 관측 구간 %.0f분, 그중 공백 %.0f분 → **가동률 %.1f%%**"
        % (expected_min, covered, lost,
           100.0 * max(0.0, covered - lost) / expected_min))
    out("")


def check_alerts():
    found = []
    for mode in MODES:
        path = os.path.join(LOGS_DIR, "ALERT_%s.txt" % mode)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                found.append((mode, f.read().strip()))
    out("## 경보")
    out("")
    if not found:
        out("- 활성 경보 없음")
    for mode, text in found:
        out("**%s 모드 경보**" % mode)
        out("")
        out("```")
        out(text)
        out("```")
    out("")


def check_quota(day):
    out("## 호출량")
    out("")
    out("| 모드 | %s 사용 |" % day)
    out("|---|---|")
    import json
    for mode in MODES:
        path = os.path.join(LOGS_DIR, "quota_%s_%s.json" % (mode, day))
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    out("| %s | %s건 |" % (mode, format(int(json.load(f)["count"]), ",")))
                continue
            except (ValueError, OSError, KeyError):
                pass
        out("| %s | 기록 없음 |" % mode)
    out("")
    out("일일 한도 500,000건")
    out("")


def summarize_all():
    out("## 전체 기간 요약")
    out("")
    out("| 날짜 | core 행 | network 행 |")
    out("|---|---|---|")
    days = set()
    for path in glob.glob(os.path.join(REALTIME_DIR, "*_*.csv")):
        name = os.path.basename(path)
        days.add(name.rsplit("_", 1)[1].replace(".csv", ""))
    total = defaultdict(int)
    for day in sorted(days):
        counts = {}
        for mode in MODES:
            rows, _ = load_day(mode, day)
            n = len(rows) if rows else 0
            counts[mode] = n
            total[mode] += n
        out("| %s | %s | %s |"
            % (day, format(counts["core"], ","), format(counts["network"], ",")))
    out("")
    out("- 수집 일수: %d일 | core 누적 %s행 | network 누적 %s행"
        % (len(days), format(total["core"], ","), format(total["network"], ",")))
    out("")


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y%m%d")

    out("# 수집 건강 점검")
    out("")
    out("점검 시각: %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    out("")

    check_alerts()

    if arg == "all":
        summarize_all()
    else:
        check_quota(arg)
        out("## 수집 상태")
        out("")
        for mode, interval in MODES.items():
            analyse(mode, arg, interval)

    path = os.path.join(LOGS_DIR, "health_report.md")
    try:
        os.makedirs(LOGS_DIR, exist_ok=True)
        with io.open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(_lines) + "\n")
        print("리포트 저장: %s" % path)
    except OSError:
        pass


if __name__ == "__main__":
    main()
