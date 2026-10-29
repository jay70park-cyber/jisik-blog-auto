# -*- coding: utf-8 -*-
"""화성시 「동탄트램 추진현황」 게시판을 훑어 새 회차를 알린다.

게시판  https://www.hscity.go.kr/www/user/bbs/BD_selectBbsList.do?q_bbsCode=1154
출력    data/tram_status.csv   누적 목록
        텔레그램               새 회차가 올라온 날만

이 게시판은 **본문 글자가 없다. 이미지 한 장이 전부다.**
그래서 내용을 자동으로 읽어낼 방법이 없다. 대신 할 수 있는 것이 둘이다.

  ① 새 회차가 올라오면 **그림째로** 텔레그램에 보낸다.
     사람이 보고 중요한 것만 사실 대장에 반영한다.
  ② 이미지 주소를 쌓아 두면 트램 글에 **늘 최신 현황**이 붙는다.
     화성시 서버 주소가 공개라 우리가 파일을 올릴 필요가 없다.

2026-10-29 확인: 93건이 쌓여 있었고 우리는 한 번도 안 봤다.
주간이지만 간격이 5~9일로 들쭉날쭉해 요일을 고정하면 놓친다. 매일 돈다.

실행
    python3 scripts/collect_tram_status.py            # 수집 + 알림
    python3 scripts/collect_tram_status.py --probe    # 긁어서 보여주기만
"""
import os
import re
import csv
import io
import sys
import time
import html as html_mod
import datetime
import urllib.parse
import urllib.request

BBS_CODE = "1154"
LIST_URL = ("https://www.hscity.go.kr/www/user/bbs/BD_selectBbsList.do"
            "?q_bbsCode=" + BBS_CODE)
DETAIL_URL = ("https://www.hscity.go.kr/www/user/bbs/BD_selectBbs.do"
              "?q_bbsCode=" + BBS_CODE + "&q_bbscttSn=")
SITE = "https://www.hscity.go.kr"

DATA_DIR = "data"
OUT_CSV = os.path.join(DATA_DIR, "tram_status.csv")
FIELDS = ["회차", "등록일", "글링크", "이미지주소", "수집일"]

# 이 기간 안에 새 글이 하나도 없으면 사이트 구조가 바뀐 것을 의심한다.
# 주간 게시판이라 2주가 비면 우리 쪽 문제일 가능성이 크다.
STALE_DAYS = 16
MAX_NEW_NOTIFY = 3          # 한 번에 알릴 최대 건수 (첫 실행 대비)

# 미리보기는 상세 페이지를 몇 개만 연다.
#
# 2026-10-29: 미리보기인데도 10개를 다 열게 해 두었다. 상세 조회가
# 막히면 한 장에 재시도 3회 × 30초라 10장이면 15분이다. 워크플로
# 제한이 10분이라 **확인하려고 돌린 것이 시간 초과로 죽는다.**
# 미리보기의 목적은 '이미지 주소를 뽑을 수 있는가' 이고, 그건 두세
# 장이면 안다.
PROBE_DETAILS = 3

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

KST = datetime.timezone(datetime.timedelta(hours=9))


def today_kst():
    return datetime.datetime.now(KST).date()


def fetch(url, timeout=30, retries=3):
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=dict(BROWSER_HEADERS))
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return res.read().decode("utf-8", errors="replace")
        except Exception as e:
            print("  조회 실패 ({}/{}): {}".format(attempt, retries, e))
            if attempt < retries:
                time.sleep(attempt * 3)
    return ""


def strip_tags(s):
    return html_mod.unescape(re.sub(r"<[^>]+>", " ", s or "")).strip()


def parse_list(page_html):
    """목록에서 (회차제목, 등록일, 글번호)를 뽑는다.

    열 순서를 믿지 않는다. 게시판 틀이 바뀌어도 살아남게
    **내용으로** 알아본다 — 날짜꼴인 칸이 등록일, '추진현황' 이
    든 칸이 제목, 17자리 숫자가 글번호다.
    """
    rows = []
    for m in re.finditer(r"<tr[^>]*>(.*?)</tr>", page_html, re.S | re.I):
        block = m.group(1)
        if re.search(r"<th[^>]*>", block, re.I):
            continue
        tds = re.findall(r"<td[^>]*>(.*?)</td>", block, re.S | re.I)
        if len(tds) < 2:
            continue
        cells = [strip_tags(t) for t in tds]

        date = ""
        for c in cells:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", c):
                date = c
                break

        title = ""
        for c in cells:
            if "추진현황" in c:
                title = c
                break
        if not title:                      # 틀이 바뀐 경우의 대비
            title = max(cells, key=len) if cells else ""
        title = re.sub(r"\s*※?\s*새글\s*$", "", title).strip()

        sn = ""
        for pat in (r"q_bbscttSn=(\d{6,})",
                    r"[Vv]iew\(\s*['\"](\d{6,})['\"]",
                    r"['\"](\d{17})['\"]"):
            g = re.search(pat, block)
            if g:
                sn = g.group(1)
                break

        if not (title and date and sn):
            continue
        rows.append({"회차": title, "등록일": date, "글번호": sn})
    return rows


def parse_image(detail_html):
    """상세 페이지에서 본문 이미지 주소를 뽑는다. 없으면 빈 문자열.

    이 게시판은 ckeditor 로 올린 이미지 한 장이 본문 전체다.
    webcontent 경로를 먼저 찾고, 없으면 아무 img 나 집는다.
    """
    for pat in (r'<img[^>]+src\s*=\s*["\']([^"\']*webcontent[^"\']+)["\']',
                r'<img[^>]+src\s*=\s*["\']([^"\']+\.(?:png|jpg|jpeg))["\']'):
        m = re.search(pat, detail_html, re.I)
        if not m:
            continue
        src = html_mod.unescape(m.group(1)).strip()
        if src.startswith("//"):
            return "https:" + src
        if src.startswith("/"):
            return SITE + src
        if src.startswith("http"):
            return src
        return SITE + "/" + src.lstrip("./")
    return ""


def load_seen():
    if not os.path.exists(OUT_CSV):
        return {}
    with io.open(OUT_CSV, encoding="utf-8-sig", newline="") as f:
        return {r.get("회차", ""): r for r in csv.DictReader(f)}


def save_all(merged):
    os.makedirs(DATA_DIR, exist_ok=True)
    with io.open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(merged.values(),
                           key=lambda r: r.get("등록일", ""), reverse=True))


def send_message(text, timeout=20):
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


def send_photo(photo_url, caption, timeout=30):
    """사진을 주소로 보낸다. 내려받지 않는다 — 텔레그램이 직접 가져간다.

    실패하면 글로라도 보낸다. 알림이 통째로 사라지는 것이 가장 나쁘다.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    url = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendPhoto"
    body = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "photo": photo_url,
        "caption": caption[:1000],
    }).encode("utf-8")
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, data=body, method="POST"),
                timeout=timeout) as res:
            res.read()
        return True
    except Exception as e:
        print("사진 전송 실패, 글로 보냅니다: " + str(e))
        send_message(caption + "\n" + photo_url)
        return False


def main():
    probe = "--probe" in sys.argv[1:]

    print("게시판을 훑습니다: " + LIST_URL)
    page = fetch(LIST_URL)
    if not page:
        msg = ("⚠ 동탄트램 추진현황 게시판을 열지 못했습니다.\n"
               "사이트 점검이거나 주소가 바뀌었을 수 있습니다.\n" + LIST_URL)
        print(msg)
        if not probe:
            send_message(msg)
        return 1

    rows = parse_list(page)
    print("목록에서 {}건을 읽었습니다. (<tr> {}개)".format(
        len(rows), len(re.findall(r"<tr", page, re.I))))
    if not rows:
        msg = ("⚠ 동탄트램 추진현황 목록을 한 건도 읽지 못했습니다.\n"
               "게시판 틀이 바뀐 것 같습니다. parse_list 를 확인하세요.\n"
               + LIST_URL)
        print(msg)
        if not probe:
            send_message(msg)
        return 1

    seen = load_seen()
    new = [r for r in rows if r["회차"] not in seen]
    print("새 회차 {}건 · 누적 {}건".format(len(new), len(seen)))

    if probe:
        print()
        print("── 목록 (아무것도 저장하지 않습니다) ──")
        for r in rows[:10]:
            mark = "새 " if r["회차"] not in seen else "   "
            print("  {}{} | {} | {}".format(
                mark, r["등록일"], r["회차"][:40], r["글번호"]))
        print()
        print("── 상세 {}건만 열어 이미지 주소를 확인합니다 ──".format(
            min(PROBE_DETAILS, len(new))))

    # 새 글의 이미지 주소를 상세 페이지에서 가져온다.
    todo = new[:PROBE_DETAILS] if probe else new
    merged = dict(seen)
    got = []
    for r in todo:
        detail = DETAIL_URL + r["글번호"]
        img = parse_image(fetch(detail, retries=2))
        if not img:
            print("  이미지를 못 찾음: " + r["회차"])
        row = {
            "회차": r["회차"],
            "등록일": r["등록일"],
            "글링크": detail,
            "이미지주소": img,
            "수집일": today_kst().isoformat(),
        }
        merged[r["회차"]] = row
        got.append(row)
        time.sleep(0.5)

    if probe:
        for r in got:
            print("  {} → {}".format(r["회차"], r["이미지주소"] or "(이미지 없음)"))
        ok = sum(1 for r in got if r["이미지주소"])
        print()
        print("판정: 목록 {}건 · 이미지 {}/{}건".format(
            len(rows), ok, len(got)))
        print("이미지가 0건이면 parse_image 를 고쳐야 합니다.")
        return 0

    if got:
        save_all(merged)
        print("{} 에 {}건을 더했습니다.".format(OUT_CSV, len(got)))

    # ── 알림 ──
    #
    # 주간 게시판이라 대부분의 날은 새 글이 없다. 그런 날까지 알리면
    # 소음이 되어 정작 올라온 날을 흘려보낸다. 새 글이 있는 날만 알린다.
    # 대신 오래 비면 그때 알린다 — 조용한 것과 고장난 것은 다르다.
    for r in got[:MAX_NEW_NOTIFY]:
        caption = "[동탄트램 추진현황] {}\n{}\n{}".format(
            r["회차"], r["등록일"], r["글링크"])
        if r["이미지주소"]:
            send_photo(r["이미지주소"], caption)
        else:
            send_message(caption + "\n(본문 이미지를 못 찾았습니다)")
    if len(got) > MAX_NEW_NOTIFY:
        send_message("그 밖 {}건이 더 들어왔습니다. {} 를 보세요.".format(
            len(got) - MAX_NEW_NOTIFY, OUT_CSV))

    if not got:
        last = max((r.get("등록일", "") for r in merged.values()), default="")
        print("새 회차 없음 · 마지막 {}".format(last or "?"))
        if last:
            try:
                gap = (today_kst() - datetime.date(*map(int, last.split("-")))).days
            except Exception:
                gap = 0
            if gap >= STALE_DAYS:
                send_message(
                    "⚠ 동탄트램 추진현황이 {}일째 새 글이 없습니다.\n"
                    "주간 게시판이라 우리 쪽에서 못 긁고 있을 수 있습니다.\n"
                    .format(gap) + LIST_URL)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
