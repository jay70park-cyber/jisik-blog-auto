# -*- coding: utf-8 -*-
"""
지역 개발 이슈 키워드 자동 발굴

네이버 뉴스에서 동탄 관련 최근 기사를 모아 자주 등장하는 말을 뽑고,
검색어 트렌드로 "실제로 검색되는 말"만 걸러 키워드 후보를 만든다.

고정 목록을 손으로 고치는 대신 이 파일이 data/local_keywords.json 을 관리한다.
collect_sources.py 가 그 파일을 읽어 지역 트랙 키워드로 쓴다.

핵심 규칙
  - 뉴스에 2회 이상 등장 + 검색 트렌드 > 0 인 것만 후보로 인정
  - 60일 넘게 뉴스에 안 보이고 트렌드도 죽은 키워드는 자동 은퇴
  - pinned: true 인 키워드는 은퇴시키지 않는다 (직접 지정한 것)
"""
import os
import re
import json
import time
import datetime
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET

NAVER_ID = os.environ["NAVER_CLIENT_ID"]
NAVER_SECRET = os.environ["NAVER_CLIENT_SECRET"]
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

DATA_DIR = "data"
KEYWORD_FILE = os.path.join(DATA_DIR, "local_keywords.json")

# 뉴스를 긁어올 씨앗 질의.
# "동탄 개발" 처럼 넓게 잡으면 지역 생활·연예 기사가 대량으로 딸려온다.
# 산업·부동산 문맥을 질의 자체에 박아 넣어야 쓸 만한 후보가 나온다.
SEED_QUERIES = [
    "동탄 지식산업센터",
    "동탄 산업단지",
    "동탄 반도체 클러스터",
    "화성 동탄 개발계획",
    "동탄 교통망 착공",
    "동탄 상업용지",
    "동탄 부동산 시장",
]

NEWS_PER_QUERY = 100        # 질의당 기사 수 (최대 100)
RECENT_DAYS = 21            # 최근 며칠 기사만 볼 것인가
MIN_NEWS_COUNT = 2          # 뉴스에 최소 몇 번 나와야 후보로 볼 것인가
TREND_CHECK_LIMIT = 20      # 트렌드 조회는 API 부담이 있어 상위 N개만
RETIRE_DAYS = 60            # 며칠 안 보이면 은퇴시킬 것인가
MAX_KEYWORDS = 12           # 목록 최대 크기

# 부동산·산업 문맥 판별어.
# 제목에 이 중 하나라도 없는 기사는 통째로 버린다.
# 워터파크 개장, 연예인 집들이, 봉사활동 기사가 걸러지는 지점이다.
CONTEXT_WORDS = {
    "분양", "매매", "임대", "임차", "전세", "월세", "매물",
    "지식산업센터", "지산", "산업단지", "산단", "공장", "창고",
    "상가", "오피스", "빌딩", "사옥", "연구소",
    "부동산", "시세", "집값", "청약", "입주", "준공", "착공", "허가",
    "개발", "투자", "분양가", "낙찰", "경매", "재건축", "재개발",
    "클러스터", "반도체", "노선", "역세권", "교통망", "철도", "도로",
    "용지", "택지", "지구단위", "인허가", "공급",
}

# 뉴스 제목에 흔하지만 소재가 될 수 없는 말들
STOPWORDS = {
    # 일반
    "동탄", "화성", "화성시", "경기", "경기도", "기자", "뉴스", "속보", "단독",
    "오늘", "내일", "올해", "작년", "내년", "이번", "지난", "최근", "현재",
    "관련", "대한", "위해", "위한", "통해", "따라", "대해", "가운데",
    "밝혔다", "전했다", "고민", "우리", "그것", "이것", "모두", "함께",
    "포토", "영상", "사진", "종합", "주요", "전체", "공개", "발표",
    "시장", "시민", "주민", "지역", "일대", "현장", "관계자",
    "코스피", "코스닥", "특례시", "서울", "수도권",
    # 생활·연예 기사에서 딸려오는 말
    "홈즈", "이현이", "남편", "아내", "부부", "출연", "방송", "예능",
    "패밀리풀", "물놀이", "행사", "개최", "축제", "공연", "체험", "나들이",
    "사회공헌", "봉사", "기부", "삼성맨", "이사", "맛집", "카페",
    "교통약자", "어린이", "학생", "학부모", "병원", "진료",
}


def strip_tags(s):
    """네이버 검색 결과의 <b> 태그와 HTML 엔티티를 걷어낸다."""
    s = re.sub(r"<[^>]+>", "", s or "")
    for a, b in [("&quot;", '"'), ("&amp;", "&"), ("&lt;", "<"),
                 ("&gt;", ">"), ("&apos;", "'"), ("&nbsp;", " ")]:
        s = s.replace(a, b)
    return s.strip()


def fetch_news(query, display=100, timeout=20):
    """구글 뉴스 RSS에서 기사 목록을 가져온다.
    네이버 뉴스 API는 별도 신청 대상이라 401이 떠서 이쪽을 쓴다."""
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({
        "q": query,
        "hl": "ko",
        "gl": "KR",
        "ceid": "KR:ko",
    })
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; jisik-blog-auto/1.0)",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            xml_text = res.read().decode("utf-8", errors="replace")
    except Exception as e:
        print("뉴스 검색 오류(" + query + "): " + str(e))
        return []

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print("RSS 파싱 오류(" + query + "): " + str(e))
        return []

    items = []
    for it in root.iter("item"):
        items.append({
            "title": (it.findtext("title") or "").strip(),
            "link": (it.findtext("link") or "").strip(),
            "pubDate": (it.findtext("pubDate") or "").strip(),
        })
        if len(items) >= display:
            break
    return items


def parse_pubdate(s):
    """'Mon, 25 Aug 2026 09:00:00 +0900' 형식을 date로."""
    try:
        return datetime.datetime.strptime(
            s[:25].strip(), "%a, %d %b %Y %H:%M:%S").date()
    except (ValueError, TypeError):
        return None


def extract_terms(text):
    """제목에서 소재가 될 만한 한글 낱말을 뽑는다.

    형태소 분석기 없이 하는 단순 추출이라 완벽하지 않다.
    그래서 여기서 나온 것을 그대로 쓰지 않고, 뒤에서 트렌드로 한 번 더 거른다.
    """
    text = strip_tags(text)
    # 한글 2~6자 덩어리만. 조사가 붙어 있어도 대체로 앞부분이 살아남는다.
    tokens = re.findall(r"[가-힣]{2,6}", text)
    out = []
    for t in tokens:
        if t in STOPWORDS:
            continue
        # 흔한 조사·어미로 끝나는 것은 잘라본다
        for suffix in ("에서", "으로", "에는", "이나", "까지", "부터", "라고", "한다", "했다"):
            if t.endswith(suffix) and len(t) > len(suffix) + 1:
                t = t[:-len(suffix)]
                break
        if len(t) >= 2 and t not in STOPWORDS:
            out.append(t)
    return out


def naver_trend_ratio(keyword, timeout=20):
    """최근 4주 검색어 트렌드의 마지막 구간 지수. 검색이 거의 없으면 0."""
    end = datetime.date.today()
    start = end - datetime.timedelta(days=28)
    url = "https://naverapihub.apigw.ntruss.com/search-trend/v1/search"
    body = json.dumps({
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "timeUnit": "week",
        "keywordGroups": [{"groupName": keyword, "keywords": [keyword]}],
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "X-NCP-APIGW-API-KEY-ID": NAVER_ID,
        "X-NCP-APIGW-API-KEY": NAVER_SECRET,
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            data = json.load(res)
        results = data.get("results", [])
        if results and results[0].get("data"):
            return float(results[0]["data"][-1]["ratio"])
        return 0.0
    except Exception as e:
        print("트렌드 조회 오류(" + keyword + "): " + str(e))
        return 0.0


def load_existing():
    if not os.path.exists(KEYWORD_FILE):
        return {}
    with open(KEYWORD_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {k["keyword"]: k for k in data.get("keywords", [])}


def send_telegram(text, timeout=20):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendMessage"
    body = urllib.parse.urlencode(
        {"chat_id": TELEGRAM_CHAT_ID, "text": text}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            res.read()
    except Exception as e:
        print("텔레그램 전송 오류: " + str(e))


def main():
    today = datetime.date.today()
    cutoff = today - datetime.timedelta(days=RECENT_DAYS)

    # ── 1. 뉴스 수집 ──────────────────────────
    titles = []
    seen_links = set()
    skipped = 0
    for q in SEED_QUERIES:
        items = fetch_news(q, NEWS_PER_QUERY)
        for it in items:
            link = it.get("link", "")
            if link in seen_links:
                continue
            seen_links.add(link)
            pub = parse_pubdate(it.get("pubDate", ""))
            if pub and pub < cutoff:
                continue
            title = strip_tags(it.get("title", ""))
            # 부동산·산업 문맥이 없는 기사는 버린다.
            # 이 한 줄이 워터파크·연예 기사를 통째로 걸러낸다.
            if not any(w in title for w in CONTEXT_WORDS):
                skipped += 1
                continue
            titles.append(title)
        time.sleep(0.3)

    print("최근 {}일 기사 {}건 채택 (문맥 불일치 {}건 제외)".format(
        RECENT_DAYS, len(titles), skipped))
    if not titles:
        print("기사를 못 가져왔습니다. 기존 목록을 유지합니다.")
        return

    # ── 2. 빈출어 추출 ────────────────────────
    freq = {}
    for t in titles:
        # 한 기사에서 같은 말이 여러 번 나와도 1회로 센다
        for term in set(extract_terms(t)):
            freq[term] = freq.get(term, 0) + 1

    cands = [(t, n) for t, n in freq.items() if n >= MIN_NEWS_COUNT]
    cands.sort(key=lambda x: -x[1])
    cands = cands[:TREND_CHECK_LIMIT]
    print("뉴스 {}회 이상 등장한 후보 {}개".format(MIN_NEWS_COUNT, len(cands)))

    # ── 3. 트렌드로 검증 ──────────────────────
    # "동탄 OO" 형태로 만들어 실제 검색되는지 확인한다.
    verified = []
    for term, n in cands:
        kw = "동탄 " + term
        ratio = naver_trend_ratio(kw)
        print("  {} | 뉴스 {}회 | 트렌드 {}".format(kw, n, ratio))
        if ratio > 0:
            verified.append({"keyword": kw, "news_count": n, "trend": ratio})
        time.sleep(0.4)

    print("검색 확인된 키워드 {}개".format(len(verified)))

    # ── 4. 기존 목록과 병합 ───────────────────
    existing = load_existing()
    merged = {}

    for v in verified:
        k = v["keyword"]
        prev = existing.get(k, {})
        merged[k] = {
            "keyword": k,
            "first_seen": prev.get("first_seen", today.isoformat()),
            "last_seen": today.isoformat(),
            "news_count": v["news_count"],
            "trend": v["trend"],
            "pinned": prev.get("pinned", False),
        }

    # 이번에 안 잡힌 기존 키워드 처리
    retired = []
    for k, prev in existing.items():
        if k in merged:
            continue
        last = prev.get("last_seen", prev.get("first_seen", ""))
        try:
            last_date = datetime.date.fromisoformat(last)
        except ValueError:
            last_date = today
        age = (today - last_date).days

        if prev.get("pinned"):
            merged[k] = prev          # 직접 지정한 것은 유지
        elif age <= RETIRE_DAYS:
            merged[k] = prev          # 아직 유예 기간
        else:
            retired.append(k)

    # ── 5. 상위 N개만 남긴다 ──────────────────
    # pinned 우선, 그다음 트렌드 높은 순
    items = sorted(
        merged.values(),
        key=lambda x: (not x.get("pinned"), -float(x.get("trend", 0))),
    )[:MAX_KEYWORDS]

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(KEYWORD_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "updated": today.isoformat(),
            "source": "네이버 뉴스 최근 {}일 + 검색어 트렌드 검증".format(RECENT_DAYS),
            "keywords": items,
        }, f, ensure_ascii=False, indent=2)

    print("저장: {} ({}개)".format(KEYWORD_FILE, len(items)))

    # ── 6. 변화 알림 ──────────────────────────
    new_ones = [i["keyword"] for i in items
                if i.get("first_seen") == today.isoformat()]

    lines = ["[지역 이슈 키워드 갱신]", "기준일: " + today.isoformat(), ""]
    if new_ones:
        lines.append("새로 추가")
        for k in new_ones:
            lines.append("  + " + k)
        lines.append("")
    if retired:
        lines.append("은퇴 ({}일 이상 뉴스 없음)".format(RETIRE_DAYS))
        for k in retired:
            lines.append("  - " + k)
        lines.append("")
    lines.append("현재 목록 {}개".format(len(items)))
    for i in items:
        mark = "*" if i.get("pinned") else " "
        lines.append("  {} {} (뉴스 {}회, 트렌드 {})".format(
            mark, i["keyword"], i.get("news_count", 0), i.get("trend", 0)))
    lines.append("")
    lines.append("고정하고 싶은 키워드는 data/local_keywords.json 에서")
    lines.append("pinned 를 true 로 바꾸면 자동 은퇴하지 않습니다.")

    text = "\n".join(lines)
    print()
    print(text)
    if new_ones or retired:
        send_telegram(text)


if __name__ == "__main__":
    main()
