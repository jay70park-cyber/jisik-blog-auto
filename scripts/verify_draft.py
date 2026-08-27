# -*- coding: utf-8 -*-
"""
초안이 기획안대로 쓰였는지 검증한다.

두 겹으로 본다.
  1) 기계 검사 — 세는 것으로 판정되는 항목 (분량, 해시태그, 금지 표현, 섹션 유무)
  2) 의미 검사 — Claude에게 기획안과 본문을 나란히 주고 문단별로 대조시킨다

산출물
  state/verify_report.txt   검증 리포트 (텔레그램으로도 발송)

이 스크립트는 초안을 고치지 않는다. 어긋난 곳을 알려줄 뿐이고,
고칠지 말지는 사람이 판단한다. 자동 재생성은 비용이 크고,
검증기가 틀렸을 때 멀쩡한 초안을 망칠 수 있어서다.
"""
import os
import re
import json
import urllib.request
import urllib.parse

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

MODEL = "claude-sonnet-5"
STATE_DIR = "state"
PLAN_FILE = os.path.join(STATE_DIR, "plan.json")
DRAFT_FILE = os.path.join(STATE_DIR, "draft.md")
REPORT_FILE = os.path.join(STATE_DIR, "verify_report.txt")

# 톤앤매너에서 금지한 표현. 문단 끝에 오면 특히 문제가 된다.
BANNED = [
    "할 수 있습니다", "영향을 줍니다", "가 중요합니다",
    "전문가와 상담", "꼼꼼히 따져", "신중한 접근",
    "임장하세요", "문의 주세요", "상담 환영",
]

MIN_CHARS = 2000
MAX_CHARS = 3400          # 3000자 기준에 여유를 둔다
MIN_HASHTAGS = 5


def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_text(path):
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ─────────────────────────────────────────────
# 1. 기계 검사
# ─────────────────────────────────────────────

def check_mechanical(draft, plan):
    """세어서 판정되는 것들. Claude를 부르지 않는다."""
    out = []

    body = re.sub(r"\s", "", draft)
    n = len(body)
    if n < MIN_CHARS:
        out.append(("분량", "실패", "{}자로 짧습니다 (최소 {}자)".format(n, MIN_CHARS)))
    elif n > MAX_CHARS:
        out.append(("분량", "주의", "{}자로 깁니다 (권장 {}자)".format(n, MAX_CHARS)))
    else:
        out.append(("분량", "통과", "{}자".format(n)))

    tags = re.findall(r"#[가-힣A-Za-z0-9]+", draft)
    if len(tags) < MIN_HASHTAGS:
        out.append(("해시태그", "실패", "{}개뿐입니다".format(len(tags))))
    else:
        out.append(("해시태그", "통과", "{}개".format(len(tags))))

    hits = [w for w in BANNED if w in draft]
    if hits:
        out.append(("금지 표현", "주의", ", ".join(hits[:5])))
    else:
        out.append(("금지 표현", "통과", "없음"))

    # 이미지는 정확히 2곳
    imgs = re.findall(r"\[이미지:", draft)
    if len(imgs) != 2:
        out.append(("이미지", "주의", "{}곳 (2곳이어야 함)".format(len(imgs))))
    else:
        out.append(("이미지", "통과", "2곳"))

    # 계산기 링크와 기획안의 탭이 맞는지
    calc_tab = (plan or {}).get("calc_tab", "없음")
    has_link = "jisik-calc" in draft
    if calc_tab and calc_tab != "없음":
        if has_link:
            out.append(("계산기 링크", "통과", calc_tab + " 탭"))
        else:
            out.append(("계산기 링크", "실패",
                        "기획안은 '{}' 탭인데 본문에 링크가 없습니다".format(calc_tab)))
    else:
        if has_link:
            out.append(("계산기 링크", "주의", "기획안은 '없음'인데 링크가 있습니다"))
        else:
            out.append(("계산기 링크", "통과", "해당 없음"))

    # 자리표시자가 남아 있으면 발행 전 채워야 한다
    ph = re.findall(r"<mark>\[[^\]]+\]</mark>", draft)
    if ph:
        out.append(("자리표시자", "확인 필요", "{}곳 — 발행 전 채우세요".format(len(ph))))

    # 표가 하나라도 있는지
    if "|" not in draft or draft.count("|") < 8:
        out.append(("표", "주의", "표가 없거나 너무 작습니다"))
    else:
        out.append(("표", "통과", "있음"))

    return out


# ─────────────────────────────────────────────
# 2. 의미 검사 (Claude)
# ─────────────────────────────────────────────

def build_verify_prompt(plan, draft):
    criteria = plan.get("criteria", [])
    crit_text = "\n".join(
        "   {}. {}".format(i, c) for i, c in enumerate(criteria[:3], 1))

    return """아래는 블로그 글의 [확정 기획안]과 그에 따라 작성된 [초안]입니다.
초안이 기획안대로 쓰였는지 검증해주세요.

[확정 기획안]
- 독자: {reader}
- 산출물 유형: {output}
- 핵심 결론: {conclusion}
- 판단 기준 3가지:
{crit}

[초안]
{draft}

──────────────────────────────
아래 6가지를 각각 판정하세요. 후하게 보지 말고 실제로 그러한지 확인하세요.

1. 독자 일치 — 본문이 처음부터 끝까지 이 독자 한 명만 겨냥하는가.
   중간에 다른 독자(투자자↔실사용자↔임차인)를 위한 내용이 섞이지 않았는가.
   특히 계산 예시가 이 독자가 실제로 할 행동에 맞는지 보세요.
   (예: 실사용 매수자 글에 임대수익률 계산이 있으면 불일치)

2. 산출물 일치 — 기획한 산출물 유형에 맞는 도구가 실제로 본문에 있는가.
   독자가 자기 상황을 대입할 수 있는 형태인가.

3. 핵심 결론 일치 — 기획안의 결론이 서두에 그대로 제시되고,
   본문 전개가 그 결론을 뒷받침하는가.

4. 판단 기준 반영 — 기획안의 판단 기준 3가지가 본문에 모두 등장하고
   각각 설명되었는가. 빠지거나 다른 내용으로 바뀌지 않았는가.

5. 문단별 정합성 — 각 섹션이 기획안의 흐름에 맞게 배치되었는가.
   기획과 무관한 곳으로 새는 문단이 있는가.

6. 중복 — 같은 사실·수치·주장이 여러 섹션에서 표현만 바꿔 반복되지 않는가.

아래 JSON 형식으로만 출력하세요. 다른 설명은 붙이지 마세요.

{{
  "items": [
    {{"name": "독자 일치", "verdict": "통과|주의|실패", "note": "한 문장 근거"}},
    {{"name": "산출물 일치", "verdict": "...", "note": "..."}},
    {{"name": "핵심 결론 일치", "verdict": "...", "note": "..."}},
    {{"name": "판단 기준 반영", "verdict": "...", "note": "..."}},
    {{"name": "문단별 정합성", "verdict": "...", "note": "..."}},
    {{"name": "중복", "verdict": "...", "note": "..."}}
  ],
  "worst": "가장 시급하게 고쳐야 할 것 한 문장. 문제가 없으면 빈 문자열",
  "fix_request": "수정 요청으로 그대로 보낼 수 있는 문장. 문제가 없으면 빈 문자열"
}}""".format(
        reader=plan.get("reader", "-"),
        output=plan.get("output_type", "-"),
        conclusion=plan.get("conclusion", "-"),
        crit=crit_text or "   (없음)",
        draft=draft[:12000],
    )


def call_claude(prompt, timeout=180):
    url = "https://api.anthropic.com/v1/messages"
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 8000,
        "messages": [{"role": "user", "content": prompt}],
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as res:
        data = json.load(res)
    return "".join(p.get("text", "") for p in data.get("content", [])
                   if p.get("type") == "text")


def check_semantic(plan, draft):
    try:
        raw = call_claude(build_verify_prompt(plan, draft))
        if not raw or not raw.strip():
            print("의미 검사 실패: Claude 응답이 비어 있습니다 (토큰 한도 확인)")
            return None
        raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except Exception as e:
        print("의미 검사 실패: " + str(e))
        return None


# ─────────────────────────────────────────────
# 3. 리포트
# ─────────────────────────────────────────────

MARK = {"통과": "○", "주의": "△", "실패": "×", "확인 필요": "!"}


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


def main():
    plan = load_json(PLAN_FILE)
    draft = load_text(DRAFT_FILE)

    if not plan or not draft:
        print("기획안 또는 초안이 없어 검증을 건너뜁니다.")
        return

    lines = ["[초안 검증 리포트]", ""]

    # 기계 검사
    mech = check_mechanical(draft, plan)
    lines.append("■ 형식")
    for name, verdict, note in mech:
        lines.append("  {} {} — {}".format(
            MARK.get(verdict, "·"), name, note))

    # 의미 검사
    sem = check_semantic(plan, draft)
    if sem:
        lines.append("")
        lines.append("■ 기획안 대조")
        for it in sem.get("items", []):
            lines.append("  {} {} — {}".format(
                MARK.get(it.get("verdict"), "·"),
                it.get("name", "?"), it.get("note", "")))

        worst = (sem.get("worst") or "").strip()
        if worst:
            lines += ["", "■ 가장 시급한 것", "  " + worst]

        fix = (sem.get("fix_request") or "").strip()
        if fix:
            lines += ["", "■ 수정 요청 문장 (그대로 답장하면 반영됩니다)",
                      "  " + fix]
    else:
        lines += ["", "■ 기획안 대조", "  검증 호출에 실패해 건너뛰었습니다."]

    # 종합
    all_verdicts = [v for _, v, _ in mech]
    if sem:
        all_verdicts += [i.get("verdict", "") for i in sem.get("items", [])]
    fails = all_verdicts.count("실패")
    warns = all_verdicts.count("주의")
    checks = all_verdicts.count("확인 필요")

    lines += ["", "─────────────"]
    if fails:
        lines.append("실패 {}건, 주의 {}건 — 수정을 권합니다.".format(fails, warns))
    elif warns:
        lines.append("주의 {}건 — 확인 후 발행하세요.".format(warns))
    elif checks:
        lines.append("형식은 통과. 자리표시자 {}곳만 채우면 됩니다.".format(checks))
    else:
        lines.append("모두 통과했습니다.")

    text = "\n".join(lines)
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    send_telegram(text)


if __name__ == "__main__":
    main()
