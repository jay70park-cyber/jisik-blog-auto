# -*- coding: utf-8 -*-
"""
법원경매정보에서 복사한 아파트형공장(지식산업센터) 목록을 CSV로 파싱한다.

브라우저에서 복사하면 줄바꿈이 사라져 한 줄로 붙는 경우가 많다.
그래서 줄 단위가 아니라 텍스트 전체를 정규식으로 훑는다.
줄바꿈이 있든 없든 같은 결과가 나온다.

입력
  data/auction_raw_*.txt      법원경매정보에서 복사한 원문 (지역별로 여러 개 가능)

출력
  data/auction_cases.csv      사건별 구조화 데이터
  data/auction_summary.txt    프롬프트·텔레그램에 넣을 요약

한 물건은 아래 순서로 이어진다.
  아파트형공장 / 사건번호|법원 / 소재지 / 면적 / (특이사항) /
  감정가 / 최저가(N%) / 매각가(N%) / 매각(N%) / 매각기일end / 조회수
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
         (50, 70, "50~70%"), (70, 300, "70% 이상")]

# 동탄구 법정동. 2026년 분구 이전 사건은 '화성시 영천동'처럼 구 없이 표기되므로,
# 이 목록에 해당하면 '화성시 동탄구'로 통일해 실거래 데이터와 축을 맞춘다.
DONGTAN_DONGS = {
    "영천동", "여울동", "능동", "반송동", "석우동", "송동", "신동",
    "오산동", "청계동", "산척동", "장지동", "목동", "방교동", "중동",
}

# 목록에 섞여 나오는 다른 용도들. 덩어리를 자르는 기준으로 함께 쓴다.
CATEGORIES = ["아파트형공장", "근린상가", "오피스텔", "숙박시설", "공장",
              "창고", "상가", "대지", "임야", "전답", "아파트",
              "다세대", "연립주택", "단독주택", "기타"]


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


def split_records(text):
    """용도 이름을 기준으로 덩어리를 자르고, 아파트형공장만 돌려준다."""
    text = re.sub(r"\s*\n\s*", "", text)
    text = text.replace("자료시작", "").replace("자료끝", "")

    pattern = "|".join(re.escape(c) for c in CATEGORIES)
    parts = re.split(r"(" + pattern + r")(?=\d{4}타경)", text)

    records = []
    for i in range(1, len(parts) - 1, 2):
        if parts[i] == "아파트형공장":
            records.append(parts[i + 1])
    return records


def parse_address(seg):
    """소재지에서 시군구·법정동·지번·단지명·호실을 분리한다."""
    out = {"시군구": "", "법정동": "", "지번": "", "단지명": "", "호실": ""}

    m = re.search(r"([가-힣]+시\s*[가-힣]+구|[가-힣]+시|[가-힣]+군)", seg)
    if m:
        out["시군구"] = re.sub(r"\s+", " ", m.group(1)).strip()

    m = re.search(r"([가-힣]+[동리])\s+(\d+(?:-\d+)?)", seg)
    if not m:
        return out
    out["법정동"] = m.group(1)
    out["지번"] = m.group(2)
    rest = seg[m.end():].strip()

    # '834-1, 8층813호 (영천동,와이씨아이더스트타워)' 처럼
    # 단지명이 괄호 안 뒤쪽에 오는 표기가 섞여 있다.
    m2 = re.search(r"\(([^)]*)\)\s*$", rest)
    if m2 and "," in m2.group(1):
        out["단지명"] = m2.group(1).split(",")[-1].strip()
        out["호실"] = rest[:m2.start()].strip(" ,")
        return out

    m3 = re.search(r"^(.*?)\s*(제\s*[가-힣]*\d*층.*)$", rest)
    if m3:
        out["단지명"] = m3.group(1).strip()
        out["호실"] = m3.group(2).strip()
    else:
        out["단지명"] = rest.strip()
    return out


def parse_record(body):
    """물건 하나를 dict 로 만든다. 매각되지 않았으면 None."""
    m = re.search(r"^(\d{4}타경\d+(?:\(\d+\))?)\|(.+?)(?=경기도|서울|인천|충청|강원)", body)
    if not m:
        return None
    case_no, court = m.group(1), m.group(2)

    m = re.search(r"((?:경기도|서울|인천).*?)(?=토지\s|건물\s|감정가)", body)
    addr_seg = m.group(1) if m else ""

    land = build = 0.0
    m = re.search(r"토지\s*([\d.]+)\s*㎡", body)
    if m:
        land = to_float(m.group(1))
    m = re.search(r"건물\s*([\d.]+)\s*㎡", body)
    if m:
        build = to_float(m.group(1))

    m = re.search(r"감정가\s*([\d,]+)", body)
    appraise = to_int(m.group(1)) if m else 0

    m = re.search(r"최저가\((\d+)%\)\s*([\d,]+)", body)
    min_rate = int(m.group(1)) if m else 0
    min_price = to_int(m.group(2)) if m else 0

    m = re.search(r"매각가\((\d+)%\)\s*([\d,]+)", body)
    if m:
        sold_rate = float(m.group(1))
        sold_price = to_int(m.group(2))
    else:
        # 매각가 항목이 비어 있고 '매각(N%)'만 있는 경우가 있다.
        # 최저가와 비율이 같으면 최저가로 낙찰된 것으로 본다.
        m2 = re.search(r"매각\((\d+)%\)", body)
        if m2 and min_price and abs(int(m2.group(1)) - min_rate) <= 1:
            sold_rate = float(m2.group(1))
            sold_price = min_price
        else:
            return None          # 유찰·진행 중인 물건은 제외

    m = re.search(r"(\d{4}-\d{2}-\d{2})", body)
    sale_date = m.group(1) if m else ""

    addr = parse_address(addr_seg)
    if addr["시군구"] == "화성시" and addr["법정동"] in DONGTAN_DONGS:
        addr["시군구"] = "화성시 동탄구"

    # 감정가 대비 최저가 비율로 유찰 횟수를 가늠한다.
    # 통상 1회 유찰마다 30%씩 저감된다(70% -> 49% -> 34% -> 24% -> 17% -> 12%).
    fail_map = [(100, 0), (70, 1), (49, 2), (34, 3), (24, 4), (17, 5), (12, 6)]
    fails = ""
    for k, v in fail_map:
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
        "평당감정가": round(appraise / 10000 / pyeong) if pyeong else "",
        "대지권미등기": "O" if "대지권미등기" in body else "",
        "대항력임차인": "O" if "대항력있는임차인" in body else "",
    }


def band_of(rate):
    for lo, hi, name in BANDS:
        if lo <= rate < hi:
            return name
    return "기타"


def med(vals):
    v = sorted(vals)
    if not v:
        return 0
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def half_of(date_str):
    """'2026-08-28' -> '2026 하반기'. 반기 단위로 묶어야 표본이 충분해진다."""
    try:
        y, m, _ = [int(x) for x in date_str.split("-")]
        return "{} {}".format(y, "상반기" if m <= 6 else "하반기")
    except (ValueError, AttributeError):
        return ""


def quarter_of(date_str):
    """'2026-08-28' -> '2026 3Q'."""
    try:
        y, m, _ = [int(x) for x in date_str.split("-")]
        return "{} {}Q".format(y, (m - 1) // 3 + 1)
    except (ValueError, AttributeError):
        return ""


def year_of(date_str):
    try:
        return date_str.split("-")[0]
    except (ValueError, AttributeError):
        return ""


def size_band(pyeong):
    """전용 규모대. 소형 호실과 대형 호실은 매수층이 달라 따로 본다."""
    try:
        p = float(pyeong)
    except (ValueError, TypeError):
        return ""
    if p < 15:
        return "15평 미만"
    if p < 30:
        return "15~30평"
    if p < 50:
        return "30~50평"
    return "50평 이상"


def floor_band(floor_text):
    """호실 표기에서 층수를 뽑아 구간으로 나눈다.
    지산은 저층부(상가·근생)와 중고층부(업무)의 성격이 달라 값이 갈린다."""
    m = re.search(r"제?\s*지하\s*(\d+)\s*층", str(floor_text))
    if m:
        return "지하"
    m = re.search(r"제?\s*(\d+)\s*층", str(floor_text))
    if not m:
        return ""
    f = int(m.group(1))
    if f <= 2:
        return "1~2층"
    if f <= 5:
        return "3~5층"
    if f <= 10:
        return "6~10층"
    return "11층 이상"


def trend_arrow(vals):
    """앞뒤 값을 견줘 방향을 한 글자로. 표에 붙이면 흐름이 눈에 들어온다."""
    if len(vals) < 2:
        return ""
    diff = vals[-1] - vals[0]
    if abs(diff) < 3:
        return "→ 보합"
    return "↑ 상승" if diff > 0 else "↓ 하락"


def group_line(name, items, indent="  "):
    """한 그룹의 통계 한 줄. 여러 곳에서 같은 모양으로 쓴다."""
    rates = [i["매각가율"] for i in items]
    pp = [i["평당매각가"] for i in items if i["평당매각가"]]
    return "{}{} | {}건 | 매각가율 중앙값 {:.0f}% | 평당 {}만원".format(
        indent, name, len(items), med(rates),
        "{:,.0f}".format(med(pp)) if pp else "-")


def write_summary(cases, total_listed):
    lines = []
    lines.append("[동탄·기흥 지식산업센터 경매 낙찰 요약]")
    lines.append("출처: 법원경매정보 물건상세검색 (아파트형공장), 직접 집계")
    lines.append("집계일: " + datetime.date.today().isoformat())
    dates = sorted(c["매각기일"] for c in cases if c["매각기일"])
    if dates:
        lines.append("매각기일 {} ~ {} / 검색 {}건 중 매각 {}건".format(
            dates[0], dates[-1], total_listed, len(cases)))
    lines.append("")

    lines.append("■ 지역별 (감정가 대비 매각가율 %)")
    by_sgg = defaultdict(list)
    for c in cases:
        by_sgg[c["시군구"]].append(c)
    for sgg, items in sorted(by_sgg.items(), key=lambda x: -len(x[1])):
        rates = [i["매각가율"] for i in items]
        pp = [i["평당매각가"] for i in items if i["평당매각가"]]
        lines.append("  {} | {}건 | 매각가율 중앙값 {:.0f}% (범위 {:.0f}~{:.0f}%) | 평당매각가 중앙값 {}만원".format(
            sgg, len(items), med(rates), min(rates), max(rates),
            "{:,.0f}".format(med(pp)) if pp else "-"))
    lines.append("")

    # ── 시계열 ────────────────────────────────
    lines.append("■ 반기별 추이")
    by_half = defaultdict(list)
    for c in cases:
        h = half_of(c["매각기일"])
        if h:
            by_half[h].append(c)
    halves = sorted(by_half)
    for h in halves:
        lines.append(group_line(h, by_half[h]))
    if len(halves) >= 2:
        seq = [med([i["매각가율"] for i in by_half[h]]) for h in halves]
        lines.append("  → 매각가율 {:.0f}% ({}) 에서 {:.0f}% ({}) 로 {}".format(
            seq[0], halves[0], seq[-1], halves[-1], trend_arrow(seq)))
    lines.append("")

    lines.append("■ 분기별 추이")
    by_q = defaultdict(list)
    for c in cases:
        q = quarter_of(c["매각기일"])
        if q:
            by_q[q].append(c)
    for q in sorted(by_q):
        lines.append(group_line(q, by_q[q]))
    lines.append("")

    lines.append("■ 연도별 × 지역")
    by_ys = defaultdict(list)
    for c in cases:
        y = year_of(c["매각기일"])
        if y:
            by_ys[(y, c["시군구"])].append(c)
    for (y, sgg) in sorted(by_ys):
        lines.append(group_line("{} {}".format(y, sgg), by_ys[(y, sgg)]))
    lines.append("")

    lines.append("■ 반기별 × 지역 (같은 방향으로 움직이는가)")
    by_hs = defaultdict(list)
    for c in cases:
        h = half_of(c["매각기일"])
        if h:
            by_hs[(c["시군구"], h)].append(c)
    for sgg in sorted(set(k[0] for k in by_hs)):
        seq, labels = [], []
        for h in halves:
            items = by_hs.get((sgg, h))
            if items:
                seq.append(med([i["매각가율"] for i in items]))
                labels.append("{} {:.0f}%({}건)".format(h[-3:], seq[-1], len(items)))
        if labels:
            lines.append("  {} : {} {}".format(
                sgg, " → ".join(labels), trend_arrow(seq)))
    lines.append("")

    # ── 단면 ──────────────────────────────────
    lines.append("■ 단지별 (2건 이상)")
    by_cx = defaultdict(list)
    for c in cases:
        by_cx[(c["시군구"], c["단지명"])].append(c)
    for (sgg, cx), items in sorted(by_cx.items(), key=lambda x: -len(x[1])):
        if len(items) < 2 or not cx:
            continue
        lines.append(group_line(cx, items))
    lines.append("")

    lines.append("■ 매각가율 구간 분포")
    bands = defaultdict(int)
    for c in cases:
        bands[band_of(c["매각가율"])] += 1
    for _, _, name in BANDS:
        if bands.get(name):
            lines.append("  {} : {}건 ({:.0f}%)".format(
                name, bands[name], bands[name] * 100.0 / len(cases)))
    lines.append("")

    lines.append("■ 유찰 횟수별")
    by_fail = defaultdict(list)
    for c in cases:
        if c["유찰횟수"] != "":
            by_fail[c["유찰횟수"]].append(c)
    prev = None
    for k in sorted(by_fail):
        cur = med([i["매각가율"] for i in by_fail[k]])
        gap = ""
        if prev is not None:
            gap = " (직전 회차 대비 {:+.0f}%p)".format(cur - prev)
        lines.append("  {}회 유찰 | {}건 | 매각가율 중앙값 {:.0f}%{}".format(
            k, len(by_fail[k]), cur, gap))
        prev = cur
    lines.append("")

    lines.append("■ 전용 규모별")
    by_size = defaultdict(list)
    for c in cases:
        s = size_band(c["건물평수"])
        if s:
            by_size[s].append(c)
    for s in ["15평 미만", "15~30평", "30~50평", "50평 이상"]:
        if by_size.get(s):
            lines.append(group_line(s, by_size[s]))
    lines.append("")

    lines.append("■ 층별")
    by_floor = defaultdict(list)
    for c in cases:
        f = floor_band(c["호실"])
        if f:
            by_floor[f].append(c)
    for f in ["지하", "1~2층", "3~5층", "6~10층", "11층 이상"]:
        if by_floor.get(f):
            lines.append(group_line(f, by_floor[f]))
    lines.append("")

    unreg = [c for c in cases if c["대지권미등기"]]
    reg = [c for c in cases if not c["대지권미등기"]]
    if unreg and reg:
        lines.append("■ 대지권 미등기 여부")
        lines.append(group_line("미등기", unreg))
        lines.append(group_line("등기  ", reg))
        lines.append("")

    tenant = [c for c in cases if c["대항력임차인"]]
    if tenant:
        lines.append("■ 대항력 있는 임차인")
        lines.append(group_line("있음", tenant))
        lines.append(group_line("없음", [c for c in cases if not c["대항력임차인"]]))
        lines.append("")

    lines.append("■ 감정가 규모별")
    by_amt = defaultdict(list)
    for c in cases:
        a = c["감정가"] / 100000000.0        # 억 단위
        if a < 1.5:
            by_amt["1.5억 미만"].append(c)
        elif a < 3:
            by_amt["1.5~3억"].append(c)
        elif a < 5:
            by_amt["3~5억"].append(c)
        else:
            by_amt["5억 이상"].append(c)
    for k in ["1.5억 미만", "1.5~3억", "3~5억", "5억 이상"]:
        if by_amt.get(k):
            lines.append(group_line(k, by_amt[k]))

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
    total_listed = 0
    for path in files:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        records = split_records(text)
        total_listed += len(records)
        n = 0
        for body in records:
            c = parse_record(body)
            if c:
                cases.append(c)
                n += 1
        print("{} : 물건 {}개 중 매각 {}건".format(
            os.path.basename(path), len(records), n))

    if not cases:
        print("파싱된 매각 사건이 없습니다.")
        raise SystemExit(1)

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

    write_summary(cases, total_listed)


if __name__ == "__main__":
    main()
