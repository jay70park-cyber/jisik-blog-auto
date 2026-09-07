# -*- coding: utf-8 -*-
"""
화성시 고시 수집 주간 요약

매일 오는 알림은 '오늘 뭐가 떴나'를 본다.
이 주간 요약은 다른 것을 본다. '이 시스템이 쓸 만한가'.

4주 시범운영(2026-09-07 ~ 2026-10-05) 동안 판단 근거를 모으는 게 목적이다.
핵심 숫자는 채택률이다. 알림이 아무리 많이 와도 글감이 안 나오면
필터가 헐거운 것이고, 반대면 조여야 할 이유가 없다.

글감 표시는 사람이 한다. hscity_notices.csv 의 '글감' 열에 O 를 적으면
여기서 세어준다. 표시가 없으면 채택률은 나오지 않는다.
"""
import os
import csv
import collections
import datetime
import urllib.parse
import urllib.request

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

CSV_PATH = os.path.join("data", "hscity_notices.csv")
AGENDA_CSV = os.path.join("data", "local_agenda.csv")

TRIAL_START = datetime.date(2026, 9, 7)
TRIAL_WEEKS = 4


def load(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("(텔레그램 설정이 없어 전송을 건너뜁니다)")
        return
    url = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendMessage"
    body = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, data=body, method="POST"),
                timeout=20) as res:
            res.read()
    except Exception as e:
        print("텔레그램 전송 오류: " + str(e))


def rate(part, whole):
    return "{}/{} ({:.0f}%)".format(
        part, whole, 100.0 * part / whole) if whole else "0/0"


def main():
    today = datetime.date.today()
    since = today - datetime.timedelta(days=7)
    rows = load(CSV_PATH)
    if not rows:
        print("누적 CSV가 없습니다.")
        return

    week = min((today - TRIAL_START).days // 7 + 1, TRIAL_WEEKS)
    recent = [r for r in rows
              if since.isoformat() <= r.get("공고일자", "") <= today.isoformat()]

    picked = [r for r in rows if (r.get("글감") or "").strip()]
    picked_recent = [r for r in recent if (r.get("글감") or "").strip()]
    marked_any = bool(picked)

    lines = ["[화성시 고시 주간 요약] {}주차 / {}주".format(week, TRIAL_WEEKS),
             "{} ~ {}".format(since.isoformat(), today.isoformat()), ""]

    lines.append("이번 주 {}건 (하루 평균 {:.1f}건)".format(
        len(recent), len(recent) / 7.0))
    lines.append("  현안 관련 {}건".format(
        sum(1 for r in recent if r.get("현안"))))
    lines.append("  동탄권 {}건".format(
        sum(1 for r in recent if r.get("동탄관련") == "O")))

    if marked_any:
        lines.append("  글감 채택 " + rate(len(picked_recent), len(recent)))
    else:
        lines.append("  글감 표시 없음 — csv '글감' 열에 O 를 적어주세요")

    top = collections.Counter(r.get("부서", "") for r in recent).most_common(5)
    if top:
        lines.append("")
        lines.append("많이 올린 부서")
        for dept, n in top:
            lines.append("  {} {}건".format(dept or "?", n))

    if marked_any:
        lines.append("")
        lines.append("누적 채택 " + rate(len(picked), len(rows)))
        lines.append("최근 채택 건")
        for r in sorted(picked, key=lambda r: r.get("공고일자", ""),
                        reverse=True)[:5]:
            lines.append("  {} {}".format(
                r.get("공고일자", ""), r.get("제목", "")[:45]))

    agenda = load(AGENDA_CSV)
    doubt = [a for a in agenda if (a.get("확인필요") or "").strip()]
    blank = [a for a in agenda if not (a.get("단계") or "").strip()]
    if doubt or blank:
        lines.append("")
        lines.append("현안 목록 손볼 것")
        for a in doubt:
            lines.append("  {} — {}".format(a.get("현안명", ""),
                                            a.get("확인필요", "")))
        for a in blank:
            lines.append("  {} — 단계 비어 있음".format(a.get("현안명", "")))

    lines.append("")
    if week >= TRIAL_WEEKS:
        lines.append("4주 운영이 끝났습니다. 채택률을 보고")
        lines.append("키워드 파이프라인과 연결할지 판단할 시점입니다.")
    else:
        lines.append("판단 시점까지 {}주 남았습니다.".format(TRIAL_WEEKS - week))

    text = "\n".join(lines)
    print(text)
    send_telegram(text)


if __name__ == "__main__":
    main()
