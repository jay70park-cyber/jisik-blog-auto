# -*- coding: utf-8 -*-
"""
화성특례시 고시·공고 수집

지역 현안은 기사보다 지자체 고시가 먼저 나온다.
"○○지구 도시관리계획 결정 고시"가 뜨면 그게 현안의 시작이고,
언론 기사는 그것을 받아쓴 결과물이다.

설계 근거 (2026-09 실측)
- RSS 없음. 게시판 HTML을 직접 읽는다.
- 검색폼이 POST라서 q_depNm(부서), q_currPage(페이지)를 URL에 붙여도
  무시되고 항상 1페이지가 온다. 그래서 부서 필터는 파이썬에서 건다.
- 1페이지는 10건뿐이다. 하루 게시량이 그 정도라 매일 돌려야 놓치지 않는다.
- 게시판이 둘이다. 고시(BD_notice)와 일반공고(BD_selectGosiList).
  현안은 대부분 고시 쪽이지만 주민 의견청취 공고는 일반공고로 나온다.

출력  data/hscity_notices.csv   누적 목록
      텔레그램                   이번에 새로 발견된 것
"""
import os
import re
import csv
import time
import html
import datetime
import urllib.request

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

DATA_DIR = "data"
OUT_CSV = os.path.join(DATA_DIR, "hscity_notices.csv")

BOARDS = [
    ("고시", "https://www.hscity.go.kr/www/gosi/BD_notice.do"),
    ("일반공고", "https://www.hscity.go.kr/www/gosi/BD_selectGosiList.do"),
]
DETAIL = ("https://www.hscity.go.kr/www/gosi/"
          "BD_selectNoticeDetail.do?q_notAncmtMgtNo=")

# 이 부서 것이면 제목과 무관하게 일단 담는다.
# 실제 부서선택 드롭다운에서 확인한 이름 그대로 쓴다.
WATCH_DEPTS = {
    "도시정책과", "도시개발과", "신도시조성과", "토지정보과",
    "도시계획상임기획단", "도로과", "철도전략과", "교통정책과",
    "주택정책과", "공동주택과", "건축과", "공공건축과",
    "투자유치과", "첨단산업과", "지역경제과", "건설과",
    "트램건설추진단", "도시건축과", "도시환경과",
}

# 다른 부서 것이라도 제목에 이 말이 있으면 담는다.
AGENDA_WORDS = [
    "산업단지", "지식산업센터", "도시관리계획", "지구단위계획",
    "실시계획", "도시개발", "택지", "용도지역", "용도지구", "용도변경",
    "개발행위", "지형도면", "사업인정", "수용", "보상", "환지",
    "도로구역", "철도", "역세권", "트램", "정비구역", "주택건설",
]

# 부서가 맞아도 이 말이 있으면 버린다. 행정 잡무다.
NOISE_WORDS = [
    "공시송달", "과태료", "반송", "체납", "압류", "공매",
    "생활임금", "채용", "모집", "선정 결과", "입찰", "낙찰",
    "위원 공개모집", "성과평가", "정기분", "면허세",
]

DONGTAN_WORDS = [
    "동탄", "영천동", "여울동", "능동", "반송동", "석우동", "송동",
    "신동", "오산동", "청계동", "산척동", "장지동", "목동", "방교동",
]

FIELDS = ["게시판", "부서", "고시번호", "제목", "공고일자",
          "게재기간", "동탄관련", "링크", "수집일"]


def fetch(url, timeout=30, retries=3):
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
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def parse_rows(page_html):
    """목록 표를 읽는다.

    열 순서는 고정이다.
      0 고시공고번호  1 제목  2 담당부서  3 게재(공고)일자  4 게재기간
    제목 칸의 링크는 javascript:opGosiView('149620') 형태이고,
    그 숫자가 상세페이지 관리번호다.
    """
    rows = []
    for m in re.finditer(r"<tr[^>]*>(.*?)</tr>", page_html, re.S | re.I):
        block = m.group(1)
        if re.search(r"<th[^>]*>", block, re.I):
            continue
        tds = re.findall(r"<td[^>]*>(.*?)</td>", block, re.S | re.I)
        if len(tds) < 4:
            continue

        cells = [strip_tags(t) for t in tds]
        num = re.search(r"opGosiView\(\s*['\"](\d+)['\"]", tds[1])
        date = cells[3] if re.match(r"^\d{4}-\d{2}-\d{2}$", cells[3]) else ""

        if not cells[1] or not date:
            continue

        rows.append({
            "고시번호": cells[0],
            "제목": cells[1],
            "부서": cells[2],
            "공고일자": date,
            "게재기간": cells[4] if len(cells) > 4 else "",
            "링크": DETAIL + num.group(1) if num else "",
        })
    return rows


def keep(row):
    title = row["제목"]
    if any(w in title for w in NOISE_WORDS):
        return False
    return row["부서"] in WATCH_DEPTS or any(w in title for w in AGENDA_WORDS)


def is_dongtan(title):
    return any(w in title for w in DONGTAN_WORDS)


def load_existing():
    if not os.path.exists(OUT_CSV):
        return {}
    with open(OUT_CSV, "r", encoding="utf-8-sig", newline="") as f:
        return {(r.get("고시번호", ""), r.get("제목", "")): r
                for r in csv.DictReader(f)}


def send_telegram(text, timeout=20):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    import urllib.parse
    url = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendMessage"
    body = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, data=body, method="POST"),
                timeout=timeout) as res:
            res.read()
    except Exception as e:
        print("텔레그램 전송 오류: " + str(e))


def diagnose(page_html):
    """0건일 때 원인을 보여준다. 구조를 모르는 채 고치는 것을 막는다."""
    print("\n=== 진단 ===")
    print("HTML 길이 {}자, <tr> {}개".format(
        len(page_html), len(re.findall(r"<tr", page_html, re.I))))
    for w in ["고시공고번호", "담당부서", "opGosiView",
              "데이터가 존재하지", "일시중단", "로그인"]:
        if w in page_html:
            print("'{}' 있음".format(w))
    m = re.search(r"<table.*?</table>", page_html, re.S | re.I)
    print("\n--- 첫 table 1500자 ---")
    print((m.group(0) if m else page_html)[:1500])
    print("=== 진단 끝 ===\n")


def main():
    today = datetime.date.today().isoformat()
    existing = load_existing()
    print("기존 누적 {}건".format(len(existing)))

    picked, first_html = [], ""
    for board, url in BOARDS:
        page_html = fetch(url)
        if not page_html:
            print("{} : HTML 수신 실패".format(board))
            continue
        first_html = first_html or page_html

        rows = parse_rows(page_html)
        kept = 0
        for r in rows:
            if not keep(r):
                continue
            r["게시판"] = board
            r["동탄관련"] = "O" if is_dongtan(r["제목"]) else ""
            r["수집일"] = today
            picked.append(r)
            kept += 1
        print("{} : 행 {}개 중 {}건 채택".format(board, len(rows), kept))
        time.sleep(1)

    if not picked:
        print("\n채택된 고시가 없습니다.")
        if first_html:
            # 행은 읽혔는데 필터에서 다 걸린 것인지 구분한다
            if parse_rows(first_html):
                print("(파싱은 됐습니다. 오늘 올라온 게 다 행정 잡무라는 뜻입니다.)")
                return
            diagnose(first_html)
        else:
            print("모든 요청 실패. 러너에서 화성시 홈페이지 접근이 막혔습니다.")
        raise SystemExit(1)

    new_rows = []
    merged = dict(existing)
    for r in picked:
        key = (r["고시번호"], r["제목"])
        if key not in merged:
            new_rows.append(r)
        merged[key] = r

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(merged.values(),
                           key=lambda r: r.get("공고일자", ""), reverse=True))
    print("\n저장 {} (누적 {}건, 신규 {}건)".format(
        OUT_CSV, len(merged), len(new_rows)))

    if not new_rows:
        print("새로 올라온 것이 없습니다.")
        return

    new_rows.sort(key=lambda r: (r["동탄관련"] != "O", r.get("공고일자", "")))
    lines = ["[화성시 신규 고시] " + today, ""]
    for r in new_rows[:15]:
        lines.append("{} {} | {}".format(
            "★" if r["동탄관련"] == "O" else "·", r["공고일자"], r["제목"][:60]))
        lines.append("   {} {}".format(r["부서"], r["링크"]))
    lines += ["", "★ = 동탄권",
              "글감이 될 만한 것은 data/local_agenda.csv 에 추가하세요."]
    text = "\n".join(lines)
    print()
    print(text)
    send_telegram(text)


if __name__ == "__main__":
    main()
