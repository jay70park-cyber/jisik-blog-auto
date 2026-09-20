# -*- coding: utf-8 -*-
"""
중기지방재정계획 색인

고시는 "결정했다"를 알려주고 회의록은 "논의 중이다"를 알려준다.
재정계획은 그 둘에 없는 것을 알려준다 — **언제 얼마를 쓸 계획인가.**

실제로 2026~2030 화성시 계획에는 트램이 이렇게 잡혀 있다.
    총사업비 1조 701억 · 기투자 1,247억
    2026년 700억 → 2027년 4,497억 → 2028년 4,257억 → 이후 0
회의록에서 나온 "총사업비 9,982억"과 숫자가 다르다.
이런 대조는 고시로도 회의록으로도 못 한다.

파싱 구조 (2026-09 실측, 경기도·화성시 두 파일에서 확인)
    사업명                      ← 숫자 없는 줄
    기간: … 대상: … 규모: … 내용: …   ← 이 줄이 앵커다
    Y N N N N N                ← 플래그
    계 [숫자 9개]               ← 총사업비·기투자·향후·2026~2030·이후
    재 량 … 시군구비 …           ← 재원 구분

'기간:' 을 앵커로 삼는 이유는, 부문 요약줄도 '이름 + 숫자 9개' 형태라
그것만으로는 사업과 요약을 못 가르기 때문이다.
실제로 요약줄을 사업명으로 잘못 잡는 사고가 있었다.

**단위가 파일마다 다르다.** 경기도는 억원, 화성시는 백만원이다.
머리말에서 읽어내고, 못 읽으면 사람이 CSV 에서 고치게 남겨둔다.

**PDF 를 그대로 넣어도 된다.** 텍스트로 미리 바꿀 필요가 없다.
pypdf 와 pdfplumber 를 둘 다 시도해 사업이 더 많이 잡히는 쪽을 쓴다.
추출기마다 줄바꿈 자리가 달라 파서가 먹고 안 먹고가 갈리기 때문이다.
추출한 텍스트는 data/plans/_extracted/ 에 남겨 두므로,
건수가 이상하면 그 파일을 열어 눈으로 확인하면 된다.

사용
    python3 scripts/index_plans.py            # data/plans/ 의 pdf·txt 전부
    python3 scripts/index_plans.py --check    # 색인만 하고 저장 안 함

출력  data/plan_projects.csv
      data/plans/_extracted/*.txt   (PDF 에서 뽑은 원문)
"""
import os
import re
import csv
import sys
import datetime

PLAN_DIR = os.path.join("data", "plans")
OUT_CSV = os.path.join("data", "plan_projects.csv")

FIELDS = ["출처", "계획명", "계획기간", "단위", "사업명", "사업개요",
          "총사업비", "기투자", "향후", "이후", "y1", "y2", "y3", "y4", "y5",
          "기준연도", "행", "수집일"]

NUM9 = re.compile(r"((?:[\d,]+\s+){8}[\d,]+)\s*$")
FLAGS = re.compile(r"^[YN\s]+$")

# 사업 블록의 앵커 — 사업기간이 적힌 줄.
# 화성시는 "기간: 2019.05~2028.12", 경기도는 "2014.01~2028.12" 처럼
# 라벨 없이 날짜만 있는 경우가 많다. 둘 다 받는다.
PERIOD = re.compile(
    r"기간\s*[:：]"
    r"|^\(?(19|20)\d\d\s*[.\-년]\s*\d{1,2}.{0,12}?[~∼-]"
    r"|^\(?(19|20)\d\d\s*[~∼-]\s*(19|20)?\d\d")

# 사업명 후보에서 뺄 줄
SKIP_NAME = re.compile(r"^(계|의 무|재 량|국 고|기 금|소 계)\b|^[-–]\s|^\(단위")

# 머리말에서 단위를 찾는다
UNIT_PAT = re.compile(r"단위\s*[:：]\s*(백만원|억원|천원|원)")

# 계획 기간 — "2026~2030" 또는 "2026 ~ 2030"
RANGE_PAT = re.compile(r"(20\d\d)\s*[~∼-]\s*(20\d\d)")


EXTRACT_DIR = os.path.join(PLAN_DIR, "_extracted")


def extract_pypdf(path):
    from pypdf import PdfReader
    reader = PdfReader(path)
    return "\n".join((p.extract_text() or "") for p in reader.pages)


def extract_pdfplumber(path):
    import pdfplumber
    out = []
    with pdfplumber.open(path) as pdf:
        for p in pdf.pages:
            out.append(p.extract_text() or "")
    return "\n".join(out)


def count_anchors(text):
    """사업 블록이 몇 개나 잡힐지 어림한다. 추출기를 고르는 잣대."""
    n = 0
    for l in text.split("\n"):
        if PERIOD.search(l.strip()):
            n += 1
    return n


def load_text(path):
    """PDF 든 텍스트든 문자열로 돌려준다.

    PDF 는 추출기마다 결과가 딴판이다. 줄바꿈이 어디 들어가느냐에 따라
    '기간:' 앵커가 살기도 죽기도 한다. 그래서 둘 다 돌려보고
    앵커가 많이 잡히는 쪽을 쓴다.
    """
    if not path.lower().endswith(".pdf"):
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(), "text"

    best, best_n, best_how = "", -1, ""
    for how, fn in (("pypdf", extract_pypdf),
                    ("pdfplumber", extract_pdfplumber)):
        try:
            t = fn(path)
        except Exception as e:
            print("   {} 실패: {}".format(how, e))
            continue
        n = count_anchors(t)
        print("   {:<11} {:>7,}자 · 앵커 {:>4}개".format(how, len(t), n))
        if n > best_n:
            best, best_n, best_how = t, n, how

    if best:
        os.makedirs(EXTRACT_DIR, exist_ok=True)
        stem = os.path.splitext(os.path.basename(path))[0]
        out = os.path.join(EXTRACT_DIR, stem + ".txt")
        with open(out, "w", encoding="utf-8") as f:
            f.write(best)
        print("   → {} 채택, 원문 저장: {}".format(best_how, out))
    return best, best_how


def money(s):
    m = NUM9.search(s.strip())
    if not m:
        return None
    return [int(x.replace(",", "")) for x in m.group(1).split()]


def read_head(lines, n=40):
    return " ".join(l.strip() for l in lines[:n] if l.strip())


def detect_unit(lines):
    """머리말에서 금액 단위를 찾는다. 못 찾으면 빈 문자열."""
    head = " ".join(l for l in lines[:400])
    m = UNIT_PAT.search(head)
    return m.group(1) if m else ""


def detect_plan(lines, fallback):
    """계획명과 기간을 머리말에서 추정한다."""
    head = read_head(lines)
    m = RANGE_PAT.search(head)
    period = "{}~{}".format(m.group(1), m.group(2)) if m else ""
    name = ""
    for l in lines[:30]:
        t = l.strip()
        if "중기지방재정계획" in t or "기본계획" in t or "종합계획" in t:
            # 앞줄에 지자체 이름이 있으면 붙인다
            name = t
            break
    if not name:
        name = fallback
    return name[:40], period


def base_year(period, lines):
    """연도별 투자계획의 첫 해. 없으면 계획기간 시작연도."""
    m = RANGE_PAT.search(period or "")
    if m:
        return int(m.group(1))
    m = RANGE_PAT.search(read_head(lines))
    return int(m.group(1)) if m else 0


def parse_file(path):
    """한 파일에서 사업 블록을 뽑는다. PDF 도 받는다."""
    text, how = load_text(path)
    if not text.strip():
        print("   본문을 못 읽었습니다. 스캔 PDF 라면 OCR 이 필요합니다.")
        return [], "", os.path.basename(path), ""
    lines = [l.rstrip() for l in text.split("\n")]

    src = os.path.basename(path)
    unit = detect_unit(lines)
    plan_name, period = detect_plan(lines, src)
    y0 = base_year(period, lines)
    today = datetime.date.today().isoformat()

    out = []
    for i, l in enumerate(lines):
        s = l.strip()
        if not PERIOD.search(s):
            continue

        # 사업명 — 위로 올라가며 모은다.
        # 경기도 파일은 "광역급행철도 GTX-A / (삼성~동탄) 분담금 / (자체/직접)"
        # 처럼 한 이름이 여러 줄로 쪼개져 있다. 붙여야 뜻이 통한다.
        parts = []
        for j in range(i - 1, max(-1, i - 5), -1):
            t = lines[j].strip()
            if not t or FLAGS.match(t) or SKIP_NAME.search(t):
                break
            if money(t):
                break          # 앞 사업의 금액줄 — 여기서 끊는다
            parts.append(t)
            if len(parts) >= 3:
                break
        name = "".join(reversed(parts)).strip()

        # 금액 — 아래로 내려가며 '계' 로 시작하는 줄.
        # 경기도는 대상·규모·내용·소관부처가 줄줄이 이어져 10줄을 넘기도 한다.
        amt = None
        desc = [s]
        for j in range(i + 1, min(len(lines), i + 16)):
            t = lines[j].strip()
            if t.startswith("계 "):
                amt = money(t)
                if amt:
                    break
            # 다음 사업의 앵커를 만나면 이 블록은 금액이 없는 것
            if j > i + 1 and PERIOD.search(t):
                break
            if t and not FLAGS.match(t) and not money(t):
                desc.append(t)
        if not name or not amt:
            continue

        out.append({
            "출처": src,
            "계획명": plan_name,
            "계획기간": period,
            "단위": unit,
            "사업명": name[:60],
            "사업개요": re.sub(r"\s+", " ", " ".join(desc))[:200],
            "총사업비": amt[0], "기투자": amt[1],
            "향후": amt[2] + amt[8],   # 2026~2030 소계 + 2031 이후
            "이후": amt[8],            # 2031 이후만
            "y1": amt[3], "y2": amt[4], "y3": amt[5],
            "y4": amt[6], "y5": amt[7],
            "기준연도": y0,
            "행": i + 1,
            "수집일": today,
        })
    return out, unit, plan_name, period


def main():
    check = "--check" in sys.argv
    if not os.path.isdir(PLAN_DIR):
        print("계획 폴더가 없습니다: " + PLAN_DIR)
        print("계획 PDF 나 텍스트를 이 폴더에 넣으세요.")
        return

    files = sorted(f for f in os.listdir(PLAN_DIR)
                   if f.lower().endswith((".txt", ".md", ".pdf")))
    if not files:
        print("계획 파일이 없습니다: " + PLAN_DIR)
        return

    rows = []
    for fn in files:
        print("\n[{}]".format(fn))
        got, unit, name, period = parse_file(os.path.join(PLAN_DIR, fn))
        print("   {:>5}건  단위 {:<5} {} {}".format(
            len(got), unit or "??", name[:20], period))
        if not unit:
            print("   ⚠ 단위를 못 읽었습니다. CSV 의 '단위' 열을 직접 채우세요.")
        rows += got

    print("\n합계 {}건".format(len(rows)))
    if check:
        print("(--check 라 저장하지 않습니다)")
        return

    os.makedirs("data", exist_ok=True)
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print("저장: " + OUT_CSV)


if __name__ == "__main__":
    main()
