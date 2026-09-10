# -*- coding: utf-8 -*-
"""
date_guard.py — 지나간 날짜를 미래형으로 쓴 문장을 잡는다.

왜 코드로 하는가.
  초안도 Claude가 쓰고 검증도 Claude가 하는데, 둘이 같은 지식 시점을 공유한다.
  3월 기사의 "8월 개통 목표"를 학습했으면 검증하는 쪽도 그게 미래인 줄 안다.
  같은 눈으로 두 번 보는 셈이라 검증 항목을 늘려도 안 잡힌다.

  날짜 비교는 확률적 판단이 아니라 뺄셈이다. 코드로 하면 100% 잡힌다.

사용법
    from date_guard import check_dates
    out = check_dates(draft_text)          # [(name, verdict, note), ...]

단독 실행하면 자체 테스트가 돈다.
    python date_guard.py
"""

import re
from datetime import date

# 미래를 가리키는 표지. 이 말이 같은 문장에 있는데
# 날짜가 이미 지났으면 문장이 상한 것이다.
FUTURE_MARKERS = [
    "목표", "예정", "전망", "계획", "추진", "앞두고", "앞둔",
    "될 것", "될 전망", "예상", "예측", "기대", "논의 중", "검토 중",
    "개통한다", "준공한다", "시행한다", "입주한다", "착공한다",
    "개통될", "준공될", "시행될", "입주할", "착공할",
]

# 이미 일어난 일을 서술하는 표지. 지난 날짜와 함께 있으면 정상이다.
PAST_MARKERS = [
    "개통했", "개통됐", "준공했", "준공됐", "시행됐", "시행했",
    "밝혔", "발표했", "着공했", "착공했", "입주했", "마쳤", "완료했", "완료됐",
    "였다", "이었다", "했다가", "됐다", "돼 있다", "된 바",
]

# 며칠 안 남은 날짜는 발행 시점에 지나갈 수 있으니 미리 알린다.
SOON_DAYS = 30


def _end_of_month(y, m):
    if m == 12:
        return date(y, 12, 31)
    nxt = date(y, m + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)


def _split_sentences(text):
    """한국어 문장 분리. 완벽할 필요는 없고 날짜와 표지가 같이 묶이면 된다."""
    text = re.sub(r"\s+", " ", text)
    parts = re.split(r"(?<=다\.)\s|(?<=[.!?])\s|(?<=니다\.)\s", text)
    return [p.strip() for p in parts if p.strip()]


def _extract_dates(sent, today):
    """문장에서 날짜를 뽑아 (표기, 그 시점의 마지막 날) 목록으로 돌려준다."""
    found = []

    # 2026년 8월 28일
    for m in re.finditer(r"(20\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일", sent):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            try:
                found.append((m.group(0), date(y, mo, d)))
            except ValueError:
                pass

    # 2026년 8월  (일자 없는 것만)
    for m in re.finditer(r"(20\d{2})\s*년\s*(\d{1,2})\s*월(?!\s*\d{1,2}\s*일)", sent):
        y, mo = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            found.append((m.group(0), _end_of_month(y, mo)))

    # 2026년 상반기 / 하반기 / 1분기
    for m in re.finditer(r"(20\d{2})\s*년\s*(상반기|하반기|([1-4])\s*분기)", sent):
        y = int(m.group(1))
        if m.group(3):
            found.append((m.group(0), _end_of_month(y, int(m.group(3)) * 3)))
        elif "상반기" in m.group(2):
            found.append((m.group(0), _end_of_month(y, 6)))
        else:
            found.append((m.group(0), date(y, 12, 31)))

    # 2026년 (월 없이 연도만)
    for m in re.finditer(r"(20\d{2})\s*년(?!\s*\d{1,2}\s*월)(?!\s*(상반기|하반기|[1-4]\s*분기))", sent):
        y = int(m.group(1))
        found.append((m.group(0), date(y, 12, 31)))

    # 올해 8월 / 내년 6월
    for m in re.finditer(r"(올해|금년|내년|명년)\s*(\d{1,2})\s*월", sent):
        y = today.year + (1 if m.group(1) in ("내년", "명년") else 0)
        mo = int(m.group(2))
        if 1 <= mo <= 12:
            found.append((m.group(0), _end_of_month(y, mo)))

    # 2026.8 / 2026-08
    for m in re.finditer(r"(20\d{2})[.\-](\d{1,2})(?![\d.\-])", sent):
        mo = int(m.group(2))
        if 1 <= mo <= 12:
            found.append((m.group(0), _end_of_month(int(m.group(1)), mo)))

    # 중복 제거 (같은 표기가 여러 규칙에 걸릴 수 있다)
    seen, out = set(), []
    for label, d in found:
        key = (label.strip(), d)
        if key not in seen:
            seen.add(key)
            out.append((label.strip(), d))
    return out


def _clip(sent, limit=70):
    s = sent.strip()
    return s if len(s) <= limit else s[:limit] + "…"


def check_dates(draft, today=None):
    """(name, verdict, note) 목록을 돌려준다. verify_draft.py 규약과 같다."""
    today = today or date.today()
    stale, soon = [], []

    for sent in _split_sentences(draft):
        markers = [w for w in FUTURE_MARKERS if w in sent]
        if not markers:
            continue
        # 과거 서술이 함께 있으면 이미 일어난 일을 적은 것이다
        if any(w in sent for w in PAST_MARKERS):
            continue

        for label, when in _extract_dates(sent, today):
            gap = (when - today).days
            if gap < 0:
                stale.append((label, -gap, markers[0], sent))
            elif gap <= SOON_DAYS:
                soon.append((label, gap, markers[0], sent))

    out = []
    if stale:
        note = "; ".join(
            "'{}'({}일 지남, '{}') → {}".format(lb, g, mk, _clip(s))
            for lb, g, mk, s in stale[:3])
        if len(stale) > 3:
            note += " 외 {}건".format(len(stale) - 3)
        out.append(("지난 날짜", "실패", note))
    elif soon:
        note = "; ".join(
            "'{}'({}일 남음) → {}".format(lb, g, _clip(s))
            for lb, g, mk, s in soon[:3])
        out.append(("임박한 날짜", "주의", note + " — 발행 시점에 확인하세요"))
    else:
        out.append(("날짜 시점", "통과", "오늘({}) 기준 이상 없음".format(today)))

    if stale and soon:
        out.append(("임박한 날짜", "주의",
                    "{}건 더 있습니다 (발행 전 확인)".format(len(soon))))
    return out


# ─────────────────────────────────────────────
if __name__ == "__main__":
    TODAY = date(2026, 9, 10)

    cases = [
        # 실제로 걸린 문장
        ("남은 서울역~수서역 구간은 국토부가 2026년 8월 개통을 목표로 밝힌 상태다.",
         "실패"),
        # 정상 — 미래
        ("전 구간 완전 개통은 2028년 4월을 목표로 2027년 부분 개통을 추진 중이다.",
         "통과"),
        # 정상 — 과거 사실 서술
        ("GTX-A는 2024년 3월 수서역~동탄역 구간이 먼저 열렸다.", "통과"),
        ("2024년 12월 28일 운정중앙~서울역 구간이 추가로 개통됐다.", "통과"),
        # 임박
        ("입주는 2026년 9월 예정이다.", "주의"),
        # 상반기 표현
        ("착공은 2026년 상반기를 목표로 한다.", "실패"),
        # 올해 표현
        ("올해 6월 준공될 전망이다.", "실패"),
        # 연도만
        ("2025년 시행될 계획이다.", "실패"),
        # 날짜 없음
        ("개통 시점이 여러 차례 미뤄져 왔다.", "통과"),
    ]

    ok = 0
    for text, expect in cases:
        res = check_dates(text, today=TODAY)
        got = res[0][1]
        mark = "○" if got == expect else "×"
        if got == expect:
            ok += 1
        print("{} 기대={} 결과={}  {}".format(mark, expect, got, text[:46]))
        if got != expect:
            print("     →", res)
    print("\n{}/{} 통과".format(ok, len(cases)))

    print("\n─── 실제 초안 일부로 시험 ───")
    sample = (
        "GTX-A는 2024년 3월 수서역~동탄역 구간이 먼저 열렸고, "
        "2024년 12월 28일 운정중앙~서울역 구간이 추가로 개통됐다. "
        "남은 서울역~수서역 구간은 국토부가 2026년 8월 개통을 목표로 밝힌 상태다. "
        "다만 삼성역은 아직 무정차 통과 구간이고, 전 구간 완전 개통은 "
        "2028년 4월을 목표로 2027년 부분 개통을 추진 중이다."
    )
    for name, verdict, note in check_dates(sample, today=TODAY):
        print("  [{}] {} — {}".format(verdict, name, note))


# ─────────────────────────────────────────────
# 기사 나이에 따른 톤 검사
#
# 넉 달 전 기사를 "최근"이라고 쓰면 그것도 오류다.
# 근거 기사가 오래됐는데 신선도를 주장하는 표현이 있으면 잡는다.
# ─────────────────────────────────────────────

# 새 소식임을 주장하는 표현. 기사가 오래됐으면 쓰면 안 된다.
RECENCY_WORDS = [
    "최근", "요즘", "근래", "이번 주", "금주", "지난주", "이번 달", "이달 들어",
    "오늘", "어제", "그제", "엊그제", "방금", "막 나온", "갓 나온",
    "속보", "새롭게 확인", "새로 나온", "새롭게 드러난", "밝혀졌다",
    "알려졌다", "전해졌다", "나왔다는 소식",
]

FRESH_DAYS = 7          # 이 안쪽이면 새 소식으로 다뤄도 된다


def check_recency(draft, source_dates=None, today=None):
    """근거 기사의 나이와 본문의 신선도 표현이 맞는지 본다.

    source_dates : 근거로 쓴 기사들의 발행일 목록 (datetime.date).
                   수집 단계에서 pubDate 를 넘겨주면 정확해진다.
                   없으면 신선도 표현이 있는지만 알려준다.
    """
    today = today or date.today()
    hits = []
    for sent in _split_sentences(draft):
        for w in RECENCY_WORDS:
            if w in sent:
                hits.append((w, sent))
                break

    if not hits:
        return [("기사 나이", "통과", "신선도 표현 없음 — 사실관계 서술")]

    if not source_dates:
        note = "; ".join("'{}' → {}".format(w, _clip(s)) for w, s in hits[:3])
        return [("기사 나이", "확인 필요",
                 "신선도 표현 {}곳 — 근거 기사가 일주일 이내인지 확인하세요: {}"
                 .format(len(hits), note))]

    newest = max(source_dates)
    age = (today - newest).days
    if age <= FRESH_DAYS:
        return [("기사 나이", "통과",
                 "가장 새 근거가 {}일 전 — 새 소식 톤 허용".format(age))]

    note = "; ".join("'{}' → {}".format(w, _clip(s)) for w, s in hits[:3])
    if len(hits) > 3:
        note += " 외 {}건".format(len(hits) - 3)
    return [("기사 나이", "실패",
             "가장 새 근거가 {}일 전인데 신선도 표현이 있습니다 "
             "— 사실관계 서술로 바꾸세요: {}".format(age, note))]
