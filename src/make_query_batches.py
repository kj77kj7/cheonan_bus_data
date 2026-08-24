"""정류장 목록을 조회 화면에 붙여넣기 좋은 묶음으로 자른다.

STCIS 노선·정류장 지표는 정류장을 지정해야 조회가 되는데, 입력창이 한 번에
받아주는 개수가 정해져 있다. 그 수에 맞춰 미리 잘라두면 파일을 순서대로
열어 붙여넣기만 하면 된다.

export_stop_list.py 가 경유 노선 수 내림차순으로 정렬해 두었으므로, 앞
묶음일수록 이용량이 많은 정류장이다. 중간에 그만두더라도 중요한 정류장은
확보된다.

사용법
    python src/make_query_batches.py           # 100개씩
    python src/make_query_batches.py 50        # 50개씩
    python src/make_query_batches.py 50 ars    # ARS번호로, 50개씩
"""

import os
import sys

from config import DATA_DIR

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SOURCES = {
    "name": ("stop_names.txt", "정류소명"),
    "ars": ("stop_ars.txt", "ARS번호"),
}
OUT_DIR = os.path.join(DATA_DIR, "query_batches")
DEFAULT_SIZE = 100


def parse_args(argv):
    size = DEFAULT_SIZE
    kind = "name"
    for arg in argv[1:]:
        if arg.isdigit():
            size = int(arg)
        elif arg in SOURCES:
            kind = arg
        else:
            print("[오류] 알 수 없는 인자: %s" % arg)
            print("       사용법: python src/make_query_batches.py [묶음크기] [name|ars]")
            return None
    if size < 1:
        print("[오류] 묶음 크기는 1 이상이어야 합니다.")
        return None
    return size, kind


def main():
    parsed = parse_args(sys.argv)
    if parsed is None:
        return 1
    size, kind = parsed

    filename, label = SOURCES[kind]
    source = os.path.join(DATA_DIR, filename)
    if not os.path.exists(source):
        print("[오류] %s 가 없습니다." % source)
        print("       python src/export_stop_list.py 를 먼저 실행하십시오.")
        return 1

    with open(source, encoding="utf-8") as f:
        items = [line.strip() for line in f if line.strip()]
    if not items:
        print("[오류] %s 가 비어 있습니다." % source)
        return 1

    os.makedirs(OUT_DIR, exist_ok=True)
    # 앞선 실행이 더 잘게 쪼갠 파일을 남겨두면 어디까지 받았는지 헷갈린다.
    for old in os.listdir(OUT_DIR):
        if old.startswith(kind + "_") and old.endswith(".txt"):
            os.remove(os.path.join(OUT_DIR, old))

    total = (len(items) + size - 1) // size
    for i in range(total):
        chunk = items[i * size:(i + 1) * size]
        path = os.path.join(OUT_DIR, "%s_%02d.txt" % (kind, i + 1))
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(chunk) + "\n")

    print("%s %d개를 %d개씩 %d묶음으로 잘랐습니다." % (label, len(items), size, total))
    print("저장: %s" % OUT_DIR)
    print()
    print("%s_01.txt 부터 순서대로 조회하십시오." % kind)
    print("앞 묶음일수록 경유 노선이 많은 정류장입니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
