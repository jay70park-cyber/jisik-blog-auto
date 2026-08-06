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
"""
import os
import json
import time
import urllib.request
import urllib.parse

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

MODEL = "claude-sonnet-5"
STATE_DIR = "state"
PLAN_FILE = os.path.join(STATE_DIR, "plan.json")

CALC_URL = os.environ.get("CALC_URL", "https://jay70park-cyber.github.io/jisik-calc/")

WAIT_SECONDS = int(os.environ.get("PLAN_WAIT_SECONDS", "300"))  # 5분
POLL_INTERVAL = 20
MAX_REVISIONS = 2

READERS = ["임대수익 목적 투자자", "실사용 매수자", "실사용 임차인"]
OUTPUT_TYPES = [
    "① 계산 공식 — 내 숫자를 대입해 답을 내는 법",
    "② 합격/불합격 기준 — 걸러야 할 물건 판별법",
    "③ A vs B 선택표 — 상황별 어느 쪽이 유리한지",
    "④ 위험 신호 목록 — 계약 전 돌아서야 할 징후",
    "⑤ 순서 안내 — 그대로 따라 하는 절차",
]
CALC_TABS = ["실투자금", "임대수익률", "매수 vs 임차", "없음"]


def call_claude(prompt, timeout=120):
    url = "https://api.anthropic.com/v1/messages"
    body = json.dumps(
        {"model": MODEL, "max_tokens": 1500, "messages": [{"role": "user", "content": prompt}]},
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
    text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)


def build_plan_prompt(result, feedback=None, previous=None):
    kw = result["top_keyword"]
    cat = result["category_display"]
    base = f"""당신은 경기도 동탄 지역 지식산업센터 전문 공인중개사의 블로그 기획을 돕습니다.

이번 글의 카테고리는 "{cat}", 검색 관심도 1위 키워드는 "{kw}" 입니다.

이 키워드로 글을 쓰기 전에 기획안을 먼저 만드세요. 원칙은 아래와 같습니다.

1. 독자는 반드시 한 명만 고릅니다. 후보: {", ".join(READERS)}
   - 같은 사실도 독자에 따라 정반대 결론이 되므로, 두 명 이상을 겨냥하지 마세요.
2. 이 글이 독자에게 남길 '산출물'을 아래 5가지 중 하나로 정합니다.
{chr(10).join("   " + t for t in OUTPUT_TYPES)}
   - 단순한 정보 요약이나 뉴스 해설은 산출물이 아닙니다. 독자가 자기 물건에 대입해 답을 낼 수 있어야 합니다.
3. 핵심 결론을 한 문장으로 씁니다. "무엇이 일어났다"가 아니라 "그래서 독자는 무엇을 해야 한다"의 형태여야 합니다.
4. 판단 기준 3가지를 만듭니다.
   - 숫자 컷라인(예: 수익률 4% 이상)은 쓰지 마세요. 책임 소재가 될 수 있습니다.
   - 대신 "이것이 정리되지 않으면 결론을 내리기 이르다" 형태의 정성적 확인 항목으로 씁니다.
   - 각 항목은 한 문장, 독자가 스스로 예/아니오를 판단할 수 있어야 합니다.
5. 연결할 계산기 탭을 고릅니다. 후보: {", ".join(CALC_TABS)}
   - 이 글의 주제와 직접 관련이 없으면 "없음"으로 두세요.

아래 JSON 형식으로만 출력하세요. 다른 설명은 붙이지 마세요.

{{
  "reader": "독자 상황 (위 후보 중 하나)",
  "output_type": "산출물 유형 (위 5가지 중 하나, 번호 포함)",
  "conclusion": "핵심 결론 한 문장",
  "criteria": ["판단 기준 1", "판단 기준 2", "판단 기준 3"],
  "calc_tab": "계산기 탭 (위 후보 중 하나)",
  "title_draft": "가제 (독자가 얻어갈 산출물이 드러나게)"
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
    lines = [
        head + " " + result["category_display"] + " · " + result["top_keyword"],
        "",
        "독자 상황   : " + plan.get("reader", "-"),
        "산출물 유형 : " + plan.get("output_type", "-"),
        "핵심 결론   : " + plan.get("conclusion", "-"),
        "",
        "판단 기준 3가지",
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

    plan = parse_json(call_claude(build_plan_prompt(result)))
    baseline = latest_update_id()
    send_message(format_plan_message(plan, result, 0))

    for round_no in range(1, MAX_REVISIONS + 1):
        feedback, baseline = wait_for_feedback(baseline)
        if not feedback:
            print("무응답 — 기획안 확정하고 본문 작성으로 진행합니다.")
            break
        low = feedback.strip().lower()
        if low in ("ok", "ㅇㅋ", "좋아", "진행", "승인"):
            print("확인 응답 수신 — 즉시 진행합니다.")
            break
        print("기획안 수정 요청 수신: " + feedback)
        plan = parse_json(call_claude(build_plan_prompt(result, feedback=feedback, previous=plan)))
        send_message(format_plan_message(plan, result, round_no))
    else:
        print("수정 횟수 상한 도달 — 현재 기획안으로 진행합니다.")

    plan["calc_url"] = CALC_URL
    with open(PLAN_FILE, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)

    # 본문 단계가 기획안 답장을 '초안 수정 요청'으로 오해하지 않도록 기준점을 갱신
    with open(os.path.join(STATE_DIR, "last_update_id.txt"), "w", encoding="utf-8") as f:
        f.write(str(latest_update_id()))

    print("기획안 확정:")
    print(json.dumps(plan, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
