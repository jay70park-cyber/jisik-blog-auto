# -*- coding: utf-8 -*-
"""
주간 소재 수집 스크립트
- 요일과 주차에 따라 트랙을 결정 (월: 지산 5개 순환 / 목: 실거래·지역·경매)
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

# ── 월요일: 5개 순환 (독자 여정 순서) ──
CATEGORIES = {
    "개념_자격": ["동탄 지식산업센터 입주업종", "동탄 지식산업센터 조건", "동탄 지산 입주자격"],
    "세금_정책": ["지식산업센터 취득세 감면", "동탄 지식산업센터 취득세", "지식산업센터 재산세"],
    "물건_검증": ["동탄 지식산업센터 실거래가", "지식산업센터 등기부 확인", "동탄 지식산업센터 전용률"],
}
CATEGORY_NAMES = {
    "개념_자격": "개념·입주자격",
    "세금_정책": "세금·정책",
    "물건_검증": "물건 검증",
}
ROTATION = ["개념_자격", "세금_정책", "물건_검증"]
# 재료 부족으로 잠시 뺀 것들. 나중에 되살릴 때 참고용으로 남겨둔다.
#   "시설_설비": 단지별 층고·하중·전력 실측 자료가 있어야 쓸 수 있다
#   "거래_실행": 계약·대출 실무 경험이 쌓인 뒤에 넣는다

# ── 목요일 1·3주: 실거래가 분석 ──
REALPRICE_KEYWORDS = ["동탄 지식산업센터 시세", "동탄 상가 시세", "동탄 지식산업센터 매매"]

# ── 목요일 2주: 지역 개발 이슈 ──
LOCAL_KEYWORDS = ["동탄 개발 호재", "동탄 반도체", "동탄 교통 개발"]

def load_approved_local_keywords():
    """discover_local_keywords.py 가 만든 목록에서 승인된 것만 가져온다.
    파일이 없거나 승인된 게 없으면 위의 기본 목록을 쓴다."""
    path = os.path.join("data", "local_keywords.json")
    if not os.path.exists(path):
        print("지역 키워드 파일 없음 - 기본 목록 사용")
        return LOCAL_KEYWORDS
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        approved = [k["keyword"] for k in data.get("keywords", [])
                    if k.get("pinned")]
        if approved:
            print("승인된 지역 키워드 {}개 사용".format(len(approved)))
            return approved
    except Exception as e:
        print("지역 키워드 파일 읽기 실패: " + str(e))
    print("승인된 키워드가 없어 기본 목록을 씁니다.")
    return LOCAL_KEYWORDS
    
# ── 목요일 4주: 경매 (진전 있을 때, 없으면 지역 개발) ──
AUCTION_KEYWORDS = ["지식산업센터 경매", "동탄 공장 경매", "상가 경매 권리분석"]

# 순환의 기준 시작일 (이 주가 1번째 카테고리)

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


ROTATION_STATE_FILE = "state/rotation_index.txt"


def next_rotation_index():
    """실행할 때마다 1씩 증가하는 카운터를 state에 저장해, 월/목 실행마다 다음 카테고리로 넘어가게 한다."""
    idx = 0
    if os.path.exists(ROTATION_STATE_FILE):
        with open(ROTATION_STATE_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
            idx = int(content) if content else 0
    idx = idx % len(ROTATION)
    os.makedirs("state", exist_ok=True)
    with open(ROTATION_STATE_FILE, "w", encoding="utf-8") as f:
        f.write(str((idx + 1) % len(ROTATION)))
    return idx
    
def pick_track(today=None):
    """오늘 날짜로 (트랙, 카테고리키, 표시명, 키워드목록, 인덱스)을 결정한다.
    GitHub 러너는 UTC로 돌므로 반드시 KST로 변환해서 요일을 판단한다.

    월요일        -> 지산 본류 5개 순환
    목요일 1·3주  -> 실거래가 분석
    목요일 2주    -> 지역 개발 이슈
    목요일 4주    -> 경매 (환경변수 AUCTION_READY=1일 때만, 아니면 지역 개발)
    """
    return "auction", "경매취득", "경매 취득", AUCTION_KEYWORDS, 1   # 임시 테스트
    if today is None:
        kst = datetime.timezone(datetime.timedelta(hours=9))
        today = datetime.datetime.now(kst).date()

    if today.weekday() == 0:          # 월요일
        idx = next_rotation_index()
        key = ROTATION[idx]
        return "jisik", key, CATEGORY_NAMES[key], CATEGORIES[key], idx

    # 목요일 - 이 달의 몇 번째 주인가 (1~5)
    week_of_month = (today.day - 1) // 7 + 1

    if week_of_month in (1, 3):
        return "realprice", "실거래분석", "실거래가 분석", REALPRICE_KEYWORDS, week_of_month

    if week_of_month == 4 and os.environ.get("AUCTION_READY") == "1":
        return "auction", "경매취득", "경매 취득", AUCTION_KEYWORDS, week_of_month

    return "local", "지역이슈", "동탄 지역 개발 이슈", load_approved_local_keywords(), week_of_month

def main():
    track, category, category_display, keywords, idx = pick_track()

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

    # 트렌드지수 0은 검색 관심 신호가 없다는 뜻이라 후보에서 뺀다.
    # 살아남은 것들 중에서 순환 인덱스로 고른다 (같은 키워드 반복 방지).
    rows.sort(key=lambda x: x["score"], reverse=True)
    alive = [r for r in rows if r["trend"] > 0]
    if not alive:
        print("트렌드지수가 모두 0 - 전체 후보에서 순환합니다.")
        alive = rows
    top = alive[idx % len(alive)]
    print("선정: {} (후보 {}개 중 {}번)".format(
        top["keyword"], len(alive), idx % len(alive) + 1))

    result = {
        "week_index": idx,
        "track": track,
        "category": category,
        "category_display": category_display,
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
