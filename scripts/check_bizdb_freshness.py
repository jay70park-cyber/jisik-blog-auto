#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_bizdb_freshness.py — 업체 DB 자료 갱신 시점 알림

원본 파일이 구글 드라이브에 있어 저장소에서 날짜를 읽을 수 없으므로,
'마지막으로 갱신한 시점'을 이 파일 안에 기록해두고 경과일로 판단한다.

갱신하고 나면 아래 LAST_UPDATED 날짜만 고쳐서 커밋하면 된다.

발송 전용이다. 답장은 읽지 않는다.
(블로그 승인 봇이 30분마다 텔레그램을 읽으므로, 여기서 답장을 받으면
 초안 수정 요청으로 오독될 수 있다. 알림만 보낸다.)
"""

import os
import urllib.request
import urllib.parse
from datetime import date, timedelta

# ──────────────────────────────────────────────────────────────────
# 갱신할 때 이 날짜만 고치면 된다 (자료를 받아 적재한 날)
# ──────────────────────────────────────────────────────────────────
LAST_UPDATED = {
    "nps":     date(2026, 8, 20),   # 국민연금 가입 사업장 내역 (2026-06 기준분 적재)
    "factory": date(2026, 8, 20),   # 전국등록공장현황 (2024-12 기준분 적재)
}

# 자료별 갱신 주기와 안내
SPECS = {
    "nps": {
        "name": "국민연금 가입 사업장 내역",
        "cycle_days": 90,            # 분기 1회
        "grace_days": 14,            # 이 기간까지는 여유로 본다
        "search": "국민연금공단_국민연금 가입 사업장 내역",
        "note": "이 자료가 DB의 본체입니다. 분기를 거르면 그 시점은 복구 불가.",
    },
    "factory": {
        "name": "전국등록공장현황",
        "cycle_days": 365,           # 연 1회
        "grace_days": 30,
        "search": "한국산업단지공단_전국등록공장현황 등록공장현황자료",
        "note": "산업단지명 매칭에 쓰입니다. 오래되면 신규 단지가 안 잡힙니다.",
    },
}

HOW = [
    "1. data.go.kr 접속 → 위 검색어로 검색",
    "2. 반드시 '파일데이터' 탭에서 받기 (오픈API 탭 아님)",
    "3. 구글 드라이브 bizdb 폴더에 업로드",
    "4. Colab 업체DB.ipynb 열기 → 1·2·3번 셀 실행",
    "5. '다음 분기 자료 추가' 셀 실행",
    "6. 이 스크립트의 LAST_UPDATED 날짜 수정 후 커밋",
]


def send_telegram(text: str, timeout: int = 20) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat = os.environ["TELEGRAM_CHAT_ID"]
    url = "https://api.telegram.org/bot" + token + "/sendMessage"
    body = urllib.parse.urlencode({
        "chat_id": chat,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    with urllib.request.urlopen(
            urllib.request.Request(url, data=body, method="POST"),
            timeout=timeout) as res:
        res.read()


def check(key: str) -> dict:
    """{name, level, state, how} 형태로 반환 — 기존 점검 스크립트와 같은 규약."""
    spec = SPECS[key]
    last = LAST_UPDATED[key]
    elapsed = (date.today() - last).days
    due = spec["cycle_days"]
    over = elapsed - due

    if over < -spec["grace_days"]:
        level, state = "ok", f"{last} 갱신, {due - elapsed}일 뒤 예정"
    elif over < 0:
        level, state = "soon", f"{last} 갱신, {due - elapsed}일 뒤 예정"
    elif over <= spec["grace_days"]:
        level, state = "warn", f"{last} 갱신 후 {elapsed}일 경과 — 갱신 시점"
    else:
        level, state = "late", f"{last} 갱신 후 {elapsed}일 경과 — {over}일 지연"

    return {
        "name": spec["name"],
        "level": level,
        "state": state,
        "search": spec["search"],
        "note": spec["note"],
        "how": HOW,
    }


ICON = {"ok": "✅", "soon": "🕒", "warn": "⚠️", "late": "🔴"}


def build_message(items: list) -> str:
    lines = ["📊 업체 DB 자료 갱신 점검", ""]
    for it in items:
        lines.append(f"{ICON[it['level']]} {it['name']}")
        lines.append(f"   {it['state']}")
        if it["level"] in ("warn", "late"):
            lines.append(f"   검색어: {it['search']}")
            lines.append(f"   {it['note']}")
        lines.append("")

    if any(it["level"] in ("warn", "late") for it in items):
        lines.append("— 갱신 방법 —")
        lines.extend("   " + h for h in HOW)
        lines.append("")
        lines.append("⚠️ 원본 CSV는 지우지 마세요. 과거 시점은 복구 불가입니다.")
    return "\n".join(lines).rstrip()


def main() -> None:
    items = [check(k) for k in SPECS]

    # 갱신할 게 없으면 조용히 넘어간다 (알림 피로 방지)
    if all(it["level"] == "ok" for it in items):
        print("갱신 시점 아님 — 발송 생략")
        for it in items:
            print(f"  {it['name']}: {it['state']}")
        return

    msg = build_message(items)
    print(msg)
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        send_telegram(msg)
        print("\n텔레그램 발송 완료")
    else:
        print("\n(TELEGRAM_BOT_TOKEN 없음 — 발송 생략)")


if __name__ == "__main__":
    main()
