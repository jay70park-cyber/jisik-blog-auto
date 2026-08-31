# -*- coding: utf-8 -*-
"""
데이터 신선도 점검

파이프라인이 쓰는 데이터 중 일부는 자동 수집이 안 되어 직접 갱신해야 한다.
갱신을 잊으면 글이 낡은 숫자를 근거로 쓰이게 되므로,
정기적으로 각 데이터의 최신 시점을 확인해 텔레그램으로 알린다.

알림은 "무엇이 오래됐다"에서 끝내지 않고
어디서 무엇을 받아 어느 파일에 어떻게 넣어야 하는지까지 적는다.

점검 대상
  경매 낙찰 실적   data/auction_cases.csv       수동    90일
  실거래           data/realprice_raw.csv       자동    45일
  지번 매핑        data/jibun_master.csv        수동    미매핑 발생 시
  지역 키워드      data/local_keywords.json     반자동  60일
"""
import os
import csv
import json
import datetime
import urllib.request
import urllib.parse
from collections import defaultdict

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

DATA_DIR = "data"
AUCTION_CASES = os.path.join(DATA_DIR, "auction_cases.csv")
REALPRICE_RAW = os.path.join(DATA_DIR, "realprice_raw.csv")
JIBUN_MASTER = os.path.join(DATA_DIR, "jibun_master.csv")
LOCAL_KEYWORDS = os.path.join(DATA_DIR, "local_keywords.json")

# 며칠이 지나면 갱신을 알릴 것인가
AUCTION_STALE_DAYS = 90
REALPRICE_STALE_DAYS = 45
KEYWORD_STALE_DAYS = 60

TODAY = datetime.date.today()


def days_since(date_str):
    """'2026-08-28' 또는 '2026-8-28' 에서 오늘까지 며칠 지났는가."""
    try:
        y, m, d = [int(x) for x in str(date_str).strip().split("-")]
        return (TODAY - datetime.date(y, m, d)).days
    except (ValueError, AttributeError):
        return None


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def norm_jibun(s):
    """엑셀이 3-1을 '03월 01일'로 바꿔놓는 사고를 흡수한다."""
    s = str(s).strip()
    if "월" in s and "일" in s:
        try:
            parts = s.replace("월", " ").replace("일", " ").split()
            return "{}-{}".format(int(parts[0]), int(parts[1]))
        except (ValueError, IndexError):
            return s
    return s


# ─────────────────────────────────────────────
# 점검 항목
# ─────────────────────────────────────────────

def check_auction():
    rows = read_csv(AUCTION_CASES)
    if not rows:
        return {
            "name": "경매 낙찰 실적",
            "level": "warn",
            "state": "파일이 없습니다",
            "how": [
                "법원경매정보(courtauction.go.kr) → 물건상세검색",
                "소재지: 경기도 화성시 / 용인시 기흥구 (각각 따로)",
                "용도: 아파트형공장",
                "기간: 최근 1년",
                "검색 결과 목록을 통째로 복사",
                "→ data/auction_raw_hwaseong.txt",
                "→ data/auction_raw_giheung.txt",
                "올린 뒤 Collect Real Price 워크플로우 실행",
            ],
        }

    dates = sorted(r.get("매각기일", "") for r in rows if r.get("매각기일"))
    last = dates[-1] if dates else ""
    gap = days_since(last)

    if gap is None:
        level, state = "warn", "매각기일을 읽을 수 없습니다"
    elif gap > AUCTION_STALE_DAYS:
        level = "warn"
        state = "마지막 매각기일 {} ({}일 전) · {}건".format(last, gap, len(rows))
    else:
        level = "ok"
        state = "마지막 매각기일 {} ({}일 전) · {}건".format(last, gap, len(rows))

    item = {"name": "경매 낙찰 실적", "level": level, "state": state}
    if level == "warn":
        # 다음에 받아야 할 기간을 직접 계산해 알려준다
        try:
            y, m, d = [int(x) for x in last.split("-")]
            frm = datetime.date(y, m, d) + datetime.timedelta(days=1)
            period = "{} ~ {}".format(frm.isoformat(), TODAY.isoformat())
        except (ValueError, AttributeError):
            period = "최근 1년"
        item["how"] = [
            "법원경매정보(courtauction.go.kr) → 물건상세검색",
            "소재지: 경기도 화성시 / 용인시 기흥구 (각각 따로 검색)",
            "용도: 아파트형공장",
            "기간: " + period,
            "검색 결과 목록을 통째로 복사해서 아래 파일에 붙여넣기",
            "→ data/auction_raw_hwaseong.txt (화성시)",
            "→ data/auction_raw_giheung.txt (기흥구)",
            "기존 내용은 지우지 말고 뒤에 이어붙이면 됩니다 (중복은 자동 제거)",
            "올린 뒤 Collect Real Price 워크플로우 실행",
        ]
    return item


def check_realprice():
    rows = read_csv(REALPRICE_RAW)
    if not rows:
        return {
            "name": "실거래 데이터",
            "level": "warn",
            "state": "파일이 없습니다",
            "how": [
                "Collect Real Price 워크플로우를 backfill 모드로 실행",
            ],
        }

    def ymd(r):
        try:
            return datetime.date(
                int(r.get("dealYear", 0)),
                int(r.get("dealMonth", 0)),
                int(r.get("dealDay", 0)))
        except (ValueError, TypeError):
            return None

    dates = sorted(d for d in (ymd(r) for r in rows) if d)
    if not dates:
        return {"name": "실거래 데이터", "level": "warn",
                "state": "거래일을 읽을 수 없습니다"}

    last = dates[-1]
    gap = (TODAY - last).days
    state = "마지막 거래일 {} ({}일 전) · {}건".format(
        last.isoformat(), gap, len(rows))

    if gap > REALPRICE_STALE_DAYS:
        return {
            "name": "실거래 데이터",
            "level": "warn",
            "state": state,
            "how": [
                "자동 수집(매월 5일)이 실패했을 수 있습니다",
                "Actions → Collect Real Price 최근 실행 로그 확인",
                "국토부 API 응답이 없으면 시간을 두고 재실행",
            ],
        }
    return {"name": "실거래 데이터", "level": "ok", "state": state}


def check_jibun_mapping():
    """업무시설인데 매핑에 없는 지번을 찾는다. 지산·오피스는 자동 분류가 안 된다."""
    master_rows = read_csv(JIBUN_MASTER)
    raw_rows = read_csv(REALPRICE_RAW)
    if not raw_rows:
        return {"name": "지번 매핑", "level": "ok", "state": "실거래 데이터 없음 - 건너뜀"}

    mapped = set()
    for r in master_rows:
        mapped.add((
            r.get("시군구", "").strip(),
            r.get("법정동", "").strip(),
            norm_jibun(r.get("지번", "")),
        ))

    missing = defaultdict(int)
    for r in raw_rows:
        if "업무" not in r.get("buildingUse", ""):
            continue
        key = (
            r.get("sggNm", "").strip(),
            r.get("umdNm", "").strip(),
            norm_jibun(r.get("jibun", "")),
        )
        if key not in mapped:
            missing[key] += 1

    if not missing:
        return {"name": "지번 매핑", "level": "ok",
                "state": "업무시설 전부 매핑됨 ({}개 지번)".format(len(mapped))}

    top = sorted(missing.items(), key=lambda x: -x[1])[:8]
    how = [
        "아래 지번을 카카오맵에서 검색해 건물명을 확인하세요",
    ]
    for (sgg, umd, jb), n in top:
        how.append("  · {} {} {} ({}건)".format(sgg, umd, jb, n))
    how += [
        "확인한 내용을 data/jibun_master.csv 에 아래 형식으로 추가",
        "  시군구,법정동,지번,단지명,유형확정,인근 지산",
        "  용인시 기흥구,영덕동,975-5,○○타워,지산,기흥 지산벨트",
        "유형은 지산 / 오피스 / 오피스텔 / 연구시설 / 제외 중에서 고르세요",
        "올린 뒤 Collect Real Price 워크플로우 실행",
    ]
    return {
        "name": "지번 매핑",
        "level": "warn",
        "state": "업무시설 미매핑 {}개 지번 (거래 {}건)".format(
            len(missing), sum(missing.values())),
        "how": how,
    }


def check_local_keywords():
    if not os.path.exists(LOCAL_KEYWORDS):
        return {"name": "지역 이슈 키워드", "level": "warn",
                "state": "파일이 없습니다",
                "how": ["Discover Local Keywords 워크플로우 실행"]}
    try:
        with open(LOCAL_KEYWORDS, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return {"name": "지역 이슈 키워드", "level": "warn",
                "state": "파일을 읽을 수 없습니다: " + str(e)}

    updated = data.get("updated", "")
    gap = days_since(updated)
    active = [k for k in data.get("keywords", []) if k.get("pinned")]
    state = "갱신일 {} ({}일 전) · 사용 중 {}개".format(
        updated, gap if gap is not None else "?", len(active))

    if gap is not None and gap > KEYWORD_STALE_DAYS:
        return {
            "name": "지역 이슈 키워드",
            "level": "warn",
            "state": state,
            "how": [
                "자동 발굴(매월 1·15일)이 실패했을 수 있습니다",
                "Actions → Discover Local Keywords 수동 실행",
                "텔레그램으로 후보가 오면 뺄 것을 -번호로 답장",
            ],
        }
    if len(active) < 3:
        return {
            "name": "지역 이슈 키워드",
            "level": "warn",
            "state": state + " — 너무 적습니다",
            "how": [
                "사용 중인 키워드가 3개 미만이면 같은 주제가 반복됩니다",
                "data/local_keywords.json 에서 쓸 키워드의 pinned 를 true 로 바꾸거나",
                "Discover Local Keywords 를 실행해 후보를 새로 받으세요",
            ],
        }
    return {"name": "지역 이슈 키워드", "level": "ok", "state": state}


# ─────────────────────────────────────────────

MARK = {"ok": "○", "warn": "⚠"}


def send_telegram(text, timeout=20):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("텔레그램 설정이 없어 발송을 건너뜁니다.")
        return
    url = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendMessage"
    body = urllib.parse.urlencode(
        {"chat_id": TELEGRAM_CHAT_ID, "text": text}).encode("utf-8")
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, data=body, method="POST"),
                timeout=timeout) as res:
            res.read()
    except Exception as e:
        print("텔레그램 전송 오류: " + str(e))


def main():
    items = [
        check_auction(),
        check_realprice(),
        check_jibun_mapping(),
        check_local_keywords(),
    ]

    warns = [i for i in items if i["level"] == "warn"]

    lines = ["[데이터 점검] " + TODAY.isoformat(), ""]

    if warns:
        lines.append("■ 갱신이 필요합니다")
        for i in warns:
            lines.append("")
            lines.append("{} {}".format(MARK["warn"], i["name"]))
            lines.append("   " + i["state"])
            for h in i.get("how", []):
                lines.append("   " + h)
        lines.append("")

    ok = [i for i in items if i["level"] == "ok"]
    if ok:
        lines.append("■ 정상")
        for i in ok:
            lines.append("  {} {} — {}".format(MARK["ok"], i["name"], i["state"]))

    lines.append("")
    lines.append("─────────────")
    if warns:
        lines.append("{}건 갱신 필요 / {}건 정상".format(len(warns), len(ok)))
    else:
        lines.append("모두 정상입니다.")

    text = "\n".join(lines)
    print(text)

    # 문제가 없으면 알리지 않는다. 매달 오는 "정상" 알림은 곧 무시하게 된다.
    if warns:
        send_telegram(text)
    else:
        print("\n갱신할 것이 없어 텔레그램 발송을 건너뜁니다.")


if __name__ == "__main__":
    main()
