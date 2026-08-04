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
            "max_tokens": 8000,
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
    print("Claude stop_reason: " + str(data.get("stop_reason")))
    print("Claude content block types: " + str([p.get("type") for p in parts]))
    print("생성된 텍스트 길이: " + str(len(text)))
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

**글 구성 — 반드시 "하나의 핵심 인사이트"를 중심으로 단계별로 전개할 것**

이 글은 정보 나열이 아니라, 하나의 명확한 주장/결론(인사이트)을 서두에 제시하고 본문 전체가 그것을 뒷받침하는 논리적 흐름으로 구성되어야 합니다.

1. 제목: 핵심 인사이트가 드러나는, 독자(투자자/임차인)의 관심을 끄는 제목
2. **핵심 인사이트 (서두 첫 문단)**: "이 글에서 전하고 싶은 결론은 무엇인가"를 1~2문장으로 명확히 먼저 제시하세요. 예: "지금 지식산업센터 투자에 관심이 몰리는 이유는 단순 시세차익이 아니라 OOO 때문이다" 같은 형태로, 데이터(트렌드지수 등)를 근거로 삼아 독자가 이 글에서 무엇을 얻어갈지 첫 문단에서 바로 알 수 있게 하세요.
3. 본문은 그 핵심 인사이트를 증명해나가는 **단계별 전개**로 구성하세요 (아래 순서를 소제목으로 명시):
   - **왜 지금인가**: 관심도 데이터가 보여주는 현재 상황
   - **무엇이 근거인가**: 웹 검색으로 확인한 최신 법령·세율·정책 등 구체적 사실
   - **그래서 어떤 의미인가**: 위 사실이 투자자/임차인에게 갖는 실질적 시사점
   - **어떻게 대응할 것인가**: 독자가 지금 취할 수 있는 구체적 다음 행동이나 체크포인트
   각 섹션은 앞 섹션의 결론을 이어받아 다음 섹션으로 자연스럽게 연결되어야 하며, 서로 무관한 정보 나열이 되지 않도록 하세요.
4. **근거 링크는 해당 내용 바로 아래에 배치**: 특정 수치·법령·정책을 언급한 문단이 나오면, 그 문단 바로 밑에 인용부호(>) 형식으로 근거 출처와 링크를 붙이세요. 예시 형식:
   > 근거: 국세청 「OOO 안내」 (https://...)
   글 하단에 모아두지 말고, 독자가 해당 내용을 읽는 그 자리에서 바로 출처를 확인할 수 있게 각 섹션 안에 분산 배치하는 것이 핵심입니다. 링크는 실제로 검색해서 확인한 URL만 쓰고, 지어내지 마세요.
5. 표 배치: 위 키워드별 관심도 데이터 표는 "왜 지금인가" 섹션 안(또는 바로 뒤)에 배치하세요. 이 데이터는 도입부 주장의 근거이므로 글 끝이 아니라 앞쪽에 와야 합니다.
6. 이미지 제안: 본문 중 2곳 정도에 어떤 이미지가 어울릴지 [이미지: 설명] 형식으로 표시 (실제 이미지는 첨부하지 않음, 제안만)
7. **실행 체크리스트 (필수 섹션)**: "어떻게 대응할 것인가" 안에 독자가 오늘 바로 실행할 수 있는 체크리스트를 반드시 넣으세요.
   - "임장을 통해 확인하세요", "전문가와 상담하세요", "꼼꼼히 따져보세요" 같은 추상적 조언은 **금지**입니다. 독자는 이미 그걸 알고 검색했습니다.
   - 대신 이런 수준으로 구체적이어야 합니다: 현장에서 확인할 항목 5가지(각각 무엇을 어떻게 보는지), 세무사/중개사에게 던질 질문 3가지(질문 문장 그대로), 공실률·실거래가를 조회할 수 있는 구체적 사이트나 기관 이름.
8. **숫자 계산 예시 1개 이상 필수**: 수익률·세금·대출 이자 등을 다룬다면 반드시 가상의 구체적 사례로 계산 과정을 보여주세요. 예: "분양가 3억, 대출 70%(2.1억), 금리 O%라면 월 이자 O만원 → 손익분기 월 임대료는 O만원" 형태. 사용한 금리·세율은 웹 검색으로 확인한 최신 값을 쓰고, 그 값 옆에 근거 링크를 붙이세요.
9. 마무리: 서두의 핵심 인사이트를 다시 한번 짧게 상기시키며 마무리
10. 하단 "더 읽어보기" 섹션: 위에 제공된 네이버 블로그 참고 링크만 제목과 함께 나열 (본문 중에 이미 배치한 공식 출처 링크는 여기서 반복하지 말 것)

**가독성 규칙 (스마트폰으로 읽는 독자 기준 — 반드시 지킬 것)**
- 한 문단은 최대 3~4줄. 그보다 길어지면 반드시 끊으세요. 7~8줄짜리 긴 문단은 모바일에서 '글자 벽'처럼 보여 이탈을 유발합니다.
- 조건·비율·항목처럼 나열되는 정보는 줄글로 쓰지 말고 **반드시 불릿(-)으로** 정리하세요. 예: 대출 한도, 감면 조건, 체크 항목 등.
- 각 섹션의 핵심 결론 문장은 **볼드 처리**해서 훑어보기만 해도 요점이 잡히게 하세요.

**쉬운 언어 규칙**
- 전문 용어·업계 은어는 첫 등장 시 반드시 괄호로 쉬운 설명을 붙이세요. 예: 무피(프리미엄 없이 분양가에 되파는 매물), 마피(분양가보다 싸게 내놓는 매물), LTV(집값 대비 대출 한도 비율), 공실률(비어 있는 사무실 비율).
- 어려운 개념은 일상적인 비유로 한 번 풀어주세요. 예: 공급과잉을 "한 건물에 카페가 열 곳 생긴 상황"에 빗대는 식.
- 초보 투자자가 처음 읽어도 한 번에 이해되는지를 기준으로 문장을 다듬으세요.

**표 서식 규칙**
- 숫자가 들어가는 열은 마크다운 정렬 문법으로 **우측 정렬**하세요. 구분선에 `---:` 를 사용합니다.
- 숫자 셀은 값 앞에 **한 칸 공백**을 넣어 여백을 주세요. 예: `|  260,820 |`
- 소수점이 있는 값은 **소수점 자릿수를 열 전체에서 통일**하세요(예: 모두 소수 둘째 자리 `27.78`, `0.13`, `1.00`). 자릿수를 맞춰야 우측 정렬 시 소수점 위치가 세로로 정확히 맞습니다.
- 천 단위 숫자는 쉼표를 넣어 읽기 쉽게 하세요(예: 260,820).
- 예시:
  | 키워드 | 블로그 건수 | 검색트렌드지수 |
  |---|---:|---:|
  | 지식산업센터 투자 |  260,820 |  27.78 |
  | 동탄 지식산업센터 시세 |  11,206 |   0.00 |

**포지셔닝 규칙 (매우 중요 — 은근하게)**
이 블로그의 운영자는 향후 지식산업센터 전문 공인중개사로 개업할 예정입니다. 독자(투자자·임차인)가 잠재 고객이 되도록, 글 전반에서 "이 사람은 이 분야를 깊이 아는 사람이고, 좋은 물건은 미리 움직이는 사람이 잡는다"는 인상이 자연스럽게 남게 하세요. 단, **노골적인 영업·홍보는 절대 하지 마세요.**

지켜야 할 것:
- 현장 감각이 묻어나는 서술을 섞으세요. 예: "실제로 단지마다 편차가 큰 부분입니다", "이 조건은 도면상으로는 잘 드러나지 않습니다" 처럼, 자료만 읽어서는 알기 어려운 관점을 한두 군데 자연스럽게 녹이세요.
- 타이밍의 중요성이 은근히 느껴지게 하세요. 예: 좋은 조건의 물건은 공개 시장에 오래 남지 않는다는 사실을, 마케팅 문구가 아니라 시장 구조 설명의 일부로 전달하세요.
- 독자가 "이건 혼자 판단하기엔 확인할 게 많구나"라고 느끼되, 그 해결책으로 특정인을 지목하지는 마세요. 판단 기준만 제시하고 결론은 독자에게 맡기세요.

절대 하지 말 것:
- "문의 주세요", "상담 환영", "저희가 도와드립니다", "연락처", "지금이 매수 타이밍입니다" 같은 직접적 영업·호객 문구
- 특정 단지나 매물을 추천·홍보하는 표현
- 글 말미에 상담 유도 문장 넣기 (마무리는 어디까지나 정보 요약으로 끝낼 것)
- 과장된 수익 전망이나 확정적인 시세 예측

**출처 신뢰도 규칙**
- 근거로는 공식 출처를 우선하세요: 국가법령정보센터, 국세청, 지자체 공고, 통계청, 한국산업단지공단, 언론 보도 등.
- 나무위키·위키백과·개인 블로그는 근거 링크로 쓰지 마세요. 전문성을 내세우는 글에서 신뢰도를 떨어뜨립니다. (단, 하단 "더 읽어보기"에 제공된 네이버 블로그 링크는 참고용이므로 예외입니다.)

전체 분량은 1500~2000자 정도로, 전문성 있으면서도 친근한 톤으로 작성하세요.

마크다운 텍스트만 출력하고 다른 설명은 붙이지 마세요."""
    return prompt



def send_telegram_message(text, timeout=20):
    url = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendMessage"
    body = urllib.parse.urlencode({"chat_id": TELEGRAM_CHAT_ID, "text": text}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as res:
        res.read()


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
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            print(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        print("텔레그램 전송 실패 응답: " + detail)
        raise


def get_latest_telegram_update_id(timeout=15):
    """현재 시점까지의 텔레그램 업데이트 중 가장 큰 update_id를 baseline으로 기록해,
    다음 승인확인 워크플로우가 이전 메시지를 재처리하지 않도록 한다."""
    url = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/getUpdates"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            data = json.load(res)
        results = data.get("result", [])
        if results:
            return max(u["update_id"] for u in results)
        return 0
    except Exception as e:
        print("baseline update_id 조회 오류: " + str(e))
        return 0


def main():
    with open("collection_result.json", "r", encoding="utf-8") as f:
        result = json.load(f)

    today = datetime.date.today().isoformat()
    refs = fetch_reference_links(result["top_keyword"])
    prompt = build_prompt(result, refs, today)

    draft = call_claude_with_search(prompt)

    if not draft or not draft.strip():
        msg = "초안 생성에 실패했습니다(빈 응답). Claude 응답이 비어 있어 전송을 중단합니다."
        print(msg)
        send_telegram_message(msg)
        raise SystemExit(1)

    os.makedirs("state", exist_ok=True)
    with open("draft.md", "w", encoding="utf-8") as f:
        f.write(draft)
    with open("state/draft.md", "w", encoding="utf-8") as f:
        f.write(draft)
    with open("state/collection_result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    mark_count = draft.count("<mark>")
    caption = (
        "[초안] " + result["category_display"] + " - " + result["top_keyword"]
        + "\n웹 검색으로 최신 수치를 채워 넣었습니다. <mark> 표시된 " + str(mark_count) + "곳만 직접 확인해주세요."
        + "\n승인하시려면 '승인'이라고, 수정이 필요하면 원하는 내용을 답장해주세요."
    )
    send_telegram_document("draft.md", caption)

    # 이 시점 이후의 답장부터 승인/수정 대상으로 처리하도록 baseline 기록
    baseline = get_latest_telegram_update_id()
    with open("state/last_update_id.txt", "w", encoding="utf-8") as f:
        f.write(str(baseline))


if __name__ == "__main__":
    main()
