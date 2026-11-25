# -*- coding: utf-8 -*-
"""
발행일이 정해진 수동 원고를 그날 아침 텔레그램으로 보낸다.

2026-11-25 추가. 파이프라인(월·목)이 쓰지 않는 글 — 업체 DB 분기 글처럼
대화창에서 Claude 와 함께 쓴 원고 — 을 발행일에 맞춰 받기 위한 것이다.

규칙
  · scheduled/YYYY-MM-DD_이름.md 를 두면, 그 날짜(KST) 아침에 보낸다.
  · 날짜가 지났는데 아직 안 보낸 파일도 보낸다 (실행이 빠졌던 날 대비).
  · 보낸 파일은 scheduled/sent/ 로 옮긴다. 다시 보내려면 되돌려 놓으면 된다.
  · 파이프라인의 상태(state/)는 건드리지 않는다. 승인 대기 기준점도 그대로다.
    그래서 이 원고에는 '승인' 답장이 필요 없다 — 네이버에 직접 올리면 끝.

환경변수는 generate_draft.py 와 같다 (그 모듈의 렌더러를 쓴다).
"""
import datetime
import glob
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import generate_draft as gd  # noqa: E402

KST = datetime.timezone(datetime.timedelta(hours=9))
DIR = "scheduled"
SENT = os.path.join(DIR, "sent")


def due_files(today):
    out = []
    for path in sorted(glob.glob(os.path.join(DIR, "*.md"))):
        m = re.match(r"(\d{4}-\d{2}-\d{2})_", os.path.basename(path))
        if not m:
            continue
        if datetime.date.fromisoformat(m.group(1)) <= today:
            out.append((m.group(1), path))
    return out


def strip_comments(text):
    return re.sub(r"<!--.*?-->\s*", "", text, flags=re.DOTALL)


def send_one(day, path):
    with open(path, encoding="utf-8") as f:
        md = strip_comments(f.read())
    m = re.search(r"^#\s+(.+)$", md, flags=re.MULTILINE)
    title = m.group(1).strip() if m else os.path.basename(path)

    html = gd.render_naver_html(md, title=title, top_keyword=None,
                                rows=None, table_position="none")
    out = os.path.join(DIR, os.path.basename(path)[:-3] + ".html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)

    caption = ("[발행일 원고] " + day + "\n" + title
               + "\n\n▶ 파일을 브라우저로 열고 전체 복사 → 네이버 글쓰기에 붙여넣기."
               + "\n「 」로 묶은 지난 글 제목에는 링크를 걸어 주세요."
               + "\n파이프라인 글이 아니라 '승인' 답장은 필요 없습니다.")
    gd.send_telegram_document(out, caption[:1000])

    try:
        h, hi, sub = gd.generate_thumbnail_copy(title, "동탄 기업 동향")
        thumb_path = os.path.join(DIR, os.path.basename(path)[:-3] + ".png")
        gd.thumb.make_thumbnail(thumb_path, label="동탄 기업 동향",
                                headline=h, highlight=hi, sub=sub,
                                crop_key="동탄 산업단지")
        gd.send_telegram_photo(thumb_path, "[썸네일] " + title)
    except Exception as e:
        print("썸네일 실패(원고에는 영향 없음): " + repr(e))

    os.makedirs(SENT, exist_ok=True)
    for p in (path, out, out[:-5] + ".png"):
        if os.path.exists(p):
            shutil.move(p, os.path.join(SENT, os.path.basename(p)))
    print("보냄: " + path)


def main():
    today = datetime.datetime.now(KST).date()
    files = due_files(today)
    if not files:
        print("오늘(" + today.isoformat() + ") 보낼 원고 없음")
        return 0
    failed = 0
    for day, path in files:
        try:
            send_one(day, path)
        except Exception as e:
            failed += 1
            print("실패: " + path + " — " + repr(e))
            gd.send_telegram_message("⚠ 발행일 원고 전송 실패: "
                                     + os.path.basename(path) + "\n" + repr(e)[:500])
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
