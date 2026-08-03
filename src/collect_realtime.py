"""단계 3 - 실시간 버스위치 상시 수집기.

실시간 데이터는 소급 수집이 불가능하므로, 한 번 띄우면 죽지 않고 계속 도는 것이
최우선이다. 개별 호출 실패는 기록만 하고 넘어가며, 어떤 예외도 루프를 멈추지
않는다. 강제 종료되어도 다시 실행하면 그 시점부터 이어서 수집한다.

두 가지 모드를 별도 프로세스로 동시에 돌린다. 하나가 죽어도 다른 하나는 살아남는다.

    python src/collect_realtime.py core      # 표본 22개 노선(37 routeId) / 60초
    python src/collect_realtime.py network   # 전체 265개 routeId / 600초

주기 설계 근거
- 호출 1건당 응답이 약 4.5초로 느려서, 순차 호출로는 37개 한 바퀴에 196초가
  걸린다. 60초 주기를 지키려면 병렬 호출이 필수다.
- 인증키당 동시 접속에 제한이 있다. 실측상 동시 16개는 절반이 거부당했고,
  8개에서 5%, 4~5개에서 0%였다. 두 모드를 합쳐 8개를 넘기지 않도록 잡았다.
- 정류장 간 거리 중앙값이 355m(표정속도 15km/h 기준 약 85초)라, 핵심 노선의
  60초 주기면 정류장을 거의 건너뛰지 않는다.
"""

import csv
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from urllib.parse import urlencode
from urllib.request import urlopen

from config import BASE_LOCATION_INFO, CITY_CODE, DATA_DIR, LOGS_DIR, ENV_PATH, load_env

# 윈도우 콘솔(cp949)이 못 찍는 문자 하나로 수집이 멈추는 일이 없어야 한다.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OPERATION = "getRouteAcctoBusLcList"
ROUTES_CSV = os.path.join(DATA_DIR, "routes_34010.csv")
DETAIL_CSV = os.path.join(DATA_DIR, "cheonan_routes_detail.csv")
REALTIME_DIR = os.path.join(DATA_DIR, "realtime")

# 표본 22개 노선. 상·하행이 별도 routeId 라 실제로는 37개가 된다.
SAMPLE_ROUTENOS = [
    "5", "405", "80", "85", "88",                          # 처치군
    "6", "400", "11", "2", "3", "121", "90", "800", "7",   # 도심 간선
    "900", "910", "140",                                   # 중간층
    "402", "414", "82", "221", "601",                      # 외곽
]

MODES = {
    "core": {
        "label": "핵심",
        "interval": 60,
        "workers": 5,
        "budget": 120000,   # 실제 소요는 약 43,290건
    },
    "network": {
        "label": "전체",
        "interval": 600,
        "workers": 3,
        "budget": 150000,   # 실제 소요는 약 31,005건
    },
}

# 수집 시간대. 자정을 넘겨도 된다 (05:30 시작, 다음날 01:00 종료).
# 23:00 에 끊으면 그때 출발하는 막차(7·90번)와 심야 순환노선(10번, 22:00~03:00)을
# 놓친다. 심야는 혼잡이 없어 구간 소요시간이 곧 자유흐름 기준선이 되므로,
# 지연(= 실제 소요시간 - 자유흐름)을 정의하는 데 오히려 중요한 시간대다.
DAY_START, DAY_END = "05:30", "01:00"
REQUEST_TIMEOUT = 15
MAX_ATTEMPTS = 2          # 다음 순회가 곧 오므로 재시도를 얕게 한다
ALERT_AFTER = 3           # 전 노선 실패가 이만큼 이어지면 경보를 띄운다

CSV_COLUMNS = [
    "ts", "routeid", "routeno", "vehicleno",
    "nodeid", "nodenm", "nodeord", "gpslati", "gpslong", "routetp",
]


def hhmm(text):
    h, m = text.split(":")
    return int(h) * 60 + int(m)


def load_service_key():
    env = load_env(ENV_PATH)
    for name in ("SERVICE_KEY_DECODING", "SERVICE_KEY_ENCODING"):
        value = env.get(name, "").strip()
        if value and not value.startswith("여기에"):
            return value
    sys.exit("[중단] .env 에 인증키가 없습니다: " + ENV_PATH)


def load_targets(mode):
    """(routeno, routeid) 목록. core 는 표본만, network 는 전체."""
    if not os.path.exists(DETAIL_CSV):
        sys.exit("[중단] %s 가 없습니다. 단계 1 을 먼저 실행하십시오." % DETAIL_CSV)

    rows = []
    with open(DETAIL_CSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            rows.append((row["routeno"], row["routeid"]))

    if mode == "network":
        # 상세 조회에서 누락된 노선이 있을 수 있어 원본 목록으로 보강한다
        known = {rid for _, rid in rows}
        if os.path.exists(ROUTES_CSV):
            with open(ROUTES_CSV, encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    if row["routeid"] not in known:
                        rows.append((row.get("routeno", ""), row["routeid"]))
        return rows

    wanted = set(SAMPLE_ROUTENOS)
    targets = [(no, rid) for no, rid in rows if no in wanted]
    missing = wanted - {no for no, _ in targets}
    if missing:
        print("[경고] 노선목록에서 찾지 못한 노선번호: %s" % ", ".join(sorted(missing)))
    return targets


class Quota:
    """날짜별 호출량을 파일에 남겨, 재시작해도 그날 쓴 양을 잃지 않게 한다."""

    def __init__(self, mode, budget):
        self.mode = mode
        self.budget = budget
        self.date = None
        self.count = 0
        self._load(datetime.now())

    def _path(self, day):
        return os.path.join(LOGS_DIR, "quota_%s_%s.json" % (self.mode, day))

    def _load(self, dt):
        self.date = dt.strftime("%Y%m%d")
        path = self._path(self.date)
        self.count = 0
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    self.count = int(json.load(f).get("count", 0))
            except (ValueError, OSError, KeyError):
                pass  # 파일이 깨졌으면 0 부터 다시 센다

    def _save(self):
        try:
            os.makedirs(LOGS_DIR, exist_ok=True)
            with open(self._path(self.date), "w", encoding="utf-8") as f:
                json.dump({"date": self.date, "mode": self.mode, "count": self.count}, f)
        except OSError:
            pass  # 기록 실패가 수집을 멈추게 해선 안 된다

    def roll(self, dt):
        day = dt.strftime("%Y%m%d")
        if day != self.date:
            self._load(dt)
            return True
        return False

    def remaining(self):
        return self.budget - self.count

    def spend(self, n):
        self.count += n
        self._save()


def fetch_route(service_key, route_id):
    """한 노선의 운행 중 차량 목록.

    (rows, 호출횟수, 응답시각) 을 반환한다. 한 순회가 수십 초에 걸치므로
    순회 시작시각이 아니라 호출별 응답시각을 기록해야 위치가 정확해진다.
    """
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
            ts = datetime.now()
            data = json.loads(raw)
            response = data.get("response", {})
            header = response.get("header", {})
            if header.get("resultCode") != "00":
                raise RuntimeError("resultCode=%s msg=%s"
                                   % (header.get("resultCode"), header.get("resultMsg")))
            items = response.get("body", {}).get("items", "")
            if items in ("", None):
                return [], calls, ts       # 운행 중인 차량이 없는 정상 상태
            item = items.get("item", [])
            if isinstance(item, dict):
                item = [item]
            return item, calls, ts
        except Exception as e:
            last_err = e
            if attempt + 1 < MAX_ATTEMPTS:
                time.sleep(0.4)
    return None, calls, last_err


class Writer:
    """날짜별 CSV. 자정을 넘기면 새 파일로 넘어간다."""

    def __init__(self, mode):
        self.mode = mode
        self.day = None
        self.fh = None
        self.writer = None

    def _open(self, day):
        os.makedirs(REALTIME_DIR, exist_ok=True)
        path = os.path.join(REALTIME_DIR, "%s_%s.csv" % (self.mode, day))
        is_new = not os.path.exists(path)
        self.fh = open(path, "a", encoding="utf-8-sig", newline="")
        self.writer = csv.DictWriter(self.fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        if is_new:
            self.writer.writeheader()
        self.day = day
        return path

    def write(self, ts, routeno, route_id, rows):
        day = ts.strftime("%Y%m%d")
        # writer 가 None 인지도 함께 본다. 날짜가 같아도 닫혀 있을 수 있다
        # (수집 시간대가 01:00 에 끝나고 같은 날 05:30 에 재개되는 경우)
        if day != self.day or self.writer is None:
            self.close()
            log(self.mode, "수집 파일: %s" % self._open(day))
        stamp = ts.isoformat(timespec="seconds")
        for r in rows:
            self.writer.writerow({
                "ts": stamp,
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
        # day 를 비우지 않으면, 같은 날짜로 다시 write 할 때 파일을 열지 않고
        # None 이 된 writer 를 그대로 써서 그날 수집이 통째로 유실된다
        self.day = None


def log(mode, msg):
    line = "[%s][%s] %s" % (datetime.now().strftime("%m-%d %H:%M:%S"), mode, msg)
    try:
        print(line, flush=True)
    except Exception:
        pass  # 콘솔 출력 실패가 수집을 멈추게 해선 안 된다
    try:
        os.makedirs(LOGS_DIR, exist_ok=True)
        path = os.path.join(LOGS_DIR, "collect_%s_%s.log"
                            % (mode, datetime.now().strftime("%Y%m%d")))
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def in_day_window(dt):
    """수집 시간대 안인지. 시작 > 종료면 자정을 넘는 구간으로 해석한다."""
    m = dt.hour * 60 + dt.minute
    start, end = hhmm(DAY_START), hhmm(DAY_END)
    if start < end:
        return start <= m < end
    return m >= start or m < end


def seconds_until_day_start(dt):
    start_m = hhmm(DAY_START)
    target = dt.replace(hour=start_m // 60, minute=start_m % 60, second=0, microsecond=0)
    if (dt.hour * 60 + dt.minute) >= start_m:
        target += timedelta(days=1)
    return max(1, int((target - dt).total_seconds()))


# 인증키 문제로 보이는 응답. 이런 오류는 기다린다고 저절로 낫지 않으므로
# 일시적 네트워크 장애와 구분해 알려야 한다.
AUTH_ERROR_HINTS = (
    "SERVICE_KEY", "SERVICE KEY", "LIMITED_NUMBER_OF_SERVICE_REQUESTS",
    "resultCode=30", "resultCode=31", "resultCode=32",
    "resultCode=20", "resultCode=22",
)


def looks_like_auth_error(text):
    upper = str(text).upper()
    return any(h in upper for h in AUTH_ERROR_HINTS)


def sweep(service_key, targets, writer, quota, mode, workers):
    """대상 노선을 병렬로 한 바퀴 돌며 수집한다.

    (소요초, 실패수, 대상수, 대표오류) 를 반환한다. 예산이 소진되면 None.
    """
    if quota.remaining() < len(targets) * MAX_ATTEMPTS:
        log(mode, "[한도] 잔여 %d건 — 오늘 수집을 중단합니다." % quota.remaining())
        return None

    started = time.time()
    # CSV 쓰기는 메인 스레드에서만 한다 (스레드 안전성 확보)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(lambda t: (t, fetch_route(service_key, t[1])), targets))

    vehicles = failed = calls = 0
    first_err = None
    for (routeno, route_id), (rows, used, ts_or_err) in results:
        calls += used
        if rows is None:
            failed += 1
            if first_err is None:
                first_err = ts_or_err
            if failed <= 3:  # 로그가 넘치지 않게 앞의 몇 건만 남긴다
                log(mode, "  실패 %s(%s): %s" % (routeno, route_id, ts_or_err))
            continue
        try:
            writer.write(ts_or_err, routeno, route_id, rows)
            vehicles += len(rows)
        except Exception as e:
            log(mode, "  쓰기 실패 %s: %r" % (route_id, e))

    writer.flush()
    quota.spend(calls)
    elapsed = time.time() - started
    log(mode, "순회 완료 | 차량 %d대 | 호출 %d건 | 실패 %d건 | %.1f초 | 오늘 %d/%d건"
        % (vehicles, calls, failed, elapsed, quota.count, quota.budget))
    return elapsed, failed, len(targets), first_err


def alert_path(mode):
    return os.path.join(LOGS_DIR, "ALERT_%s.txt" % mode)


def raise_alert(mode, streak, err):
    """전멸이 이어지면 눈에 띄게 남긴다. 로그만 보면 놓치기 쉽다."""
    auth = looks_like_auth_error(err)
    lines = [
        "수집 중단 상태입니다.",
        "발생 시각: %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "모드: %s" % mode,
        "연속 전멸 순회: %d회" % streak,
        "대표 오류: %s" % err,
        "",
    ]
    if auth:
        lines += [
            "인증키 문제로 보입니다. 다음을 확인하십시오.",
            "  1. data.go.kr 마이페이지 > 활용신청 현황 — 활용기간이 만료되지 않았는지",
            "  2. 일일 트래픽 초과 여부 (한도 500,000건)",
            "  3. .env 의 키가 그대로인지",
            "키를 갱신했으면 .env 를 고치고 수집기를 다시 실행하면 됩니다.",
        ]
    else:
        lines += [
            "네트워크 또는 서버 장애로 보입니다.",
            "  1. 인터넷 연결 확인",
            "  2. data.go.kr 공지사항에서 점검 일정 확인",
            "복구되면 수집기가 알아서 다시 수집합니다. 껐다 켤 필요 없습니다.",
        ]

    log(mode, "!" * 58)
    for line in lines:
        log(mode, line)
    log(mode, "!" * 58)
    try:
        with open(alert_path(mode), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass


def clear_alert(mode):
    try:
        if os.path.exists(alert_path(mode)):
            os.remove(alert_path(mode))
            log(mode, "수집이 정상으로 돌아왔습니다. 경보를 해제합니다.")
    except OSError:
        pass


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "core"
    if mode not in MODES:
        sys.exit("사용법: python collect_realtime.py [core|network]")

    cfg = MODES[mode]
    service_key = load_service_key()
    targets = load_targets(mode)

    log(mode, "=" * 58)
    log(mode, "단계 3 실시간 수집 시작 — %s 모드" % cfg["label"])
    log(mode, "대상 %d개 routeId | 주기 %d초 | 동시 요청 %d개"
        % (len(targets), cfg["interval"], cfg["workers"]))
    log(mode, "수집 시간대 %s~%s | 일일 예산 %s건" % (DAY_START, DAY_END, format(cfg["budget"], ",")))
    log(mode, "중지: Ctrl+C. 다시 실행하면 그 시점부터 이어서 수집합니다.")
    log(mode, "=" * 58)

    quota = Quota(mode, cfg["budget"])
    if quota.count:
        log(mode, "오늘 이미 %d건 사용한 기록이 있습니다. 이어서 셉니다." % quota.count)

    writer = Writer(mode)
    next_at = datetime.now()
    fail_streak = 0   # 전 노선이 실패한 순회가 몇 번 이어졌는지

    try:
        while True:
            now = datetime.now()
            if quota.roll(now):
                log(mode, "날짜가 바뀌었습니다. 호출량 카운터를 새로 시작합니다.")

            if not in_day_window(now):
                writer.close()
                wait = seconds_until_day_start(now)
                log(mode, "수집 시간대 밖입니다. %s 까지 %d분 대기합니다." % (DAY_START, wait // 60))
                time.sleep(wait)
                next_at = datetime.now()
                continue

            if now >= next_at:
                result = sweep(service_key, targets, writer, quota, mode, cfg["workers"])
                if result is None:
                    # 예산 소진. 다음 날까지 쉰다
                    writer.close()
                    time.sleep(seconds_until_day_start(datetime.now()))
                    next_at = datetime.now()
                    continue

                elapsed, failed, total, first_err = result
                if failed == total:
                    fail_streak += 1
                    if fail_streak == ALERT_AFTER:
                        raise_alert(mode, fail_streak, first_err)
                    elif fail_streak > ALERT_AFTER and fail_streak % 30 == 0:
                        log(mode, "[경고] 전멸이 %d회째 이어지고 있습니다." % fail_streak)
                else:
                    if fail_streak >= ALERT_AFTER:
                        clear_alert(mode)
                    fail_streak = 0

                if elapsed > cfg["interval"]:
                    log(mode, "[주의] 순회에 %.0f초가 걸려 주기 %d초를 넘겼습니다. "
                              "실제 해상도가 낮아집니다." % (elapsed, cfg["interval"]))
                    next_at = datetime.now()
                else:
                    next_at = now + timedelta(seconds=cfg["interval"])

            # 짧게 나눠 자야 Ctrl+C 가 바로 먹는다
            time.sleep(max(1, min(5, (next_at - datetime.now()).total_seconds())))

    except KeyboardInterrupt:
        log(mode, "사용자 중지. 오늘 누적 호출 %d건." % quota.count)
    finally:
        writer.close()


if __name__ == "__main__":
    main()
