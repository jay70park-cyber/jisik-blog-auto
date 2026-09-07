# -*- coding: utf-8 -*-
"""
화성특례시 고시공고 수집

지역 현안은 기사보다 지자체 고시가 먼저 나온다.
"○○지구 도시관리계획 결정 고시"가 뜨면 그게 곧 현안의 시작이고,
언론 기사는 그것을 받아쓴 결과물이다.

RSS가 없어(서비스 중단) 게시판 HTML을 직접 읽는다.
사이트 구조가 바뀌면 파싱이 깨질 수 있으므로,
0건이 나오면 HTML 일부를 출력해 원인을 바로 볼 수 있게 했다.

입력  없음 (부서 목록은 아래 DEPARTMENTS)
출력  data/hscity_notices.csv       수집한 고시 목록
      data/hscity_new.txt           이번에 새로 발견된 것 (텔레그램 발송용)
"""
import os
import re
import csv
import time
import html
import datetime
import urllib.request
import urllib.parse

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

DATA_DIR = "data"
OUT_CSV = os.path.join(DATA_DIR, "hscity_notices.csv")

BASE = "https://www.hscity.go.kr/www/gosi/BD_notice.do"

# 부동산·개발과 관련된 부서만 본다.
# 노사협력과·복지과 같은 곳은 지산 콘텐츠와 무관해 아예 조회하지 않는다.
DEPARTMENTS = [
    "투자유치과",
    "토지정보과",
    "도로과",
    "도시정책과",
    "도시개발과",
    "주택정책과",
    "건축과",
    "산업입지과",
]

# 제목에 이 말이 있으면 현안 후보로 본다.
AGENDA_WORDS = [
    "산업단지", "지식산업센터", "도시관리계획", "지구단위계획",
    "실시계획", "도시개발", "택지", "용도지역", "용도변경",
    "도로", "철도", "역세권", "교통", "개발행위",
    "건축허가", "사업계획", "지정", "승인", "결정", "변경",
]

# 동탄권 지명. 제목에 있으면 우선순위를 올린다.
DONGTAN_WORDS = [
    "동탄", "영천동", "여울동", "능동", "반송동", "석우동", "송동",
    "신동", "오산동", "청계동", "산척동", "장지동", "목동", "방교동",
]

FIELDS = ["부서", "고시번호", "제목", "공고일자", "게재기간", "동탄관련", "수집일"]


def fetch(dep, page=1, timeout=30, retries=2):
    """부서별 고시 목록 HTML을 받아온다."""
    params = {
        "q_notAncmtSeCode": "01",     # 01 = 고시
        "q_depNm": dep,
    }
    if page > 1:
        params["q_cp"] = page
    url = BASE + "?" + urllib.parse.urlencode(params)

    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "ko-KR,ko;q=0.9",
            })
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return res.read().decode("utf-8", errors="replace")
        except Exception as e:
            print("  조회 실패 ({}/{}): {}".format(attempt, retries, e))
            if attempt < retries:
                time.sleep(attempt * 3)
    return ""


def strip_tags(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def parse_rows(page_html):
    """표의 각 행에서 고시번호·제목·부서·날짜를 뽑는다.

    사이트 구조를 모르는 상태로 만든 파서라 <tr> 안의 <td>를 순서대로 읽는다.
    열 순서가 바뀌면 어긋나므로, 값의 생김새로 어느 칸인지 판별한다.
    """
    rows = []
    for m in re.finditer(r"<tr[^>]*>(.*?)</tr>", page_html, re.S | re.I):
        tds = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", m.group(1), re.S | re.I)
        if len(tds) < 3:
            continue
        cells = [strip_tags(t) for t in tds]

        no = title = dep = date = period = ""
        for c in cells:
            if not c:
                continue
            if re.search(r"고시\s*제?\s*\d{4}-\d+호", c):
                no = c
            elif re.match(r"^\d{4}-\d{2}-\d{2}$", c):
                if not date:
                    date = c
            elif re.match(r"^\d{4}-\d{2}-\d{2}\s*~", c):
                period = c
            elif c.endswith("과") or c.endswith("관") or c.endswith("소"):
                dep = c
            elif len(c) > 8 and not title:
                title = c

        if no or (title and date):
            rows.append({
                "고시번호": no,
                "제목": title,
                "부서": dep,
                "공고일자": date,
                "게재기간": period,
            })
    return rows


def is_agenda(title):
    return any(w in title for w in AGENDA_WORDS)


def is_dongtan(title):
    return any(w in title for w in DONGTAN_WORDS)


def load_existing():
    if not os.path.exists(OUT_CSV):
        return {}
    out = {}
    with open(OUT_CSV, "r", encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            key = (r.get("고시번호", ""), r.get("제목", ""))
            out[key] = r
    return out


def send_telegram(text, timeout=20):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendMessage"
    body = urllib.parse.urlencode(
        {"chat_id": TELEGRAM_CHAT_ID, "text": text}).encode("utf-8")
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, data=body, method="POST"),
                timeout=timeout) as res:
            res.read()
    except Exception as e:
        print("텔레그램 전송 오류: " + str(e))


def diagnose(page_html):
    """파싱이 0건일 때 원인을 보여준다. 구조를 모르는 채 고치는 것을 막는다."""
    print("\n=== 진단 ===")
    print("HTML 길이: {}자".format(len(page_html)))
    print("<table> 개수: {}".format(len(re.findall(r"<table", page_html, re.I))))
    print("<tr> 개수: {}".format(len(re.findall(r"<tr", page_html, re.I))))
    for word in ["고시공고번호", "담당부서", "데이터가 존재하지",
                 "일시중단", "서비스", "로그인"]:
        if word in page_html:
            print("본문에 '{}' 있음".format(word))
    m = re.search(r"<table.*?</table>", page_html, re.S | re.I)
    if m:
        print("\n--- 첫 table 앞부분 1200자 ---")
        print(m.group(0)[:1200])
    else:
        print("\n--- body 앞부분 1200자 ---")
        b = re.search(r"<body.*?>(.*)", page_html, re.S | re.I)
        print((b.group(1) if b else page_html)[:1200])
    print("=== 진단 끝 ===\n")


def main():
    today = datetime.date.today().isoformat()
    existing = load_existing()
    print("기존 누적: {}건".format(len(existing)))

    all_rows = []
    first_html = ""
    for dep in DEPARTMENTS:
        page_html = fetch(dep)
        if not page_html:
            print("{} : HTML을 받지 못했습니다.".format(dep))
            continue
        if not first_html:
            first_html = page_html

        rows = parse_rows(page_html)
        kept = 0
        for r in rows:
            if not r["제목"] or not is_agenda(r["제목"]):
                continue
            r["부서"] = r["부서"] or dep
            r["동탄관련"] = "O" if is_dongtan(r["제목"]) else ""
            r["수집일"] = today
            all_rows.append(r)
            kept += 1
        print("{} : 행 {}개 중 현안 후보 {}건".format(dep, len(rows), kept))
        time.sleep(0.5)

    if not all_rows:
        print("\n수집된 고시가 없습니다.")
        if first_html:
            diagnose(first_html)
        else:
            print("모든 요청이 실패했습니다. 러너에서 화성시 홈페이지에 "
                  "접근하지 못하는 것으로 보입니다.")
        raise SystemExit(1)

    # 새로 발견된 것만 추린다
    new_rows = []
    merged = dict(existing)
    for r in all_rows:
        key = (r["고시번호"], r["제목"])
        if key not in merged:
            new_rows.append(r)
        merged[key] = r

    os.makedirs(DATA_DIR, exist_ok=True)
    ordered = sorted(merged.values(),
                     key=lambda r: r.get("공고일자", ""), reverse=True)
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(ordered)
    print("\n저장: {} (누적 {}건, 신규 {}건)".format(
        OUT_CSV, len(ordered), len(new_rows)))

    if not new_rows:
        print("새로 올라온 고시가 없습니다.")
        return

    # 동탄 관련을 위로
    new_rows.sort(key=lambda r: (r["동탄관련"] != "O", r.get("공고일자", "")))

    lines = ["[화성시 신규 고시] " + today, ""]
    for r in new_rows[:20]:
        mark = "★" if r["동탄관련"] == "O" else " "
        lines.append("{} {} | {}".format(mark, r.get("공고일자", ""), r["제목"]))
        lines.append("    {} {}".format(r.get("부서", ""), r.get("고시번호", "")))
    lines += [
        "",
        "★ = 동탄권 관련",
        "글감이 될 만한 것은 data/local_agenda.csv 에 추가하세요.",
    ]
    text = "\n".join(lines)
    print()
    print(text)
    send_telegram(text)


if __name__ == "__main__":
    main()
