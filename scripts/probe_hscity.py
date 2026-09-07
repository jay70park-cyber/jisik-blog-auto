# -*- coding: utf-8 -*-
"""
화성시 고시 게시판 POST 파라미터 탐침

목록 1페이지 10건만 읽을 수 있는 게 지금의 한계다.
검색과 페이지 넘김이 POST라서 URL에 붙여도 무시된다.
폼 필드 이름을 모르니, 그럴듯한 조합을 하나씩 넣어보고
'총 N건'과 첫 행이 달라지는지로 성공을 판별한다.

성공한 조합이 나오면 그것으로 수집기를 고친다.
전부 실패하면 화면 검색을 직접 하는 수밖에 없다.
"""
import re
import html
import time
import urllib.parse
import urllib.request

URL = "https://www.hscity.go.kr/www/gosi/BD_notice.do"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def request(data=None):
    body = urllib.parse.urlencode(data).encode("utf-8") if data else None
    req = urllib.request.Request(URL, data=body, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Referer": URL,
        "Content-Type": "application/x-www-form-urlencoded",
    })
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.read().decode("utf-8", errors="replace")


def summarize(page):
    total = re.search(r"총\s*<?[^>]*>?\s*([\d,]+)\s*</?[^>]*>?\s*건", page)
    total = total.group(1) if total else "?"
    titles = []
    for m in re.finditer(r"<tr[^>]*>(.*?)</tr>", page, re.S | re.I):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", m.group(1), re.S | re.I)
        if len(tds) >= 4:
            t = re.sub(r"<[^>]+>", " ", tds[1])
            t = re.sub(r"\s+", " ", html.unescape(t)).strip()
            if t:
                titles.append(t[:34])
    return total, titles


def show(label, data=None):
    try:
        total, titles = summarize(request(data))
    except Exception as e:
        print("{:32} 오류 {}".format(label, e))
        return None
    print("{:32} 총 {:>7}건  행 {:>2}개  | {}".format(
        label, total, len(titles), titles[0] if titles else "-"))
    time.sleep(1)
    return (total, titles[0] if titles else "")


print("=== 기준 (파라미터 없음) ===")
base = show("GET 그대로")
print()

print("=== 페이지 넘김 시도 ===")
for field in ["q_currPage", "currPage", "pageIndex", "q_pageIndex",
              "q_cp", "page"]:
    show("POST " + field + "=3", {field: "3"})
print()

print("=== 제목 검색 시도 ('트램') ===")
KEY = "트램"
combos = [
    {"q_searchVal": KEY},
    {"q_searchVal": KEY, "q_seachType": "title"},
    {"q_searchVal": KEY, "q_searchType": "title"},
    {"q_searchVal": KEY, "q_searchKey": "title"},
    {"q_searchVal": KEY, "q_seachType": "1"},
    {"q_searchVal": KEY, "q_searchGubun": "1"},
    {"searchVal": KEY, "searchType": "title"},
    {"q_searchWrd": KEY, "q_searchCnd": "1"},
]
for c in combos:
    show("POST " + str(c)[:26], c)
print()

print("=== 부서 검색 시도 ===")
for c in [{"q_depNm": "도시정책과"},
          {"q_deptNm": "도시정책과"},
          {"q_depCode": "도시정책과"},
          {"q_searchDept": "도시정책과"}]:
    show("POST " + str(c)[:26], c)
print()

print("=== 목록 개수 시도 ===")
for field in ["q_rowPerPage", "rowPerPage", "q_listScale", "pageUnit"]:
    show("POST " + field + "=50", {field: "50"})

print()
print("기준과 '총 건수'나 '첫 행'이 다른 줄이 성공한 조합입니다.")
