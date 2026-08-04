# -*- coding: utf-8 -*-
"""
Claude API(+웹 검색 도구)를 이용해 수집 결과(collection_result.json)를 바탕으로
최신 법령/세율 등을 직접 조사해 반영한 블로그 초안을 생성하고,
텔레그램으로 초안 파일을 발송한다.
"""
import os
import json
import datetime
import urllib.request
import urllib.parse

NAVER_ID = os.environ["NAVER_CLIENT_ID"]
NAVER_SECRET = os.environ["NAVER_CLIENT_SECRET"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

MODEL = "claude-sonnet-5"


def fetch_reference_links(keyword, count=3, timeout=15):
    """대표 키워드로 실제 네이버 블로그 글 몇 개를 검색해 제목+링크만 가져온다."""
    url = "https://naverapihub.apigw.ntruss.com/search/v1/blog?" + urllib.parse.urlencode(
        {"query": keyword, "display": count, "sort": "sim"}
    )
    req = urllib.request.Request(
        url,
        headers={
            "X-NCP-APIGW-API-KEY-ID": NAVER_ID,
            "X-NCP-APIGW-API-KEY": NAVER_SECRET,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            data = json.load(res)
        items = data.get("items", [])
        refs = []
        for it in items:
            title = it.get("title", "").replace("<b>", "").replace("</b>", "")
            link = it.get("link", "")
            refs.append({"title": title, "link": link})
        return refs
    except Exception as e:
        print("참고 링크 검색 오류: " + str(e))
        return []


def call_claude_with_search(prompt, timeout=120):
    """웹 검색 도구를 활성화해 Claude API를 호출한다."""
    url = "https://api.anthropic.com/v1/messages"
    body = json.dumps(
        {
            "model": MODEL,
            "max_tokens": 4000,
            "tools": [{"type": "web_search_20250305", "name": "web_search"}],
            "messages": [{"role": "user", "content": prompt}],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as res:
        data = json.load(res)
    parts = data.get("content", [])
    text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
    return text


def build_prompt(result, refs, today):
    rows_desc = "\n".join(
        "- {kw}: 블로그 {blog}건, 카페 {cafe}건, 검색트렌드지수 {trend}, 관심도스코어 {score}".format(
            kw=r["keyword"], blog=r["blog"], cafe=r["cafe"], trend=r["trend"], score=r["score"]
        )
        for r in result["rows"]
    )
    refs_desc = "\n".join("- {t} ({l})".format(t=r["title"], l=r["link"]) for r in refs) or "(참고 자료 없음)"

    prompt = f"""당신은 경기도 동탄 지역 지식산업센터 전문 공인중개사의 블로그 콘텐츠 작성을 돕는 어시스턴트입니다.
오늘 날짜는 {today} 입니다.

아래는 이번 주 "{result['category_display']}" 카테고리에서 수집한 키워드별 관심도 데이터이고,
대표 키워드로 선정된 것은 "{result['top_keyword']}" 입니다.

[키워드별 관심도 데이터]
{rows_desc}

[참고 자료 후보 (실제 검색된 블로그 글 제목/링크)]
{refs_desc}

이 데이터를 바탕으로 네이버 블로그에 올릴 분석 글 초안을 마크다운으로 작성해주세요.

**가장 중요한 지침 — 웹 검색 활용**
- 웹 검색 도구를 적극적으로 사용해서, 이 주제와 관련된 최신 법령·세율·정책·시세 수치를 오늘 날짜 기준으로 직접 조사해 본문에 실제 숫자로 채워 넣으세요.
- 지어내지 말고, 반드시 검색으로 확인한 내용만 숫자로 쓰세요.
- 각 수치 옆에는 괄호로 간단한 출처(예: 기관명, 문서명)를 표기하세요. 원문을 그대로 인용하지 말고 당신의 표현으로 요약하세요.
- 검색해도 확인이 어렵거나, 변경 가능성이 높거나(예: 최근 개정 여부가 불확실한 세율), 지역·상황에 따라 달라질 수 있는 부분은 <mark>이 태그</mark>로 감싸서 표시하세요. 이 태그로 감싼 부분은 나중에 노란 음영으로 렌더링되어 "직접 확인 필요"라는 신호로 쓰입니다. 확실한 정보에는 <mark> 태그를 쓰지 마세요.

**글 구성**
1. 제목: 독자(투자자/임차인)의 관심을 끄는 제목
2. 도입부: 왜 지금 이 주제가 관심을 받고 있는지, 위 관심도 데이터(트렌드지수 등)를 자연스럽게 인용해 설명
3. 본문: 실제 검색으로 확인한 최신 수치를 반영한 분석
4. 표 하나 포함: 위 키워드별 관심도 데이터를 마크다운 표로 정리
5. 이미지 제안: 본문 중 2곳 정도에 어떤 이미지가 어울릴지 [이미지: 설명] 형식으로 표시 (실제 이미지는 첨부하지 않음, 제안만)
6. 하단에 "참고 자료" 섹션: 위에 제공된 블로그 링크 + 이번에 검색으로 확인한 공식 출처(법령정보센터, 국세청, 지자체 공고 등) 링크를 함께 나열 (내용 인용 없이 링크와 제목만)
7. 전체적으로 전문성 있으면서도 친근한 톤, 1000~1500자 분량

마크다운 텍스트만 출력하고 다른 설명은 붙이지 마세요."""
    return prompt


def send_telegram_document(filepath, caption, timeout=30):
    url = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendDocument"
    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    with open(filepath, "rb") as f:
        file_content = f.read()

    body = b""
    body += ("--" + boundary + "\r\n").encode()
    body += b'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
    body += (TELEGRAM_CHAT_ID + "\r\n").encode()
    body += ("--" + boundary + "\r\n").encode()
    body += b'Content-Disposition: form-data; name="caption"\r\n\r\n'
    body += (caption + "\r\n").encode()
    body += ("--" + boundary + "\r\n").encode()
    body += ('Content-Disposition: form-data; name="document"; filename="draft.md"\r\n').encode()
    body += b"Content-Type: text/markdown\r\n\r\n"
    body += file_content
    body += ("\r\n--" + boundary + "--\r\n").encode()

    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary},
    )
    with urllib.request.urlopen(req, timeout=timeout) as res:
        print(res.read().decode("utf-8"))


def main():
    with open("collection_result.json", "r", encoding="utf-8") as f:
        result = json.load(f)

    today = datetime.date.today().isoformat()
    refs = fetch_reference_links(result["top_keyword"])
    prompt = build_prompt(result, refs, today)

    draft = call_claude_with_search(prompt)

    with open("draft.md", "w", encoding="utf-8") as f:
        f.write(draft)

    mark_count = draft.count("<mark>")
    caption = (
        "[초안] " + result["category_display"] + " - " + result["top_keyword"]
        + "\n웹 검색으로 최신 수치를 채워 넣었습니다. <mark> 표시된 " + str(mark_count) + "곳만 직접 확인해주세요."
    )
    send_telegram_document("draft.md", caption)


if __name__ == "__main__":
    main()
