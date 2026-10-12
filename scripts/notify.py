# -*- coding: utf-8 -*-
"""텔레그램 발송 한 군데.

스크립트마다 같은 함수를 따로 들고 있다가 한쪽만 고쳐져서
사고가 났다. 여기 하나만 둔다.

  2026-10-12  검증 리포트가 4,378자가 되어 통째로 안 갔다.
              sendMessage 한 통의 한도는 4,096자이고 넘기면
              400 Bad Request 다. 사실 확인을 붙여 리포트가
              두 배 가까이 길어졌는데 보내는 쪽을 안 봤다.
              실패는 로그 맨 끝 한 줄로만 남아, 검증이 돌았는지
              안 돌았는지조차 알 수 없었다.

  collect_council.py 는 text[:3900] 으로 **잘라서** 보내고 있었다.
  400 은 안 나지만 긴 알림의 뒷부분이 사라진다. 회의 끝에 있는
  의결 결과가 거기 있다. 자르지 말고 나눈다.

쓰는 법
    from notify import send_telegram
    send_telegram(text)                      # 환경변수에서 토큰을 읽는다
    send_telegram(text, token, chat_id)      # 직접 넘겨도 된다
"""
import os
import urllib.parse
import urllib.request

# 한도는 4,096자. 조각 번호 머리글과 여유분을 빼고 잡는다.
TELEGRAM_LIMIT = 3900


def split_for_telegram(text, limit=TELEGRAM_LIMIT):
    """줄 단위로 끊어 limit 이하 조각으로 나눈다.

    줄 가운데에서 끊지 않는다. 검증 리포트나 회의록 발췌는
    한 줄이 한 항목이라 가운데가 끊기면 읽을 수 없다.
    한 줄이 통째로 한도를 넘을 때만 글자 수로 자른다.

    나눈 것을 줄바꿈으로 다시 이으면 원문과 같다.
    """
    out, buf = [], ""
    for line in str(text).split("\n"):
        while len(line) > limit:
            if buf:
                out.append(buf)
                buf = ""
            out.append(line[:limit])
            line = line[limit:]
        if buf and len(buf) + 1 + len(line) > limit:
            out.append(buf)
            buf = line
        else:
            buf = (buf + "\n" + line) if buf else line
    if buf:
        out.append(buf)
    return out or [""]


def send_telegram(text, token=None, chat_id=None, timeout=20):
    """길면 나눠 보낸다. 한 조각이 실패해도 나머지는 계속 보낸다.

    반환 (보낸 조각 수, 전체 조각 수). 토큰이 없으면 (0, 0).
    """
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return 0, 0

    url = "https://api.telegram.org/bot" + token + "/sendMessage"
    parts = split_for_telegram(text)
    total = len(parts)
    sent = 0
    for i, part in enumerate(parts, 1):
        msg = part if total == 1 else "({}/{})\n{}".format(i, total, part)
        body = urllib.parse.urlencode(
            {"chat_id": chat_id, "text": msg}).encode("utf-8")
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(url, data=body, method="POST"),
                    timeout=timeout) as res:
                res.read()
            sent += 1
        except Exception as e:
            print("텔레그램 전송 오류 ({}/{}, {}자): {}".format(
                i, total, len(msg), e))
    if total > 1:
        print("텔레그램 {}조각 중 {}조각 발송".format(total, sent))
    return sent, total


if __name__ == "__main__":
    # 한도 안팎을 모두 돌려본다
    for name, text in [
        ("짧은 글", "안녕하세요"),
        ("한도 근처", "\n".join("줄 {} ".format(i) + "가" * 60
                              for i in range(70))),
        ("한 줄이 긴 글", "나" * 9000),
    ]:
        parts = split_for_telegram(text)
        joined = "\n".join(parts) if "\n" in text else "".join(parts)
        ok = max(len(p) for p in parts) + 8 <= 4096
        print("{:<12} {:>6,}자 → {}조각 / 최대 {:,}자 / 한도이내 {}".format(
            name, len(text), len(parts), max(len(p) for p in parts),
            "O" if ok else "X"))
        assert ok, name
    # 줄 단위 분할은 원문을 보존한다
    src = "\n".join("항목 {}".format(i) + "다" * 200 for i in range(40))
    assert "\n".join(split_for_telegram(src)) == src
    print("원문 보존: 통과")
