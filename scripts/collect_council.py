# -*- coding: utf-8 -*-
"""
화성특례시의회 회의록 수집

고시는 행정이 '확정된 것'을 공고하는 문서다.
회의록은 그 전에 오간 말이다. 왜 늦어지는지, 얼마가 모자라는지,
누가 무엇을 따졌는지가 남는다. 고시에는 절대 안 실리는 내용이다.

실제로 2026-09-03 도시건설위원회 회의록에는 트램 공사가 6차까지
유찰됐고 공사비를 720억 증액해 겨우 성사시켰다는 답변이 있다.
이런 건 공고문으로 나올 일이 없다.

구조 (2026-09 실측)
- 목록도 본문도 GET 으로 서버 렌더링된다. CSRFToken 은 없어도 된다.
- 목록 1페이지 15건이면 충분하다. 회의는 회기 중에만 열린다.
- 본문 링크는 mntsViewer.php?schSn=7717 형태고 이 번호가 식별자다.
- 회의록은 회의 후 1~2주 지나 올라온다. 그래도 고시보다는 빠르다.

현안 키워드에 걸린 회의록만 알리고, 걸린 대목의 앞뒤를 잘라 보낸다.
링크를 눌러 5만 자를 읽게 만들면 아무도 안 읽는다.
"""
import os
import re
import csv
import html
import time
import datetime
import json
import urllib.parse
import urllib.request

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
SUMMARY_MODEL = "claude-haiku-4-5"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

DATA_DIR = "data"
OUT_CSV = os.path.join(DATA_DIR, "council_minutes.csv")
AGENDA_CSV = os.path.join(DATA_DIR, "local_agenda.csv")

LIST_URL = ("https://council.hscity.go.kr/cnts/mnt/mntsList.php"
            "?bbsCd=mnt&bbsSubCd=mnt01")
VIEW_URL = "https://council.hscity.go.kr/cnts/mnt/mntsViewer.php?schSn="

# 현안 키워드에 안 걸려도 이 말이 있으면 발췌한다.
# 지역 현안이 처음 등장할 때는 아직 이름이 없기 때문이다.
EXTRA_WORDS = [
    "동탄", "지식산업센터", "산업단지", "지구단위계획",
    "용도변경", "역세권", "유찰", "지방채",
]

FIELDS = ["schSn", "회수", "차수", "회의명", "회의일",
          "현안", "적중어", "요약", "글감", "링크", "수집일"]

# 사람이 손으로 적는 열. 다시 수집해도 덮어쓰면 안 된다.
MANUAL_FIELDS = ["글감"]

MAX_EXCERPT = 3      # 회의록 하나에서 보낼 발췌 수
CONTEXT = 120        # 적중어 앞뒤로 잘라낼 글자 수


def fetch(url, timeout=40, retries=3):
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
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s or "")
    s = re.sub(r"(?s)<!--.*?-->", " ", s)      # 주석 안 메뉴가 본문에 섞인다
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def body_only(text):
    """목차와 의사일정을 잘라내고 실제 발언부터 남긴다.

    회의록 앞부분은 메뉴, 안건 목록, 의사일정이 반복된다.
    키워드가 거기서 먼저 걸리면 알맹이 없는 발췌만 나온다.
    개의 선언 이후가 실제 회의다.
    """
    m = re.search(r"\(\s*\d{1,2}시\s*\d{1,2}분\s*개의\s*\)", text)
    if m:
        text = text[m.end():]
    # 끝의 출석 명단에는 부서장 직함이 줄줄이 나온다.
    # '트램건설추진단장'이 명단에 있다는 이유로 트램 논의로 잡히면 곤란하다.
    tail = re.search(r"○\s*출석\s*(위원|공무원|전문위원)", text)
    if tail:
        text = text[:tail.start()]
    return text


def parse_list(page_html):
    """목록 표에서 회의 한 건씩 뽑는다.

    열은 연번, 회수, 차수, 회의명, 회의일 순서다.
    회의명 칸의 링크에 schSn 이 들어 있다.
    """
    rows = []
    for m in re.finditer(r"<tr[^>]*>(.*?)</tr>", page_html, re.S | re.I):
        block = m.group(1)
        sn = re.search(r"schSn=(\d+)", block)
        if not sn:
            continue
        tds = [strip_tags(t) for t in
               re.findall(r"<td[^>]*>(.*?)</td>", block, re.S | re.I)]
        if len(tds) < 5:
            continue
        rows.append({
            "schSn": sn.group(1),
            "회수": tds[1],
            "차수": tds[2],
            "회의명": tds[3],
            "회의일": re.sub(r"[^\d.]", "", tds[4]).strip(".").replace(".", "-"),
            "링크": VIEW_URL + sn.group(1),
        })
    return rows


def load_agenda():
    if not os.path.exists(AGENDA_CSV):
        return []
    out = []
    with open(AGENDA_CSV, "r", encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            name = (r.get("현안명") or "").strip()
            words = [w.strip() for w in (r.get("키워드") or "").split("|")
                     if w.strip()]
            if name and words:
                out.append((name, words))
    return out


def find_hits(text, agenda):
    """현안 키워드와 보조 키워드가 본문 어디에 나오는지 찾는다."""
    issues, words = [], []
    for name, keys in agenda:
        for k in keys:
            if k in text:
                if name not in issues:
                    issues.append(name)
                if k not in words:
                    words.append(k)
    for w in EXTRA_WORDS:
        if w in text and w not in words:
            words.append(w)
    return issues, words


def score(window, words):
    # 산회 선포 뒤는 명단이다. 걸려도 내용이 없다.
    """이 대목이 읽을 가치가 있는지 점수를 매긴다.

    같은 단어라도 목차에 있는 것과 질의답변에 있는 것은 값이 다르다.
    첫 등장을 쓰면 거의 항상 목차가 걸린다. 그래서 점수로 고른다.
    """
    pts = sum(1 for w in words if w in window)
    if re.search(r"\d[\d,]*\s*억", window):
        pts += 3                       # 금액이 나오면 구체적인 이야기다
    if re.search(r"유찰|지연|감액|무산|재심의|반려|미확보", window):
        pts += 3                       # 문제가 드러난 대목
    pts += 2 * len(re.findall(r"○", window))   # 발언 주고받는 대목
    if re.search(r"의사일정|안건보기|맨위로|회 의 록|선택취소", window):
        pts -= 8                       # 목차·머리말
    if re.search(r"산회를 선포|출석위원|출석공무원", window):
        pts -= 8                       # 회의 끝의 명단
    # 스쳐 지나간 언급과 실제 논의를 가른다.
    # 그 사안을 다루는 대목이라면 같은 말이 여러 번 나온다.
    if not any(len(re.findall(re.escape(w), window)) >= 2 for w in words):
        pts -= 2
    return pts


def excerpts(text, words):
    """읽을 만한 대목만 잘라낸다.

    회의록은 5만 자가 넘는다. 링크만 던지면 안 읽는다.
    걸린 대목 중 점수가 높은 것부터 보여주고,
    더 볼지는 사람이 정하게 한다.
    """
    cands = []
    for w in words:
        for m in re.finditer(re.escape(w), text):
            a = max(0, m.start() - CONTEXT)
            b = min(len(text), m.end() + CONTEXT)
            cands.append((score(text[a:b], words), a, b))

    picked = []
    for pts, a, b in sorted(cands, key=lambda c: -c[0]):
        if pts <= 0:
            break
        if any(a < pb and pa < b for _, pa, pb in picked):
            continue               # 이미 고른 대목과 겹친다
        picked.append((pts, a, b))
        if len(picked) >= MAX_EXCERPT:
            break

    picked.sort(key=lambda c: c[1])
    return ["…" + text[a:b].strip() + "…" for _, a, b in picked]


SUMMARY_PROMPT = """다음은 화성특례시의회 회의록 전문이다.

이 회의록에서 동탄·화성 부동산과 지역 개발에 관련된 논의만 뽑아
3~5줄로 요약하라. 각 줄은 사실 하나씩, 한 줄에 60자 안팎.

- 금액, 일정, 공정률, 찬반 같은 구체적인 숫자를 반드시 포함할 것
- 사업이 지연·유찰·감액·무산된 이유가 나오면 우선적으로 담을 것
- 결론이 안 난 사안은 무엇이 미정인지 밝힐 것
- 인사말, 절차 진행, 일반 행정, 복지·환경미화 등은 제외
- 부동산·개발과 관련된 논의가 없으면 "없음" 한 단어만 출력

줄머리에 기호나 번호를 붙이지 말고 줄바꿈으로만 구분하라.

회의록:
"""


def summarize(text, timeout=120):
    """회의록을 요약한다.

    회의록은 발언이 오가는 형식이라 발췌로는 한계가 뚜렷하다.
    "증액을 했습니다 / 증액을 더 하셔서? / 네." 같은 대목을
    그대로 보내봐야 읽히지 않는다.

    요약이 잡음 필터 역할도 한다. 키워드는 걸렸지만 실제로는
    폐기물이나 청소 용역 이야기였다면 "없음"이 돌아오고,
    그러면 알리지 않는다.
    """
    if not ANTHROPIC_API_KEY:
        return ""
    body = json.dumps({
        "model": SUMMARY_MODEL,
        "max_tokens": 700,
        "messages": [{"role": "user",
                      "content": SUMMARY_PROMPT + text[:150000]}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body, headers={
            "content-type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            data = json.loads(res.read().decode("utf-8"))
        out = "".join(b.get("text", "") for b in data.get("content", [])
                      if b.get("type") == "text").strip()
        return "" if out.replace(".", "").strip() == "없음" else out
    except Exception as e:
        print("    요약 실패: {}".format(e))
        return None          # None = 실패, "" = 관련 없음


def load_existing():
    if not os.path.exists(OUT_CSV):
        return {}
    with open(OUT_CSV, "r", encoding="utf-8-sig", newline="") as f:
        return {r["schSn"]: r for r in csv.DictReader(f)}


def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("(텔레그램 설정이 없어 전송을 건너뜁니다)")
        return
    url = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendMessage"
    body = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text[:3900],
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, data=body, method="POST"),
                timeout=20) as res:
            res.read()
    except Exception as e:
        print("텔레그램 전송 오류: " + str(e))


def main():
    today = datetime.date.today().isoformat()
    agenda = load_agenda()
    seen = load_existing()
    print("기존 {}건, 현안 {}건".format(len(seen), len(agenda)))

    page = fetch(LIST_URL)
    if not page:
        print("목록 조회 실패. 의회 홈페이지 접근이 막혔습니다.")
        raise SystemExit(1)

    meetings = parse_list(page)
    print("목록 {}건".format(len(meetings)))
    if not meetings:
        print("표를 못 읽었습니다. 화면 구조가 바뀌었을 수 있습니다.")
        print(page[:1200])
        raise SystemExit(1)

    fresh = [m for m in meetings if m["schSn"] not in seen]
    print("새 회의록 {}건".format(len(fresh)))

    blocks = []
    for m in fresh:
        body = fetch(m["링크"])
        time.sleep(1)
        if not body:
            continue
        text = body_only(strip_tags(body))
        issues, words = find_hits(text, agenda)
        m["현안"] = ", ".join(issues)
        m["적중어"] = ", ".join(words)
        m["수집일"] = today
        for c in MANUAL_FIELDS:
            m.setdefault(c, "")
        seen[m["schSn"]] = m

        mark = "◆" if issues else ("★" if words else "·")
        print("  {} {} {} | 적중 {}".format(
            mark, m["회의일"], m["회의명"], m["적중어"] or "없음"))
        if not words:
            continue

        summary = summarize(text)
        if summary == "":
            print("     → 부동산 관련 논의 없음, 알리지 않음")
            m["요약"] = "없음"
            continue

        lines = ["{} {} {} {}".format(
            mark, m["회의일"], m["회의명"], m["차수"])]
        if issues:
            lines.append("현안: " + m["현안"])
        if summary:
            lines.append(summary)
            m["요약"] = summary[:300]
        else:
            # 요약이 실패하면 예전처럼 발췌를 보낸다. 알림을 거르지는 않는다.
            lines += excerpts(text, words)
        lines.append(m["링크"])
        blocks.append((bool(issues), "\n".join(lines)))

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(seen.values(),
                           key=lambda r: r.get("회의일", ""), reverse=True))
    print("\n저장 {} (누적 {}건)".format(OUT_CSV, len(seen)))

    if not blocks:
        print("알릴 것이 없습니다.")
        return

    blocks.sort(key=lambda b: not b[0])
    text = "\n\n".join(["[화성시의회 회의록] " + today]
                       + [b[1] for b in blocks[:4]])
    print()
    print(text)
    send_telegram(text)


if __name__ == "__main__":
    main()
