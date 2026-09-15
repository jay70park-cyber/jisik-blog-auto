# -*- coding: utf-8 -*-
"""
본문을 쓰기 전에 '기획안'을 먼저 만들어 텔레그램으로 보내고,
5분간 수정 요청을 기다린 뒤(무응답이면 자동 진행) state/plan.json 을 확정한다.

기획안이 정하는 것:
  - 독자 상황 (한 명만)
  - 산출물 유형 (독자가 손에 쥐고 갈 것)
  - 핵심 결론 (한 문장)
  - 판단 기준 3가지
  - 연결할 계산기 탭

트랙에 따라 위 항목의 성격이 달라진다.
지산·상가·경매는 독자의 의사결정을 돕는 글이고,
지역 개발·시의회 회의록은 독자가 몰랐던 것을 알려주는 글이다.
후자에 "그래서 무엇을 해야 한다"를 요구하면 경고문이 되어 버린다.
그래서 트랙별 규칙은 content_rules.py 한 곳에서 가져다 쓴다.
"""
import os
import csv
import json
import time
import datetime
import urllib.request
import urllib.parse

import content_rules as cr

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

MODEL = "claude-sonnet-5"
STATE_DIR = "state"
PLAN_FILE = os.path.join(STATE_DIR, "plan.json")
HISTORY_FILE = os.path.join(STATE_DIR, "plan_history.json")
COUNCIL_CSV = os.path.join("data", "council_minutes.csv")

CALC_URL = os.environ.get("CALC_URL", "https://jay70park-cyber.github.io/jisik-calc/")

WAIT_SECONDS = int(os.environ.get("PLAN_WAIT_SECONDS", "300"))  # 5분
POLL_INTERVAL = 20
MAX_REVISIONS = 2

# ── 트랙별 독자 후보 ────────────────────────────
# 지역 개발 글의 독자를 '투자자'로 두면 글이 매수 판단으로 흘러간다.
# 이 글의 독자는 이미 여기서 일하거나 살거나 사업을 준비하는 사람이다.
READERS = ["임대수익 목적 투자자", "실사용 매수자", "실사용 임차인"]
READERS_BY_TRACK = {
    "local": [
        "동탄에서 사업장을 찾고 있는 사업주",
        "동탄에 이미 자리 잡은 사업주",
        "동탄에 살면서 이 동네 변화가 궁금한 사람",
    ],
    "council": [
        "동탄에서 사업장을 찾고 있는 사업주",
        "동탄에 이미 자리 잡은 사업주",
        "동탄에 살면서 이 동네 변화가 궁금한 사람",
    ],
}

# 정보 전달이 목적인 트랙. 산출물·결론의 규칙이 다르다.
INFO_TRACKS = ("local", "council")

CALC_TABS = ["실투자금", "임대수익률", "매수 vs 임차", "입주 가능 업종", "취득세·재산세", "없음"]

# ── 승인 표현 인식 ──────────────────────────────
# 짧은 확인 답장은 표현이 제각각이라 넉넉하게 인식한다.
APPROVE_TOKENS = {
    "ok", "okay", "oks", "okok", "k", "go", "good",
    "ㅇㅋ", "ㅇㅇ", "ㄱㄱ", "ㅇ",
    "오키", "오케", "오케이", "옼", "옹",
    "승인", "승인함", "승인합니다",
    "좋아", "좋아요", "좋습니다", "좋음",
    "진행", "진행해", "진행해줘", "진행합시다", "진행하자",
    "그대로", "그대로진행", "그대로해", "그대로가", "이대로", "이대로진행",
    "네", "넵", "예", "응", "굿", "콜", "고고", "가자", "확인", "동의", "게시", "발행",
}


def is_approval(text):
    """'오키', 'ㅇㅋ', '그대로 진행' 같은 짧은 확인 답장을 승인으로 인식한다."""
    if not text:
        return False
    s = text.strip().lower()
    # 문장부호·이모지·공백 제거 후 비교
    cleaned = "".join(ch for ch in s if ch.isalnum() or ("\uac00" <= ch <= "\ud7a3") or ("\u3131" <= ch <= "\u318e"))
    if not cleaned:
        return False
    if cleaned in APPROVE_TOKENS:
        return True
    # 'ㅋ' 등 웃음 문자만 덧붙인 경우 ("오키ㅋㅋ")
    stripped = cleaned.rstrip("ㅋㅎ")
    if stripped and stripped in APPROVE_TOKENS:
        return True
    # 아주 짧은 답장이 승인 토큰으로 시작하면 승인으로 본다 ("오키요", "진행할게")
    if len(cleaned) <= 8:
        for t in APPROVE_TOKENS:
            if len(t) >= 2 and cleaned.startswith(t):
                return True
    return False


def call_claude(prompt, timeout=120):
    url = "https://api.anthropic.com/v1/messages"
    body = json.dumps(
        {"model": MODEL, "max_tokens": 4000, "messages": [{"role": "user", "content": prompt}]},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as res:
        data = json.load(res)
    return "".join(p.get("text", "") for p in data.get("content", []) if p.get("type") == "text")


def parse_json(text):
    if not text or not text.strip():
        raise ValueError("Claude 응답이 비어 있습니다 (토큰 한도 확인)")
    text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)


HISTORY_KEEP = 8          # 최근 몇 회를 기억할 것인가


def load_history():
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("plans", [])
    except Exception as e:
        print("기획 이력 읽기 실패: " + str(e))
        return []


def save_history(plan, result):
    """이번 기획을 이력에 남긴다. 오래된 것은 버린다."""
    plans = load_history()
    plans.append({
        "date": datetime.date.today().isoformat(),
        "category": result.get("category_display", ""),
        "keyword": result.get("top_keyword", ""),
        "reader": plan.get("reader", ""),
        "output_type": plan.get("output_type", ""),
        "conclusion": plan.get("conclusion", ""),
        "criteria": plan.get("criteria", [])[:3],
    })
    plans = plans[-HISTORY_KEEP:]
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump({"plans": plans}, f, ensure_ascii=False, indent=2)
    print("기획 이력 저장: {}건 보관".format(len(plans)))


def format_history(plans):
    """프롬프트에 넣을 이력 요약. 없으면 빈 문자열."""
    if not plans:
        return ""
    lines = ["[최근 기획 이력 — 이것과 겹치지 않게 만드세요]"]
    for p in plans:
        crit = " / ".join(c[:28] for c in p.get("criteria", [])[:3])
        lines.append("- {} {} | 독자: {} | 산출물: {}".format(
            p.get("date", ""), p.get("keyword", ""),
            p.get("reader", ""), p.get("output_type", "")[:12]))
        if crit:
            lines.append("    판단기준: " + crit)
    lines += [
        "",
        "위 목록을 보고 아래를 지키세요.",
        "- 최근 3회에 쓴 독자는 고르지 마세요. 매번 같은 독자를 겨냥하면 글이 똑같아집니다.",
        "- 최근 3회에 쓴 산출물 유형도 피하세요.",
        "- 판단 기준에 이미 쓴 항목(전용률, 입주업종 등)을 그대로 반복하지 마세요.",
        "  같은 주제라도 이번 키워드에서만 나올 수 있는 기준을 찾으세요.",
        "",
    ]
    return "\n".join(lines)


def load_council(days=35, limit=8):
    """최근 회의록 요약을 읽는다.

    회의록 수집은 매일 돌지만 활용은 주간 발행에 녹인다.
    기획 단계에서 이걸 알고 있어야 소재로 고를 수 있다.
    """
    if not os.path.exists(COUNCIL_CSV):
        return []
    since = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    rows = []
    with open(COUNCIL_CSV, "r", encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            s = (r.get("요약") or "").strip()
            if s and s != "없음" and r.get("회의일", "") >= since:
                rows.append(r)
    rows.sort(key=lambda r: r.get("회의일", ""), reverse=True)
    return rows[:limit]


def format_council(rows, track):
    """회의록 요약을 프롬프트용 블록으로 만든다."""
    if not rows:
        return ""
    body = "\n".join("- {} {} : {}".format(
        r.get("회의일", ""), r.get("회의명", ""), (r.get("요약") or "")[:200])
        for r in rows)
    if track == "council":
        head = ("[이번 글의 재료 — 최근 한 달 화성특례시의회 회의록]\n"
                "이 글은 아래 회의록에서만 소재를 고릅니다.\n"
                "한 회의를 통째로 옮기지 말고, 여러 회의에 걸쳐 나온\n"
                "같은 사업의 이야기를 모아 하나의 흐름으로 엮으세요.\n")
    else:
        head = ("[참고 — 최근 화성특례시의회 회의록]\n"
                "주제와 닿는 내용이 있으면 소재로 쓰세요. 없으면 무시하세요.\n")
    return head + body + "\n"


def build_plan_prompt(result, feedback=None, previous=None):
    kw = result["top_keyword"]
    cat = result["category_display"]
    track = result.get("track", "jisik")
    is_info = track in INFO_TRACKS

    readers = READERS_BY_TRACK.get(track, READERS)
    output_types = cr.build_output_types(track)
    # 회의록은 정보 전달 트랙에서만 재료로 쓴다.
    # 지산·상가 글에 끼우면 주제가 흐려진다.
    council_block = format_council(load_council(), track) if is_info else ""

    track_note = ""
    if track == "realprice":
        track_note = """..."""
    elif track == "auction":
        track_note = """
[이번 글은 경매 낙찰 분석 트랙입니다]
- 우리가 가진 데이터는 법원경매 아파트형공장 낙찰 실적입니다.
  매각가율, 평당매각가, 유찰 횟수별 통계, 단지별 비교가 들어 있습니다.
- 따라서 이 글은 "경매로 지산을 취득하려는 사람"을 위한 것입니다.
  임차인 보증금 보호, 권리분석 일반론은 우리 데이터로 뒷받침할 수 없으니 고르지 마세요.
- 독자는 "임대수익 목적 투자자" 또는 "실사용 매수자" 중에서 고르세요.
- 산출물은 ① 계산 공식 또는 ③ A vs B 선택표가 잘 맞습니다.
"""
    elif track == "local":
        track_note = cr.LOCAL_TOPIC_GUIDE
    elif track == "council":
        track_note = cr.COUNCIL_TOPIC_GUIDE

    history = format_history(load_history())

    # ── 정보 전달 트랙과 의사결정 트랙은 2·3·4번 원칙이 다르다 ──
    if is_info:
        rule_2 = """2. 이 글이 독자에게 남길 '산출물'을 아래 중 하나로 정합니다.
{types}
   - 이 글의 목적은 독자가 몰랐던 것을 알게 하는 것입니다.
     독자가 자기 물건에 대입해 답을 내는 글이 아닙니다.""".format(types=output_types)
        rule_3 = """3. 핵심 결론을 한 문장으로 씁니다.
   - "무엇이 어떻게 달라졌다"는 사실 서술이어야 합니다.
   - "그래서 독자는 무엇을 해야 한다", "확인해야 한다", "주의해야 한다"로
     쓰지 마세요. 이 글은 경고문이 아닙니다."""
        rule_4 = """4. 이 글에서 짚을 사실 3가지를 만듭니다.
   - 각 항목은 한 문장. 확인된 사실이어야 하고 추측이면 그렇다고 밝히세요.
   - 숫자와 일정이 들어가면 좋습니다.
   - 판단 기준이나 체크리스트 형태로 쓰지 마세요."""
        field_4 = '"criteria": ["짚을 사실 1", "짚을 사실 2", "짚을 사실 3"],'
        calc_note = '   - 이 트랙은 대개 "없음"이 맞습니다.'
    else:
        rule_2 = """2. 이 글이 독자에게 남길 '산출물'을 아래 중 하나로 정합니다.
{types}
   - 단순한 정보 요약이나 뉴스 해설은 산출물이 아닙니다.
     독자가 자기 물건에 대입해 답을 낼 수 있어야 합니다.""".format(types=output_types)
        rule_3 = """3. 핵심 결론을 한 문장으로 씁니다.
   "무엇이 일어났다"가 아니라 "그래서 독자는 무엇을 해야 한다"의 형태여야 합니다."""
        rule_4 = """4. 판단 기준 3가지를 만듭니다.
   - "수익률 4% 이상이면 매수" 같은 단정적 투자 권유는 쓰지 마세요. 책임 소재가 될 수 있습니다.
   - 다만 "전용률 50% 미만이면 실사용에 부적합" 같은 사실 기반의 확인 기준은 좋습니다.
   - 각 항목은 한 문장, 독자가 스스로 예/아니오를 판단할 수 있어야 합니다."""
        field_4 = '"criteria": ["판단 기준 1", "판단 기준 2", "판단 기준 3"],'
        calc_note = '   - 이 글의 주제와 직접 관련이 없으면 "없음"으로 두세요.'

    base = f"""당신은 경기도 동탄 지역 지식산업센터 전문 공인중개사의 블로그 기획을 돕습니다.

이번 글의 카테고리는 "{cat}", 검색 관심도 1위 키워드는 "{kw}" 입니다.

{history}
{track_note}
{council_block}
이 키워드로 글을 쓰기 전에 기획안을 먼저 만드세요. 원칙은 아래와 같습니다.

1. 독자는 반드시 한 명만 고릅니다. 후보: {", ".join(readers)}
   - 같은 사실도 독자에 따라 정반대 결론이 되므로, 두 명 이상을 겨냥하지 마세요.
{rule_2}
{rule_3}
{rule_4}
5. 연결할 계산기 탭을 고릅니다. 후보: {", ".join(CALC_TABS)}
{calc_note}

아래 JSON 형식으로만 출력하세요. 다른 설명은 붙이지 마세요.

{{
  "reader": "독자 상황 (위 후보 중 하나)",
  "output_type": "산출물 유형 (위 목록 중 하나, 번호 포함)",
  "conclusion": "핵심 결론 한 문장",
  {field_4}
  "calc_tab": "계산기 탭 (위 후보 중 하나)",
  "title_draft": "가제 (독자가 얻어갈 것이 드러나게)"
}}"""

    if feedback and previous:
        base += f"""

[직전 기획안]
{json.dumps(previous, ensure_ascii=False, indent=2)}

[수정 요청]
{feedback}

위 수정 요청을 반영해 기획안을 다시 만들어 같은 JSON 형식으로 출력하세요."""
    return base


def format_plan_message(plan, result, round_no):
    head = "[기획안]" if round_no == 0 else "[기획안 수정본]"
    track = result.get("track", "jisik")
    label = "짚을 사실 3가지" if track in INFO_TRACKS else "판단 기준 3가지"
    lines = [
        head + " " + result["category_display"] + " · " + result["top_keyword"],
        "",
        "독자 상황   : " + plan.get("reader", "-"),
        "산출물 유형 : " + plan.get("output_type", "-"),
        "핵심 결론   : " + plan.get("conclusion", "-"),
        "",
        label,
    ]
    for i, c in enumerate(plan.get("criteria", [])[:3], 1):
        lines.append("  " + str(i) + ". " + c)
    lines += [
        "",
        "계산기      : " + str(plan.get("calc_tab", "없음")),
        "가제        : " + plan.get("title_draft", "-"),
        "",
        "─────────────",
        "이대로 진행합니다. 바꿀 부분이 있으면 " + str(max(1, WAIT_SECONDS // 60)) + "분 안에 답장해주세요.",
        "(무응답이면 자동으로 본문 작성으로 넘어갑니다)",
    ]
    return "\n".join(lines)


def send_message(text, timeout=20):
    url = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendMessage"
    body = urllib.parse.urlencode({"chat_id": TELEGRAM_CHAT_ID, "text": text}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as res:
        res.read()


def get_updates(offset, timeout=20):
    url = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/getUpdates?" + urllib.parse.urlencode(
        {"offset": offset, "timeout": 0}
    )
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.load(res).get("result", [])
    except Exception as e:
        print("getUpdates 오류: " + str(e))
        return []


def latest_update_id():
    ups = get_updates(0)
    return max([u["update_id"] for u in ups], default=0)


def wait_for_feedback(baseline):
    """WAIT_SECONDS 동안 답장을 기다린다. 있으면 (텍스트, 새 baseline), 없으면 (None, baseline)."""
    deadline = time.time() + WAIT_SECONDS
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL)
        ups = get_updates(baseline + 1)
        msgs = [
            u for u in ups
            if u.get("message", {}).get("chat", {}).get("id") == int(TELEGRAM_CHAT_ID)
            and u.get("message", {}).get("text")
        ]
        if msgs:
            new_baseline = max(u["update_id"] for u in ups)
            return msgs[-1]["message"]["text"].strip(), new_baseline
        remain = int(deadline - time.time())
        if remain > 0:
            print("대기 중... 남은 시간 " + str(remain) + "초")
    return None, baseline


def main():
    with open("collection_result.json", "r", encoding="utf-8") as f:
        result = json.load(f)

    os.makedirs(STATE_DIR, exist_ok=True)
    print("트랙: {} / 키워드: {}".format(
        result.get("track", "jisik"), result.get("top_keyword", "")))

    plan = parse_json(call_claude(build_plan_prompt(result)))
    baseline = latest_update_id()
    send_message(format_plan_message(plan, result, 0))

    for round_no in range(1, MAX_REVISIONS + 1):
        feedback, baseline = wait_for_feedback(baseline)
        if not feedback:
            print("무응답 — 기획안 확정하고 본문 작성으로 진행합니다.")
            break
        if is_approval(feedback):
            print("확인 응답 수신 — 즉시 진행합니다.")
            break
        print("기획안 수정 요청 수신: " + feedback)
        plan = parse_json(call_claude(build_plan_prompt(result, feedback=feedback, previous=plan)))
        send_message(format_plan_message(plan, result, round_no))
    else:
        print("수정 횟수 상한 도달 — 현재 기획안으로 진행합니다.")

    save_history(plan, result)
    plan["calc_url"] = CALC_URL
    plan["track"] = result.get("track", "jisik")
    with open(PLAN_FILE, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)

    # 본문 단계가 기획안 답장을 '초안 수정 요청'으로 오해하지 않도록 기준점을 갱신
    with open(os.path.join(STATE_DIR, "last_update_id.txt"), "w", encoding="utf-8") as f:
        f.write(str(latest_update_id()))

    print("기획안 확정:")
    print(json.dumps(plan, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
