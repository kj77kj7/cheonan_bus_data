"""천안시 노선 개편·증차 공지를 게시판에서 긁어 모은다.

배차간격 변경 이력을 정보공개청구로 받으려면 회신까지 열흘 넘게 걸린다.
공지는 이미 공개돼 있으므로, 그쪽을 먼저 훑어 "언제 어느 노선의 배차가
바뀌었는가"를 확보한다.

게시판마다 마크업이 달라 표 구조를 미리 알 수 없다. 그래서 특정 태그에
기대지 않고, 링크 글자와 그 주변 날짜만 보고 목록을 만든다. 제목에 노선·
배차 관련 낱말이 든 것만 남긴다.

    probe   응답을 그대로 저장하고 구조를 요약한다 (마크업 확인용)
    list    목록 쪽을 훑어 후보 공지를 뽑는다  -> data/notices.csv
    detail  후보의 본문을 받아 저장한다        -> data/notices/*.txt

사용법
    python src/fetch_notices.py probe  <목록URL>
    python src/fetch_notices.py list   <목록URL> [쪽수]
    python src/fetch_notices.py detail

목록 URL 에 쪽 번호가 들어가면 그 자리를 {page} 로 바꿔서 준다.
    "https://.../list.do?pageIndex={page}"
"""

import csv
import html
import os
import re
import sys
import time
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from config import DATA_DIR, LOGS_DIR

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

NOTICES_CSV = os.path.join(DATA_DIR, "notices.csv")
NOTICES_DIR = os.path.join(DATA_DIR, "notices")
COLUMNS = ["source", "date", "title", "url", "matched"]

# 제목에 이 낱말들이 있으면 후보로 본다. 배차 관련이 첫 묶음, 노선 관련이
# 둘째 묶음이다. 어느 한쪽만 걸려도 남기고, 판단은 사람이 한다.
KEYWORDS = [
    "배차", "증차", "감차", "증회", "감회", "운행횟수", "운행 횟수",
    "노선", "개편", "조정", "신설", "폐선", "연장", "단축", "변경",
]
# 이것들이 함께 있어야 버스 이야기다. 없으면 도로·상수도 공지까지 딸려온다.
CONTEXT = ["버스", "시내버스", "노선", "대중교통", "교통"]

DATE_RE = re.compile(r"(20\d{2})[-./년\s]+(\d{1,2})[-./월\s]+(\d{1,2})")
LINK_RE = re.compile(r'<a\b([^>]*)>(.*?)</a>', re.S | re.I)
HREF_RE = re.compile(r'href\s*=\s*["\']([^"\']+)["\']', re.I)
ONCLICK_RE = re.compile(r'onclick\s*=\s*["\']([^"\']+)["\']', re.I)
TAG_RE = re.compile(r"<[^>]+>")
ROW_RE = re.compile(r"<tr\b.*?</tr>", re.S | re.I)

TIMEOUT = 30
MIN_GAP = 1.0
MAX_ATTEMPTS = 3


def text_of(fragment):
    return html.unescape(TAG_RE.sub(" ", fragment or "")).replace("\xa0", " ").strip()


def fetch(url):
    last = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        request = Request(url, method="GET")
        request.add_header("User-Agent",
                           "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
        request.add_header("Accept", "text/html,application/xhtml+xml")
        try:
            with urlopen(request, timeout=TIMEOUT) as response:
                raw = response.read()
                charset = response.headers.get_content_charset()
                if not charset:
                    head = raw[:2048].decode("ascii", "replace").lower()
                    charset = "euc-kr" if "euc-kr" in head or "cp949" in head else "utf-8"
                return raw.decode(charset, errors="replace")
        # URLError 만 잡으면 안 된다. 본문이 안 오는 경우 read() 가
        # socket.timeout 을 그대로 올리는데 그것은 URLError 가 아니다.
        except OSError as e:
            last = e
        if attempt < MAX_ATTEMPTS:
            time.sleep(2.0 * attempt)
    raise IOError("요청 실패: %s (%s)" % (last, url))


def looks_relevant(title):
    if not any(k in title for k in KEYWORDS):
        return False
    return any(c in title for c in CONTEXT)


def parse_list(page_html, base_url):
    """행 단위로 훑어 (날짜, 제목, 링크) 를 뽑는다.

    게시판마다 표 구조가 달라 열 위치를 못 믿는다. 행 안에서 가장 긴 링크
    글자를 제목으로 보고, 같은 행 어딘가의 날짜를 가져온다.
    """
    found = []
    seen = set()
    rows = ROW_RE.findall(page_html) or [page_html]
    for row in rows:
        best = None
        for attrs, inner in LINK_RE.findall(row):
            title = text_of(inner)
            if len(title) < 6 or len(title) > 200:
                continue
            if best is None or len(title) > len(best[0]):
                best = (title, attrs)
        if not best:
            continue
        title, attrs = best

        href_match = HREF_RE.search(attrs)
        href = href_match.group(1).strip() if href_match else ""
        if href.lower().startswith("javascript") or not href or href == "#":
            # 자바스크립트로 여는 게시판이 많다. onclick 을 그대로 남겨
            # 사람이 보고 판단하게 한다.
            click = ONCLICK_RE.search(attrs)
            href = "javascript: " + click.group(1).strip() if click else ""
        elif href:
            href = urljoin(base_url, href)

        date_match = DATE_RE.search(text_of(row))
        date = ("%s-%02d-%02d" % (date_match.group(1), int(date_match.group(2)),
                                  int(date_match.group(3)))) if date_match else ""

        key = (title, href)
        if key in seen:
            continue
        seen.add(key)
        found.append({"date": date, "title": title, "url": href,
                      "matched": "Y" if looks_relevant(title) else ""})
    return found


def stage_probe(url):
    page = fetch(url)
    os.makedirs(LOGS_DIR, exist_ok=True)
    path = os.path.join(LOGS_DIR, "notice_probe.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)

    items = parse_list(page, url)
    hits = [i for i in items if i["matched"]]
    print("응답 %d자 / 저장: %s" % (len(page), path))
    print("링크 후보 %d개, 그중 관련 있어 보이는 것 %d개" % (len(items), len(hits)))
    print()
    for item in (hits or items)[:20]:
        print("  [%s] %-58s %s" % (item["date"] or "날짜?", item["title"][:58],
                                   item["url"][:40]))
    if not items:
        print("  링크를 하나도 못 찾았습니다. 목록이 자바스크립트로 그려지는")
        print("  게시판일 수 있습니다. logs/notice_probe.html 을 열어 보십시오.")
    return 0


def stage_list(url_template, pages):
    os.makedirs(DATA_DIR, exist_ok=True)
    source = re.sub(r"^https?://([^/]+).*", r"\1", url_template)
    collected = []
    for page in range(1, pages + 1):
        url = url_template.replace("{page}", str(page))
        try:
            page_html = fetch(url)
        except IOError as e:
            print("  [실패] %d쪽: %s" % (page, e))
            continue
        items = parse_list(page_html, url)
        for item in items:
            item["source"] = source
        collected.extend(items)
        hits = sum(1 for i in items if i["matched"])
        print("  %d쪽  링크 %d개 / 관련 %d개" % (page, len(items), hits))
        if "{page}" not in url_template:
            break
        time.sleep(MIN_GAP)

    seen = set()
    unique = []
    for item in collected:
        key = (item["title"], item["url"])
        if key not in seen:
            seen.add(key)
            unique.append(item)

    is_new = not os.path.exists(NOTICES_CSV)
    with open(NOTICES_CSV, "a", encoding="utf-8-sig", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=COLUMNS, extrasaction="ignore")
        if is_new:
            writer.writeheader()
        writer.writerows(unique)

    hits = [i for i in unique if i["matched"]]
    print()
    print("공지 %d개 중 관련 후보 %d개를 %s 에 저장했습니다."
          % (len(unique), len(hits), NOTICES_CSV))
    for item in hits[:30]:
        print("  [%s] %s" % (item["date"] or "날짜?", item["title"][:70]))
    return 0


def stage_detail():
    if not os.path.exists(NOTICES_CSV):
        print("[오류] %s 가 없습니다. list 를 먼저 돌리십시오." % NOTICES_CSV)
        return 1
    with open(NOTICES_CSV, encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["matched"] == "Y"]
    targets = [r for r in rows if r["url"].startswith("http")]
    skipped = len(rows) - len(targets)

    os.makedirs(NOTICES_DIR, exist_ok=True)
    print("본문을 받을 공지 %d개" % len(targets))
    if skipped:
        print("(자바스크립트 링크 %d개는 건너뜁니다. notices.csv 에서 직접 확인하십시오)"
              % skipped)

    for i, row in enumerate(targets, 1):
        name = re.sub(r"[^\w가-힣]+", "_", row["title"])[:60]
        path = os.path.join(NOTICES_DIR, "%s_%s.txt" % (row["date"] or "nodate", name))
        if os.path.exists(path):
            continue
        try:
            body = fetch(row["url"])
        except IOError as e:
            print("  [실패] %s: %s" % (row["title"][:40], e))
            continue
        with open(path, "w", encoding="utf-8") as f:
            f.write("제목: %s\n날짜: %s\n출처: %s\n\n%s\n"
                    % (row["title"], row["date"], row["url"], text_of(body)))
        if i % 10 == 0 or i == len(targets):
            print("  %d/%d" % (i, len(targets)))
        time.sleep(MIN_GAP)

    print("저장: %s" % NOTICES_DIR)
    return 0


def main():
    stage = sys.argv[1] if len(sys.argv) > 1 else ""
    if stage == "probe" and len(sys.argv) >= 3:
        return stage_probe(sys.argv[2])
    if stage == "list" and len(sys.argv) >= 3:
        pages = int(sys.argv[3]) if len(sys.argv) > 3 else 1
        return stage_list(sys.argv[2], pages)
    if stage == "detail":
        return stage_detail()
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
