# -*- coding: utf-8 -*-
"""
state/draft.md 를 새로 쓰지 않고 그대로 다시 렌더링해 텔레그램으로 보낸다.

2026-11-19 추가. 대화창에서 Claude 가 초안을 고쳐 state/draft.md 에 올렸을 때
쓴다. 수집·기획·본문 생성(generate_draft.main)을 다시 돌리면 안건이 소모되고
본문도 새로 써져 버리므로, 렌더링·전송만 따로 떼었다.

하는 일
  1. state/draft.md + state/collection_result.json 으로 draft.html 렌더링
     (지도는 collection_result 의 agenda 로 그린다)
  2. 텔레그램에 [수정본] 문서 전송
  3. 제목으로 썸네일 다시 만들어 전송
  4. 승인 대기 기준점(last_update_id) 갱신 — 이후 답장을 이 수정본 기준으로 처리

환경변수는 generate_draft.py 와 같다.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import generate_draft as gd  # noqa: E402

NOTE_FILE = os.path.join("state", "resend_request.txt")


def main():
    with open(os.path.join("state", "draft.md"), encoding="utf-8") as f:
        draft = f.read()
    with open(os.path.join("state", "collection_result.json"), encoding="utf-8") as f:
        result = json.load(f)

    note = ""
    if os.path.exists(NOTE_FILE):
        with open(NOTE_FILE, encoding="utf-8") as f:
            note = "\n".join(l for l in f.read().splitlines()
                             if l.strip() and not l.startswith("#"))

    track = result.get("track", "jisik")
    title = "[수정본] " + result["category_display"] + " - " + result["top_keyword"]
    html = gd.render_naver_html(
        draft, title=title,
        category_display=result["category_display"],
        top_keyword=result["top_keyword"],
        rows=result.get("rows", []), table_position="none",
        track=track, agenda=result.get("agenda"))
    with open("draft.html", "w", encoding="utf-8") as f:
        f.write(html)
    with open(os.path.join("state", "draft.html"), "w", encoding="utf-8") as f:
        f.write(html)

    marks = draft.count("<mark>")
    caption = (title
               + ("\n" + note if note else "")
               + "\n노란 하이라이트 " + str(marks) + "곳만 직접 확인해주세요."
               + "\n\n▶ 파일을 브라우저로 열면 서식이 그대로 보입니다."
               + "\n승인하시려면 '승인'이라고, 수정이 필요하면 원하는 내용을 답장해주세요.")
    gd.send_telegram_document("draft.html", caption[:1000])

    try:
        m = re.search(r"^#\s+(.+)$", draft, flags=re.MULTILINE)
        post_title = m.group(1).strip() if m else result["top_keyword"]
        gd.save_title_history(post_title)
        h, hi, sub = gd.generate_thumbnail_copy(post_title, result["category_display"])
        thumb_path = os.path.join("state", "thumbnail.png")
        gd.thumb.make_thumbnail(thumb_path, label=result["category_display"],
                                headline=h, highlight=hi, sub=sub,
                                crop_key=result["top_keyword"])
        gd.send_telegram_photo(thumb_path, "[썸네일] " + post_title
                               + "\n블로그 발행 시 대표 이미지로 사용하세요.")
    except Exception as e:
        print("썸네일 실패(본문에는 영향 없음): " + repr(e))

    baseline = gd.get_latest_telegram_update_id()
    with open(os.path.join("state", "last_update_id.txt"), "w", encoding="utf-8") as f:
        f.write(str(baseline))


if __name__ == "__main__":
    main()
