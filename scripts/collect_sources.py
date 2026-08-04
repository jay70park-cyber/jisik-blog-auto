# -*- coding: utf-8 -*-
"""
주간 소재 수집 스크립트
- 6주 순환 카테고리 중 이번 주 카테고리를 결정
- 카테고리별 후보 키워드에 대해 블로그/카페 검색 결과 수 + 검색어 트렌드 지수를 수집
- 가중합으로 관심도 스코어를 계산해 이번 주 대표 키워드를 선정
- 결과를 collection_result.json / telegram_message.txt 로 저장
"""
import os
import sys
import json
import urllib.request
import urllib.parse
import urllib.error
import datetime

NAVER_ID = os.environ["NAVER_CLIENT_ID"]
NAVER_SECRET = os.environ["NAVER_CLIENT_SECRET"]

CATEGORIES = {
    "시세_투자분석": ["동탄 지식산업센터 시세", "지식산업센터 투자", "지식산업센터 임대수익률"],
    "지역밀착": ["동탄 지식산업센터", "2동탄 지식산업센터", "동탄 GTX"],
    "입문_기초": ["지식산업센터란", "지식산업센터 아파트형공장"],
    "세제_정책": ["지식산업센터 취득세", "지식산업센터 임대사업자"],
    "실무체크리스트": ["지식산업센터 계약 주의사항", "지식산업센터 분쟁"],
    "전문가코너": ["지식산업센터 층고 하중", "지식산업센터 화물엘리베이터", "지식산업센터 수전용량"],
}
CATEGORY_NAMES = {
    "시세_투자분석": "시세·투자분석",
    "지역밀착": "지역 밀착(2동탄)",
    "입문_기초": "입문/기초",
    "세제_정책": "세제·정책",
    "실무체크리스트": "실무 체크리스트",
    "전문가코너": "전문가 코너",
}
ROTATION = ["시세_투자분석", "지역밀착", "입문_기초", "세제_정책", "실무체크리스트", "전문가코너"]

# 순환의 기준 시작일 (이 주가 1번째 카테고리)
BASE_DATE = datetime.date(2026, 8, 4)


def naver_search_total(endpoint, query, timeout=15):
    """네이버 검색 API(블로그/카페 등)에서 검색결과 총 건수(total)를 가져온다."""
    url = "https://naverapihub.apigw.ntruss.com/search/v1/" + endpoint + "?" + urllib.parse.urlencode(
        {"query": query, "display": 1}
    )
    req = urllib.request.Request(
        url,
        headers={
            "X-NCP-APIGW-API-KEY-ID": NAVER_ID,
            "X-NCP-APIGW-API-KEY": NAVER_SECRET,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            data = json.load(res)
        return int(data.get("total", 0))
    except Exception as e:
        print("검색 API 오류(" + endpoint + ", " + query + "): " + str(e))
        return 0


def naver_trend_ratio(keyword, start, end, timeout=15):
    """네이버 데이터랩 검색어 트렌드에서 가장 최근 구간의 상대 지수(ratio)를 가져온다."""
    url = "https://naverapihub.apigw.ntruss.com/search-trend/v1/search"
    body = json.dumps(
        {
            "startDate": start,
            "endDate": end,
            "timeUnit": "week",
            "keywordGroups": [{"groupName": keyword, "keywords": [keyword]}],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "X-NCP-APIGW-API-KEY-ID": NAVER_ID,
            "X-NCP-APIGW-API-KEY": NAVER_SECRET,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            data = json.load(res)
        results = data.get("results", [])
        if results and results[0].get("data"):
            return float(results[0]["data"][-1]["ratio"])
        return 0.0
    except Exception as e:
        print("데이터랩 API 오류(" + keyword + "): " + str(e))
        return 0.0


def week_index(today=None):
    today = today or datetime.date.today()
    weeks = (today - BASE_DATE).days // 7
    return weeks % len(ROTATION)


def main():
    idx = week_index()
    category = ROTATION[idx]
    keywords = CATEGORIES[category]

    end = datetime.date.today()
    start = end - datetime.timedelta(days=28)

    rows = []
    for kw in keywords:
        blog = naver_search_total("blog", kw)
        cafe = naver_search_total("cafearticle", kw)
        trend = naver_trend_ratio(kw, start.isoformat(), end.isoformat())
        rows.append({"keyword": kw, "blog": blog, "cafe": cafe, "trend": trend})

    max_blog = max((r["blog"] for r in rows), default=0) or 1
    max_cafe = max((r["cafe"] for r in rows), default=0) or 1
    max_trend = max((r["trend"] for r in rows), default=0) or 1

    for r in rows:
        r["score"] = round(
            (r["trend"] / max_trend) * 0.5
            + (r["blog"] / max_blog) * 0.3
            + (r["cafe"] / max_cafe) * 0.2,
            3,
        )

    rows.sort(key=lambda x: x["score"], reverse=True)
    top = rows[0]

    result = {
        "week_index": idx,
        "category": category,
        "category_display": CATEGORY_NAMES[category],
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "rows": rows,
        "top_keyword": top["keyword"],
    }

    with open("collection_result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # 텔레그램용 요약 메시지 생성
    lines = []
    lines.append("[이번 주 소재 수집 결과]")
    lines.append("카테고리: " + result["category_display"])
    lines.append("")
    for r in rows:
        marker = "★ " if r["keyword"] == top["keyword"] else "- "
        lines.append(
            marker + r["keyword"]
            + " | 블로그 " + str(r["blog"])
            + " | 카페 " + str(r["cafe"])
            + " | 트렌드지수 " + str(r["trend"])
            + " | 스코어 " + str(r["score"])
        )
    lines.append("")
    lines.append("이번 주 대표 키워드: " + top["keyword"])

    with open("telegram_message.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print("\n".join(lines))


if __name__ == "__main__":
    main()
