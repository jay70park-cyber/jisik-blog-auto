# -*- coding: utf-8 -*-
"""
텔레그램에 새 답장이 왔는지 주기적으로 확인해서:
- "승인" 계열 답장이면 -> 게시 단계로 진행 (현재는 네이버 로그인 인증 전이라 안내 메시지만 발송)
- 그 외 텍스트면 -> 수정 요청으로 간주해 Claude로 초안을 다시 생성해서 재발송
상태(state/)는 저장소에 커밋되어 다음 실행에서도 이어서 참조된다.
"""
import os
import json
import urllib.request
import urllib.parse

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

MODEL = "claude-sonnet-5"
STATE_DIR = "state"
LAST_UPDATE_FILE = os.path.join(STATE_DIR, "last_update_id.txt")
DRAFT_FILE = os.path.join(STATE_DIR, "draft.md")
STATUS_FILE = os.path.join(STATE_DIR, "status.json")

APPROVE_WORDS = ["승인", "ok", "approve", "좋아", "게시"]


def read_last_update_id():
    if os.path.exists(LAST_UPDATE_FILE):
        with open(LAST_UPDATE_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
            return int(content) if content else 0
    return 0


def write_last_update_id(update_id):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(LAST_UPDATE_FILE, "w", encoding="utf-8") as f:
        f.write(str(update_id))


def get_telegram_updates(offset, timeout=15):
    url = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/getUpdates?" + urllib.parse.urlencode(
        {"offset": offset, "timeout": 0}
    )
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as res:
        data = json.load(res)
    return data.get("result", [])


def send_telegram_message(text, timeout=20):
    url = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendMessage"
    body = urllib.parse.urlencode({"chat_id": TELEGRAM_CHAT_ID, "text": text}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as res:
        res.read()


def send_telegram_document(filepath, caption, timeout=30):
    url = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendDocument"
    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    with open(filepath, "rb") as f:
        file_content = f.read()
    body = b""
    body += ("--" + boundary + "\r\n").encode()
    body += b'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
    body += (TELEGRAM_CHAT_ID + "\r\n").encode()
    body += ("--" + boundary + "\r\n").encode()
    body += b'Content-Disposition: form-data; name="caption"\r\n\r\n'
    body += (caption + "\r\n").encode()
    body += ("--" + boundary + "\r\n").encode()
    body += ('Content-Disposition: form-data; name="document"; filename="draft.md"\r\n').encode()
    body += b"Content-Type: text/markdown\r\n\r\n"
    body += file_content
    body += ("\r\n--" + boundary + "--\r\n").encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary},
    )
    with urllib.request.urlopen(req, timeout=timeout) as res:
        res.read()


def call_claude_revise(previous_draft, instruction, timeout=120):
    prompt = f"""아래는 네이버 블로그용으로 작성 중인 마크다운 초안입니다.

[기존 초안]
{previous_draft}

[수정 지시사항]
{instruction}

위 지시사항을 반영해서 초안 전체를 다시 작성해주세요. 다음 규칙은 계속 지켜주세요:
- 확실하지 않거나 확인이 필요한 수치/사실은 <mark>이 태그</mark>로 감싸기
- 표, 이미지 제안([이미지: 설명]), 하단 참고 자료 링크 구성은 유지
- 마크다운 텍스트만 출력하고 다른 설명은 붙이지 마세요."""

    url = "https://api.anthropic.com/v1/messages"
    body = json.dumps(
        {
            "model": MODEL,
            "max_tokens": 4000,
            "tools": [{"type": "web_search_20250305", "name": "web_search"}],
            "messages": [{"role": "user", "content": prompt}],
        },
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
    parts = data.get("content", [])
    return "".join(p.get("text", "") for p in parts if p.get("type") == "text")


def main():
    last_id = read_last_update_id()
    updates = get_telegram_updates(offset=last_id + 1)

    # 우리 챗봇 채팅방에서 온, 텍스트가 있는 메시지만 필터링
    relevant = [
        u for u in updates
        if u.get("message", {}).get("chat", {}).get("id") == int(TELEGRAM_CHAT_ID)
        and u.get("message", {}).get("text")
    ]

    if not relevant:
        print("새 메시지 없음. 종료합니다.")
        return

    # 가장 최근 메시지 하나만 처리 (여러 개 밀려있어도 최신 지시를 기준으로 처리)
    latest = relevant[-1]
    text = latest["message"]["text"].strip()
    max_update_id = max(u["update_id"] for u in updates)

    if text.lower() in [w.lower() for w in APPROVE_WORDS]:
        print("승인 메시지 감지: " + text)
        status = {"state": "approved", "message": text}
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False, indent=2)
        send_telegram_message(
            "승인 확인했습니다. 다만 네이버 블로그 자동 게시는 아직 로그인 인증 연결 전이라, "
            "이 초안은 승인 상태로 표시만 해두었습니다. 인증 연결되면 자동으로 게시되도록 이어서 설정할게요."
        )
    else:
        print("수정 요청 감지: " + text)
        if not os.path.exists(DRAFT_FILE):
            send_telegram_message("이전 초안 파일을 찾을 수 없어서 수정할 수 없습니다. 관리자에게 문의해주세요.")
        else:
            with open(DRAFT_FILE, "r", encoding="utf-8") as f:
                previous_draft = f.read()
            revised = call_claude_revise(previous_draft, text)
            with open(DRAFT_FILE, "w", encoding="utf-8") as f:
                f.write(revised)
            mark_count = revised.count("<mark>")
            send_telegram_document(
                DRAFT_FILE,
                "[수정된 초안] 요청하신 내용을 반영했습니다. <mark> 표시 " + str(mark_count) + "곳 확인해주세요.\n"
                "승인하시려면 '승인'이라고 답장해주세요.",
            )

    write_last_update_id(max_update_id)


if __name__ == "__main__":
    main()
