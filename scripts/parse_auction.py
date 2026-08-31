# -*- coding: utf-8 -*-
"""
법원경매정보에서 복사한 아파트형공장(지식산업센터) 목록을 CSV로 파싱한다.

법원경매정보에는 검색 결과를 파일로 받는 기능이 없어,
화면에서 복사한 텍스트를 그대로 붙여넣은 파일을 읽어 구조화한다.

입력
  data/auction_raw_*.txt      법원경매정보에서 복사한 원문 (지역별로 여러 개 가능)

출력
  data/auction_cases.csv      사건별 구조화 데이터
  data/auction_summary.txt    프롬프트·텔레그램에 넣을 요약

한 물건은 아래 형태로 반복된다.

    아파트형공장
    2025타경54996|수원본원지방법원
    경기도 화성시 동탄구 영천동 853-1 동탄에스케이브이원센터 제13층 제1308호
    토지 64.7485㎡ (19.59평)|건물 222.08㎡ (67.18평)
    감정가1,240,000,000
    최저가(49%)607,600,000
    매각가(55%)684,000,000
    매각(55%)
    2026-08-28end
    336
"""
import os
import re
import csv
import glob
import datetime
from collections import defaultdict

DATA_DIR = "data"
RAW_GLOB = os.path.join(DATA_DIR, "auction_raw_*.txt")
OUT_CASES = os.path.join(DATA_DIR, "auction_cases.csv")
OUT_SUMMARY = os.path.join(DATA_DIR, "auction_summary.txt")

# 매각가율 구간. 글에서 "얼마짜리가 몇 건" 식으로 쓸 때 기준이 된다.
BANDS = [(0, 30, "30% 미만"), (30, 40, "30~40%"), (40, 50, "40~50%"),
         (50, 70, "50~70%"), (70, 200, "70% 이상")]

# 동탄구 법정동. 2026년 분구 이전 사건은 '화성시 영천동'처럼 구 없이 표기되므로,
# 이 목록에 해당하면 '화성시 동탄구'로 통일해 실거래 데이터와 축을 맞춘다.
DONGTAN_DONGS = {
    "영천동", "여울동", "능동", "반송동", "석우동", "송동", "신동",
    "오산동", "청계동", "산척동", "장지동", "목동", "방교동", "중동",
}


def to_int(s):
    try:
        return int(re.sub(r"[^\d]", "", str(s)))
    except (ValueError, TypeError):
        return 0


def to_float(s):
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def parse_area(line):
    """'토지 20.7㎡ (6.26평)|건물 54.9㎡ (16.61평)' 에서 면적을 뽑는다.
    건물면적이 없으면 0을 돌려준다."""
    land = build = 0.0
    m = re.search(r"토지\s*([\d.]+)\s*㎡", line)
    if m:
        land = to_float(m.group(1))
    m = re.search(r"건물\s*([\d.]+)\s*㎡", line)
    if m:
        build = to_float(m.group(1))
    return land, build


def parse_address(line):
    """소재지 줄에서 시군구·법정동·지번·단지명·층호를 분리한다.

    '경기도 화성시 동탄구 영천동 853-1 동탄에스케이브이원센터 제13층 제1308호'
    분구 전 표기('경기도 화성시 영천동 846-3')도 함께 처리한다.
    """
    out = {"시군구": "", "법정동": "", "지번": "", "단지명": "", "호실": ""}

    m = re.search(r"(화성시\s*동탄구|화성시|용인시\s*기흥구|[가-힣]+시\s*[가-힣]+구|[가-힣]+시)", line)
    if m:
        out["시군구"] = re.sub(r"\s+", " ", m.group(1)).strip()

    m = re.search(r"([가-힣]+[동리])\s+(\d+(?:-\d+)?)", line)
    if m:
        out["법정동"] = m.group(1)
        out["지번"] = m.group(2)
        rest = line[m.end():].strip()
        # 단지명은 지번 뒤부터 '제N층' 앞까지
        m2 = re.search(r"^(.*?)\s*제\s*[\w가-힣]*\d*층", rest)
        if m2:
            out["단지명"] = m2.group(1).strip()
            out["호실"] = rest[len(m2.group(1)):].strip()
        else:
            out["단지명"] = rest.strip()

    # 동탄구는 2026년 분구라, 그 이전 사건은 '화성시 영천동'처럼 구 없이 표기된다.
    # 실거래 데이터(41597)와 같은 축으로 묶으려면 동탄 법정동을 동탄구로 맞춰준다.
    if out["시군구"] == "화성시" and out["법정동"] in DONGTAN_DONGS:
        out["시군구"] = "화성시 동탄구"
    return out


def parse_block(block):
    """물건 하나(줄 목록)를 dict 로 만든다. 매각되지 않았으면 None."""
    text = "\n".join(block)

    m = re.search(r"(\d{4}타경\d+(?:\(\d+\))?)\s*\|\s*(\S+)", text)
    if not m:
        return None
    case_no, court = m.group(1), m.group(2)

    addr_line = ""
    area_line = ""
    for ln in block:
        if ln.startswith("경기도") or ln.startswith("서울") or "시 " in ln[:12]:
            if not addr_line:
                addr_line = ln
        if ln.startswith("토지") or ln.startswith("건물"):
            area_line = ln

    appraise = 0
    m = re.search(r"감정가\s*([\d,]+)", text)
    if m:
        appraise = to_int(m.group(1))

    min_price = 0
    m = re.search(r"최저가\((\d+)%\)\s*([\d,]+)", text)
    if m:
        min_price = to_int(m.group(2))

    sold_price = 0
    sold_rate = 0.0
    m = re.search(r"매각가\((\d+)%\)\s*([\d,]+)", text)
    if m:
        sold_rate = float(m.group(1))
        sold_price = to_int(m.group(2))
    else:
        return None          # 매각되지 않은 물건은 제외

    sale_date = ""
    m = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if m:
        sale_date = m.group(1)

    land, build = parse_area(area_line)
    addr = parse_address(addr_line)

    # 감정가 대비 최저가 비율로 유찰 횟수를 가늠한다.
    # 통상 1회 유찰마다 30%씩 저감된다(70% -> 49% -> 34% -> 24%).
    fail_map = {100: 0, 70: 1, 49: 2, 34: 3, 24: 4, 17: 5}
    min_rate = round(min_price / appraise * 100) if appraise else 0
    fails = ""
    for k, v in fail_map.items():
        if abs(min_rate - k) <= 2:
            fails = v
            break

    pyeong = build / 3.3058 if build else 0
    return {
        "사건번호": case_no,
        "법원": court,
        "매각기일": sale_date,
        "시군구": addr["시군구"],
        "법정동": addr["법정동"],
        "지번": addr["지번"],
        "단지명": addr["단지명"],
        "호실": addr["호실"],
        "토지면적": round(land, 2) if land else "",
        "건물면적": round(build, 2) if build else "",
        "건물평수": round(pyeong, 2) if pyeong else "",
        "감정가": appraise,
        "최저가": min_price,
        "매각가": sold_price,
        "매각가율": sold_rate,
        "최저가율": min_rate,
        "유찰횟수": fails,
        "평당매각가": round(sold_price / 10000 / pyeong) if pyeong else "",
        "대지권미등기": "O" if "대지권미등기" in text else "",
        "대항력임차인": "O" if "대항력있는임차인" in text else "",
    }


def split_blocks(text):
    """'아파트형공장' 으로 시작하는 덩어리로 자른다."""
    lines = [ln.strip() for ln in text.splitlines()]
    blocks, cur = [], []
    for ln in lines:
        if ln in ("아파트형공장", "공장", "근린상가", "오피스텔", "상가"):
            if cur:
                blocks.append(cur)
            cur = [ln]
        elif cur:
            cur.append(ln)
    if cur:
        blocks.append(cur)
    # 아파트형공장만 남긴다
    return [b for b in blocks if b and b[0] == "아파트형공장"]


def band_of(rate):
    for lo, hi, name in BANDS:
        if lo <= rate < hi:
            return name
    return "기타"


def write_summary(cases):
    lines = []
    lines.append("[동탄·기흥 지식산업센터 경매 낙찰 요약]")
    lines.append("출처: 법원경매정보 물건상세검색 (아파트형공장), 직접 집계")
    lines.append("집계일: " + datetime.date.today().isoformat())
    dates = sorted(c["매각기일"] for c in cases if c["매각기일"])
    if dates:
        lines.append("매각기일: {} ~ {} / 매각 {}건".format(
            dates[0], dates[-1], len(cases)))
    lines.append("")

    def med(vals):
        v = sorted(vals)
        if not v:
            return 0
        n = len(v)
        return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2

    lines.append("■ 지역별 매각가율 (감정가 대비 %)")
    by_sgg = defaultdict(list)
    for c in cases:
        by_sgg[c["시군구"]].append(c["매각가율"])
    for sgg, rates in sorted(by_sgg.items(), key=lambda x: -len(x[1])):
        lines.append("  {} | {}건 | 중앙값 {:.0f}% | 범위 {:.0f}~{:.0f}%".format(
            sgg, len(rates), med(rates), min(rates), max(rates)))
    lines.append("")

    lines.append("■ 단지별 (2건 이상)")
    by_cx = defaultdict(list)
    for c in cases:
        by_cx[(c["시군구"], c["단지명"])].append(c)
    for (sgg, cx), items in sorted(by_cx.items(), key=lambda x: -len(x[1])):
        if len(items) < 2:
            continue
        rates = [i["매각가율"] for i in items]
        pp = [i["평당매각가"] for i in items if i["평당매각가"]]
        lines.append("  {} ({}) | {}건 | 매각가율 중앙값 {:.0f}% | 평당 {}만원".format(
            cx, sgg, len(items), med(rates),
            "{:,.0f}".format(med(pp)) if pp else "-"))
    lines.append("")

    lines.append("■ 매각가율 구간 분포")
    bands = defaultdict(int)
    for c in cases:
        bands[band_of(c["매각가율"])] += 1
    for _, _, name in BANDS:
        if bands.get(name):
            lines.append("  {} : {}건".format(name, bands[name]))
    lines.append("")

    unreg = [c for c in cases if c["대지권미등기"]]
    if unreg:
        lines.append("■ 대지권 미등기 물건")
        lines.append("  {}건 | 매각가율 중앙값 {:.0f}%".format(
            len(unreg), med([c["매각가율"] for c in unreg])))
        reg = [c for c in cases if not c["대지권미등기"]]
        if reg:
            lines.append("  (대지권 등기 물건 {}건은 중앙값 {:.0f}%)".format(
                len(reg), med([c["매각가율"] for c in reg])))

    text = "\n".join(lines)
    with open(OUT_SUMMARY, "w", encoding="utf-8") as f:
        f.write(text)
    print(text)


def main():
    files = sorted(glob.glob(RAW_GLOB))
    if not files:
        print("원문 파일이 없습니다: " + RAW_GLOB)
        raise SystemExit(1)

    cases = []
    for path in files:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        blocks = split_blocks(text)
        n = 0
        for b in blocks:
            c = parse_block(b)
            if c:
                cases.append(c)
                n += 1
        print("{} : 물건 {}개 중 매각 {}건".format(
            os.path.basename(path), len(blocks), n))

    if not cases:
        print("파싱된 매각 사건이 없습니다.")
        raise SystemExit(1)

    # 사건번호 중복 제거 (여러 파일에 겹쳐 들어간 경우)
    dedup = {}
    for c in cases:
        dedup[c["사건번호"]] = c
    cases = sorted(dedup.values(), key=lambda c: c["매각기일"], reverse=True)

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUT_CASES, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(cases[0].keys()))
        w.writeheader()
        w.writerows(cases)
    print("\n저장: {} ({}건)\n".format(OUT_CASES, len(cases)))

    write_summary(cases)


if __name__ == "__main__":
    main()
