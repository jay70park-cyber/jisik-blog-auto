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
import time
import json
import urllib.request
import urllib.parse

# GitHub Actions 는 UTC 로 돈다. KST 오전은 UTC 로 전날이어서
# date.today() 가 하루 전을 돌려준다. 2026-10-01 리포트가 그 날짜의
# 초안을 "기준일 09-30 과 어긋난다"고 틀리게 지적한 원인이 이것이다.
#
# 이 줄은 날짜를 쓰는 다른 라이브러리까지 덮는 안전망이다.
# 다만 setdefault 는 TZ 가 이미 있으면 덮지 않으므로 이것만 믿을 수 없다.
# 이 스크립트가 쓰는 날짜는 아래 today_kst() 로 직접 가져온다.
os.environ.setdefault("TZ", "Asia/Seoul")
if hasattr(time, "tzset"):
    time.tzset()

from datetime import date
from date_guard import check_dates, check_recency, today_kst
import content_rules as cr

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

MODEL = "claude-sonnet-5"
STATE_DIR = "state"
PLAN_FILE = os.path.join(STATE_DIR, "plan.json")
DRAFT_FILE = os.path.join(STATE_DIR, "draft.md")
REPORT_FILE = os.path.join(STATE_DIR, "verify_report.txt")

# 없는 행정구역. 2026-09-21 에 프롬프트 규칙으로 막았는데 10-05 에
# 두 번 더 나왔다. 글자 그대로 찾으면 잡히므로 기계가 본다.
#
# 다만 그냥 찾으면 '트램 동탄구간', '기흥시장' 같은 멀쩡한 말이 걸린다.
# 어제 '상가주택' 이 '상가' 로 잡히던 것과 같은 함정이다.
# 행정구역으로 쓰일 때는 뒤에 조사·공백·문장부호가 오므로 그때만 본다.
# ('동탄구간' 의 '간', '기흥시장' 의 '장' 은 조사가 아니다)
_JOSA = r"(?=[\s,.)\]}·…]|$|[은는이가을를의에와과로도만씩부터까지])"

WRONG_PLACES = [
    ("동탄구", "화성시에 '동탄구'는 없습니다 (행정동은 동탄1~9동)"),
    ("동탄시", "'동탄시'라는 지자체는 없습니다 (화성시)"),
    ("기흥시", "'기흥시'는 없습니다 (용인시 기흥구)"),
    ("동탄구청", "'동탄구청'은 없습니다 (화성시청)"),
]

# 톤앤매너에서 금지한 표현. 문단 끝에 오면 특히 문제가 된다.
BANNED = [
    "할 수 있습니다", "영향을 줍니다", "가 중요합니다",
    "전문가와 상담", "꼼꼼히 따져", "신중한 접근",
    "임장하세요", "문의 주세요", "상담 환영",
]

MIN_CHARS = 2000
MAX_CHARS = 3600          # 3000자 기준에 여유를 둔다
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

def source_dates():
    """collection_result.json 에서 근거 기사 발행일을 뽑는다.

    구조를 모르므로 재귀로 훑으면서 날짜처럼 생긴 값을 모은다.
    네이버 뉴스는 'Mon, 26 May 2026 07:50:00 +0900',
    네이버 블로그는 '20260526' 형식이라 둘 다 받는다.
    """
    path = os.path.join(STATE_DIR, "collection_result.json")
    if not os.path.exists(path):
        return None

    MONTHS = {m: i for i, m in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}

    def parse(v):
        s = str(v).strip()
        # Mon, 26 May 2026 07:50:00 +0900
        m = re.search(r"(\d{1,2})\s+([A-Z][a-z]{2})\s+(20\d{2})", s)
        if m and m.group(2) in MONTHS:
            try:
                return date(int(m.group(3)), MONTHS[m.group(2)], int(m.group(1)))
            except ValueError:
                return None
        # 2026-05-26 / 2026.05.26 / 2026/05/26
        m = re.search(r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})", s)
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                return None
        # 20260526
        m = re.fullmatch(r"(20\d{2})(\d{2})(\d{2})", s)
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                return None
        return None

    out = []

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, (dict, list)):
                    walk(v)
                elif "date" in str(k).lower():      # pubDate, postdate, date ...
                    d = parse(v)
                    if d:
                        out.append(d)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    try:
        with open(path, encoding="utf-8") as f:
            walk(json.load(f))
    except Exception:
        return None

    today = today_kst()
    # 미래 날짜나 10년 이전은 잘못 잡힌 값으로 본다
    out = [d for d in out if (today - d).days >= 0 and (today - d).days < 3650]
    return out or None

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
            # 링크가 있는지만 보면 기획안이 '임대수익률' 탭인데 본문이
            # '실투자금' 탭을 걸어도 통과한다. 탭 이름이 본문에 있는지 본다.
            if calc_tab in draft:
                out.append(("계산기 링크", "통과", calc_tab + " 탭"))
            else:
                out.append(("계산기 링크", "주의",
                            "기획안은 '{}' 탭인데 본문에 그 탭 이름이 없습니다"
                            .format(calc_tab)))
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

    # 없는 행정구역
    wrong = [(w, why) for w, why in WRONG_PLACES
             if re.search(re.escape(w) + _JOSA, draft)]
    if wrong:
        out.append(("행정구역", "실패",
                    "; ".join("'{}' — {}".format(w, why) for w, why in wrong)))
    else:
        out.append(("행정구역", "통과", "없는 지명 없음"))

    # 법정동 코드는 10자리다. 짧게 적혀 있으면 지어낸 숫자다.
    # '법정동 코드는 10자리입니다' 같은 설명문은 빼야 하므로
    # 숫자 뒤에 '자리' 가 오면 코드가 아니라 설명으로 본다.
    for m in re.finditer(
            r"법정동\s*코드[^0-9]{0,12}(\d{1,9})(?!\d)(?!\s*자리)", draft):
        out.append(("법정동 코드", "실패",
                    "'{}' 는 {}자리입니다 — 법정동 코드는 10자리".format(
                        m.group(1), len(m.group(1)))))
        break

    out.extend(check_dates(draft, today=today_kst()))

    # 외부 기사를 근거로 쓰지 않은 글에는 신선도 판정이 성립하지 않는다.
    # 예전에는 날짜를 모를 때도 "근거 기사가 일주일 이내인지 확인하세요"를
    # 내보냈고, 바로 아래 의미 검사 8번이 "해당 없음"으로 부정해
    # 한 리포트 안에서 두 항목이 서로 모순됐다.
    srcs = source_dates()
    if srcs:
        out.extend(check_recency(draft, srcs))
    else:
        out.append(("기사 나이", "통과", "외부 기사 근거 없음 — 판정 대상 아님"))

    return out


# ─────────────────────────────────────────────
# 2. 의미 검사 (Claude)
# ─────────────────────────────────────────────

def build_verify_prompt(plan, draft):
    criteria = plan.get("criteria", [])
    crit_text = "\n".join(
        "   {}. {}".format(i, c) for i, c in enumerate(criteria[:3], 1))

    return """오늘은 {today}입니다. 이 날짜를 기준으로 판단하세요.

아래는 블로그 글의 [확정 기획안]과 그에 따라 작성된 [초안]입니다.
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
아래 항목을 각각 판정하세요. 후하게 보지 말고 실제로 그러한지 확인하세요.

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
   이 항목은 **일치 여부만** 본다. 기준이 올바른지는 11번에서 따로
   판정하므로, 여기서 '통과' 가 기준이 타당하다는 뜻은 아니다.

5. 문단별 정합성 — 각 섹션이 기획안의 흐름에 맞게 배치되었는가.
   기획과 무관한 곳으로 새는 문단이 있는가.

6. 중복 — 같은 사실·수치·주장이 여러 섹션에서 표현만 바꿔 반복되지 않는가.

7. 시점 정합성 — 두 방향을 모두 봅니다.
   (가) 오늘({today}) 기준으로 이미 지난 날짜를 "~할 예정",
       "~를 목표로", "~할 전망"처럼 미래형으로 쓴 곳이 없는가.
       개통·준공·시행·입주 일정은 특히 주의해서 보세요.
       당신의 학습 시점에는 미래였더라도 오늘 기준으로는 지났을 수 있습니다.
   (나) **반대로, 아직 시행·확정되지 않은 것을 "시행되면서 ~됐다"처럼
       과거형으로 쓴 곳이 없는가.** 입법예고·개정안 발표·국회 제출은
       시행이 아닙니다. 제도 변화를 다룬 문장이 어느 단계인지 확인하세요.
   날짜가 나오면 반드시 오늘과 비교하세요.

8. 기사 나이와 근거 — 근거로 든 기사가 일주일 이내인가.
   오래된 기사인데 "최근", "이번 주", "알려졌다"처럼 새 소식인 양
   쓰지 않았는가. 오래된 근거는 사실관계만 서술해야 합니다.
   그리고 **법령 조항·판례·개정 내용을 언급한 대목에 출처 링크가 있는가.**
   조문 번호나 선고 날짜를 링크 없이 적었다면 '실패'로 판정하세요.
   
9. 데이터 구분 이름 — 본문이 데이터에 있는 구분 이름을 그대로 쓰는가.
   블록·유형 구분을 "도보 10분 이내" 처럼 다른 기준으로 바꿔 부르지
   않았는가. 데이터에 없는 항목(도보 시간, 역세권 여부)으로 분류하지
   않았는가. **표에 없는 항목을 본문에서 언급하지 않았는가.**
   독자가 표에서 찾을 수 없는 구분을 본문이 말하면 확인할 길이 없다.

10. 적은 표본 표기 — 거래 건수가 10건 미만인 항목에 건수가 함께
    적혀 있고, 본문에서 참고치임을 밝혔는가.

11. 판단 기준의 타당성 — 이것은 기획안과의 일치가 아니라
    **기준 자체가 성립하는지**를 보는 항목이다. 기획안에 있던
    기준이라도 아래에 걸리면 '실패' 로 판정하라.
    · 출처 없는 숫자로 선을 그었는가 ("3배를 넘으면", "50% 미만이면")
    · 독자가 그 자리에서 확인할 수 없는 것을 기준으로 삼았는가
      (등기부를 여러 건 떼야 하는 것, 국토부 실거래가에 없는
       매도인·호실을 알아야 하는 것)
    · 단위가 다른 값을 배수로 비교했는가
      (지금 쌓인 매물 건수 ÷ 기간당 거래 건수)
    · 확인 경로(사이트 → 메뉴 → 무엇을 고를지)가 실제로 그렇게
      동작하는가. 없는 메뉴나 없는 필터 항목을 안내하지 않았는가
    아래 규칙이 판정 기준이다.

{criteria}

아래 JSON 형식으로만 출력하세요. 다른 설명은 붙이지 마세요.

{{
  "items": [
    {{"name": "독자 일치", "verdict": "통과|주의|실패", "note": "한 문장 근거"}},
    {{"name": "산출물 일치", "verdict": "...", "note": "..."}},
    {{"name": "핵심 결론 일치", "verdict": "...", "note": "..."}},
    {{"name": "판단 기준 반영", "verdict": "...", "note": "..."}},
    {{"name": "문단별 정합성", "verdict": "...", "note": "..."}},
    {{"name": "중복", "verdict": "...", "note": "..."}},
    {{"name": "시점 정합성", "verdict": "...", "note": "..."}},
    {{"name": "기사 나이와 톤", "verdict": "...", "note": "..."}},
    {{"name": "데이터 구분 이름", "verdict": "...", "note": "..."}},
    {{"name": "적은 표본 표기", "verdict": "...", "note": "..."}},
    {{"name": "판단 기준의 타당성", "verdict": "...", "note": "..."}}
  ],
  "worst": "가장 시급하게 고쳐야 할 것 한 문장. 문제가 없으면 빈 문자열",
  "fix_request": "수정 요청으로 그대로 보낼 수 있는 문장. 문제가 없으면 빈 문자열"
}}""".format(
        today=today_kst().strftime("%Y년 %m월 %d일"),
        reader=plan.get("reader", "-"),
        output=plan.get("output_type", "-"),
        conclusion=plan.get("conclusion", "-"),
        crit=crit_text or "   (없음)",
        criteria=cr.build_criteria_rules(),
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