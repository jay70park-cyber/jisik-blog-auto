# -*- coding: utf-8 -*-
"""
수집된 실거래 원본에 지번 매핑을 붙이고, 블로그 글에 쓸 집계표를 만든다.

입력
  data/realprice_raw.csv     collect_realprice.py 가 만든 원본
  data/jibun_master.csv      직접 작성한 지번-단지명 매핑

출력
  data/by_complex.csv        단지별 집계 (거래건수, 평당가, 최근 거래일)
  data/by_type.csv           유형별 집계 (지산/오피스/오피스텔/...)
  data/by_block.csv          입지 블록별 집계 (테크노밸리/삼성전자화성/...)
  data/monthly_trend.csv     유형별 월간 추이
  data/analysis_summary.txt  텔레그램·프롬프트에 넣을 요약 텍스트

매핑에 없는 지번은 "미분류"로 처리하고, 개수를 로그에 남긴다.
"""
import os
import csv
import datetime
from collections import defaultdict

DATA_DIR = "data"
RAW_FILE = os.path.join(DATA_DIR, "realprice_raw.csv")
MASTER_FILE = os.path.join(DATA_DIR, "jibun_master.csv")

OUT_COMPLEX = os.path.join(DATA_DIR, "by_complex.csv")
OUT_TYPE = os.path.join(DATA_DIR, "by_type.csv")
OUT_BLOCK = os.path.join(DATA_DIR, "by_block.csv")
OUT_TREND = os.path.join(DATA_DIR, "monthly_trend.csv")
OUT_SUMMARY = os.path.join(DATA_DIR, "analysis_summary.txt")

# 블로그에서 주로 다룰 유형. 이 순서로 표에 나온다.
TYPE_ORDER = [
    "지산", "오피스", "업무(미확인)", "오피스텔", "연구시설",
    "근린상가", "판매시설", "교육연구", "숙박", "아파트", "기타",
]

# 건물용도(buildingUse) -> 유형 자동 분류.
# 매핑 파일에 없는 지번에만 적용한다. 매핑이 있으면 그쪽이 우선한다.
USE_TO_TYPE = [
    ("근린생활", "근린상가"),
    ("판매", "판매시설"),
    ("교육연구", "교육연구"),
    ("숙박", "숙박"),
    ("공장", "공장"),
    ("창고", "창고"),
    ("의료", "의료시설"),
    ("운동", "운동시설"),
    ("문화", "문화시설"),
    ("업무", "업무(미확인)"),   # 지산인지 오피스인지 수동 확인 필요
]


def auto_type(building_use):
    """건물용도 문자열로 유형을 추정한다. 판단이 안 되면 '기타'."""
    u = (building_use or "").strip()
    for keyword, typ in USE_TO_TYPE:
        if keyword in u:
            return typ
    return "기타"


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


def norm_jibun(s):
    """엑셀이 3-1을 날짜로 바꿔놓는 사고를 흡수한다."""
    s = str(s).strip()
    # "03월 01일" 같은 형태를 3-1 로 되돌린다
    if "월" in s and "일" in s:
        try:
            parts = s.replace("월", " ").replace("일", " ").split()
            return "{}-{}".format(int(parts[0]), int(parts[1]))
        except (ValueError, IndexError):
            return s
    return s


def load_master():
    """지번 매핑을 (시군구, 법정동, 지번) -> dict 로 읽는다.
    다른 시군구에 같은 법정동명이 있을 수 있어 시군구까지 키에 넣는다."""
    if not os.path.exists(MASTER_FILE):
        print("매핑 파일이 없습니다: " + MASTER_FILE)
        return {}
    out = {}
    with open(MASTER_FILE, "r", encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            key = (
                r.get("시군구", "").strip(),
                r.get("법정동", "").strip(),
                norm_jibun(r.get("지번", "")),
            )
            out[key] = {
                "단지명": r.get("단지명", "").strip(),
                "유형": r.get("유형확정", "").strip() or "미분류",
                "블록": r.get("인근 지산", r.get("인근지산", "")).strip(),
            }
    print("매핑 로드: {}개 지번".format(len(out)))
    return out


def load_raw(master):
    """원본에 매핑을 붙여 목록으로 돌려준다."""
    rows = []
    unmapped = defaultdict(int)

    with open(RAW_FILE, "r", encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            sgg = r.get("sggNm", "").strip()
            umd = r.get("umdNm", "").strip()
            jibun = norm_jibun(r.get("jibun", ""))
            m = master.get((sgg, umd, jibun))

            if m:
                r["단지명"] = m["단지명"]
                r["유형"] = m["유형"]
                r["블록"] = m["블록"]
                r["분류출처"] = "매핑"
            else:
                r["단지명"] = "{} {} {}".format(sgg, umd, jibun)
                r["유형"] = auto_type(r.get("buildingUse", ""))
                r["블록"] = ""
                r["분류출처"] = "자동"
                unmapped[(sgg, umd, jibun)] += 1

            r["_면적"] = to_float(r.get("buildingAr"))
            r["_금액"] = to_int(r.get("dealAmount"))
            r["_평당가"] = to_int(r.get("pyeongPrice"))
            r["_층"] = to_int(r.get("floor"))
            r["_ym"] = "{}-{:02d}".format(
                r.get("dealYear", ""), to_int(r.get("dealMonth")))
            rows.append(r)

    return rows, unmapped


def stats(items):
    """거래 목록에서 요약 통계를 뽑는다."""
    prices = [r["_평당가"] for r in items if r["_평당가"] > 0]
    areas = [r["_면적"] for r in items if r["_면적"] > 0]
    if not prices:
        return None
    prices_sorted = sorted(prices)
    mid = len(prices_sorted) // 2
    median = (prices_sorted[mid] if len(prices_sorted) % 2 == 1
              else (prices_sorted[mid - 1] + prices_sorted[mid]) // 2)
    return {
        "건수": len(items),
        "평당가_중앙값": median,
        "평당가_평균": round(sum(prices) / len(prices)),
        "평당가_최저": min(prices),
        "평당가_최고": max(prices),
        "전용면적_중앙값": round(sorted(areas)[len(areas) // 2], 1) if areas else "",
        "최근거래": max(r["_ym"] for r in items),
    }


def write_group(rows, keyfunc, keyname, path, extra=None):
    groups = defaultdict(list)
    for r in rows:
        k = keyfunc(r)
        if k:
            groups[k].append(r)

    out = []
    for k, items in groups.items():
        s = stats(items)
        if not s:
            continue
        row = {keyname: k}
        if extra:
            row.update(extra(items))
        row.update(s)
        out.append(row)

    out.sort(key=lambda r: -r["건수"])
    if not out:
        print("집계 결과 없음: " + path)
        return out

    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)
    print("저장: {} ({}행)".format(path, len(out)))
    return out


def write_trend(rows):
    """유형별 월간 거래건수·평당가 중앙값."""
    groups = defaultdict(list)
    for r in rows:
        groups[(r["_ym"], r["유형"])].append(r)

    out = []
    for (ym, typ), items in sorted(groups.items()):
        s = stats(items)
        if not s:
            continue
        out.append({
            "연월": ym, "유형": typ,
            "거래건수": s["건수"],
            "평당가_중앙값": s["평당가_중앙값"],
        })

    if not out:
        return out
    with open(OUT_TREND, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)
    print("저장: {} ({}행)".format(OUT_TREND, len(out)))
    return out


def write_summary(rows, by_type, by_block, by_complex):
    """프롬프트에 그대로 넣을 수 있는 요약 텍스트."""
    total = len(rows)
    period = "{} ~ {}".format(
        min(r["_ym"] for r in rows), max(r["_ym"] for r in rows))

    lines = []
    lines.append("[동탄·기흥 상업업무용 실거래 요약]")
    lines.append("기준: 국토교통부 실거래가 공개시스템 / 화성시 동탄구(41597), 용인시 기흥구(41463)")
    lines.append("기간: {} / 전체 {}건".format(period, total))
    lines.append("생성일: " + datetime.date.today().isoformat())
    lines.append("")

    lines.append("■ 유형별 (평당가 만원, 건물면적 기준)")
    for r in by_type:
        lines.append("  {} | {}건 | 중앙값 {:,} | 범위 {:,}~{:,}".format(
            r["유형"], r["건수"], r["평당가_중앙값"],
            r["평당가_최저"], r["평당가_최고"]))
    lines.append("")

    lines.append("■ 입지 블록별")
    for r in by_block:
        lines.append("  {} | {}건 | 중앙값 {:,}".format(
            r["블록"], r["건수"], r["평당가_중앙값"]))
    lines.append("")

    lines.append("■ 단지별 상위 10곳")
    for r in by_complex[:10]:
        lines.append("  {} ({}) | {}건 | 중앙값 {:,} | 최근 {}".format(
            r["단지명"], r.get("유형", ""), r["건수"],
            r["평당가_중앙값"], r["최근거래"]))

    text = "\n".join(lines)
    with open(OUT_SUMMARY, "w", encoding="utf-8") as f:
        f.write(text)
    print("저장: " + OUT_SUMMARY)
    print()
    print(text)


def main():
    if not os.path.exists(RAW_FILE):
        print("원본이 없습니다. 먼저 collect_realprice.py 를 실행하세요.")
        raise SystemExit(1)

    master = load_master()
    rows, unmapped = load_raw(master)
    print("전체 거래: {}건".format(len(rows)))

    if unmapped:
        mapped_cnt = sum(1 for r in rows if r.get("분류출처") == "매핑")
        print("매핑 적용: {}건 / 자동 분류: {}건 (커버리지 {:.0f}%)".format(
            mapped_cnt, len(rows) - mapped_cnt, mapped_cnt * 100.0 / len(rows)))

        # 업무(미확인)은 지산/오피스 구분이 안 되므로 수동 확인이 필요하다.
        need_check = defaultdict(int)
        for r in rows:
            if r["유형"] == "업무(미확인)":
                need_check[(r["sggNm"].strip(), r["umdNm"].strip(),
                            norm_jibun(r["jibun"]))] += 1

        if need_check:
            print("\n[수동 확인 필요] 업무시설인데 매핑에 없는 지번 "
                  "{}개 (거래 {}건):".format(
                      len(need_check), sum(need_check.values())))
            for (sgg, umd, jb), n in sorted(need_check.items(), key=lambda x: -x[1]):
                print("   {} {} {} : {}건".format(sgg, umd, jb, n))

        top = sorted(unmapped.items(), key=lambda x: -x[1])[:15]
        print("\n[단지명 미입력] 거래 많은 순 상위 15:")
        for (sgg, umd, jb), n in top:
            print("   {} {} {} : {}건".format(sgg, umd, jb, n))

    by_complex = write_group(
        rows, lambda r: r["단지명"], "단지명", OUT_COMPLEX,
        extra=lambda items: {
            "유형": items[0]["유형"],
            "블록": items[0]["블록"],
        })

    by_type = write_group(rows, lambda r: r["유형"], "유형", OUT_TYPE)
    by_type.sort(key=lambda r: TYPE_ORDER.index(r["유형"])
                 if r["유형"] in TYPE_ORDER else 99)

    by_block = write_group(rows, lambda r: r["블록"], "블록", OUT_BLOCK)

    write_trend(rows)
    write_summary(rows, by_type, by_block, by_complex)


if __name__ == "__main__":
    main()
