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
import re
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


SWEEP_RE = re.compile(r"^\[(\d\d)-(\d\d) (\d\d):(\d\d):(\d\d)\].*순회 완료.*\|\s*([\d.]+)초")


def sweep_starts(mode, day):
    """순회 시작 시각들을 수집기 로그에서 직접 읽는다.

    CSV 타임스탬프만으로는 순회를 나눌 수 없다. 요청을 주기 전체에 펴면서
    한 순회가 수십~수백 초에 걸치므로, 간격으로 묶는 방식은 한 순회를 둘로
    쪼개 실제보다 짧은 주기를 보고한다. 로그에는 순회 종료 시각과 소요 시간이
    같이 남으므로 (종료 - 소요) 로 시작 시각을 정확히 복원할 수 있다.
    """
    path = os.path.join(LOGS_DIR, "collect_%s_%s.log" % (mode, day))
    if not os.path.exists(path):
        return None
    year = int(day[:4])
    starts = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = SWEEP_RE.match(line)
                if not m:
                    continue
                mon, dd, hh, mi, ss, took = m.groups()
                end = datetime(year, int(mon), int(dd), int(hh), int(mi), int(ss))
                starts.append(end - timedelta(seconds=float(took)))
    except OSError:
        return None
    return starts


def closed_seconds(a, b):
    """[a, b] 중 수집 시간대 밖에 해당하는 초.

    수집 시간대는 05:30~다음날 01:00 이라, 01:00~05:30 은 원래 쉬는 구간이다.
    이걸 빼지 않으면 매일 아침 4시간 반짜리 '빈 구간' 이 유실로 잡힌다.
    """
    end_m, start_m = hhmm(DAY_END), hhmm(DAY_START)   # 60, 330
    total = 0.0
    cur = a.replace(hour=0, minute=0, second=0, microsecond=0)
    while cur <= b:
        lo = max(a, cur + timedelta(minutes=end_m))
        hi = min(b, cur + timedelta(minutes=start_m))
        if hi > lo:
            total += (hi - lo).total_seconds()
        cur += timedelta(days=1)
    return total


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

    # 순회는 로그에서 읽는다. CSV 타임스탬프를 간격으로 묶는 방식은 한 순회가
    # 길어지면(요청을 주기 전체에 펴면 그렇게 된다) 순회를 쪼개 잘못 센다.
    sweeps = sweep_starts(mode, day)
    if sweeps is None:
        out("- 순회 간격: 로그가 없어 계산하지 못했습니다 (`collect_%s_%s.log`)" % (mode, day))
        sweeps = []

    gaps = []
    for a, b in zip(sweeps, sweeps[1:]):
        gap = (b - a).total_seconds()
        # 수집 시간대 밖(01:00~05:30)은 원래 쉬는 구간이라 빼고 본다
        real = gap - closed_seconds(a, b)
        if real > interval * 2.5:
            gaps.append((a, b, real))

    if sweeps:
        out("- 순회 횟수: %d회" % len(sweeps))
    if len(sweeps) > 1:
        spans = sorted((b - a).total_seconds() for a, b in zip(sweeps, sweeps[1:]))
        out("- 순회 간격: 중앙값 %.0f초 (기대 %d초)" % (spans[len(spans) // 2], interval))

    if gaps:
        total_lost = sum(g for _, _, g in gaps)
        out("- **빈 구간 %d곳, 합계 %.0f분** (수집 시간대 밖은 제외)"
            % (len(gaps), total_lost / 60))
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

    # 수집 시간대 대비 커버리지. 쉬는 구간(01:00~05:30)은 분자·분모 양쪽에서 뺀다.
    now = datetime.now()
    midnight = times[0].replace(hour=0, minute=0, second=0, microsecond=0)
    if day == now.strftime("%Y%m%d"):
        # 아직 오지 않은 시간을 분모에 넣으면 하루 종일 가동률이 낮게 보인다
        span_end = now
        label = "지금까지"
    else:
        span_end = midnight + timedelta(days=1)
        label = "하루"
    expected_min = max(
        1.0, ((span_end - midnight).total_seconds() - closed_seconds(midnight, span_end)) / 60)
    # 관측 구간은 순회 기준으로 잰다. 심야·첫차 전에는 운행 차량이 없어 행이
    # 남지 않는데, 행 기준으로 재면 그 시간이 통째로 '수집 안 됨' 으로 보인다.
    # 순회는 차량이 없어도 돌므로 '수집기가 살아 있었는가' 를 정확히 반영한다.
    obs = sweeps if len(sweeps) > 1 else times
    covered = ((obs[-1] - obs[0]).total_seconds()
               - closed_seconds(obs[0], obs[-1])) / 60
    lost = sum(g for _, _, g in gaps) / 60
    # 관측 구간이 날짜 경계에 살짝 걸치면 분자가 분모를 넘을 수 있어 100% 로 자른다
    rate = min(100.0, 100.0 * max(0.0, covered - lost) / expected_min)
    out("- 수집 시간대 %s %.0f분 중 관측 %.0f분, 그중 공백 %.0f분 → **가동률 %.1f%%**"
        % (label, expected_min, covered, lost, rate))
    out("")


def check_alerts():
    # 경보 종류가 늘어도 놓치지 않도록 파일명을 훑는다 (ALERT_core.txt,
    # ALERT_core_write.txt, ...). 모드별로 하나씩만 보면 새 경보를 못 본다.
    found = []
    for path in sorted(glob.glob(os.path.join(LOGS_DIR, "ALERT_*.txt"))):
        try:
            with open(path, encoding="utf-8") as f:
                found.append((os.path.basename(path), f.read().strip()))
        except OSError:
            continue
    out("## 경보")
    out("")
    if not found:
        out("- 활성 경보 없음")
    for name, text in found:
        out("**%s**" % name)
        out("")
        out("```")
        out(text)
        out("```")
    out("")


def check_write_failures(day):
    """저장 실패를 로그에서 직접 센다.

    호출이 성공해도 저장에 실패하면 순회 로그는 '실패 0건' 으로 찍힌다.
    CSV 행 수만 봐서는 '수집이 원래 적은 날' 과 구분되지 않으므로 로그를 본다.
    """
    out("## 저장 실패")
    out("")
    total = 0
    for mode in MODES:
        path = os.path.join(LOGS_DIR, "collect_%s_%s.log" % (mode, day))
        if not os.path.exists(path):
            out("- %s: 로그 없음" % mode)
            continue
        count = 0
        sample = None
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if "쓰기 실패" in line:
                        count += 1
                        if sample is None:
                            sample = line.strip()
        except OSError:
            out("- %s: 로그를 읽지 못했습니다" % mode)
            continue
        total += count
        if count:
            out("- **%s: %s건** — 수집은 됐지만 저장되지 않았습니다" % (mode, format(count, ",")))
            out("  - `%s`" % sample)
        else:
            out("- %s: 없음" % mode)
    if total:
        out("")
        out("> 저장 실패가 있으면 그 시간대 데이터는 남지 않습니다. "
            "디스크 여유 공간과 data/realtime/ 쓰기 권한을 확인하고, "
            "코드 문제로 보이면 scripts/restart_tasks.ps1 로 재시작하십시오.")
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
        check_write_failures(arg)
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
