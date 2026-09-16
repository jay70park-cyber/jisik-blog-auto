# -*- coding: utf-8 -*-
"""
변경분 자체 점검 — 상태 파일을 건드리지 않고, 외부 호출도 하지 않는다.

저장소 루트에서 실행한다.
    python selftest.py

통과하면 마지막 줄에 '모두 통과'가 찍힌다.
"""
import os
import sys
import csv
import json
import datetime
import importlib

OK, NG = "  [OK]", "  [NG]"
fails = []


def check(cond, msg):
    print((OK if cond else NG) + " " + msg)
    if not cond:
        fails.append(msg)
    return cond


def section(t):
    print("\n" + "=" * 58)
    print(t)
    print("=" * 58)


# ─────────────────────────────────────────────
section("1. 모듈 import")
# ─────────────────────────────────────────────
for name in ["content_rules", "collect_sources", "generate_plan", "generate_draft"]:
    try:
        importlib.import_module(name)
        check(True, name)
    except Exception as e:
        check(False, "{} — {}: {}".format(name, type(e).__name__, e))

if fails:
    print("\nimport 단계에서 막혔습니다. 위 오류를 먼저 고치세요.")
    sys.exit(1)

import content_rules as cr
import collect_sources as cs
import generate_plan as gp
import generate_draft as gd


# ─────────────────────────────────────────────
section("2. local_agenda.csv 스키마")
# ─────────────────────────────────────────────
NEED = ["id", "상태", "현안명", "분류", "단계", "최근진전일", "진전내용",
        "중요도", "출처", "키워드", "메모", "확인필요", "추가일", "마지막발행일"]

path = os.path.join("data", "local_agenda.csv")
if check(os.path.exists(path), "파일 존재: " + path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        cols = rd.fieldnames or []
        rows = list(rd)
    missing = [c for c in NEED if c not in cols]
    check(not missing, "필수 열 14개 — 누락: {}".format(missing or "없음"))
    check(len(rows) > 0, "데이터 행 {}건".format(len(rows)))

    bad_len = [r.get("id") for r in rows if len(r) != len(cols) or None in r.values()]
    check(not bad_len, "열 수 불일치 행 — {}".format(bad_len or "없음"))

    ids = [r.get("id", "") for r in rows]
    check(all(ids), "모든 행에 id 있음")
    check(len(ids) == len(set(ids)), "id 중복 없음")

    bad_state = [r["id"] for r in rows if (r.get("상태") or "").strip() not in ("활성", "보류")]
    check(not bad_state, "상태 값이 활성/보류 — 이상: {}".format(bad_state or "없음"))

    bad_date = []
    for r in rows:
        for col in ("최근진전일", "추가일"):
            v = (r.get(col) or "").strip()
            if v:
                try:
                    datetime.date.fromisoformat(v)
                except ValueError:
                    bad_date.append("{}.{}={}".format(r["id"], col, v))
    check(not bad_date, "날짜 형식 — 이상: {}".format(bad_date or "없음"))

    for r in rows:
        print("      {} {:<12} 상태={} 키워드={}".format(
            r["id"], (r.get("현안명") or "")[:12], r.get("상태"), (r.get("키워드") or "")[:40]))


# ─────────────────────────────────────────────
section("3. 트랙 배정 (앞으로 10개 목요일)")
# ─────────────────────────────────────────────
d = cs.today_kst()
seen = {}
n = 0
while n < 10:
    d += datetime.timedelta(days=1)
    if d.weekday() != 3:
        continue
    n += 1
    track, _, name, _, _ = cs.pick_track(d)
    seen[track] = seen.get(track, 0) + 1
    print("      {} {}".format(d, name))

check("council" in seen, "회의록 특집이 배정됨 ({}회)".format(seen.get("council", 0)))
check("local" in seen, "지역 현안이 배정됨 ({}회)".format(seen.get("local", 0)))

print("\n   월요일 순환은 상태 파일을 증가시키므로 여기서는 건너뜁니다.")


# ─────────────────────────────────────────────
section("4. 아젠다 순환 (상태 미변경)")
# ─────────────────────────────────────────────
# 상태 쓰기와 뉴스 조회를 막아 둔다
cs.write_last_agenda_id = lambda *a, **k: None
cs.mark_published = lambda *a, **k: None
cs.google_news_items = lambda *a, **k: []

last = cs.read_last_agenda_id()
print("      현재 포인터: {}".format(last or "(없음 — 처음부터)"))

active = cs.load_agenda()
check(len(active) > 0, "활성 안건 {}건: {}".format(len(active), [r["id"] for r in active]))

agenda, log, why = cs.pick_agenda()
check(agenda is not None, "안건 선정됨")
if agenda:
    print("      판정 로그:")
    for l in log:
        print("        " + l)
    print("      → 선정: {} {} ({})".format(agenda["id"], agenda["현안명"], why))
    check(agenda["id"] in [r["id"] for r in active], "선정 안건이 활성 목록 안에 있음")

after = cs.read_last_agenda_id()
check(after == last, "포인터 변경 없음 (테스트 후에도 {})".format(after or "없음"))


# ─────────────────────────────────────────────
section("5. 기획 프롬프트 — 아젠다 주입")
# ─────────────────────────────────────────────
fake_local = {
    "track": "local",
    "category": "지역이슈",
    "category_display": "동탄 지역 개발 이슈",
    "top_keyword": agenda["현안명"] if agenda else "트램",
    "rows": [{"keyword": "트램", "blog": 10, "cafe": 5, "trend": 50.0, "score": 1.0}],
    "agenda": {
        "id": agenda["id"] if agenda else "A001",
        "현안명": agenda["현안명"] if agenda else "트램",
        "분류": agenda.get("분류", "") if agenda else "교통",
        "단계": agenda.get("단계", "") if agenda else "착공준비",
        "최근진전일": agenda.get("최근진전일", "") if agenda else "2026-09-08",
        "진전내용": agenda.get("진전내용", "") if agenda else "",
        "중요도": "상",
        "키워드": agenda.get("키워드", "") if agenda else "트램",
        "메모": agenda.get("메모", "") if agenda else "",
        "확인필요": agenda.get("확인필요", "") if agenda else "",
        "선정사유": why if agenda else "",
        "최근뉴스": [],
    },
}

if check(hasattr(gp, "build_agenda_block"), "build_agenda_block 함수 있음"):
    blk = gp.build_agenda_block(fake_local)
    check(fake_local["agenda"]["현안명"] in blk, "현안명이 블록에 들어감")

    p = gp.build_plan_prompt(fake_local)
    check(fake_local["agenda"]["현안명"] in p, "기획 프롬프트에 현안 포함")
    check("다른 사안으로 넘어가지 마세요" in p, "소재 고정 지시 포함")
    check("변화 연표" in p, "local 산출물 유형이 붙음")
    check("위험 신호 목록" not in p.split("[이 글에서 쓸 수 있는")[0], "판단형 유형이 앞서 새지 않음")

check(hasattr(gp, "today_kst"), "generate_plan 에 today_kst 있음")


# ─────────────────────────────────────────────
section("6. 초안 프롬프트 — 정보 트랙 격리")
# ─────────────────────────────────────────────
today = cs.today_kst().isoformat()
plan_local = {
    "reader": "동탄에서 사업장을 찾고 있는 사업주",
    "output_type": "① 변화 연표",
    "conclusion": "무엇이 어떻게 달라졌다",
    "criteria": ["사실1", "사실2", "사실3"],
    "calc_tab": "없음",
}

p_local = gd.build_prompt(fake_local, [], today, plan=plan_local, category="local")
check("국토교통부 실거래가 공개시스템(직접 집계)" not in p_local,
      "local 프롬프트에 실거래 블록 없음")
check("지금까지의 흐름" in p_local, "local 구조(연표) 적용됨")
check("이 글은 소식을 전하는 글입니다" in p_local, "정보 트랙 핵심 원칙 적용됨")

fake_council = dict(fake_local)
fake_council.update({"track": "council", "category": "시의회특집",
                     "category_display": "시의회 회의록 특집",
                     "top_keyword": "화성시의회"})
fake_council.pop("agenda", None)

p_council = gd.build_prompt(fake_council, [], today, plan=plan_local, category="council")
check("국토교통부 실거래가 공개시스템(직접 집계)" not in p_council,
      "council 프롬프트에 실거래 블록 없음")
check("이번 회기에 다뤄진 것" in p_council, "council 전용 구조 적용됨")
check("#화성시의회" in p_council, "council 해시태그 적용됨")

p_jisik = gd.build_prompt(
    {"track": "jisik", "category_display": "세금·정책", "top_keyword": "지식산업센터 취득세 감면",
     "rows": fake_local["rows"]},
    [], today,
    plan={"reader": "실사용 매수자", "output_type": "② 합격/불합격 기준",
          "conclusion": "", "criteria": [], "calc_tab": "없음"},
    category="jisik")
check("판단 도구입니다" in p_jisik, "지산 트랙은 판단 도구 원칙 유지")

check(hasattr(gd, "today_kst"), "generate_draft 에 today_kst 있음")


# ─────────────────────────────────────────────
section("7. 도입 문구 (코드가 자동 삽입하는 문장)")
# ─────────────────────────────────────────────
md = "# 테스트 제목\n\n본문입니다.\n"

html_local = gd.render_naver_html(md, top_keyword=fake_local["top_keyword"],
                                  rows=None, track="local",
                                  agenda=fake_local["agenda"])
check("지식산업센터 관련 검색 데이터" not in html_local,
      "local 글에 '검색 데이터 1위' 문구 없음")
check(fake_local["agenda"]["현안명"] in html_local, "local 도입에 현안명 들어감")

html_council = gd.render_naver_html(md, top_keyword="화성시의회", rows=None, track="council")
check("회의록" in html_council, "council 도입 문구 적용됨")
check("지식산업센터 관련 검색 데이터" not in html_council,
      "council 글에 '검색 데이터 1위' 문구 없음")

html_jisik = gd.render_naver_html(md, top_keyword="지식산업센터 취득세 감면",
                                  rows=None, track="jisik")
check("지식산업센터 관련 검색 데이터" in html_jisik, "지산 글은 기존 문구 유지")


# ─────────────────────────────────────────────
section("결과")
# ─────────────────────────────────────────────
if fails:
    print("실패 {}건".format(len(fails)))
    for f in fails:
        print("  - " + f)
    sys.exit(1)
print("모두 통과")
