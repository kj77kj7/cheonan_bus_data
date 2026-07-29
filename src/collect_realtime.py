"""단계 3 - 실시간 버스위치 상시 수집기.

실시간 데이터는 소급 수집이 불가능하므로, 한 번 띄우면 죽지 않고 계속 도는 것이
최우선이다. 개별 호출 실패는 기록만 하고 넘어가며, 어떤 예외도 루프를 멈추지
않는다. 중간에 강제 종료되어도 다시 실행하면 그 시점부터 이어서 수집한다.

수집 주기는 시간대별로 다르다. 정류장 간 거리 중앙값이 355m(표정속도 15km/h
기준 약 85초)이므로, 첨두시간에는 60초 주기로 정류장을 거의 건너뛰지 않게 하고
비첨두에는 600초로 낮춰 일일 호출 한도를 지킨다. 하루 2~3회만 운행하는 외곽
노선은 조회 대부분이 빈 응답이라 별도로 분리해 900초 주기로 돌린다.

실행:
    python src/collect_realtime.py
"""

import csv
import json
import os
import sys
import time
from datetime import datetime, timedelta
from urllib.parse import urlencode
from urllib.request import urlopen

from config import BASE_LOCATION_INFO, CITY_CODE, DATA_DIR, LOGS_DIR, ENV_PATH, load_env

OPERATION = "getRouteAcctoBusLcList"
DETAIL_CSV = os.path.join(DATA_DIR, "cheonan_routes_detail.csv")
REALTIME_DIR = os.path.join(DATA_DIR, "realtime")

# --- 수집 대상 ---
CORE_ROUTENOS = [
    "5", "405", "80", "85", "88",                        # 처치군
    "6", "400", "11", "2", "3", "121", "90", "800", "7",  # 도심 간선
    "900", "910", "140",                                  # 중간층
]
OUTER_ROUTENOS = ["402", "414", "82", "221", "601"]       # 하루 2~3회 운행

# --- 시간대별 주기(초) ---
# (시작, 끝, 주기). 끝 시각은 포함하지 않는다.
CORE_BANDS = [
    ("05:30", "07:00", 600),
    ("07:00", "09:00", 60),    # 오전 첨두
    ("09:00", "17:00", 600),
    ("17:00", "19:00", 60),    # 오후 첨두
    ("19:00", "23:00", 600),
]
OUTER_BAND = ("06:00", "20:00", 900)

DAY_START, DAY_END = "05:30", "23:00"

# --- 호출 한도 ---
DAILY_QUOTA = 10000
QUOTA_RESERVE = 300        # 재시도용으로 남겨두는 몫
CALL_GAP_SEC = 0.15        # 한 순회 안에서 호출 간 간격
REQUEST_TIMEOUT = 15
MAX_ATTEMPTS = 2           # 실시간은 다음 순회가 곧 오므로 재시도를 얕게 한다

CSV_COLUMNS = [
    "ts", "routeid", "routeno", "vehicleno",
    "nodeid", "nodenm", "nodeord", "gpslati", "gpslong", "routetp",
]


def hhmm(text):
    h, m = text.split(":")
    return int(h) * 60 + int(m)


def now_minutes(dt):
    return dt.hour * 60 + dt.minute


def load_service_key():
    env = load_env(ENV_PATH)
    for name in ("SERVICE_KEY_DECODING", "SERVICE_KEY_ENCODING"):
        value = env.get(name, "").strip()
        if value and not value.startswith("여기에"):
            return value
    print("[중단] .env 에 인증키가 없습니다: " + ENV_PATH)
    sys.exit(1)


def load_route_ids():
    """노선번호 -> routeid 목록. 상·하행이 분리돼 있어 양방향을 모두 받는다."""
    if not os.path.exists(DETAIL_CSV):
        print("[중단] %s 가 없습니다. 단계 1 을 먼저 실행하십시오." % DETAIL_CSV)
        sys.exit(1)

    by_no = {}
    with open(DETAIL_CSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            by_no.setdefault(row["routeno"], []).append(row["routeid"])

    def collect(routenos, label):
        pairs = []
        for no in routenos:
            ids = by_no.get(no)
            if not ids:
                print("[경고] %s 노선 %s번을 노선목록에서 찾지 못했습니다." % (label, no))
                continue
            for rid in ids:
                pairs.append((no, rid))
        return pairs

    return collect(CORE_ROUTENOS, "핵심"), collect(OUTER_ROUTENOS, "외곽")


class Quota:
    """날짜별 호출량을 파일에 남겨, 재시작해도 그날 쓴 양을 잃지 않게 한다."""

    def __init__(self):
        self.date = None
        self.count = 0
        self._load(datetime.now())

    def _path(self, day):
        return os.path.join(LOGS_DIR, "quota_%s.json" % day)

    def _load(self, dt):
        day = dt.strftime("%Y%m%d")
        self.date = day
        path = self._path(day)
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    self.count = int(json.load(f).get("count", 0))
                return
            except (ValueError, OSError):
                pass  # 파일이 깨졌으면 0 부터 다시 센다
        self.count = 0

    def _save(self):
        os.makedirs(LOGS_DIR, exist_ok=True)
        with open(self._path(self.date), "w", encoding="utf-8") as f:
            json.dump({"date": self.date, "count": self.count}, f)

    def roll(self, dt):
        """날짜가 바뀌면 카운터를 새로 시작한다."""
        day = dt.strftime("%Y%m%d")
        if day != self.date:
            self._load(dt)
            return True
        return False

    def remaining(self):
        return DAILY_QUOTA - QUOTA_RESERVE - self.count

    def spend(self, n):
        self.count += n
        self._save()


def fetch_route(service_key, route_id):
    """한 노선의 운행 중 차량 목록. (rows, 호출횟수) 를 반환한다."""
    params = {
        "serviceKey": service_key,
        "_type": "json",
        "cityCode": CITY_CODE,
        "routeId": route_id,
        "numOfRows": 100,
        "pageNo": 1,
    }
    url = BASE_LOCATION_INFO + "/" + OPERATION + "?" + urlencode(params)

    calls = 0
    last_err = None
    for attempt in range(MAX_ATTEMPTS):
        calls += 1
        try:
            with urlopen(url, timeout=REQUEST_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            response = data.get("response", {})
            header = response.get("header", {})
            if header.get("resultCode") != "00":
                raise RuntimeError("resultCode=%s msg=%s"
                                   % (header.get("resultCode"), header.get("resultMsg")))
            items = response.get("body", {}).get("items", "")
            if items in ("", None):
                return [], calls  # 운행 중인 차량이 없는 정상 상태
            item = items.get("item", [])
            if isinstance(item, dict):
                item = [item]
            return item, calls
        except (OSError, RuntimeError, ValueError) as e:
            last_err = e
            if attempt + 1 < MAX_ATTEMPTS:
                time.sleep(0.4)
    raise RuntimeError(str(last_err))


class Writer:
    """날짜별 CSV. 자정을 넘기면 새 파일로 넘어간다."""

    def __init__(self):
        self.day = None
        self.fh = None
        self.writer = None

    def _open(self, day):
        os.makedirs(REALTIME_DIR, exist_ok=True)
        path = os.path.join(REALTIME_DIR, "buslc_%s.csv" % day)
        is_new = not os.path.exists(path)
        self.fh = open(path, "a", encoding="utf-8-sig", newline="")
        self.writer = csv.DictWriter(self.fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        if is_new:
            self.writer.writeheader()
        self.day = day
        return path

    def write(self, dt, routeno, route_id, rows):
        day = dt.strftime("%Y%m%d")
        if day != self.day:
            self.close()
            path = self._open(day)
            log("수집 파일: %s" % path)
        ts = dt.isoformat(timespec="seconds")
        for r in rows:
            self.writer.writerow({
                "ts": ts,
                "routeid": route_id,
                "routeno": routeno,
                "vehicleno": r.get("vehicleno", ""),
                "nodeid": r.get("nodeid", ""),
                "nodenm": r.get("nodenm", ""),
                "nodeord": r.get("nodeord", ""),
                "gpslati": r.get("gpslati", ""),
                "gpslong": r.get("gpslong", ""),
                "routetp": r.get("routetp", ""),
            })

    def flush(self):
        if self.fh:
            self.fh.flush()  # 강제 종료되어도 여기까지는 남는다

    def close(self):
        if self.fh:
            self.fh.close()
            self.fh = None
            self.writer = None


def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        os.makedirs(LOGS_DIR, exist_ok=True)
        path = os.path.join(LOGS_DIR, "collect_%s.log" % datetime.now().strftime("%Y%m%d"))
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass  # 로그 기록 실패가 수집을 멈추게 해선 안 된다


def core_interval(dt):
    """현재 시각에 해당하는 핵심 노선 주기(초). 수집 시간대 밖이면 None."""
    m = now_minutes(dt)
    for start, end, interval in CORE_BANDS:
        if hhmm(start) <= m < hhmm(end):
            return interval
    return None


def in_outer_band(dt):
    m = now_minutes(dt)
    return hhmm(OUTER_BAND[0]) <= m < hhmm(OUTER_BAND[1])


def sweep(service_key, pairs, writer, quota, label):
    """대상 노선을 한 바퀴 돌며 수집한다."""
    if quota.remaining() <= len(pairs):
        log("[한도] 잔여 %d건 — %s 순회를 건너뜁니다." % (quota.remaining(), label))
        return

    started = time.time()
    vehicles = 0
    failed = 0
    calls = 0

    for routeno, route_id in pairs:
        try:
            rows, used = fetch_route(service_key, route_id)
            calls += used
            writer.write(datetime.now(), routeno, route_id, rows)
            vehicles += len(rows)
        except RuntimeError as e:
            calls += MAX_ATTEMPTS
            failed += 1
            log("  실패 %s(%s): %s" % (routeno, route_id, e))
        except Exception as e:  # 예상 못한 예외로도 루프를 멈추지 않는다
            calls += 1
            failed += 1
            log("  예외 %s(%s): %r" % (routeno, route_id, e))
        time.sleep(CALL_GAP_SEC)

    writer.flush()
    quota.spend(calls)
    log("%s 순회 완료 | 차량 %d대 | 호출 %d건 | 실패 %d건 | %.1f초 | 오늘 누적 %d/%d"
        % (label, vehicles, calls, failed, time.time() - started, quota.count, DAILY_QUOTA))


def seconds_until_day_start(dt):
    """다음 수집 시작 시각까지 남은 초."""
    start_m = hhmm(DAY_START)
    m = now_minutes(dt)
    target = dt.replace(hour=start_m // 60, minute=start_m % 60, second=0, microsecond=0)
    if m >= start_m:
        target += timedelta(days=1)
    return max(1, int((target - dt).total_seconds()))


def main():
    service_key = load_service_key()
    core_pairs, outer_pairs = load_route_ids()

    log("=" * 60)
    log("단계 3 실시간 수집 시작")
    log("핵심 %d개 routeId / 외곽 %d개 routeId (합계 %d개)"
        % (len(core_pairs), len(outer_pairs), len(core_pairs) + len(outer_pairs)))
    log("수집 시간대 %s~%s | 첨두 60초 / 비첨두 600초 / 외곽 900초"
        % (DAY_START, DAY_END))
    log("일일 한도 %d건 (재시도 예비 %d건 제외하고 사용)" % (DAILY_QUOTA, QUOTA_RESERVE))
    log("중지하려면 Ctrl+C. 다시 실행하면 그 시점부터 이어서 수집합니다.")
    log("=" * 60)

    quota = Quota()
    if quota.count:
        log("오늘 이미 %d건 사용한 기록이 있습니다. 이어서 셉니다." % quota.count)

    writer = Writer()
    next_core = datetime.now()
    next_outer = datetime.now()

    try:
        while True:
            now = datetime.now()

            if quota.roll(now):
                log("날짜가 바뀌었습니다. 호출량 카운터를 새로 시작합니다.")

            interval = core_interval(now)
            if interval is None:
                writer.close()
                wait = seconds_until_day_start(now)
                log("수집 시간대 밖입니다. %s 까지 %d분 대기합니다." % (DAY_START, wait // 60))
                time.sleep(wait)
                next_core = datetime.now()
                next_outer = datetime.now()
                continue

            if now >= next_core:
                sweep(service_key, core_pairs, writer, quota, "핵심(%d초)" % interval)
                next_core = datetime.now() + timedelta(seconds=interval)

            now = datetime.now()
            if in_outer_band(now) and now >= next_outer:
                sweep(service_key, outer_pairs, writer, quota, "외곽(%d초)" % OUTER_BAND[2])
                next_outer = datetime.now() + timedelta(seconds=OUTER_BAND[2])

            # 다음 할 일까지 짧게 나눠 자야 Ctrl+C 가 바로 먹는다
            wake = min(next_core, next_outer if in_outer_band(datetime.now()) else next_core)
            time.sleep(max(1, min(5, (wake - datetime.now()).total_seconds())))

    except KeyboardInterrupt:
        log("사용자 중지. 오늘 누적 호출 %d건." % quota.count)
    finally:
        writer.close()


if __name__ == "__main__":
    main()
