# -*- coding: utf-8 -*-
"""고시가 어느 현안에 붙는지 점검한다. 아무것도 바꾸지 않는다.

collect_hscity.py 의 match_agenda() 를 그대로 불러, 누적된 고시
전체를 다시 태그해 보고 **지금 CSV에 적힌 것과 다른 곳**을 보여준다.

쓰는 때
  · 키워드나 지역 필터를 고친 뒤 효과를 확인할 때
  · 현안의 최근진전일이 이상할 때 원인 고시를 찾을 때

본체는 새로 올라온 고시만 현안에 연결하므로, 고친 필터가
기존 고시에 어떻게 작용하는지는 이 스크립트로만 볼 수 있다.

실행
    python3 scripts/check_agenda_match.py
    python3 scripts/check_agenda_match.py A004      # 한 현안만
"""
import os
import csv
import io
import sys

import collect_hscity as ch

NOTICE_CSV = os.path.join("data", "hscity_notices.csv")
AGENDA_CSV = os.path.join("data", "local_agenda.csv")


def main():
    want = sys.argv[1].strip() if len(sys.argv) > 1 else ""

    if not os.path.exists(NOTICE_CSV) or not os.path.exists(AGENDA_CSV):
        print("데이터 파일이 없습니다. data/ 아래에서 실행하세요.")
        return 1

    agenda = ch.load_agenda()
    with io.open(AGENDA_CSV, encoding="utf-8-sig", newline="") as f:
        arows = list(csv.DictReader(f))
    name_of = {r["id"]: (r.get("현안명") or "").strip() for r in arows}
    target = name_of.get(want, want)

    with io.open(NOTICE_CSV, encoding="utf-8-sig", newline="") as f:
        notices = list(csv.DictReader(f))
    print("고시 누적 {}건 · 현안 {}건".format(len(notices), len(agenda)))
    if target:
        print("대상 현안: {}".format(target))
    print()

    hit, changed = {}, []
    for n in notices:
        title = (n.get("제목") or "").strip()
        was = (n.get("현안") or "").strip()
        now = ", ".join(ch.match_agenda(title, agenda))
        if was != now:
            changed.append((n.get("공고일자", ""), title, was, now))
        for nm in [x for x in now.split(", ") if x]:
            hit.setdefault(nm, []).append(n)

    # ── 바뀌는 것 ──
    only = [c for c in changed if not target
            or target in c[2] or target in c[3]]
    print("■ 연결이 바뀌는 고시 {}건{}".format(
        len(only), " (전체 {}건 중)".format(len(changed)) if target else ""))
    for d, t, was, now in sorted(only, reverse=True)[:30]:
        print("  {} {}".format(d, t[:60]))
        print("      {}  →  {}".format(was or "(없음)", now or "(없음)"))
    if not only:
        print("  없습니다. 지금 CSV 의 연결이 새 규칙과 같습니다.")

    # ── 현안별 최신 고시 ──
    print()
    print("■ 현안별 최신 고시 (새 규칙 기준)")
    for r in arows:
        nm = (r.get("현안명") or "").strip()
        if target and nm != target:
            continue
        rows = sorted(hit.get(nm, []), key=lambda x: x.get("공고일자", ""))
        cur = (r.get("최근진전일") or "").strip() or "(비어 있음)"
        if not rows:
            print("  {} {:<22} 걸린 고시 없음 · CSV 최근진전일 {}".format(
                r["id"], nm[:20], cur))
            continue
        last = rows[-1]

        # ★ 는 '오염' 일 때만 붙인다.
        #
        # 날짜가 다르다는 것만으로 붙이면 오탐이 난다. 최근진전일은
        # 고시 말고 회의록·뉴스에서도 온다. A001 트램의 2026-09-15 는
        # 예결특위 회의록에서 온 값인데, 고시만 보는 이 스크립트가
        # '어긋났다' 고 찍었다 (2026-10-22).
        #
        # 진짜 오염은 이것이다 — **CSV 의 최근진전일과 같은 날짜의
        # 고시가 이 현안으로 태그돼 있었는데, 새 규칙에서는 안 걸린다.**
        # 그 고시가 최근진전일을 만들었고 지금은 남의 것이라는 뜻이다.
        bad = [c for c in changed
               if c[0] == cur and nm in c[2] and nm not in c[3]]
        mark = "★ " if bad else "  "
        print("  {}{} {:<22} {} {}".format(
            mark, r["id"], nm[:20], last.get("공고일자", ""),
            (last.get("제목") or "")[:44]))
        if bad:
            print("      ⚠ CSV 최근진전일 {} 은 이 고시가 만든 값입니다:"
                  .format(cur))
            print("         {}".format(bad[0][1][:60]))
            print("      → {} / {} 로 고치세요".format(
                last.get("공고일자", ""), (last.get("제목") or "")[:50]))
        elif last.get("공고일자", "") != cur:
            print("      (CSV 최근진전일 {} — 고시 말고 회의록·뉴스에서 "
                  "왔을 수 있습니다)".format(cur))
    print()
    print("★ = 최근진전일이 '남의 고시' 로 만들어진 현안 — 고쳐야 합니다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
