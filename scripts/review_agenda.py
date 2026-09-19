# -*- coding: utf-8 -*-
"""
현안 목록 점검 — 매월 1·15일

local_agenda.csv 가 안건이 모이는 단 하나의 창구다.
들어오는 길은 셋이다.

  ① 구글 뉴스   → 텔레그램에서 + 로 채택
  ② 화성시 고시 → 텔레그램에서 + 로 채택
  ③ Jay 수기    → CSV 직접 편집

나가는 길은 둘이다. 어느 쪽이든 행은 지우지 않는다.

  - 보류  당분간 조용하다. 나중에 되살릴 수 있다
  = 완료  준공됐거나 끝났다. 순환에서 영구 제외

빼는 손잡이가 없으면 목록이 늘기만 한다.
9건이면 목요일 기준 석 달에 한 바퀴인데, 15건이 되면 다섯 달이 된다.
그러면 트램에 큰 진전이 있어도 다섯 달 뒤에나 글이 나간다.

무응답이면 아무것도 바꾸지 않는다.
키워드 발굴(discover_local_keywords.py)은 무응답 시 전부 채택이지만
여기는 반대다. 답장을 놓친 날 목록이 통째로 흔들리면 안 된다.
"""
import os
import re
import csv
import json
import time
import datetime
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

MODEL = "claude-sonnet-5"

DATA_DIR = "data"
AGENDA_CSV = os.path.join(DATA_DIR, "local_agenda.csv")
NOTICE_CSV = os.path.join(DATA_DIR, "hscity_notices.csv")

KST = datetime.timezone(datetime.timedelta(hours=9))

WAIT_SECONDS = int(os.environ.get("REVIEW_WAIT_SECONDS", "600"))   # 10분
POLL_INTERVAL = 20

NEWS_DAYS = 21          # 구글 뉴스를 이 기간만 본다
NOTICE_DAYS = 45        # 고시 후보는 이 기간만
NEWS_CANDIDATES = 8     # 뉴스 후보 최대
NOTICE_CANDIDATES = 8   # 고시 후보 최대

STALE_DAYS = 90         # 최근진전일이 이만큼 지나면 ⚠ 표시

# 번호대를 나눈다. 기존 안건에 -, 후보에 + 를 쓰는데
# 번호가 이어져 있으면 '-11' 같은 오타가 조용히 무시된다.
BASE_EXISTING = 1
BASE_NEWS = 11
BASE_NOTICE = 21

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

NEWS_QUERIES = [
    "동탄 개발", "화성시 개발", "동탄 지식산업센터",
    "동탄 산업단지", "동탄 교통",
]

STATUS_ACTIVE = "활성"
STATUS_HOLD = "보류"
STATUS_DONE = "완료"


def today_kst():
    """러너는 UTC로 돈다. 날짜가 걸린 곳은 전부 이 함수를 쓴다."""
    return datetime.datetime.now(KST).date()


def log(*args):
    print(*args, flush=True)


# ─────────────────────────────────────────────
# CSV
# ─────────────────────────────────────────────

def load_agenda():
    """전체 행을 그대로 읽는다. 상태와 무관하게 다 가져온다."""
    if not os.path.exists(AGENDA_CSV):
        log("아젠다 파일 없음: " + AGENDA_CSV)
        return [], []
    with open(AGENDA_CSV, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        cols = list(reader.fieldnames or [])
        rows = list(reader)
    return rows, cols


def save_agenda(rows, cols):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(AGENDA_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def next_id(rows):
    """A001 다음은 A002. 빈 목록이면 A001."""
    nums = []
    for r in rows:
        m = re.match(r"^A(\d+)$", (r.get("id") or "").strip())
        if m:
            nums.append(int(m.group(1)))
    return "A{:03d}".format(max(nums) + 1 if nums else 1)


def days_since(datestr):
    s = (datestr or "").strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return None
    try:
        return (today_kst() - datetime.date.fromisoformat(s)).days
    except Exception:
        return None


# 거의 모든 안건에 들어가는 말. 중복 판정에서 뺀다.
# 이것을 안 빼면 "동탄" 두 글자만으로 모든 안건이 유사로 걸린다.
COMMON_WORDS = {
    "동탄", "화성", "화성시", "용인", "경기", "경기도",
    "조성", "정비", "변화", "사업", "개발", "건립", "공급", "전환",
}


def agenda_tokens(row):
    """중복 판정에 쓸 낱말.

    현안명이 아니라 키워드로 본다. 현안명은 부르는 사람마다 달라서
    겹쳐도 다른 사안이고, 안 겹쳐도 같은 사안일 수 있다.
    반면 키워드는 고시 제목을 찾는 말이라, 겹치면 같은 고시를 가져간다.
    그것이 실제로 문제가 되는 겹침이다.
    """
    out = set()
    for w in (row.get("키워드") or "").split("|"):
        w = w.strip()
        if len(w) >= 2 and w not in COMMON_WORDS:
            out.add(w)
    return out


def similar_to(keywords, rows):
    """키워드가 겹치는 기존 안건의 id 목록을 돌려준다.

    자동으로 걸러내지 않고 표시만 한다.
    걸러내면 실제로는 다른 사안인데 조용히 사라진다.
    """
    mine = {w.strip() for w in (keywords or "").split("|")
            if len(w.strip()) >= 2 and w.strip() not in COMMON_WORDS}
    if not mine:
        return []
    hits = []
    for r in rows:
        if (r.get("상태") or "").strip() != STATUS_ACTIVE:
            continue
        if agenda_tokens(r) & mine:
            hits.append(r.get("id", ""))
    return hits


# ─────────────────────────────────────────────
# 후보 ① 구글 뉴스
# ─────────────────────────────────────────────

def parse_rss_date(s):
    if not s:
        return None
    try:
        return datetime.datetime.strptime(
            s[:25].strip(), "%a, %d %b %Y %H:%M:%S").date()
    except Exception:
        return None


def google_news(query, days=NEWS_DAYS, timeout=20):
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": query, "hl": "ko", "gl": "KR", "ceid": "KR:ko"})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw = res.read()
    except Exception as e:
        log("  구글 뉴스 오류({}): {}".format(query, e))
        return []
    cutoff = today_kst() - datetime.timedelta(days=days)
    out = []
    try:
        root = ET.fromstring(raw)
    except Exception as e:
        log("  구글 뉴스 파싱 실패: " + str(e))
        return []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        d = parse_rss_date((item.findtext("pubDate") or "").strip())
        if title and d and d >= cutoff:
            out.append((d.isoformat(), title))
    return out


CLUSTER_PROMPT = """아래는 최근 {days}일간 동탄·화성 관련 뉴스 제목 목록이다.
동탄 지식산업센터·상가 블로그에서 계속 추적할 만한 '지역 현안'으로 묶어라.

# 이미 추적 중인 현안 (이것과 겹치는 것은 만들지 마라)
{existing}

# 묶는 기준
- 여러 기사가 같은 사업을 말하고 있으면 하나로 묶는다
- 한 번 언급되고 끝날 일은 만들지 않는다. 몇 달에 걸쳐 진행되는 사업만
- 동탄·화성 밖의 일은 제외한다
- 사건사고, 인사, 행사, 스포츠는 제외한다

# 키워드가 가장 중요하다
키워드는 화성시 고시 제목과 회의록에서 그 현안을 찾아낼 말이다.
기사 제목의 표현이 아니라 **행정 문서에 나올 법한 말**로 골라라.
지번을 박지 마라. 고시 제목에 그 번지가 그대로 나올 때만 걸린다.
키워드가 없으면 그 현안은 영원히 갱신되지 않는다.

  좋은 예: 준대규모점포|대규모점포        (고시 제목에 실제로 쓰이는 말)
  나쁜 예: GS더프레시동탄점               (한 점포 이름)
  좋은 예: 일반산업단지|산업단지계획
  나쁜 예: 장지동 1131                    (번지가 바뀌면 안 걸린다)

# 출력
아래 JSON만 출력한다. 다른 말은 붙이지 않는다.
후보가 없으면 빈 배열로 둔다. 빈 배열은 올바른 답이다.

{{"candidates": [
  {{"현안명": "12자 이내",
    "분류": "교통|의료|개발|산업|상권 중 하나",
    "키워드": "파이프로 구분한 2~5개",
    "근거": "이 현안으로 묶은 기사 제목 하나",
    "중요도": "상|중"}}
]}}

최대 {limit}건. 억지로 채우지 마라.

뉴스 제목:
{titles}
"""


NOTICE_PROMPT = """아래는 최근 {days}일간 화성시가 낸 동탄권 고시·공고 제목이다.
이 가운데 계속 추적할 만한 '지역 현안'으로 묶어라.

# 이미 추적 중인 현안 (이것과 겹치는 것은 만들지 마라)
{existing}

# 묶는 기준
- 여러 고시가 같은 사업을 말하고 있으면 하나로 묶는다
- 한 번 나오고 끝날 행정 처리는 만들지 않는다
  (도로지정공고, 경미한 변경, 개별 필지 처리 같은 것)
- 몇 달에 걸쳐 단계가 진행될 사업만 고른다

# 키워드가 가장 중요하다
이 키워드로 앞으로의 고시를 찾아낸다. 두 가지를 다 피해야 한다.

  너무 넓다: 실시계획|도시계획시설|택지개발지구
    → 화성시 고시 절반이 걸린다. 그 현안이 온갖 고시를 빨아들인다
  너무 좁다: 여울공원 보도블록 정비
    → 다음 고시 제목이 조금만 달라도 안 걸린다

그 사업을 부르는 고유한 말을 2~4개 고른다.
지번은 쓰지 마라. 고시 제목에 그 번지가 그대로 나올 때만 걸린다.

# 출력
아래 JSON만 출력한다. 다른 말은 붙이지 않는다.
쓸 만한 것이 없으면 빈 배열로 둔다. 빈 배열은 올바른 답이다.

{{"candidates": [
  {{"현안명": "12자 이내. 지역+무엇 형태 (예: 동탄 공원 정비)",
    "분류": "교통|의료|개발|산업|상권 중 하나",
    "키워드": "파이프로 구분한 2~4개",
    "근거": "이 현안으로 묶은 고시 제목 하나",
    "중요도": "상|중"}}
]}}

최대 {limit}건. 억지로 채우지 마라.

고시 제목:
{titles}
"""


def call_claude(prompt, timeout=120):
    if not ANTHROPIC_API_KEY:
        return ""
    body = json.dumps({
        "model": MODEL, "max_tokens": 2000,
        "messages": [{"role": "user", "content": prompt}],
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body, method="POST",
        headers={"x-api-key": ANTHROPIC_API_KEY,
                 "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            data = json.load(res)
    except Exception as e:
        log("Claude 호출 실패: " + str(e))
        return ""
    parts = data.get("content", [])
    text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
    log("  stop_reason: {} / 텍스트 {}자".format(
        data.get("stop_reason"), len(text)))
    return text


def news_candidates(rows):
    """뉴스 제목을 모아 현안 후보로 묶는다."""
    seen, titles = set(), []
    for q in NEWS_QUERIES:
        for d, t in google_news(q):
            if t not in seen:
                seen.add(t)
                titles.append((d, t))
        time.sleep(0.4)
    log("뉴스 제목 {}건 수집".format(len(titles)))
    if not titles:
        return []

    titles.sort(reverse=True)
    existing = "\n".join(
        "- {} ({})".format(r.get("현안명", ""), r.get("키워드", ""))
        for r in rows if (r.get("상태") or "").strip() == STATUS_ACTIVE
    ) or "- (없음)"

    prompt = CLUSTER_PROMPT.format(
        days=NEWS_DAYS, existing=existing, limit=NEWS_CANDIDATES,
        titles="\n".join("- {} {}".format(d, t) for d, t in titles[:120]))
    text = call_claude(prompt)
    if not text.strip():
        return []
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        m = re.search(r"\{.*\}", text, re.S)
        parsed = json.loads(m.group(0) if m else text)
    except Exception as e:
        log("후보 JSON 파싱 실패: " + str(e))
        return []

    out = []
    for c in parsed.get("candidates", [])[:NEWS_CANDIDATES]:
        name = str(c.get("현안명", "")).strip()
        kw = str(c.get("키워드", "")).strip()
        if not name or not kw:
            continue          # 키워드 없는 안건은 받지 않는다
        out.append({
            "현안명": name,
            "분류": str(c.get("분류", "")).strip() or "개발",
            "키워드": kw,
            "근거": str(c.get("근거", "")).strip(),
            "중요도": str(c.get("중요도", "")).strip() or "중",
            "출처": "뉴스",
        })
    return out


# ─────────────────────────────────────────────
# 후보 ② 화성시 고시
# ─────────────────────────────────────────────

def notice_candidates(rows):
    """동탄권인데 현안에 안 걸린 고시를 후보로 묶는다.

    매일 알림에서 눈으로 훑고 지나친 것을 2주에 한 번 다시 걸러준다.
    고시는 뉴스보다 신뢰도가 높고 이미 필터를 거쳐 들어온 것이라
    후보로서 질이 좋다.

    다만 제목에서 기계적으로 키워드를 뽑으면 '실시계획', '도시계획시설'
    같은 말이 나온다. 그러면 그 현안이 화성시 고시 절반을 빨아들인다.
    그래서 뉴스와 똑같이 Claude 에게 묶게 한다.
    """
    if not os.path.exists(NOTICE_CSV):
        log("고시 파일 없음: " + NOTICE_CSV)
        return []
    cutoff = (today_kst() - datetime.timedelta(days=NOTICE_DAYS)).isoformat()
    picked = []
    with open(NOTICE_CSV, "r", encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if (r.get("동탄관련") or "").strip() != "O":
                continue
            if (r.get("현안") or "").strip():
                continue          # 이미 현안에 걸린 것
            if (r.get("공고일자") or "") < cutoff:
                continue
            picked.append(r)

    picked.sort(key=lambda r: r.get("공고일자", ""), reverse=True)
    log("고시 원본 {}건".format(len(picked)))
    if not picked:
        return []

    existing = "\n".join(
        "- {} ({})".format(r.get("현안명", ""), r.get("키워드", ""))
        for r in rows if (r.get("상태") or "").strip() == STATUS_ACTIVE
    ) or "- (없음)"

    titles = "\n".join("- {} [{}] {}".format(
        r.get("공고일자", ""), r.get("부서", ""), r.get("제목", ""))
        for r in picked[:60])

    text = call_claude(NOTICE_PROMPT.format(
        days=NOTICE_DAYS, existing=existing,
        limit=NOTICE_CANDIDATES, titles=titles))
    if not text.strip():
        return []
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        m = re.search(r"\{.*\}", text, re.S)
        parsed = json.loads(m.group(0) if m else text)
    except Exception as e:
        log("고시 후보 JSON 파싱 실패: " + str(e))
        return []

    out = []
    for c in parsed.get("candidates", [])[:NOTICE_CANDIDATES]:
        name = str(c.get("현안명", "")).strip()
        kw = str(c.get("키워드", "")).strip()
        if not name or not kw:
            continue
        out.append({
            "현안명": name[:16],
            "분류": str(c.get("분류", "")).strip() or "개발",
            "키워드": kw,
            "근거": str(c.get("근거", "")).strip(),
            "중요도": str(c.get("중요도", "")).strip() or "중",
            "출처": "고시",
        })
    return out


# ─────────────────────────────────────────────
# 텔레그램
# ─────────────────────────────────────────────

def send_message(text, timeout=20):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log("(텔레그램 설정 없음 — 전송 건너뜀)")
        return
    url = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendMessage"
    body = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID, "text": text[:3900],
        "disable_web_page_preview": "true"}).encode("utf-8")
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, data=body, method="POST"),
                timeout=timeout) as res:
            res.read()
    except Exception as e:
        log("텔레그램 전송 오류: " + str(e))


def get_updates(offset, timeout=20):
    if not TELEGRAM_BOT_TOKEN:
        return []
    url = ("https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN
           + "/getUpdates?" + urllib.parse.urlencode(
               {"offset": offset, "timeout": 0}))
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url), timeout=timeout) as res:
            return json.load(res).get("result", [])
    except Exception as e:
        log("getUpdates 오류: " + str(e))
        return []


def latest_update_id():
    return max([u["update_id"] for u in get_updates(0)], default=0)


def wait_for_reply(baseline):
    """답장을 기다린다. 없으면 None — 그때는 아무것도 바꾸지 않는다."""
    deadline = time.time() + WAIT_SECONDS
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL)
        ups = get_updates(baseline + 1)
        msgs = [u for u in ups
                if u.get("message", {}).get("chat", {}).get("id")
                == int(TELEGRAM_CHAT_ID or 0)
                and u.get("message", {}).get("text")]
        if msgs:
            return msgs[-1]["message"]["text"].strip()
        remain = int(deadline - time.time())
        if remain > 0:
            log("대기 중... 남은 시간 {}초".format(remain))
    return None


def build_message(active, news, notices, rows):
    lines = ["[안건 점검] " + today_kst().strftime("%-m/%d"), ""]

    lines.append("■ 현재 안건  ( - 보류 / = 완료 )")
    for i, r in enumerate(active, BASE_EXISTING):
        d = days_since(r.get("최근진전일"))
        warn = "  ⚠{}일".format(d) if d is not None and d >= STALE_DAYS else ""
        lines.append("{:>2} {} {}".format(i, r.get("id", ""), r.get("현안명", "")))
        lines.append("     {} · {}{}".format(
            (r.get("단계") or "-"), (r.get("최근진전일") or "-"), warn))

    if news:
        lines += ["", "■ 뉴스 후보  ( + 채택 )"]
        for i, c in enumerate(news, BASE_NEWS):
            dup = similar_to(c["키워드"], rows)
            tag = "  ※{} 유사".format(",".join(dup)) if dup else ""
            lines.append("{:>2} {}{}".format(i, c["현안명"], tag))
            lines.append("     {}".format(c["키워드"][:44]))
            if c.get("근거"):
                lines.append("     {}".format(c["근거"][:46]))

    if notices:
        lines += ["", "■ 고시 후보  ( + 채택 )"]
        for i, c in enumerate(notices, BASE_NOTICE):
            dup = similar_to(c["키워드"], rows)
            tag = "  ※{} 유사".format(",".join(dup)) if dup else ""
            lines.append("{:>2} {}{}".format(i, c["현안명"], tag))
            lines.append("     {}".format(c["키워드"][:44]))
            if c.get("근거"):
                lines.append("     {}".format(c["근거"][:46]))

    lines += [
        "",
        "─────────────",
        "답장 예: -2 =5 +12 +21",
        "무응답이면 아무것도 바꾸지 않습니다.",
        "({}분 대기)".format(max(1, WAIT_SECONDS // 60)),
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────
# 답장 처리
# ─────────────────────────────────────────────

def parse_reply(text):
    """'-2 =5 +12' 를 {'-': [2], '=': [5], '+': [12]} 로."""
    out = {"-": [], "=": [], "+": []}
    bad = []
    for m in re.finditer(r"([-=+])\s*(\d+)", text or ""):
        out[m.group(1)].append(int(m.group(2)))
    # 기호 없이 숫자만 적은 경우를 잡아낸다
    stripped = re.sub(r"[-=+]\s*\d+", "", text or "")
    for m in re.finditer(r"\d+", stripped):
        bad.append(m.group(0))
    return out, bad


def apply_reply(picks, active, news, notices):
    """CSV 를 다시 읽어 병합한다.

    07:10 고시·회의록 수집이 그 사이 단계와 진전일을 갱신했을 수 있다.
    07:31 에 읽어둔 내용을 그대로 덮으면 그 갱신이 날아간다.
    """
    rows, cols = load_agenda()
    for c in ("위도", "경도"):
        if c not in cols:
            cols.append(c)
    by_id = {(r.get("id") or "").strip(): r for r in rows}
    today = today_kst().isoformat()
    changed = []

    for sym, status in (("-", STATUS_HOLD), ("=", STATUS_DONE)):
        for n in picks[sym]:
            idx = n - BASE_EXISTING
            if not (0 <= idx < len(active)):
                changed.append(("무시", "{}{} — 그런 번호가 없습니다".format(sym, n)))
                continue
            aid = active[idx].get("id", "")
            row = by_id.get(aid)
            if row is None:
                changed.append(("무시", "{}{} — {} 를 못 찾음".format(sym, n, aid)))
                continue
            row["상태"] = status
            changed.append((status, "{} {}".format(aid, row.get("현안명", ""))))

    for n in picks["+"]:
        if BASE_NEWS <= n < BASE_NEWS + len(news):
            c = news[n - BASE_NEWS]
            new = {
                "현안명": c["현안명"], "분류": c["분류"],
                "단계": "미정", "최근진전일": today,
                "진전내용": "[뉴스] " + c.get("근거", "")[:70],
                "중요도": c["중요도"], "출처": "뉴스",
                "키워드": c["키워드"], "메모": "",
                "확인필요": "단계 확인 · 좌표 입력",
            }
        elif BASE_NOTICE <= n < BASE_NOTICE + len(notices):
            c = notices[n - BASE_NOTICE]
            new = {
                "현안명": c["현안명"], "분류": c["분류"],
                "단계": "미정", "최근진전일": today,
                "진전내용": "[고시] " + c.get("근거", "")[:70],
                "중요도": c["중요도"], "출처": "고시",
                "키워드": c["키워드"], "메모": "",
                "확인필요": "단계 확인 · 좌표 입력",
            }
        else:
            changed.append(("무시", "+{} — 그런 번호가 없습니다".format(n)))
            continue

        new["id"] = next_id(rows)
        new["상태"] = STATUS_ACTIVE
        new["추가일"] = today
        new["마지막발행일"] = ""
        new["위도"] = ""
        new["경도"] = ""
        for c2 in cols:
            new.setdefault(c2, "")
        rows.append(new)
        by_id[new["id"]] = new
        changed.append(("추가", "{} {}".format(new["id"], new["현안명"])))

    if changed:
        save_agenda(rows, cols)
    return changed


# ─────────────────────────────────────────────

def main():
    rows, cols = load_agenda()
    if not rows:
        log("현안 목록이 비어 있습니다.")
        return
    active = [r for r in rows
              if (r.get("상태") or STATUS_ACTIVE).strip() == STATUS_ACTIVE]
    active.sort(key=lambda r: r.get("id", ""))
    log("활성 {}건 / 전체 {}건".format(len(active), len(rows)))

    log("\n[뉴스 후보]")
    news = news_candidates(rows)
    log("  {}건".format(len(news)))

    log("\n[고시 후보]")
    notices = notice_candidates(rows)
    log("  {}건".format(len(notices)))

    msg = build_message(active, news, notices, rows)
    log("\n" + msg)

    baseline = latest_update_id()
    send_message(msg)

    reply = wait_for_reply(baseline)
    if not reply:
        log("\n무응답 — 아무것도 바꾸지 않습니다.")
        return

    log("\n답장: " + reply)
    picks, bad = parse_reply(reply)
    if not any(picks.values()):
        note = "기호를 못 읽었습니다. -2 =5 +12 형태로 다시 보내주세요."
        if bad:
            note += "\n기호 없는 숫자: " + ", ".join(bad)
        log(note)
        send_message("[안건 점검] " + note)
        return

    changed = apply_reply(picks, active, news, notices)

    lines = ["[안건 점검 반영]"]
    for kind, what in changed:
        lines.append("  {} {}".format(kind, what))
    if bad:
        lines.append("")
        lines.append("기호 없이 적힌 숫자는 무시했습니다: " + ", ".join(bad))
    added = [w for k, w in changed if k == "추가"]
    if added:
        lines += ["", "새 안건은 좌표가 비어 있어 지도가 안 붙습니다.",
                  "네이버·구글 지도에서 Plus Code 를 받아 알려주세요."]
    out = "\n".join(lines)
    log("\n" + out)
    send_message(out)


if __name__ == "__main__":
    main()
