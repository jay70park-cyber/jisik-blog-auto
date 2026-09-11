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
    "가능성", "관측", "될 것으로", "미뤄질", "연기될", "앞당겨",
    "개통한다", "준공한다", "시행한다", "입주한다", "착공한다",
    "개통될", "준공될", "시행될", "입주할", "착공할",
]

# 이미 일어난 일을 서술하는 표지. 지난 날짜와 함께 있으면 정상이다.
# 그 날짜 '바로 앞'에 붙어야만 회고로 인정하는 말.
# 문장 앞머리의 '당초'가 문장 끝의 다른 날짜까지 면제해서는 안 되므로,
# 직전 30자 안에 있을 때만 본다.
RETRO_MARKERS = ["당초", "원래", "기존", "애초", "처음에는", "본래"]
RETRO_WINDOW = 30

# 이미 일어난 일을 서술하는 표지.
# '밝혔다', '발표됐다' 같은 전달 동사는 뺀다. 발표 행위는 과거지만
# 발표된 내용은 미래일 수 있어, 넣으면 문장 전체가 검사에서 빠진다.
PAST_MARKERS = [
    "개통했", "개통됐", "개통되었", "준공했", "준공됐", "준공되었",
    "시행됐", "시행했", "착공했", "착공됐", "입주했", "입주됐",
    "마쳤", "완료했", "완료됐", "열렸", "끝났",
    "였다", "이었다", "했었", "던 것",
]

# 며칠 안 남은 날짜는 발행 시점에 지나갈 수 있으니 미리 알린다.
SOON_DAYS = 30


def _end_of_month(y, m):
    if m == 12:
        return date(y, 12, 31)
    nxt = date(y, m + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)


def _split_sentences(text):
    """한국어 문장 분리.

    마크다운 초안은 불릿과 표가 많아 공백만으로 이으면 여러 항목이
    한 덩어리가 된다. 그러면 앞 불릿의 날짜와 뒤 불릿의 미래형 표지가
    엮여 엉뚱한 오탐이 난다. 줄바꿈과 불릿을 문장 경계로 본다.
    """
    text = str(text)
    # 표 행은 통째로 한 조각 (셀 구분자 | 로 날짜와 표지가 섞이는 것을 막는다)
    chunks = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        # 불릿·번호 기호를 경계로 한 번 더 쪼갠다
        for piece in re.split(r"\s+(?=[-*•]\s)|\s+(?=\d+\.\s)", line):
            piece = piece.strip()
            if piece:
                chunks.append(piece)

    out = []
    for c in chunks:
        for p in re.split(r"(?<=다\.)\s|(?<=니다\.)\s|(?<=[.!?])\s", c):
            p = p.strip()
            if p:
                out.append(p)
    return out


def _strip_quotes(sent):
    """따옴표 안은 독자가 남에게 물어볼 예시 문장이므로 판정에서 뺀다.

    예: 관리사무소에 "최근 공실이 몇 개월 만에 찼나요?" 라고 질문
    여기의 '최근'은 기사 신선도 주장이 아니다.
    """
    return re.sub(r'["“”\'‘’「」『』]([^"“”\'‘’「」『』]{0,120})["“”\'‘’「」『』]', " ", sent)


def _extract_dates(sent, today):
    """문장에서 날짜를 뽑아 (표기, 그 시점의 마지막 날) 목록으로 돌려준다."""
    found = []

    # 2026년 8월 28일
    for m in re.finditer(r"(20\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일", sent):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            try:
                found.append((m.group(0), date(y, mo, d), m.start(), m.end()))
            except ValueError:
                pass

    # 2026년 8월  (일자 없는 것만)
    for m in re.finditer(r"(20\d{2})\s*년\s*(\d{1,2})\s*월(?!\s*\d{1,2}\s*일)", sent):
        y, mo = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            found.append((m.group(0), _end_of_month(y, mo), m.start(), m.end()))

    # 2026년 상반기 / 하반기 / 1분기
    for m in re.finditer(r"(20\d{2})\s*년\s*(상반기|하반기|([1-4])\s*분기)", sent):
        y = int(m.group(1))
        if m.group(3):
            found.append((m.group(0), _end_of_month(y, int(m.group(3)) * 3), m.start(), m.end()))
        elif "상반기" in m.group(2):
            found.append((m.group(0), _end_of_month(y, 6), m.start(), m.end()))
        else:
            found.append((m.group(0), date(y, 12, 31), m.start(), m.end()))

    # 2026년 (월 없이 연도만)
    for m in re.finditer(r"(20\d{2})\s*년(?!\s*\d{1,2}\s*월)(?!\s*(상반기|하반기|[1-4]\s*분기))", sent):
        y = int(m.group(1))
        found.append((m.group(0), date(y, 12, 31), m.start(), m.end()))

    # 올해 8월 / 내년 6월
    for m in re.finditer(r"(올해|금년|내년|명년)\s*(\d{1,2})\s*월", sent):
        y = today.year + (1 if m.group(1) in ("내년", "명년") else 0)
        mo = int(m.group(2))
        if 1 <= mo <= 12:
            found.append((m.group(0), _end_of_month(y, mo), m.start(), m.end()))

    # 2026.8 / 2026-08
    for m in re.finditer(r"(20\d{2})[.\-](\d{1,2})(?![\d.\-])", sent):
        mo = int(m.group(2))
        if 1 <= mo <= 12:
            found.append((m.group(0), _end_of_month(int(m.group(1)), mo), m.start(), m.end()))

    # 연도 없는 표기: '6월 27일', '7~8월', '8월 말'
    # 연도를 안 쓰면 올해를 가리키는 것이 보통이다. 다만 이미 넉 달 넘게
    # 지난 달이면 내년을 뜻할 가능성이 커서 판정에서 뺀다.
    def _bare(mo, day, label, st, en):
        when = date(today.year, mo, day) if day else _end_of_month(today.year, mo)
        if (today - when).days > 120:      # 너무 오래 지났으면 내년으로 본다
            return
        found.append((label, when, st, en))

    for m in re.finditer(r"(?<![0-9년])(\d{1,2})\s*월\s*(\d{1,2})\s*일", sent):
        mo, d = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            try:
                _bare(mo, d, m.group(0), m.start(), m.end())
            except ValueError:
                pass

    # '7~8월' 은 뒤쪽 달을 기준으로 본다
    for m in re.finditer(r"(?<![0-9년])(\d{1,2})\s*[~\-]\s*(\d{1,2})\s*월", sent):
        mo = int(m.group(2))
        if 1 <= mo <= 12:
            _bare(mo, None, m.group(0), m.start(), m.end())

    # 단독 '8월' (앞에 연도나 숫자가 없고, 일자도 따라붙지 않는 경우)
    for m in re.finditer(r"(?<![0-9년~\-])(\d{1,2})\s*월(?!\s*\d{1,2}\s*일)", sent):
        mo = int(m.group(1))
        if 1 <= mo <= 12:
            _bare(mo, None, m.group(0), m.start(), m.end())

    # 겹치는 구간 제거 (같은 자리가 여러 규칙에 걸릴 수 있다)
    found.sort(key=lambda x: (x[2], -(x[3] - x[2])))
    out, taken = [], []
    for label, d, st, en in found:
        if any(st < e and en > s2 for s2, e in taken):
            continue
        taken.append((st, en))
        out.append((label.strip(), d, st, en))
    return out


def _clip(sent, limit=70):
    s = sent.strip()
    return s if len(s) <= limit else s[:limit] + "…"


def check_dates(draft, today=None):
    """(name, verdict, note) 목록을 돌려준다. verify_draft.py 규약과 같다."""
    today = today or date.today()
    stale, soon = [], []

    for raw in _split_sentences(draft):
        sent = _strip_quotes(raw)          # 예시 질문문은 판정에서 뺀다
        if not any(w in sent for w in FUTURE_MARKERS):
            continue

        dates = _extract_dates(sent, today)
        for i, (label, when, st, en) in enumerate(dates):
            # 한 문장에 날짜가 여럿이면 표지가 어느 날짜에 걸리는지 가려야 한다.
            # 이 날짜 뒤부터 다음 날짜 앞까지가 그 날짜의 서술 구간이다.
            nxt = dates[i + 1][2] if i + 1 < len(dates) else len(sent)
            after = sent[en:nxt]
            prev = dates[i - 1][3] if i > 0 else 0
            before = sent[prev:st]

            markers = [w for w in FUTURE_MARKERS if w in after]
            if not markers:
                continue
            if any(w in before + after for w in PAST_MARKERS):
                continue
            # 회고 표지는 바로 앞에 붙은 경우만 인정한다
            if any(w in before[-RETRO_WINDOW:] for w in RETRO_MARKERS):
                continue

            gap = (when - today).days
            if gap < 0:
                stale.append((label, -gap, markers[0], raw))
            elif gap <= SOON_DAYS:
                soon.append((label, gap, markers[0], raw))

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

# '최근'은 기간·상태를 가리키는 용법이 더 흔하다.
# '최근 1년 상승률', '최근 공실 기간'은 기사 신선도 주장이 아니다.
# 이런 말이 바로 뒤에 붙으면 신선도 표현으로 세지 않는다.
_PERIOD_AFTER = (
    r"\s*(\d+\s*(년|개월|달|주|일|분기|회|건|차)"
    # 한자 수사도 받는다 ('석 달', '두 달', '서너 해')
    r"|(한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|석|넉|서너|두세)\s*(달|해|주|날)"
    r"|[1-9]\d*년간|수년|몇\s*년"
    # 앞에 한두 글자가 더 붙어도 인식한다 ('실거래', '월임대료' 등)
    r"|[가-힣]{0,2}(공실|거래|시세|실적|추세|흐름|동향|임대료|매매가"
    r"|낙찰|계약|입주|분양|기준|자료|데이터|통계|수치|가격))"
)


def check_recency(draft, source_dates=None, today=None):
    """근거 기사의 나이와 본문의 신선도 표현이 맞는지 본다.

    source_dates : 근거로 쓴 기사들의 발행일 목록 (datetime.date).
                   수집 단계에서 pubDate 를 넘겨주면 정확해진다.
                   없으면 신선도 표현이 있는지만 알려준다.
    """
    today = today or date.today()
    hits = []
    for raw in _split_sentences(draft):
        sent = _strip_quotes(raw)
        # 독자가 확인할 항목을 안내하는 줄은 기사 신선도 주장이 아니다
        if re.match(r"^[-*•]?\s*(확인|확인할 것|확인 방법|질문|체크)", sent):
            continue
        for w in RECENCY_WORDS:
            if w not in sent:
                continue
            # '최근 1년', '최근 공실'처럼 기간·대상을 수식하는 용법은 제외
            if re.search(re.escape(w) + _PERIOD_AFTER, sent):
                continue
            hits.append((w, raw))
            break

    if not hits:
        return [("기사 나이", "통과", "신선도 표현 없음 — 사실관계 서술")]

    if not source_dates:
        note = "; ".join("'{}' → {}".format(w, _clip(s)) for w, s in hits[:3])
        return [("기사 나이", "확인 필요",
                 "신선도 표현 {}곳 — 근거 기사가 일주일 이내인지 확인하세요: {}"
                 .format(len(hits), note))]

    newest, oldest = max(source_dates), min(source_dates)
    age = (today - newest).days
    if age <= FRESH_DAYS:
        spread = (newest - oldest).days
        if spread > 30:
            note = "; ".join("'{}' → {}".format(w, _clip(s)) for w, s in hits[:2])
            return [("기사 나이", "주의",
                     "가장 새 근거는 {}일 전이지만 근거가 {}일에 걸쳐 있습니다. "
                     "신선도 표현 {}곳이 오래된 사실을 가리키지 않는지 "
                     "확인하세요: {}".format(age, spread, len(hits), note))]
        return [("기사 나이", "통과",
                 "가장 새 근거가 {}일 전 — 새 소식 톤 허용".format(age))]

    note = "; ".join("'{}' → {}".format(w, _clip(s)) for w, s in hits[:3])
    if len(hits) > 3:
        note += " 외 {}건".format(len(hits) - 3)
    return [("기사 나이", "실패",
             "가장 새 근거가 {}일 전인데 신선도 표현이 있습니다 "
             "— 사실관계 서술로 바꾸세요: {}".format(age, note))]
