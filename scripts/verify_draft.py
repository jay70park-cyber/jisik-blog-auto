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

# 없는 행정구역.
#
# 2026-10-06 정정: '동탄구' 를 여기 넣었던 것은 틀렸다.
# 화성특례시는 2026-02-01 부터 만세구·효행구·병점구·동탄구 4개 구
# 체제이고, 동탄구는 동탄1~9동을 관할하는 실재하는 행정구다.
# 확인하지 않고 '없는 행정구역' 이라고 단정해 규칙과 검사에 넣었다.
# 맞는 표기를 실패로 찍는 검사였다.
#
# 교훈: 행정구역은 바뀐다. 목록에 넣기 전에 반드시 확인한다.
# 지금 남은 것은 시·구 체계상 성립하지 않는 이름뿐이다.
#
# 그냥 찾으면 '기흥시장' 같은 멀쩡한 말이 걸리므로
# 행정구역으로 쓰일 때(뒤에 조사·공백·문장부호)만 본다.
_JOSA = r"(?=[\s,.)\]}·…]|$|[은는이가을를의에와과로도만씩부터까지])"

WRONG_PLACES = [
    ("동탄시", "'동탄시'라는 지자체는 없습니다 (화성특례시 동탄구)"),
    ("기흥시", "'기흥시'는 없습니다 (용인시 기흥구)"),
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

    # 지역코드 용어. 법정동 코드는 10자리, 시군구 코드는 5자리다.
    #
    # 2026-10-06 정정: 41597(화성시 동탄구)을 '법정동 코드' 라고 부른
    # 초안을 '지어낸 숫자' 로 판정했는데, 숫자는 맞고 용어가 틀린 것이었다.
    # 가이드 B1 에 '41597 화성시 동탄구' 가 적혀 있었다.
    # 그래서 '실패' 가 아니라 '주의' 로, 용어를 짚어 주기만 한다.
    for m in re.finditer(
            r"법정동\s*코드[^0-9]{0,12}(\d{1,9})(?!\d)(?!\s*자리)", draft):
        n = len(m.group(1))
        why = ("시군구 코드(5자리)를 '법정동 코드' 라고 부른 듯합니다"
               if n == 5 else "법정동 코드는 10자리입니다")
        out.append(("지역코드 용어", "주의",
                    "'{}' 는 {}자리 — {}".format(m.group(1), n, why)))
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

12. 사건의 시제와 인과 — 근거 자료가 **과거의 원인**으로 적은 일을
    본문이 **이번에 새로 일어난 사건**으로 옮기지 않았는가.
    자료에 "A로 B가 되었다" 라고 적혀 있으면 A는 이미 지난 일이고
    이번 소식은 B다. 이것을 "이번에 A가 일어났다" 로 쓰면 사실이
    뒤집힌다. 아래를 각각 확인하라.
    · 자료 문장이 "~로 인해", "~로", "~에 따라" 로 **원인**을 가리키는데
      본문이 그 원인을 이번 회차의 사건으로 서술했는가
    · 같은 글 안에서 **앞 절과 뒤 절이 서로 모순**되지 않는가.
      앞에서 "그 단계는 이미 지나 다음 단계로 넘어갔다" 고 써 놓고
      뒤에서 그 단계가 다시 일어났다고 쓰면 '실패' 다
    · 회의록·보고 자료의 날짜는 **그 일이 일어난 날이 아니라
      보고된 날**이다. 보고일을 사건 발생일로 쓰지 않았는가
    · 이미 끝난 절차를 되돌릴 수 없는 사안인가.
      (계약이 체결된 뒤에 그 입찰이 다시 유찰될 수는 없다)
    판정이 애매하면 '주의' 가 아니라 '실패' 로 하라.
    근거 자료의 해당 문장을 note 에 그대로 인용하라.

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
    {{"name": "기사 나이와 근거", "verdict": "...", "note": "..."}},
    {{"name": "데이터 구분 이름", "verdict": "...", "note": "..."}},
    {{"name": "적은 표본 표기", "verdict": "...", "note": "..."}},
    {{"name": "판단 기준의 타당성", "verdict": "...", "note": "..."}},
    {{"name": "사건의 시제와 인과", "verdict": "...", "note": "..."}}
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
# 2-2. 사실 확인
#
# 기획안 대조만으로는 사실관계 사고를 못 잡는다. 기획안과 초안이
# 둘 다 틀렸으면 서로 맞는다고 나온다. 실제로 그렇게 나갔다.
#   2026-10-05  취득세 50% (실제 35%) · 입법예고를 시행으로 ·
#               대법원 판결을 3월로 (실제 6월 14일)
#   2026-10-08  6차례 유찰 끝에 수의계약으로 넘어간 공사를
#               '9월 15일에 유찰됐다' 로. 독자가 문의해 왔다
#
# 판정하는 모델에게 대조할 자료를 안 줬다. 자기 기억으로 보니
# 틀린 사실이 들어온 경로와 같은 경로로 통과했다.
#
# 두 단계로 본다.
#   ① 자료 대조 — 우리가 모은 고시·재정계획·뉴스·회의록과 맞춰본다.
#                 검색을 안 쓰므로 싸고, 유찰 같은 사고가 여기서 걸린다.
#   ② 외부 확인 — ①에서 자료에 없던 것만, 검색 가치가 있는 종류만
#                 웹 검색으로 확인한다. 세율·법령·판례가 여기 걸린다.
#
# ②가 ①보다 비싸다. 그래서 ①이 거른 것만 넘긴다.
# ─────────────────────────────────────────────

FACTCHECK = os.environ.get("FACTCHECK", "1") != "0"
MAX_CLAIMS = 12          # 뽑을 주장 수 상한
MAX_SEARCH = 6           # 그중 검색까지 할 수 상한
MATERIAL_LIMIT = 7000    # 프롬프트에 넣을 자료 길이 상한

# 검색으로 확인할 값어치가 있는 종류.
# '고유명사' 나 '기타' 는 검색해도 출처가 갈려서 판정이 안 선다.
SEARCHABLE = {"법령·세율", "제도단계", "사업단계", "날짜", "수치"}


def load_material(limit=MATERIAL_LIMIT):
    """이 글을 쓸 때 쓴 자료를 그대로 돌려준다.

    구조가 트랙마다 다르므로 agenda 가 있으면 그것을, 없으면 전체를
    쓴다. 모양이 바뀌어도 깨지지 않게 통째로 넘긴다.
    """
    path = os.path.join(STATE_DIR, "collection_result.json")
    if not os.path.exists(path):
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print("자료를 읽지 못했습니다: " + str(e))
        return ""
    node = data.get("agenda") if isinstance(data, dict) and data.get("agenda") else data
    try:
        text = json.dumps(node, ensure_ascii=False, indent=2)
    except Exception:
        text = str(node)
    if len(text) > limit:
        text = text[:limit] + "\n(이하 생략)"
    return text


def build_source_prompt(draft, material):
    return """오늘은 {today}입니다.

아래 [자료]는 이 글을 쓸 때 실제로 쓴 원본입니다. 고시·재정계획·
뉴스 제목·회의록 요약이 들어 있습니다. [초안]은 그 자료로 쓴 글입니다.

초안에서 **사실 주장**을 뽑아 자료와 대조하세요.

사실 주장이란 맞고 틀림을 가릴 수 있는 문장입니다. 숫자, 날짜,
법령·세율, 제도나 사업이 어느 단계인지, 누가 무엇을 했는지가
여기 듭니다. 의견·전망·독자에게 주는 조언은 **뽑지 마세요.**

판정은 셋 중 하나입니다.

  자료일치   자료에 그렇게 적혀 있다
  자료와다름 자료에 다르게 적혀 있다
  자료에없음 자료에 그 내용이 없다 (틀렸다는 뜻이 아닙니다)

**'자료와다름' 을 찾을 때 특히 볼 것.**
자료가 원인으로 적은 일을 초안이 이번 사건으로 옮겼는지 보세요.
자료에 '-로 -가 되었다' 라고 있으면 앞은 지난 일이고 이번 소식은
뒤쪽입니다. 초안이 '이번에 앞의 일이 일어났다' 로 썼다면
**'자료와다름' 입니다.** 회의록 날짜를 사건이 일어난 날로 쓴 것도
마찬가지입니다. 회의록 날짜는 보고된 날입니다.

자료에 없다고 해서 틀렸다고 하지 마세요. 그것은 '자료에없음' 입니다.

최대 {maxn}개까지만 뽑되, 틀리면 독자가 손해를 보는 것부터 뽑으세요.

[자료]
{material}

[초안]
{draft}

아래 JSON 형식으로만 출력하세요. 다른 설명은 붙이지 마세요.

{{
  "claims": [
    {{
      "quote": "초안에서 그대로 옮긴 한 문장",
      "claim": "그 문장이 주장하는 사실 한 줄",
      "kind": "수치|날짜|법령·세율|제도단계|사업단계|고유명사|기타",
      "verdict": "자료일치|자료와다름|자료에없음",
      "note": "자료의 어느 대목과 맞거나 어긋나는지. 자료 문장을 그대로 인용",
      "suggest": "자료와다름일 때만, 어떻게 고치면 되는지 한 문장. 아니면 빈 문자열"
    }}
  ]
}}""".format(
        today=today_kst().strftime("%Y년 %m월 %d일"),
        maxn=MAX_CLAIMS,
        material=material or "(자료 파일이 없습니다. 이 경우 모든 주장을 '자료에없음' 으로 판정하세요.)",
        draft=draft[:12000],
    )


def build_search_prompt(claims):
    listed = "\n".join(
        "{}. [{}] {}\n   초안 문장: {}".format(
            i, c.get("kind", "기타"), c.get("claim", ""), c.get("quote", ""))
        for i, c in enumerate(claims, 1))
    return """오늘은 {today}입니다.

아래는 블로그 초안에서 뽑은 사실 주장입니다. 우리가 가진 자료에는
없는 것들이라 **웹 검색으로 확인**해야 합니다.

하나씩 검색해서 판정하세요.

  확인됨   믿을 만한 출처에서 같은 내용을 찾았다
  다름     믿을 만한 출처에서 **다른 값을 실제로 찾았다**
  확인불가 찾지 못했거나 출처가 갈린다

**'다름' 은 다른 값을 실제로 찾았을 때만 쓰세요.**
못 찾은 것은 '확인불가' 입니다. 기억으로 판정하지 마세요.
2026-10-01 에 검증기가 맞는 날짜를 틀렸다고 지적해 멀쩡한 초안을
고치게 한 적이 있습니다. 확신이 없으면 '확인불가' 가 맞습니다.

출처는 **공공기관·법제처·법원·언론**을 우선하세요. 블로그와
커뮤니티 글은 근거로 쓰지 마세요. 2026-10-05 에 블로그를 근거로
취득세율을 쓴 적이 있습니다 (실제와 15%포인트 차이).

**'다름' 과 '확인불가' 에는 어떻게 고칠지 한 문장을 붙이세요.**
지우라고만 하지 마세요. 그 대목을 빼면 앞뒤 문맥이 어떻게 되는지까지
보고, 대신 쓸 문장이나 '확인 전까지 쓰지 않는다' 중 하나를 고르세요.

[확인할 주장]
{listed}

아래 JSON 형식으로만 출력하세요. 다른 설명은 붙이지 마세요.

{{
  "results": [
    {{
      "claim": "위 주장 그대로",
      "verdict": "확인됨|다름|확인불가",
      "found": "출처에서 확인한 내용 한 줄. 확인불가면 무엇까지 찾았는지",
      "source": "출처 이름과 URL. 없으면 빈 문자열",
      "suggest": "다름·확인불가일 때 고칠 방법 한 문장. 확인됨이면 빈 문자열"
    }}
  ]
}}""".format(
        today=today_kst().strftime("%Y년 %m월 %d일"),
        listed=listed,
    )


def call_claude_search(prompt, timeout=480):
    """웹 검색을 켜고 부른다.

    검색 결과가 토큰을 크게 먹는다. max_tokens 를 넉넉히 두지 않으면
    생각만 하고 본문이 0자로 끊긴다 (2026-10-05).
    """
    url = "https://api.anthropic.com/v1/messages"
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 16000,
        "tools": [{"type": "web_search_20250305", "name": "web_search"}],
        "messages": [{"role": "user", "content": prompt}],
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as res:
        data = json.load(res)
    parts = data.get("content", [])
    print("사실 확인 stop_reason: {} / 블록: {}".format(
        data.get("stop_reason"), [p.get("type") for p in parts]))
    return "".join(p.get("text", "") for p in parts if p.get("type") == "text")


def parse_json(raw, label):
    """모델이 돌려준 JSON 을 읽는다. 실패하면 왜 실패했는지 남긴다."""
    if not raw or not raw.strip():
        print("{} 실패: 응답이 비어 있습니다 (토큰 한도 확인)".format(label))
        return None
    s = raw.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(s, strict=False)
    except Exception as e:
        print("{} JSON 해석 실패: {}".format(label, e))
        print("  끝 40자: " + repr(s[-40:]))
        return None


def check_facts(draft):
    """사실 확인 두 단계. 반환 (자료대조결과, 외부확인결과).

    어느 단계가 실패해도 나머지는 돌린다. 검증이 못 돌았다고
    발행을 막지는 않는다 — 다만 리포트에 못 돌았다고 적는다.
    """
    if not FACTCHECK:
        print("사실 확인을 건너뜁니다 (FACTCHECK=0).")
        return None, None, set()

    material = load_material()
    if not material:
        print("자료 파일이 없어 자료 대조 없이 외부 확인만 합니다.")

    src = None
    try:
        src = parse_json(call_claude(build_source_prompt(draft, material)),
                         "자료 대조")
    except Exception as e:
        print("자료 대조 실패: " + str(e))

    claims = (src or {}).get("claims", [])
    todo = [c for c in claims
            if c.get("verdict") == "자료에없음"
            and c.get("kind") in SEARCHABLE][:MAX_SEARCH]

    web = None
    if todo:
        print("외부 확인 대상 {}건".format(len(todo)))
        try:
            web = parse_json(call_claude_search(build_search_prompt(todo)),
                             "외부 확인")
        except Exception as e:
            print("외부 확인 실패: " + str(e))
    elif claims:
        print("외부 확인 대상 없음 — 뽑힌 주장이 모두 자료로 판정됐습니다.")

    # 웹으로 넘긴 주장은 아래 단계의 판정을 쓴다.
    # 양쪽에서 세면 같은 주장이 '확인 필요' 와 '통과' 로 두 번 잡힌다.
    searched = {c.get("claim", "") for c in todo}
    return src, web, searched


# ─────────────────────────────────────────────
# 3. 리포트
# ─────────────────────────────────────────────

MARK = {"통과": "○", "주의": "△", "실패": "×", "확인 필요": "!"}

# 사실 확인 판정을 리포트 기호와 종합 집계로 옮긴다.
FACT_VERDICT = {
    "자료일치": "통과",
    "자료와다름": "실패",
    "자료에없음": "확인 필요",
    "확인됨": "통과",
    "다름": "실패",
    "확인불가": "확인 필요",
}


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

        # 수정 요청 문장은 사실 확인까지 끝난 뒤에 합쳐서 낸다.
        # 여기서 내보내면 사실관계 수정안이 빠진 채로 나간다.
        fix = (sem.get("fix_request") or "").strip()
    else:
        fix = ""
        lines += ["", "■ 기획안 대조", "  검증 호출에 실패해 건너뛰었습니다."]

    # 사실 확인
    src, web, searched = check_facts(draft)
    fact_verdicts = []
    fact_fixes = []          # 수정 요청 문장에 합칠 사실관계 수정안
    if src is not None or web is not None:
        lines += ["", "■ 사실 확인"]

    if src is not None:
        claims = src.get("claims", [])
        if not claims:
            lines.append("  · 가릴 수 있는 사실 주장이 없습니다.")
        for c in claims:
            v = c.get("verdict", "")
            handed = c.get("claim", "") in searched
            if handed:
                # 집계는 아래 웹 확인 쪽에서 한다
                lines.append("  · [{}] {} — 웹에서 확인 (아래)".format(
                    v, c.get("claim", "?")))
                continue
            fact_verdicts.append(FACT_VERDICT.get(v, ""))
            lines.append("  {} [{}] {} — {}".format(
                MARK.get(FACT_VERDICT.get(v), "·"),
                v, c.get("claim", "?"), c.get("note", "")))
            if c.get("quote"):
                lines.append('      초안: "{}"'.format(c["quote"]))
            if c.get("suggest"):
                lines.append("      고칠 방법: " + c["suggest"])
                fact_fixes.append(c["suggest"])
    elif FACTCHECK:
        lines += ["", "■ 사실 확인", "  자료 대조 호출에 실패해 건너뛰었습니다."]

    if web is not None:
        lines.append("  ── 자료에 없어 웹에서 확인한 것 ──")
        for r in web.get("results", []):
            v = r.get("verdict", "")
            fact_verdicts.append(FACT_VERDICT.get(v, ""))
            lines.append("  {} [{}] {} — {}".format(
                MARK.get(FACT_VERDICT.get(v), "·"),
                v, r.get("claim", "?"), r.get("found", "")))
            if r.get("source"):
                lines.append("      출처: " + r["source"])
            if r.get("suggest"):
                lines.append("      고칠 방법: " + r["suggest"])
                if r.get("verdict") == "다름":
                    fact_fixes.append(r["suggest"])

    # 수정 요청 문장 — 기획안 대조와 사실 확인을 합쳐 한 번에 낸다.
    # 사람이 이 한 덩어리를 그대로 답장하면 둘 다 반영된다.
    fix_parts = [fix] if fix else []
    for item in fact_fixes:
        fix_parts.append(item)
    if fix_parts:
        lines += ["", "■ 수정 요청 문장 (그대로 답장하면 반영됩니다)"]
        lines += ["  " + p for p in fix_parts]

    # 종합
    all_verdicts = [v for _, v, _ in mech]
    if sem:
        all_verdicts += [i.get("verdict", "") for i in sem.get("items", [])]
    all_verdicts += [v for v in fact_verdicts if v]
    fails = all_verdicts.count("실패")
    warns = all_verdicts.count("주의")
    checks = all_verdicts.count("확인 필요")

    lines += ["", "─────────────"]
    if fails:
        lines.append("실패 {}건, 주의 {}건 — 수정을 권합니다.".format(fails, warns))
    elif warns:
        lines.append("주의 {}건 — 확인 후 발행하세요.".format(warns))
    elif checks:
        # '확인 필요' 가 두 종류다. 자리표시자(형식)와 출처 미확인(사실).
        # 뭉뚱그리면 "자리표시자만 채우면 됩니다" 가 거짓말이 된다.
        fact_checks = fact_verdicts.count("확인 필요")
        if fact_checks and fact_checks < checks:
            lines.append("확인 필요 {}건 — 자리표시자 {}곳, 출처 미확인 {}건.".format(
                checks, checks - fact_checks, fact_checks))
        elif fact_checks:
            lines.append("출처로 확인되지 않은 사실 {}건 — 확인 후 발행하세요.".format(
                fact_checks))
        else:
            lines.append("형식은 통과. 자리표시자 {}곳만 채우면 됩니다.".format(checks))
    else:
        lines.append("모두 통과했습니다.")

    text = "\n".join(lines)
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    send_telegram(text)


def check_item_count():
    """지시 번호 수와 JSON 자리 수가 같은지 센다.

    2026-10-01 에 요구는 11개인데 JSON 자리가 8개여서 9·10번 검사가
    조용히 사라진 적이 있다. 모델은 자리가 없으면 그냥 안 적는다.
    에러도 안 난다. 그래서 기계가 센다.

    반환 (지시수, 자리수, 사라진이름목록)
    """
    p = build_verify_prompt({}, "")
    head, _, tail = p.partition('"items": [')
    asked = len(re.findall(r"^\s*(\d+)\.\s", head, re.M))
    names = re.findall(r'"name":\s*"([^"]+)"', tail)
    # 지시 쪽 제목도 뽑아 둔다. 이름이 어긋나면 사람이 보고 안다.
    titles = re.findall(r"^\s*\d+\.\s*([^\n—]+?)\s*—", head, re.M)
    missing = [t.strip() for t in titles if t.strip() not in names]
    return asked, len(names), missing


if __name__ == "__main__":
    import sys
    if "--check" in sys.argv:
        asked, slots, missing = check_item_count()
        print("지시 {}개 / JSON 자리 {}개".format(asked, slots))
        if missing:
            print("이름이 자리와 안 맞는 항목:", ", ".join(missing))
        ok = (asked == slots)
        print("판정:", "통과" if ok else "★어긋남★")
        raise SystemExit(0 if ok else 1)
    main()