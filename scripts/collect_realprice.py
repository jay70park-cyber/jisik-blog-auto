# -*- coding: utf-8 -*-
"""
국토교통부 상업업무용 부동산 매매 실거래가 수집 (화성시 동탄구)

두 가지 모드로 동작한다.
  - 초기 수집(backfill): MONTHS_BACK 개월치를 한 번에 긁어온다
  - 정기 수집(incremental): 최근 3개월만 다시 긁어 갱신한다
    (실거래 신고는 최대 30일 시차가 있어 지난달 데이터가 나중에 추가된다)

산출물
  data/realprice_raw.csv      전체 거래 원본 (중복 제거 후 누적)
  data/jibun_summary.csv      지번별 집계 — 단지명 매핑용 작업 파일
"""
import os
import csv
import time
import urllib.request
import urllib.parse
import datetime
import xml.etree.ElementTree as ET
from collections import defaultdict

SERVICE_KEY = os.environ["MOLIT_API_KEY"]

# 화성시 동탄구. 2026년 분구로 신설된 코드이며, 이전 데이터는 화성시(41590)에 남아 있다.
LAWD_CD = "41597"

BASE_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcNrgTrade/getRTMSDataSvcNrgTrade"

DATA_DIR = "data"
RAW_FILE = os.path.join(DATA_DIR, "realprice_raw.csv")
SUMMARY_FILE = os.path.join(DATA_DIR, "jibun_summary.csv")

# 초기 수집 개월 수. 정기 수집 모드에서는 무시된다.
MONTHS_BACK = int(os.environ.get("MONTHS_BACK", "20"))
MODE = os.environ.get("COLLECT_MODE", "backfill")  # backfill | incremental

FIELDS = [
    "dealYear", "dealMonth", "dealDay",
    "umdNm", "jibun", "buildingUse", "buildingType",
    "buildingAr", "floor", "dealAmount", "buildYear",
    "landUse", "dealingGbn", "buyerGbn", "slerGbn",
    "cdealType", "cdealDay",
]


def month_list(months_back, until=None):
    """오늘부터 거슬러 올라가며 YYYYMM 목록을 만든다."""
    if until is None:
        until = datetime.date.today()
    out = []
    y, m = until.year, until.month
    for _ in range(months_back):
        out.append("{:04d}{:02d}".format(y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def fetch_month(ym, timeout=30, retries=3):
    """한 달치 거래를 조회해 dict 목록으로 돌려준다."""
    url = BASE_URL + "?" + urllib.parse.urlencode({
        "serviceKey": SERVICE_KEY,
        "LAWD_CD": LAWD_CD,
        "DEAL_YMD": ym,
        "numOfRows": 1000,
        "pageNo": 1,
    }, safe="%")

    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=timeout) as res:
                xml_text = res.read().decode("utf-8")
            break
        except Exception as e:
            print("  조회 실패 ({}/{}): {}".format(attempt, retries, e))
            if attempt == retries:
                return []
            time.sleep(attempt * 3)

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print("  XML 파싱 실패: " + str(e))
        return []

    code = root.findtext(".//resultCode", default="")
    if code not in ("000", "00"):
        print("  API 오류: " + str(root.findtext(".//resultMsg")))
        return []

    rows = []
    for item in root.iter("item"):
        row = {}
        for f in FIELDS:
            row[f] = (item.findtext(f) or "").strip()
        rows.append(row)
    return rows


def deal_key(r):
    """같은 거래를 식별하는 키. 재수집 시 중복을 막는다."""
    return "|".join([
        r["dealYear"], r["dealMonth"], r["dealDay"],
        r["umdNm"], r["jibun"], r["floor"],
        r["buildingAr"], r["dealAmount"],
    ])


def load_existing():
    if not os.path.exists(RAW_FILE):
        return {}
    out = {}
    with open(RAW_FILE, "r", encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            out[deal_key(r)] = r
    return out


def to_int(s):
    try:
        return int(str(s).replace(",", "").strip())
    except (ValueError, AttributeError):
        return 0


def to_float(s):
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, AttributeError):
        return 0.0


def pyeong_price(r):
    """평당 거래가(만원). 건물면적 기준."""
    ar = to_float(r["buildingAr"])
    amount = to_int(r["dealAmount"])
    if ar <= 0:
        return 0
    return round(amount / (ar / 3.3058))


def write_raw(records):
    os.makedirs(DATA_DIR, exist_ok=True)
    rows = sorted(
        records.values(),
        key=lambda r: (r["dealYear"], r["dealMonth"].zfill(2), r["dealDay"].zfill(2)),
    )
    with open(RAW_FILE, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS + ["pyeongPrice"])
        w.writeheader()
        for r in rows:
            r = dict(r)
            r["pyeongPrice"] = pyeong_price(r)
            w.writerow(r)
    print("원본 저장: {} ({}건)".format(RAW_FILE, len(rows)))


def write_summary(records):
    """지번별로 묶어 단지명 매핑용 작업 파일을 만든다.
    지산 후보를 위에 오도록 정렬한다 (집합+업무, 거래 많은 순)."""
    groups = defaultdict(list)
    for r in records.values():
        groups[(r["umdNm"], r["jibun"])].append(r)

    rows = []
    for (umd, jibun), items in groups.items():
        uses = defaultdict(int)
        types = defaultdict(int)
        areas, floors, prices = [], [], []
        for r in items:
            uses[r["buildingUse"]] += 1
            types[r["buildingType"]] += 1
            a = to_float(r["buildingAr"])
            if a > 0:
                areas.append(a)
            fl = to_int(r["floor"])
            if fl:
                floors.append(fl)
            p = pyeong_price(r)
            if p > 0:
                prices.append(p)

        main_use = max(uses.items(), key=lambda x: x[1])[0] if uses else ""
        main_type = max(types.items(), key=lambda x: x[1])[0] if types else ""

        # 지산 후보 판별: 집합건물 + 업무시설 계열 + 소형 호실이 여럿
        is_candidate = (
            main_type == "집합"
            and (("업무" in main_use) or ("공장" in main_use) or ("창고" in main_use))
        )

        rows.append({
            "법정동": umd,
            "지번": jibun,
            "거래건수": len(items),
            "주용도": main_use,
            "건물유형": main_type,
            "전용면적_최소": round(min(areas), 1) if areas else "",
            "전용면적_최대": round(max(areas), 1) if areas else "",
            "층_최저": min(floors) if floors else "",
            "층_최고": max(floors) if floors else "",
            "평당가_평균": round(sum(prices) / len(prices)) if prices else "",
            "지산후보": "O" if is_candidate else "",
            # 아래 두 칸은 비워둔다. 직접 채울 부분.
            "단지명": "",
            "유형확정": "",
        })

    rows.sort(key=lambda r: (r["지산후보"] != "O", -r["거래건수"]))

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SUMMARY_FILE, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
        w.writeheader()
        w.writerows(rows)
    print("집계 저장: {} (지번 {}개)".format(SUMMARY_FILE, len(rows)))

    cand = sum(1 for r in rows if r["지산후보"] == "O")
    print("  지산 후보 지번: {}개".format(cand))


def main():
    months = month_list(3 if MODE == "incremental" else MONTHS_BACK)
    print("수집 모드: {} / 대상 {}개월 ({} ~ {})".format(
        MODE, len(months), months[0], months[-1]))

    records = load_existing()
    print("기존 누적: {}건".format(len(records)))

    added = 0
    for ym in months:
        rows = fetch_month(ym)
        new = 0
        for r in rows:
            k = deal_key(r)
            if k not in records:
                new += 1
            records[k] = r
        added += new
        print("  {} : {}건 조회 (신규 {}건)".format(ym, len(rows), new))
        time.sleep(0.4)   # API 부담 완화

    print("신규 추가: {}건 / 누적 {}건".format(added, len(records)))

    if not records:
        print("수집된 데이터가 없습니다. 지역코드와 인증키를 확인하세요.")
        raise SystemExit(1)

    write_raw(records)
    write_summary(records)


if __name__ == "__main__":
    main()
