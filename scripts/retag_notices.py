# -*- coding: utf-8 -*-
"""누적 고시의 `현안` 열을 지금 키워드로 다시 붙인다.

왜 필요한가
-----------
고시의 `현안` 열은 **수집하던 그때의 키워드로 한 번 적히고 끝난다.**
본체(collect_hscity)는 새로 올라온 고시만 태그하므로, 키워드를 고쳐도
과거분은 옛 연결을 그대로 들고 있다.

그게 조용히 넘어가지 않는 이유가 collect_sources.py 583행이다.

    tagged = name and name in (r.get("현안") or "")
    if not (tagged or any(w in title for w in words)):
        continue

**또는**이다. 옛 태그가 붙어 있으면 키워드를 아무리 좁혀도 그 고시는
글 쓸 재료로 계속 들어온다. 2026-10-22 에 A009 키워드를 주곡·동방·무송
세 곳으로 좁혔지만, 정남·안녕 고시의 `현안` 열에는 아직
`화성 산업단지 조성`이 적혀 있어 재료로는 그대로 섞인다.

무엇을 바꾸고 무엇을 안 바꾸는가
--------------------------------
바꾼다   `현안` 열 하나.
안 바꾼다 `동탄관련`(저장된 값을 그대로 쓴다), `글감` 같은 수기 열,
          행 순서, 열 순서, `data/local_agenda.csv`.

`동탄관련`을 다시 계산하지 않는 것은 일부러다. is_dongtan() 이 그
사이 바뀌었을 수 있고, 그러면 한 번에 두 가지가 움직여 무엇이 무엇을
고쳤는지 안 보인다. **한 번에 하나만 고친다.**

실행
    python3 scripts/retag_notices.py            # 미리보기 — 아무것도 안 바꾼다
    python3 scripts/retag_notices.py --apply    # 실제로 쓴다
"""
import os
import csv
import io
import sys

import collect_hscity as ch

NOTICE_CSV = os.path.join("data", "hscity_notices.csv")
PREVIEW = 25


def read_rows(path):
    """행과 열 이름, 그리고 원본 줄바꿈을 함께 돌려준다."""
    with open(path, "rb") as f:
        raw = f.read()
    newline = "\r\n" if b"\r\n" in raw else "\n"
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    cols = list(reader.fieldnames or [])
    return rows, cols, newline


def write_rows(path, rows, cols, newline):
    """읽은 그대로 쓴다. 행 순서도 열 순서도 줄바꿈도 건드리지 않는다."""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore",
                       lineterminator=newline)
    w.writeheader()
    w.writerows(rows)
    with open(path, "wb") as f:
        f.write(b"\xef\xbb\xbf")          # utf-8-sig
        f.write(buf.getvalue().encode("utf-8"))


def main():
    apply = "--apply" in sys.argv[1:]

    if not os.path.exists(NOTICE_CSV):
        print("{} 가 없습니다. 저장소 뿌리에서 실행하세요.".format(NOTICE_CSV))
        return 1

    agenda = ch.load_agenda()
    rows, cols, newline = read_rows(NOTICE_CSV)
    if "현안" not in cols:
        print("`현안` 열이 없습니다. 파일을 확인하세요.")
        return 1

    print("고시 {}건 · 현안 {}건 · 줄바꿈 {}".format(
        len(rows), len(agenda), "CRLF" if newline == "\r\n" else "LF"))
    print("모드: {}".format("적용" if apply else "미리보기 (아무것도 안 바꿉니다)"))
    print()

    changed, gained, lost = [], {}, {}
    for r in rows:
        title = (r.get("제목") or "").strip()
        # 저장된 동탄관련 값을 그대로 쓴다. 다시 계산하지 않는다.
        dongtan = (r.get("동탄관련") or "").strip() == "O"
        was = (r.get("현안") or "").strip()
        now = ", ".join(ch.match_agenda(title, agenda, dongtan))
        if was == now:
            continue
        changed.append((r.get("공고일자", ""), title, was, now))
        wset = set(x for x in was.split(", ") if x)
        nset = set(x for x in now.split(", ") if x)
        for nm in nset - wset:
            gained[nm] = gained.get(nm, 0) + 1
        for nm in wset - nset:
            lost[nm] = lost.get(nm, 0) + 1
        r["현안"] = now

    print("■ 연결이 바뀌는 고시 {}건".format(len(changed)))
    for d, t, was, now in sorted(changed, reverse=True)[:PREVIEW]:
        print("  {} {}".format(d, t[:58]))
        print("      {}  →  {}".format(was or "(없음)", now or "(없음)"))
    if len(changed) > PREVIEW:
        print("  … 그 밖 {}건".format(len(changed) - PREVIEW))

    print()
    print("■ 현안별 증감")
    for nm in sorted(set(gained) | set(lost)):
        print("  {:<24} +{}  -{}".format(
            nm[:22], gained.get(nm, 0), lost.get(nm, 0)))
    if not (gained or lost):
        print("  없습니다. 지금 연결이 키워드와 이미 같습니다.")

    if not changed:
        print()
        print("바꿀 것이 없습니다.")
        return 0

    print()
    if not apply:
        print("미리보기였습니다. 실제로 쓰려면 --apply 를 붙이세요.")
        return 0

    write_rows(NOTICE_CSV, rows, cols, newline)
    print("{} 의 `현안` 열 {}건을 고쳤습니다.".format(NOTICE_CSV, len(changed)))
    print("local_agenda.csv 는 건드리지 않았습니다 — 최근진전일은 그대로입니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
