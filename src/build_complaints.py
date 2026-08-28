"""시내버스 민원 원본을 분석용 표로 정규화한다.

정보공개청구로 받은 엑셀은 연도마다 시트가 나뉘고 표기도 서로 다르다.

- 한 시트에 날짜 값과 '01-02' 같은 글자가 섞여 있다
- 글자 쪽은 연도가 없어 시트 이름에서 가져와야 한다
- '0311'(구분자 없음), '01-29.'(마침표), '04-'(불완전) 같은 표기가 섞인다
- 2026 시트에 2023·2024 날짜가 1,060건 들어 있고 그중 962건이 앞 시트와
  겹친다. 중복을 지우지 않으면 그 연도가 부풀려진다
- 1900-01-01 처럼 있을 수 없는 날짜가 있다
- 종류 표기가 흔들린다 ('운행시간 미준수' / '운행시간미준수')
- 한 건에 여러 종류가 붙기도 한다 ('불친절, 난폭운전')

민원의 절반 이상이 결행·무정차·운행시간미준수·노선단축이다. 이 넷을
'배차 불이행' 으로 묶으면 노선별 서비스 불안정도의 대리지표가 된다.
우리가 실시간 수집으로 재는 번칭률과 견줘 볼 수 있고, 배차간격 변경
이력을 못 받은 자리를 상당 부분 메운다.

산출물
    data/processed/complaints.csv          민원 1건 = 1행
    data/processed/complaints_by_route.csv 노선 × 연도 집계

사용법
    python src/build_complaints.py [엑셀경로]
"""

import csv
import datetime
import os
import re
import sys

from config import DATA_DIR

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_SOURCE = os.path.join(DATA_DIR, "raw",
                              "시내버스 민원건수 등(정보공개청구자료).xlsx")
OUT_DIR = os.path.join(DATA_DIR, "processed")
LONG_CSV = os.path.join(OUT_DIR, "complaints.csv")
BY_ROUTE_CSV = os.path.join(OUT_DIR, "complaints_by_route.csv")

LONG_COLUMNS = ["date", "year", "month", "hour", "route_no", "place",
                "kind_raw", "kind", "category", "sheet"]

# 이보다 이르거나 늦은 날짜는 입력 오류로 본다.
MIN_YEAR, MAX_YEAR = 2022, 2027
BY_ROUTE_COLUMNS = ["route_no", "year", "total", "service_failure",
                    "결행", "무정차", "운행시간미준수", "노선단축", "기타"]

# 표기 흔들림을 하나로 모은다. 왼쪽이 정규화된 이름이다.
KIND_ALIASES = {
    "결행": ["결행"],
    "무정차": ["무정차"],
    "운행시간미준수": ["운행시간미준수", "운행시간 미준수"],
    "노선단축": ["노선단축", "노선 단축"],
    "불친절": ["불친절"],
    "승하차거부": ["승하차거부", "승하차 거부"],
    "난폭운전": ["난폭운전", "난폭 운전"],
    "승하차전출발": ["승하차전출발", "승하차 전 출발"],
    "정류소질서문란": ["정류소질서문란", "정류소 질서문란"],
    "분실물": ["분실물"],
}
# 이 넷이 '버스가 안 오거나 안 선다' 는 이야기다.
SERVICE_FAILURE = {"결행", "무정차", "운행시간미준수", "노선단축"}

SPLIT_RE = re.compile(r"[,/]|\s및\s")


def normalize_kind(raw):
    """표기를 통일하고, 한 건에 여러 종류가 붙었으면 첫 번째를 대표로 쓴다.

    배차 불이행이 섞여 있으면 그쪽을 대표로 삼는다. '불친절, 결행' 을
    불친절로만 세면 불이행 건수가 과소집계된다.
    """
    text = (raw or "").strip()
    if not text:
        return "", "미분류"

    parts = [p.strip() for p in SPLIT_RE.split(text) if p.strip()] or [text]
    names = []
    for part in parts:
        for name, aliases in KIND_ALIASES.items():
            if any(a in part for a in aliases):
                names.append(name)
                break
        else:
            names.append(part)

    for name in names:
        if name in SERVICE_FAILURE:
            return name, "배차불이행"
    return names[0], "기타"


def make_date(year, month, day):
    try:
        date = datetime.date(year, month, day)
    except ValueError:
        return None
    return date if MIN_YEAR <= date.year <= MAX_YEAR else None


def parse_date(value, sheet_year):
    """날짜 값이면 그 연도를, 글자면 시트 연도를 쓴다.

    한 시트 안에 두 형식이 섞여 있고, 글자 쪽은 연도가 없다. 날짜 값은
    그 자체가 연도를 갖고 있으므로 시트 이름보다 그쪽을 믿는다.
    """
    if isinstance(value, datetime.datetime):
        return make_date(value.year, value.month, value.day)

    text = str(value or "").strip().rstrip(".")
    m = re.match(r"^(\d{4})[-./](\d{1,2})[-./](\d{1,2})$", text)
    if m:
        return make_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    if sheet_year is None:
        return None
    m = re.match(r"^(\d{1,2})[-./](\d{1,2})$", text)
    if m:
        return make_date(sheet_year, int(m.group(1)), int(m.group(2)))
    # '0311' 처럼 구분자 없이 넉 자로 적힌 것
    if re.match(r"^\d{4}$", text):
        return make_date(sheet_year, int(text[:2]), int(text[2:]))
    return None


def parse_hour(value):
    """'1401' 같은 네 자리에서 시만 뽑는다. 비어 있는 건이 꽤 있다."""
    text = re.sub(r"\D", "", str(value or ""))
    if len(text) == 4 and int(text[:2]) < 24:
        return text[:2]
    if len(text) == 3 and int(text[:1]) < 24:
        return text[:1].zfill(2)
    return ""


def load(path):
    import openpyxl

    workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    records = []
    skipped = 0
    duplicates = 0
    seen = set()          # 앞 시트에서 이미 본 것
    current = set()       # 지금 시트에서 본 것
    for sheet in workbook.worksheets:
        seen |= current
        current = set()
        year_match = re.search(r"(20\d{2})", sheet.title)
        sheet_year = int(year_match.group(1)) if year_match else None
        for row in sheet.iter_rows(values_only=True):
            if not row or row[0] is None or row[0] == "발생일":
                continue
            date = parse_date(row[0], sheet_year)
            route = str(row[2] or "").strip()
            if date is None or not route:
                skipped += 1
                continue
            place = str(row[3] or "").strip() if len(row) > 3 else ""
            kind_raw = str(row[4] or "").strip() if len(row) > 4 else ""
            hour = parse_hour(row[1] if len(row) > 1 else "")
            # 2026 시트에 앞 시트의 행이 다시 들어 있다. 그대로 두면
            # 2023·2024 건수가 부풀려진다. 다만 같은 시트 안의 동일 행은
            # 서로 다른 사람이 같은 결행을 신고한 것일 수 있어 남긴다.
            key = (date, hour, route, place, kind_raw)
            if key in seen:
                duplicates += 1
                continue
            current.add(key)

            kind, category = normalize_kind(kind_raw)
            records.append({
                "date": date.isoformat(),
                "year": date.year,
                "month": "%04d-%02d" % (date.year, date.month),
                "hour": hour,
                "route_no": route,
                "place": place,
                "kind_raw": kind_raw,
                "kind": kind,
                "category": category,
                "sheet": sheet.title,
            })
    return records, skipped, duplicates


def summarize_by_route(records):
    table = {}
    for r in records:
        key = (r["route_no"], r["year"])
        row = table.setdefault(key, {
            "route_no": r["route_no"], "year": r["year"], "total": 0,
            "service_failure": 0, "결행": 0, "무정차": 0,
            "운행시간미준수": 0, "노선단축": 0, "기타": 0,
        })
        row["total"] += 1
        if r["category"] == "배차불이행":
            row["service_failure"] += 1
            row[r["kind"]] += 1
        else:
            row["기타"] += 1
    return sorted(table.values(),
                  key=lambda x: (-x["service_failure"], x["route_no"]))


def main():
    source = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SOURCE
    if not os.path.exists(source):
        print("[오류] %s 가 없습니다." % source)
        print("       정보공개청구로 받은 민원 엑셀 경로를 인자로 주십시오.")
        return 1

    records, skipped, duplicates = load(source)
    if not records:
        print("[오류] 읽어낸 민원이 없습니다. 시트 구조를 확인하십시오.")
        return 1

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(LONG_CSV, "w", encoding="utf-8-sig", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=LONG_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)

    by_route = summarize_by_route(records)
    with open(BY_ROUTE_CSV, "w", encoding="utf-8-sig", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=BY_ROUTE_COLUMNS,
                                extrasaction="ignore")
        writer.writeheader()
        writer.writerows(by_route)

    failures = [r for r in records if r["category"] == "배차불이행"]
    years = sorted({r["year"] for r in records})
    routes = {r["route_no"] for r in records}

    print("민원 %d건 / 노선 %d개 / %d~%d년"
          % (len(records), len(routes), years[0], years[-1]))
    if skipped:
        print("(발생일·노선번호가 비었거나 날짜가 이상해 건너뛴 행 %d개)" % skipped)
    if duplicates:
        print("(시트 사이에 겹쳐 지운 중복 %d건)" % duplicates)
    print("배차불이행 %d건 (%.0f%%)"
          % (len(failures), 100.0 * len(failures) / len(records)))
    print()

    print("연도별  전체 / 배차불이행")
    for year in years:
        total = sum(1 for r in records if r["year"] == year)
        fail = sum(1 for r in failures if r["year"] == year)
        print("  %d  %5d / %5d  (%.0f%%)" % (year, total, fail, 100.0 * fail / total))
    print()

    print("배차불이행이 많은 노선 15개 (노선 × 연도)")
    print("  %-8s %6s %6s %6s %6s %6s %6s"
          % ("노선", "연도", "불이행", "결행", "무정차", "시간", "단축"))
    for row in by_route[:15]:
        print("  %-8s %6d %6d %6d %6d %6d %6d"
              % (row["route_no"], row["year"], row["service_failure"],
                 row["결행"], row["무정차"], row["운행시간미준수"],
                 row["노선단축"]))
    print()
    print("저장: %s" % LONG_CSV)
    print("      %s" % BY_ROUTE_CSV)
    return 0


if __name__ == "__main__":
    sys.exit(main())
