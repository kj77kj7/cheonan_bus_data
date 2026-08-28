# -*- coding: utf-8 -*-
"""일자별 수집 완전성 판정.

판정은 두 축을 같이 본다.
  1) 순회 수  — 수집기가 얼마나 돌았는가 (로그의 '순회 완료' 시각으로 복원)
  2) 행/순회  — 돈 만큼 실제로 저장됐는가
2번을 안 보면 08-01 처럼 순회 1167회를 돌고도 Writer 버그로 0행이 저장된 날이
'온전' 으로 잡힌다.
"""
import csv, glob, io, os, re, sys
from datetime import datetime, timedelta

sys.path.insert(0, "src")
from config import DATA_DIR, LOGS_DIR

RT = os.path.join(DATA_DIR, "realtime")
SWEEP = re.compile(r"^\[(\d\d)-(\d\d) (\d\d):(\d\d):(\d\d)\].*순회 완료.*\|\s*([\d.]+)초")

# 수집 시간대 05:30~다음날 01:00 → 한 달력일에 00:00~01:00(60분) + 05:30~24:00(1110분)
FULL_MIN = 1170
EXPECT_CORE = FULL_MIN * 60 // 60          # 1170 회
MIN_ROWS_PER_SWEEP = 20                    # 정상일은 순회당 약 50행
TODAY = datetime.now().strftime("%Y%m%d")


def sweep_count(mode, day):
    p = os.path.join(LOGS_DIR, "collect_%s_%s.log" % (mode, day))
    if not os.path.exists(p):
        return 0
    n = 0
    with io.open(p, encoding="utf-8", errors="replace") as f:
        for line in f:
            if SWEEP.match(line):
                n += 1
    return n


def rows_of(mode, day):
    p = os.path.join(RT, "%s_%s.csv" % (mode, day))
    if not os.path.exists(p):
        return 0, None, None
    n, first, last = 0, None, None
    with io.open(p, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            n += 1
            t = r["ts"]
            if first is None or t < first:
                first = t
            if last is None or t > last:
                last = t
    return n, first, last


days = sorted({os.path.basename(p).rsplit("_", 1)[1][:8]
               for p in glob.glob(os.path.join(RT, "*_*.csv"))})

print("%-10s %-3s %9s %9s %6s %7s %7s  %-13s %s" %
      ("날짜", "요일", "core행", "net행", "순회", "가동률", "행/순회", "관측(core)", "판정"))
print("-" * 94)

full, partial, lost = [], [], []
notes = {}
for day in days:
    d = datetime.strptime(day, "%Y%m%d")
    dow = "월화수목금토일"[d.weekday()]
    cn, cf, cl = rows_of("core", day)
    nn, _, _ = rows_of("network", day)
    ns = sweep_count("core", day)
    rate = 100.0 * ns / EXPECT_CORE
    per = cn / float(ns) if ns else 0.0
    span = ("%s~%s" % (cf[11:16], cl[11:16])) if cf and cl else "-"

    if day == TODAY:
        verdict, bucket = "진행중", partial
        notes[day] = "오늘 (수집 중)"
    elif ns and per < MIN_ROWS_PER_SWEEP and rate >= 95:
        # 하루 종일 돌았는데 저장이 안 된 경우에만 '저장 실패' 다.
        # 가동률이 낮으면서 행이 적은 건, 남은 순회가 버스 안 다니는 심야라서다.
        verdict, bucket = "**유실**", lost
        notes[day] = "수집기는 %d회(가동률 %.0f%%) 돌았으나 저장 0행 — 저장 실패" % (ns, rate)
    elif rate >= 95:
        verdict, bucket = "온전", full
    elif rate >= 40:
        verdict, bucket = "부분", partial
    else:
        verdict, bucket = "**유실**", lost
        notes[day] = "가동률 %.0f%% — 대부분 미가동 (PC 종료 등)" % rate
    bucket.append(day)

    print("%-10s  %s  %9s %9s %6d %6.1f%% %7.1f  %-13s %s" %
          (day, dow, format(cn, ","), format(nn, ","), ns, rate, per, span, verdict))

print("-" * 94)
print("온전 %d일 / 부분 %d일 / 유실 %d일  (총 %d일)"
      % (len(full), len(partial), len(lost), len(days)))
print()
print("# 분석에 쓸 날짜 화이트리스트")
print("FULL_DAYS = [")
for i in range(0, len(full), 6):
    print("    " + ", ".join('"%s"' % x for x in full[i:i+6]) + ",")
print("]")
print()
print("부분: " + ", ".join(partial))
print("유실: " + ", ".join(lost))
for k, v in sorted(notes.items()):
    print("  - %s: %s" % (k, v))

tot_c = sum(rows_of("core", d)[0] for d in full)
tot_n = sum(rows_of("network", d)[0] for d in full)
print()
print("온전한 %d일 합계: core %s행 / network %s행"
      % (len(full), format(tot_c, ","), format(tot_n, ",")))
