# -*- coding: utf-8 -*-
"""
주간 소재 수집 스크립트

트랙 결정 (모두 KST 기준)
  월요일              -> 지산 본류 3개 순환
  목요일 · 마지막 주    -> 시의회 회의록 특집
  목요일 · 분기 첫 주   -> 실거래가 분석 (1·4·7·10월 첫 목요일, 또는 REALPRICE_READY=1)
  목요일 · AUCTION_READY=1 -> 경매 취득
  목요일 · 그 외        -> 지역 현안 아젠다 순환

지역 현안은 data/local_agenda.csv 의 활성 안건을 id 순으로 돌린다.
진전이 없는 안건은 건너뛰고 다음 안건으로 넘어간다.
"""
import os
import csv
import json
import re
import urllib.request
import urllib.parse
import urllib.error
import datetime
import xml.etree.ElementTree as ET

NAVER_ID = os.environ["NAVER_CLIENT_ID"]
NAVER_SECRET = os.environ["NAVER_CLIENT_SECRET"]

KST = datetime.timezone(datetime.timedelta(hours=9))


def today_kst():
    """GitHub 러너는 UTC로 돈다. 날짜가 걸린 곳은 전부 이 함수를 쓴다."""
    return datetime.datetime.now(KST).date()


# ── 월요일: 3개 순환 (독자 여정 순서) ──
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

REALPRICE_KEYWORDS = ["동탄 지식산업센터 시세", "동탄 상가 시세", "동탄 지식산업센터 매매"]
AUCTION_KEYWORDS = ["지식산업센터 경매", "동탄 공장 경매", "상가 경매 권리분석"]
COUNCIL_KEYWORDS = ["화성시의회", "화성시 예산", "동탄 개발 현안"]

# 아젠다를 한 건도 못 고를 때만 쓰는 최후 보루
LOCAL_FALLBACK_KEYWORDS = ["동탄 개발 호재", "동탄 반도체", "동탄 교통 개발"]

AGENDA_CSV = os.path.join("data", "local_agenda.csv")
NOTICE_CSV = os.path.join("data", "hscity_notices.csv")
STATE_DIR = "state"
ROTATION_STATE_FILE = os.path.join(STATE_DIR, "rotation_index.txt")
AGENDA_STATE_FILE = os.path.join(STATE_DIR, "agenda_last_id.txt")

FRESH_DAYS = 14        # 최근진전일이 이 안이면 '진전 있음'
NEW_GRACE_DAYS = 30    # 추가된 지 이 안이면 신선도 판정 면제
NEWS_DAYS = 14         # 구글 뉴스를 이 기간만 센다
NOTICE_DAYS = 1825     # 고시는 연표 재료다. 시작점이 있어야 흐름이 보인다
                       # 백필이 부서별로 몇 년치를 가져오므로 넓게 잡는다
NOTICE_LIMIT = 10      # 한 현안에 붙일 고시 최대 건수


# ─────────────────────────────────────────────
# 네이버 API
# ─────────────────────────────────────────────

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
        url, data=body, method="POST",
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


# ─────────────────────────────────────────────
# 구글 뉴스 — 신선도 판정 재료
# ─────────────────────────────────────────────
# 네이버 뉴스 API는 별도 신청 대상이라 401이 난다. 구글 뉴스 RSS로 간다.
# 러너에서는 User-Agent가 없으면 접근이 거부된다.

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"


def google_news_items(query, days=NEWS_DAYS, timeout=20):
    """최근 days일 안의 구글 뉴스 항목 [(날짜, 제목)]을 돌려준다."""
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": query, "hl": "ko", "gl": "KR", "ceid": "KR:ko"}
    )
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw = res.read()
    except Exception as e:
        print("구글 뉴스 오류(" + query + "): " + str(e))
        return []

    cutoff = today_kst() - datetime.timedelta(days=days)
    out = []
    try:
        root = ET.fromstring(raw)
    except Exception as e:
        print("구글 뉴스 파싱 실패: " + str(e))
        return []

    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        d = parse_rss_date(pub)
        if d and d >= cutoff:
            out.append((d.isoformat(), title))
    return out


def parse_rss_date(s):
    """'Mon, 14 Sep 2026 07:00:00 GMT' 형태를 date로. 실패하면 None."""
    if not s:
        return None
    try:
        dt = datetime.datetime.strptime(s[:25].strip(), "%a, %d %b %Y %H:%M:%S")
        return dt.date()
    except Exception:
        return None


# ─────────────────────────────────────────────
# 월요일 순환
# ─────────────────────────────────────────────

def next_rotation_index():
    """월요일마다 1씩 증가하는 카운터. 다음 카테고리로 넘어가게 한다."""
    idx = 0
    if os.path.exists(ROTATION_STATE_FILE):
        with open(ROTATION_STATE_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
            idx = int(content) if content else 0
    idx = idx % len(ROTATION)
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(ROTATION_STATE_FILE, "w", encoding="utf-8") as f:
        f.write(str((idx + 1) % len(ROTATION)))
    return idx


# ─────────────────────────────────────────────
# 지역 현안 아젠다
# ─────────────────────────────────────────────

def load_agenda():
    """local_agenda.csv 의 활성 안건을 id 순으로 돌려준다."""
    if not os.path.exists(AGENDA_CSV):
        print("아젠다 파일 없음: " + AGENDA_CSV)
        return []
    rows = []
    with open(AGENDA_CSV, "r", encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if (r.get("상태") or "활성").strip() != "활성":
                continue
            if not (r.get("id") or "").strip():
                continue
            rows.append(r)
    rows.sort(key=lambda r: r["id"])
    return rows


def read_last_agenda_id():
    if not os.path.exists(AGENDA_STATE_FILE):
        return ""
    with open(AGENDA_STATE_FILE, "r", encoding="utf-8") as f:
        return f.read().strip()


def write_last_agenda_id(agenda_id):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(AGENDA_STATE_FILE, "w", encoding="utf-8") as f:
        f.write(agenda_id)


def days_since(datestr):
    """ISO 날짜 문자열에서 오늘까지 며칠. 비었거나 깨졌으면 None."""
    s = (datestr or "").strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return None
    try:
        return (today_kst() - datetime.date.fromisoformat(s)).days
    except Exception:
        return None


def agenda_queries(row, limit=2):
    """신선도 판정에 쓸 검색어. 현안명 + 키워드 앞쪽 몇 개."""
    qs = [(row.get("현안명") or "").strip()]
    for k in (row.get("키워드") or "").split("|"):
        k = k.strip()
        if k and k not in qs:
            qs.append(k)
    return [q for q in qs if q][:limit]


def check_freshness(row):
    """(진전 있음?, 사유, 뉴스목록) 을 돌려준다."""
    aid = row["id"]
    name = (row.get("현안명") or "").strip()

    # 1) 새로 들어온 안건은 첫 한 바퀴를 보장한다.
    #    고시도 회의록도 뉴스도 없는 상태로 들어오므로,
    #    면제가 없으면 추가되자마자 영원히 건너뛰게 된다.
    added = days_since(row.get("추가일"))
    if added is not None and added <= NEW_GRACE_DAYS:
        return True, "신규 안건 (추가 {}일 경과)".format(added), []

    # 2) 고시·회의록이 갱신해 둔 최근진전일
    prog = days_since(row.get("최근진전일"))
    if prog is not None and prog <= FRESH_DAYS:
        return True, "최근진전일 {}일 전".format(prog), []

    # 3) 뉴스
    news = []
    for q in agenda_queries(row):
        news += google_news_items(q)
    # 제목 중복 제거
    seen, uniq = set(), []
    for d, t in news:
        if t not in seen:
            seen.add(t)
            uniq.append((d, t))
    if uniq:
        return True, "최근 {}일 뉴스 {}건".format(NEWS_DAYS, len(uniq)), uniq

    return False, "진전 없음 (최근진전일 {}일 전, 뉴스 0건)".format(
        prog if prog is not None else "?"), []


def pick_agenda():
    """마지막으로 다룬 안건 다음부터 돌며, 진전 있는 첫 안건을 고른다.

    한 바퀴를 다 돌아도 진전 있는 안건이 없으면
    '가장 오래 안 다룬 안건'을 그냥 쓴다. 발행을 거르지는 않는다.
    """
    rows = load_agenda()
    if not rows:
        return None, [], "아젠다 없음"

    last = read_last_agenda_id()
    ids = [r["id"] for r in rows]
    start = (ids.index(last) + 1) if last in ids else 0
    order = rows[start:] + rows[:start]

    log = []
    for r in order:
        ok, why, news = check_freshness(r)
        log.append("{} {} — {}".format("○" if ok else "×", r["id"], why))
        if ok:
            r["_news"] = news
            r["_why"] = why
            return r, log, why

    # 전부 스킵됨 — 마지막발행일이 가장 오래된 것을 고른다
    def last_pub_key(r):
        s = (r.get("마지막발행일") or "").strip()
        return s if re.match(r"^\d{4}-\d{2}-\d{2}$", s) else "0000-00-00"

    pick = sorted(rows, key=last_pub_key)[0]
    pick["_news"] = []
    pick["_why"] = "전 안건 진전 없음 — 가장 오래 안 다룬 안건 선택"
    log.append("! 전부 스킵 → {} 강제 선택".format(pick["id"]))
    return pick, log, pick["_why"]


def load_notices(agenda_row):
    """이 현안에 걸린 화성시 고시를 공고일 순으로 돌려준다.

    고시는 기사보다 먼저 나오고 행정 절차가 제목에 그대로 드러난다.
    한 현안의 고시를 시간순으로 늘어놓으면 그 자체가 연표가 된다.
    지역 개발 글의 '지금까지의 흐름' 섹션 재료가 바로 이것이다.
    """
    if not os.path.exists(NOTICE_CSV):
        return []
    name = (agenda_row.get("현안명") or "").strip()
    words = [w.strip() for w in (agenda_row.get("키워드") or "").split("|")
             if w.strip()]
    cutoff = (today_kst() - datetime.timedelta(days=NOTICE_DAYS)).isoformat()

    # 현안명에 지역이 박혀 있으면 그 지역 고시만 쓴다.
    # "동탄 주택공급" 인데 남양뉴타운 고시가 재료로 들어가면
    # 글이 통째로 어긋난다. collect_hscity 가 붙여둔 동탄관련 열을 쓴다.
    dongtan_only = any(w in name for w in ("동탄", "장지동", "영천동", "오산동"))

    out = []
    with open(NOTICE_CSV, "r", encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            date = (r.get("공고일자") or "").strip()
            if date < cutoff:
                continue
            title = (r.get("제목") or "").strip()
            # collect_hscity 가 이미 붙여둔 현안 표시를 우선 믿고,
            # 없으면 키워드로 직접 대조한다.
            tagged = name and name in (r.get("현안") or "")
            if not (tagged or any(w in title for w in words)):
                continue
            if dongtan_only and (r.get("동탄관련") or "").strip() != "O":
                continue
            out.append({
                "date": date,
                "title": title,
                "dept": (r.get("부서") or "").strip(),
                "board": (r.get("게시판") or "").strip(),
                "link": (r.get("링크") or "").strip(),
            })

    out.sort(key=lambda r: r["date"])
    if dongtan_only:
        print("  고시 지역 필터: 동탄권만 ({}건)".format(len(out)))
    if len(out) <= NOTICE_LIMIT:
        return out
    # 너무 많으면 최신 쪽을 남기되 가장 오래된 하나는 지킨다.
    # 그것이 이 현안이 언제 시작됐는지를 보여주는 기준점이다.
    return [out[0]] + out[-(NOTICE_LIMIT - 1):]


def mark_published(agenda_id):
    """마지막발행일을 오늘로 갱신한다. 다른 열은 건드리지 않는다."""
    if not os.path.exists(AGENDA_CSV):
        return
    with open(AGENDA_CSV, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        rows = list(reader)
    for r in rows:
        if r.get("id") == agenda_id:
            r["마지막발행일"] = today_kst().isoformat()
    with open(AGENDA_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


# ─────────────────────────────────────────────
# 트랙 결정
# ─────────────────────────────────────────────

def is_last_thursday(d):
    return d.weekday() == 3 and (d + datetime.timedelta(days=7)).month != d.month


def is_quarter_first_thursday(d):
    """1·4·7·10월의 첫 목요일인지."""
    return d.weekday() == 3 and d.month in (1, 4, 7, 10) and d.day <= 7


def pick_track(today=None):
    """(트랙, 카테고리키, 표시명, 키워드목록, 인덱스)을 돌려준다.

    우선순위가 중요하다. 마지막 목요일이 분기 첫 목요일과 겹칠 일은 없지만,
    경매 수동 스위치가 켜진 채로 마지막 주가 오면 회의록이 밀린다.
    회의록을 가장 위에 둔다. 월 1회뿐이라 밀리면 한 달을 통째로 놓친다.
    """
    if today is None:
        today = today_kst()

    if today.weekday() == 0:                      # 월요일
        idx = next_rotation_index()
        key = ROTATION[idx]
        return "jisik", key, CATEGORY_NAMES[key], CATEGORIES[key], idx

    week_of_month = (today.day - 1) // 7 + 1

    if is_last_thursday(today):
        return "council", "시의회특집", "시의회 회의록 특집", COUNCIL_KEYWORDS, week_of_month

    if is_quarter_first_thursday(today) or os.environ.get("REALPRICE_READY") == "1":
        return "realprice", "실거래분석", "실거래가 분석", REALPRICE_KEYWORDS, week_of_month

    if os.environ.get("AUCTION_READY") == "1":
        return "auction", "경매취득", "경매 취득", AUCTION_KEYWORDS, week_of_month

    return "local", "지역이슈", "동탄 지역 개발 이슈", None, week_of_month


# ─────────────────────────────────────────────
# 본체
# ─────────────────────────────────────────────

def score_keywords(keywords, start, end):
    rows = []
    for kw in keywords:
        rows.append({
            "keyword": kw,
            "blog": naver_search_total("blog", kw),
            "cafe": naver_search_total("cafearticle", kw),
            "trend": naver_trend_ratio(kw, start, end),
        })
    max_blog = max((r["blog"] for r in rows), default=0) or 1
    max_cafe = max((r["cafe"] for r in rows), default=0) or 1
    max_trend = max((r["trend"] for r in rows), default=0) or 1
    for r in rows:
        r["score"] = round(
            (r["trend"] / max_trend) * 0.5
            + (r["blog"] / max_blog) * 0.3
            + (r["cafe"] / max_cafe) * 0.2, 3)
    rows.sort(key=lambda x: x["score"], reverse=True)
    return rows


def main():
    track, category, category_display, keywords, idx = pick_track()

    end = today_kst()
    start = end - datetime.timedelta(days=28)

    agenda = None
    skip_log = []

    if track == "local":
        # 소재를 키워드가 아니라 현안에서 고른다.
        # 키워드 점수로 고르면 같은 키워드가 계속 1등이라 소재가 고착된다.
        agenda, skip_log, why = pick_agenda()
        if agenda is None:
            print("아젠다를 못 읽어 기본 키워드로 돌립니다.")
            keywords = LOCAL_FALLBACK_KEYWORDS
        else:
            keywords = agenda_queries(agenda, limit=3) or LOCAL_FALLBACK_KEYWORDS
            print("선정 현안: {} {} — {}".format(agenda["id"], agenda["현안명"], why))

    rows = score_keywords(keywords, start.isoformat(), end.isoformat())

    if track == "local" and agenda is not None:
        # 현안명이 곧 소재다. 점수와 무관하게 고정한다.
        top_keyword = agenda["현안명"]
    else:
        alive = [r for r in rows if r["trend"] > 0] or rows
        top_keyword = alive[idx % len(alive)]["keyword"]
        print("선정: {} (후보 {}개 중 {}번)".format(
            top_keyword, len(alive), idx % len(alive) + 1))

    result = {
        "week_index": idx,
        "track": track,
        "category": category,
        "category_display": category_display,
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "rows": rows,
        "top_keyword": top_keyword,
    }

    if agenda is not None:
        result["agenda"] = {
            "id": agenda["id"],
            "현안명": agenda.get("현안명", ""),
            "분류": agenda.get("분류", ""),
            "단계": agenda.get("단계", ""),
            "최근진전일": agenda.get("최근진전일", ""),
            "진전내용": agenda.get("진전내용", ""),
            "중요도": agenda.get("중요도", ""),
            "키워드": agenda.get("키워드", ""),
            "메모": agenda.get("메모", ""),
            "확인필요": agenda.get("확인필요", ""),
            "위도": agenda.get("위도", ""),
            "경도": agenda.get("경도", ""),
            "선정사유": agenda.get("_why", ""),
            "최근뉴스": [{"date": d, "title": t} for d, t in agenda.get("_news", [])[:8]],
            "관련고시": load_notices(agenda),
        }
        write_last_agenda_id(agenda["id"])
        mark_published(agenda["id"])

    with open("collection_result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # ── 텔레그램 요약 ──
    lines = ["[이번 주 소재 수집 결과]", "카테고리: " + category_display, ""]

    if agenda is not None:
        a = result["agenda"]
        lines += [
            "현안: {} {}".format(a["id"], a["현안명"]),
            "단계: {} · 최근진전 {}".format(a["단계"] or "-", a["최근진전일"] or "-"),
            "선정사유: " + a["선정사유"],
        ]
        if a["확인필요"]:
            lines.append("확인필요: " + a["확인필요"])
        if a["관련고시"]:
            lines.append("")
            lines.append("관련 고시 {}건".format(len(a["관련고시"])))
            for n in a["관련고시"][-4:]:
                lines.append("  " + n["date"] + " " + n["title"][:50])
        if a["최근뉴스"]:
            lines.append("")
            lines.append("최근 뉴스")
            for n in a["최근뉴스"][:5]:
                lines.append("  " + n["date"] + " " + n["title"][:50])
        if skip_log:
            lines.append("")
            lines.append("순환 판정")
            lines += ["  " + s for s in skip_log]
        lines.append("")

    for r in rows:
        marker = "★ " if r["keyword"] == top_keyword else "- "
        lines.append(
            marker + r["keyword"]
            + " | 블로그 " + str(r["blog"])
            + " | 카페 " + str(r["cafe"])
            + " | 트렌드지수 " + str(r["trend"])
            + " | 스코어 " + str(r["score"])
        )
    lines += ["", "이번 주 대표 키워드: " + top_keyword]

    with open("telegram_message.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print("\n".join(lines))


if __name__ == "__main__":
    main()
