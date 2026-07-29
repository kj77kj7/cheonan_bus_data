"""공공데이터포털 활용사례 등록용 대표 이미지(248x93) 생성.

실제로 수집한 천안시 정류소 좌표를 그대로 찍어 미니맵으로 쓴다.
지도 부분만 4배로 렌더링한 뒤 축소해 점을 매끄럽게 만들고,
글자는 축소 후에 원본 해상도로 그려 또렷하게 유지한다.
"""

import csv
import math
import os

from PIL import Image, ImageDraw, ImageFont

from config import DATA_DIR, PROJECT_ROOT

STOPS_CSV = os.path.join(DATA_DIR, "route_stops.csv")
OUT_PATH = os.path.join(PROJECT_ROOT, "thumbnail_248x93.png")

W, H = 248, 93
SS = 4  # 지도 슈퍼샘플링 배율

BG = (14, 26, 43)
DOT = (77, 182, 214)
DOT_HL = (255, 190, 92)
TITLE = (240, 245, 250)
SUB = (138, 158, 180)
RULE = (38, 58, 84)

# 지도 영역 (원본 해상도 기준)
MAP_X, MAP_Y, MAP_W, MAP_H = 9, 9, 84, 75

FONT_DIR = r"C:\Windows\Fonts"
TREATMENT = {"5", "405", "80", "85", "88"}


def load_font(name, size):
    path = os.path.join(FONT_DIR, name)
    if os.path.exists(path):
        return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def main():
    with open(STOPS_CSV, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    # 정류소는 노선마다 중복 등장하므로 nodeid 로 유일화한다
    pts = {}
    for r in rows:
        try:
            lat, lon = float(r["gpslati"]), float(r["gpslong"])
        except ValueError:
            continue
        nid = r["nodeid"]
        if nid not in pts:
            pts[nid] = [lat, lon, False]
        if r["routeno"] in TREATMENT:
            pts[nid][2] = True  # 처치군 경유 정류소는 강조색

    coords = list(pts.values())
    lats = [p[0] for p in coords]
    lons = [p[1] for p in coords]
    lat_min, lat_max = min(lats), max(lats)
    lon_min, lon_max = min(lons), max(lons)

    # 위도에 따른 경도 축소를 보정해야 지도가 옆으로 늘어나지 않는다
    lat_mid = math.radians((lat_min + lat_max) / 2)
    span_x = (lon_max - lon_min) * math.cos(lat_mid)
    span_y = lat_max - lat_min
    scale = min(MAP_W * SS / span_x, MAP_H * SS / span_y) * 0.94
    off_x = (MAP_W * SS - span_x * scale) / 2
    off_y = (MAP_H * SS - span_y * scale) / 2

    # --- 지도: 4배 렌더링 후 축소 ---
    map_img = Image.new("RGB", (MAP_W * SS, MAP_H * SS), BG)
    md = ImageDraw.Draw(map_img)
    r_norm, r_hl = 1.6 * SS, 2.4 * SS
    for lat, lon, is_treat in coords:
        x = off_x + (lon - lon_min) * math.cos(lat_mid) * scale
        y = off_y + (lat_max - lat) * scale  # 위도는 위아래 반전
        r = r_hl if is_treat else r_norm
        md.ellipse([x - r, y - r, x + r, y + r],
                   fill=DOT_HL if is_treat else DOT)
    map_img = map_img.resize((MAP_W, MAP_H), Image.LANCZOS)

    # --- 합성 ---
    img = Image.new("RGB", (W, H), BG)
    img.paste(map_img, (MAP_X, MAP_Y))
    d = ImageDraw.Draw(img)

    d.line([(103, 12), (103, 81)], fill=RULE, width=1)

    f_title = load_font("malgunbd.ttf", 14)
    f_sub = load_font("malgun.ttf", 10)
    f_small = load_font("malgun.ttf", 9)

    tx = 114
    d.text((tx, 16), "천안시 시내버스", font=f_title, fill=TITLE)
    d.text((tx, 34), "지연·수요 예측", font=f_title, fill=TITLE)
    d.text((tx, 56), "노선 265 · 정류소 %s" % format(len(coords), ","),
           font=f_small, fill=SUB)
    d.text((tx, 68), "TAGO 버스정보 활용", font=f_small, fill=SUB)

    img.save(OUT_PATH, "PNG")
    print("저장: %s (%dx%d)" % (OUT_PATH, W, H))
    print("정류소 %d개 / 처치군 경유 %d개"
          % (len(coords), sum(1 for p in coords if p[2])))


if __name__ == "__main__":
    main()
