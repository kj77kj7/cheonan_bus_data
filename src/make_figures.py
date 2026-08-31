"""분석 결과를 리포트용 그림(SVG)으로 그린다.

matplotlib 을 쓰지 않는다. 이 저장소는 표준 라이브러리만으로 돌아가는 것이
강점이고, SVG 는 벡터라 인쇄에도 문서 삽입에도 그대로 쓸 수 있다. 브라우저
에서 바로 열리고, 마크마다 <title> 을 달아 두면 올려놨을 때 값도 뜬다.

그림은 셋이다.

    fig1  공표 배차 vs 실측 배차   — "40분이라더니 57분"
    fig2  중앙값에서 P90 까지      — "열 번에 한 번은 여기까지 기다린다"
    fig3  증차 1억원당 돌려주는 시간 — 개선 우선순위

색은 두 계열만 쓴다. 파랑이 실측(우리가 잰 것), 주황이 공표(천안시가 말한
것)다. 이 조합은 색각 이상과 명도 대비 검사를 통과한 값이다. 계열이 둘이라
범례를 두고, 넷 이하라 직접 라벨도 함께 단다. 숫자와 글자는 먹색을 쓴다 —
계열 색을 글자에 입히면 색만으로 뜻을 나르게 된다.

사용법
    python src/make_figures.py
"""

import csv
import os
import sys

from config import DATA_DIR

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROC_DIR = os.path.join(DATA_DIR, "processed")
HEADWAY_CSV = os.path.join(PROC_DIR, "headway_by_route.csv")
PRIORITY_CSV = os.path.join(PROC_DIR, "priority.csv")
OUT_DIR = os.path.join(DATA_DIR, "figures")

# 검증을 통과한 값이다. 눈으로 고르지 말 것.
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SOFT = "#52514e"
INK_MUTED = "#86857f"
GRID = "#e8e7e3"
ACTUAL = "#2a78d6"      # 실측 — 우리가 잰 것
OFFICIAL = "#eb6834"    # 공표 — 천안시가 말한 것

FONT = ("'Malgun Gothic','Apple SD Gothic Neo','Noto Sans KR',"
        "'Nanum Gothic',sans-serif")

TOP_N = 12              # 한 그림에 담을 노선 수
BAR = 11                # 막대 두께
GROUP_GAP = 8           # 노선 사이 여백
PAD_L, PAD_R, PAD_T, PAD_B = 92, 108, 64, 48
PAD_R_WIDE = 176   # 오른쪽 주석이 긴 그림용


def esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def read_csv(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def day_value(row, name):
    """평시(07~21시) 값이 있으면 그것을, 없으면 전 시간대 값을 쓴다.

    심야의 긴 간격을 최악값으로 세면 과장이다. build_headway.py 가
    median_day_min / p90_day_min 을 내므로 그쪽을 먼저 본다.
    """
    return num(row.get(name + "_day_min")) or num(row.get(name + "_min"))


def by_route(rows, key="routeno"):
    """같은 노선번호를 한 줄로 합친다.

    상·하행이 별도 routeid 라 headway_by_route.csv 에는 같은 번호가 두 줄
    나온다. 그대로 그리면 910번이 63분과 62분으로 두 번 서서 표가 지저분해
    지고 순위도 밀린다. 표본이 큰 쪽을 그 노선의 대표로 둔다.
    """
    best = {}
    for row in rows:
        name = str(row.get(key, "")).strip()
        n = num(row.get("n_headway"), 0) or 0
        if name not in best or n > best[name][0]:
            best[name] = (n, row)
    return [row for _, row in best.values()]


def num(text, default=None):
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return default


def nice_ticks(top, count=5):
    """0 에서 top 까지 읽기 좋은 눈금. 축 숫자가 87.3 이면 곤란하다."""
    if top <= 0:
        return [0], 1
    raw = top / float(count)
    magnitude = 10 ** (len(str(int(raw))) - 1) if raw >= 1 else 0.1
    for factor in (1, 1.5, 2, 2.5, 3, 4, 5, 10):
        step = magnitude * factor
        if step >= raw:
            break
    # 딱 떨어지면 한 칸 더 붙이지 않는다. 그만큼 오른쪽이 비어 보인다.
    upper = step * -(-top // step)
    ticks = []
    value = 0.0
    while value <= upper + 1e-9:
        ticks.append(value)
        value += step
    return ticks, upper


def svg_open(width, height, title):
    return [
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
        'viewBox="0 0 %d %d" role="img" aria-label="%s">'
        % (width, height, width, height, esc(title)),
        '<style>text{font-family:%s;dominant-baseline:middle}'
        '.t{font-size:17px;font-weight:600;fill:%s}'
        '.s{font-size:12.5px;fill:%s}'
        '.l{font-size:12.5px;fill:%s}'
        '.v{font-size:12px;font-weight:600;fill:%s}'
        '.a{font-size:11.5px;fill:%s}</style>'
        % (FONT, INK, INK_SOFT, INK, INK, INK_MUTED),
        '<rect width="%d" height="%d" fill="%s"/>' % (width, height, SURFACE),
    ]


def axis(parts, ticks, upper, x0, plot_w, y0, plot_h, unit):
    for tick in ticks:
        x = x0 + plot_w * tick / upper
        parts.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="%s" '
                     'stroke-width="1"/>' % (x, y0, x, y0 + plot_h, GRID))
        label = ("%g" % tick) + (unit if tick == ticks[-1] else "")
        parts.append('<text class="a" x="%.1f" y="%d" text-anchor="middle">%s</text>'
                     % (x, y0 + plot_h + 16, esc(label)))


def legend(parts, x, y, items):
    """계열이 둘 이상이면 범례는 늘 있어야 한다. 색만으로 뜻을 나르지 않는다.

    항목은 (색, 이름) 또는 (색, 이름, 불투명도) 다. 흐리게 그린 마크는
    범례도 같은 농도로 찍어야 서로 다른 것으로 보이지 않는다.
    """
    cursor = x
    for item in items:
        color, label = item[0], item[1]
        opacity = item[2] if len(item) > 2 else 1.0
        parts.append('<rect x="%.1f" y="%d" width="11" height="11" rx="2.5" '
                     'fill="%s" opacity="%.2f"/>'
                     % (cursor, y - 5, color, opacity))
        parts.append('<text class="s" x="%.1f" y="%d">%s</text>'
                     % (cursor + 16, y, esc(label)))
        cursor += 16 + len(label) * 7.4 + 22


def fig_headway_gap(rows, path):
    """공표와 실측을 나란히 놓는다. 계열이 둘이라 범례와 직접 라벨을 함께 단다."""
    data = [r for r in by_route(rows)
            if num(r.get("official_min")) and day_value(r, "median")
            and num(r.get("gap_vs_official"), 0) > 0]
    data.sort(key=lambda r: -num(r["gap_vs_official"], 0))
    data = data[:TOP_N]
    if not data:
        return None

    row_h = BAR * 2 + GROUP_GAP
    plot_h = row_h * len(data)
    width, height = 760, PAD_T + plot_h + PAD_B
    top = max(day_value(r, "median") for r in data)
    ticks, upper = nice_ticks(top)
    plot_w = width - PAD_L - PAD_R

    parts = svg_open(width, height, "노선별 공표 배차와 실측 배차")
    parts.append('<text class="t" x="%d" y="26">공표 배차와 실제 배차</text>' % PAD_L)
    parts.append('<text class="s" x="%d" y="46">천안시가 안내하는 값과, '
                 '3주간 60초 간격으로 관측한 값</text>' % PAD_L)
    legend(parts, PAD_L, height - 20,
           [(OFFICIAL, "공표 배차"), (ACTUAL, "실측 중앙값")])
    axis(parts, ticks, upper, PAD_L, plot_w, PAD_T, plot_h, "분")

    for i, row in enumerate(data):
        y = PAD_T + i * row_h + GROUP_GAP / 2
        official, actual = num(row["official_min"]), day_value(row, "median")
        parts.append('<text class="l" x="%d" y="%.1f" text-anchor="end">%s번</text>'
                     % (PAD_L - 12, y + BAR, esc(row["routeno"])))
        for value, color, name, offset in ((official, OFFICIAL, "공표", 0),
                                           (actual, ACTUAL, "실측", BAR)):
            w = plot_w * value / upper
            parts.append('<rect x="%d" y="%.1f" width="%.1f" height="%d" rx="4" '
                         'fill="%s"><title>%s번 %s %.0f분</title></rect>'
                         % (PAD_L, y + offset, max(w, 2), BAR - 1, color,
                            esc(row["routeno"]), name, value))
        gap = actual - official
        parts.append('<text class="v" x="%.1f" y="%.1f">%.0f분 → %.0f분</text>'
                     % (PAD_L + plot_w * actual / upper + 9, y + BAR,
                        official, actual))
        parts.append('<text class="a" x="%d" y="%.1f" text-anchor="end">+%.0f분</text>'
                     % (width - 8, y + BAR, gap))

    parts.append("</svg>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    return len(data)


def fig_p90(rows, path):
    """중앙값에서 P90 까지를 한 줄로 그린다.

    평균이 그럭저럭인데 가끔 크게 터지는 것이 이 자료의 핵심이라, 두 값을
    따로 세우는 것보다 사이를 이어 보이는 편이 낫다.
    """
    data = [r for r in by_route(rows)
            if day_value(r, "median") and day_value(r, "p90")
            and num(r.get("n_headway"), 0) >= 100]
    data.sort(key=lambda r: -(day_value(r, "p90") or 0))
    data = data[:TOP_N]
    if not data:
        return None

    row_h = 26
    plot_h = row_h * len(data)
    width, height = 760, PAD_T + plot_h + PAD_B
    ticks, upper = nice_ticks(max(day_value(r, "p90") for r in data))
    plot_w = width - PAD_L - PAD_R

    parts = svg_open(width, height, "노선별 평상시 배차와 최악 배차")
    parts.append('<text class="t" x="%d" y="26">열 번에 한 번은 이만큼 '
                 '기다린다</text>' % PAD_L)
    parts.append('<text class="s" x="%d" y="46">가운데 점이 평상시(중앙값), '
                 '오른쪽 끝이 상위 10%%(P90)</text>' % PAD_L)
    legend(parts, PAD_L, height - 20,
           [(ACTUAL, "평상시 배차"), (INK_MUTED, "P90 까지 벌어지는 구간", 0.5)])
    axis(parts, ticks, upper, PAD_L, plot_w, PAD_T, plot_h, "분")

    for i, row in enumerate(data):
        y = PAD_T + i * row_h + row_h / 2
        median, p90 = day_value(row, "median"), day_value(row, "p90")
        x1 = PAD_L + plot_w * median / upper
        x2 = PAD_L + plot_w * p90 / upper
        parts.append('<text class="l" x="%d" y="%.1f" text-anchor="end">%s번</text>'
                     % (PAD_L - 12, y, esc(row["routeno"])))
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" '
                     'stroke-width="4" stroke-linecap="round" opacity="0.5">'
                     '<title>%s번 %.0f분에서 %.0f분까지</title></line>'
                     % (x1, y, x2, y, INK_MUTED, esc(row["routeno"]), median, p90))
        # 겹치는 마크에는 표면색 링을 둘러 서로 붙어 보이지 않게 한다.
        parts.append('<circle cx="%.1f" cy="%.1f" r="5.5" fill="%s" stroke="%s" '
                     'stroke-width="2"><title>%s번 평상시 %.0f분</title></circle>'
                     % (x1, y, ACTUAL, SURFACE, esc(row["routeno"]), median))
        parts.append('<text class="v" x="%.1f" y="%.1f">%.0f분</text>'
                     % (x2 + 10, y, p90))

    parts.append("</svg>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    return len(data)


def fig_priority(rows, path):
    """계열이 하나라 범례를 두지 않는다. 제목이 무엇인지 말한다.

    '하루 857시간' 같은 총량은 규모는 크지만 아무도 체감하지 못한다. 매일
    타는 사람 한 명이 한 해에 며칠을 더 쓰는지로 바꾸면 곧바로 와닿는다.
    비용도 총액 대신 그 노선을 타는 사람 한 명당으로 적는다 — 정기권 한 달
    값과 견줄 수 있는 자릿수라야 판단이 선다.
    """
    data = [r for r in rows if num(r.get("yearly_days_per_rider"))]
    data.sort(key=lambda r: -num(r["yearly_days_per_rider"], 0))
    data = data[:TOP_N]
    if not data:
        return None

    row_h = 24
    plot_h = row_h * len(data)
    width, height = 820, PAD_T + plot_h + PAD_B - 16
    ticks, upper = nice_ticks(max(num(r["yearly_days_per_rider"], 0) for r in data))
    plot_w = width - PAD_L - PAD_R_WIDE

    parts = svg_open(width, height, "매일 타는 사람이 한 해에 더 쓰는 시간")
    parts.append('<text class="t" x="%d" y="26">매일 이 버스를 타면, '
                 '1년에 이만큼을 정류장에서 더 쓴다</text>' % PAD_L)
    parts.append('<text class="s" x="%d" y="46">공표 배차대로 왔다면 안 기다려도 '
                 '됐을 시간 (왕복 두 번 × 연 250일)</text>' % PAD_L)
    axis(parts, ticks, upper, PAD_L, plot_w, PAD_T, plot_h, "일")

    for i, row in enumerate(data):
        y = PAD_T + i * row_h + 3
        value = num(row["yearly_days_per_rider"], 0)
        hours = num(row["yearly_hours_per_rider"], 0)
        per_rider = num(row["cost_per_rider_won"], 0)
        w = plot_w * value / upper
        parts.append('<text class="l" x="%d" y="%.1f" text-anchor="end">%s번</text>'
                     % (PAD_L - 12, y + BAR / 2, esc(row["route_no"])))
        parts.append('<rect x="%d" y="%.1f" width="%.1f" height="%d" rx="4" '
                     'fill="%s"><title>%s번 — 한 해 %.0f시간(%.1f일), '
                     '버스 %s대 연 %.1f억원</title></rect>'
                     % (PAD_L, y, max(w, 2), BAR + 2, ACTUAL,
                        esc(row["route_no"]), hours, value,
                        row["buses_needed"],
                        num(row["annual_cost_won"], 0) / 1e8))
        parts.append('<text class="v" x="%.1f" y="%.1f">%.1f일</text>'
                     % (PAD_L + w + 9, y + BAR / 2, value))
        parts.append('<text class="a" x="%d" y="%.1f" text-anchor="end">'
                     '고치는 데 1인당 연 %s원</text>'
                     % (width - 8, y + BAR / 2,
                        "{:,.0f}".format(per_rider) if per_rider else "-"))

    parts.append("</svg>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    return len(data)


def main():
    headway = read_csv(HEADWAY_CSV)
    priority = read_csv(PRIORITY_CSV)
    if headway is None or priority is None:
        print("[오류] 분석 결과가 없습니다.")
        print("  - %s" % HEADWAY_CSV)
        print("  - %s" % PRIORITY_CSV)
        print("  python src/build_headway.py 와 src/analyze.py 를 먼저 돌리십시오.")
        return 1

    os.makedirs(OUT_DIR, exist_ok=True)
    jobs = [
        ("fig1_headway_gap.svg", "공표 vs 실측 배차",
         lambda p: fig_headway_gap(headway, p)),
        ("fig2_p90_wait.svg", "평상시와 최악 대기",
         lambda p: fig_p90(headway, p)),
        ("fig3_priority.svg", "개선 우선순위",
         lambda p: fig_priority(priority, p)),
    ]
    made = 0
    for name, label, draw in jobs:
        path = os.path.join(OUT_DIR, name)
        count = draw(path)
        if count:
            print("  %-24s %-18s 노선 %d개" % (name, label, count))
            made += 1
        else:
            print("  %-24s %-18s 그릴 자료가 없습니다" % (name, label))
    print()
    print("그림 %d개를 %s 에 저장했습니다." % (made, OUT_DIR))
    print("브라우저로 열면 막대에 올려 값을 볼 수 있고, 그대로 문서에 넣어도 됩니다.")
    return 0 if made else 1


if __name__ == "__main__":
    sys.exit(main())
