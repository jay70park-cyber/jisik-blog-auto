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
import markdown as md_lib
import re
import base64
import io
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import make_thumbnail as thumb

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


def call_claude_with_search(prompt, timeout=280):
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
    intro_cat = result["category_display"]
    intro_kw = result["top_keyword"]

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
   ※ 제목 바로 다음에는 "최근 지식산업센터 관련 검색 데이터를 살펴보니 {intro_kw}을(를) 찾아보는 분들이 가장 많았습니다..." 라는 소개 문장과, 키워드별 관심도 데이터 표가 시스템에 의해 자동으로 삽입됩니다. 당신은 이 소개 문장과 표를 직접 쓰지 마세요. 제목 다음 곧바로 2번(핵심 인사이트) 문단부터 작성하면 됩니다.
2. **핵심 인사이트 (서두 첫 문단)**: 반드시 "**핵심 인사이트**:" 로 문단을 시작하세요. 그 뒤에 "이 글에서 전하고 싶은 결론은 무엇인가"를 1~2문장으로 명확히 제시하세요. 예: "지금 지식산업센터 투자에 관심이 몰리는 이유는 단순 시세차익이 아니라 OOO 때문이다" 같은 형태로, 데이터(트렌드지수 등)를 근거로 삼아 독자가 이 글에서 무엇을 얻어갈지 첫 문단에서 바로 알 수 있게 하세요.
3. 본문은 그 핵심 인사이트를 증명해나가는 **단계별 전개**로 구성하세요 (아래 순서를 소제목으로 명시):
   - **왜 지금인가**: 관심도 데이터가 보여주는 현재 상황
   - **무엇이 근거인가**: 웹 검색으로 확인한 최신 법령·세율·정책 등 구체적 사실
   - **그래서 어떤 의미인가**: 위 사실이 투자자/임차인에게 갖는 실질적 시사점
   - **어떻게 대응할 것인가**: 독자가 지금 취할 수 있는 구체적 다음 행동이나 체크포인트
   각 섹션은 앞 섹션의 결론을 이어받아 다음 섹션으로 자연스럽게 연결되어야 하며, 서로 무관한 정보 나열이 되지 않도록 하세요.
4. **근거 링크는 해당 내용 바로 아래에 배치**: 특정 수치·법령·정책을 언급한 문단이 나오면, 그 문단 바로 밑에 인용부호(>) 형식으로 근거 출처와 링크를 붙이세요. 예시 형식:
   > 근거: 국세청 「OOO 안내」 (https://...)
   글 하단에 모아두지 말고, 독자가 해당 내용을 읽는 그 자리에서 바로 출처를 확인할 수 있게 각 섹션 안에 분산 배치하는 것이 핵심입니다. 링크는 실제로 검색해서 확인한 URL만 쓰고, 지어내지 마세요.
5. 표: 키워드별 관심도 데이터 표는 시스템이 서두(소개 문장 바로 뒤)에 자동으로 삽입하므로, 당신은 본문에 이 표를 다시 만들지 마세요. "왜 지금인가" 섹션에서는 그 표를 이미 본 독자를 전제로, 표의 수치를 문장으로 풀어 해석하는 데 집중하세요.
6. **이미지 삽입 (실제 이미지로 자동 치환됨)**: 본문 중 정확히 2곳에 아래 형식으로 표시하세요. 반드시 독립된 문단으로(앞뒤에 빈 줄), 문장 중간에 끼워 넣지 마세요.
   `[이미지: 한글로 어떤 장면인지 설명 | search: 검색용 영어 키워드 2~4단어]` 형식. 예: `[이미지: 지식산업센터 사무실 내부 | search: modern office interior]`. search 뒤 영어 키워드는 실제 스톡사진 검색에 쓰이므로, 구체적이고 일반적인 영단어로 작성하세요 (건물외관→office building exterior, 계약서→business contract signing, 도시전경→city skyline 등).
   **주의**: 관심도스코어/블로그건수/트렌드지수 같은 수치는 이미 서두에 표로 제공되어 있습니다. 이미지로 같은 데이터를 그래프화해서 다시 보여주지 마세요 — 같은 정보를 표와 그래프로 중복 제시하면 정보량이 부풀려 보여 오히려 신뢰도를 떨어뜨립니다. 이미지 2곳은 모두 본문 내용(현장, 서류, 건물, 계약 등)과 관련된 실사진 제안으로만 채우세요.
7. **실행 체크리스트 (필수 섹션)**: "어떻게 대응할 것인가" 안에 독자가 오늘 바로 실행할 수 있는 체크리스트를 반드시 넣으세요.
   - "임장을 통해 확인하세요", "전문가와 상담하세요", "꼼꼼히 따져보세요" 같은 추상적 조언은 **금지**입니다. 독자는 이미 그걸 알고 검색했습니다.
   - 대신 이런 수준으로 구체적이어야 합니다: 현장에서 확인할 항목 5가지(각각 무엇을 어떻게 보는지), 세무사/중개사에게 던질 질문 3가지(질문 문장 그대로), 공실률·실거래가를 조회할 수 있는 구체적 사이트나 기관 이름.
8. **숫자 계산 예시 1개 이상 필수**: 수익률·세금·대출 이자 등을 다룬다면 반드시 가상의 구체적 사례로 계산 과정을 보여주세요. 예: "분양가 3억, 대출 70%(2.1억), 금리 O%라면 월 이자 O만원 → 손익분기 월 임대료는 O만원" 형태. 사용한 금리·세율은 웹 검색으로 확인한 최신 값을 쓰고, 그 값 옆에 근거 링크를 붙이세요.
9. 마무리: 서두의 핵심 인사이트를 다시 한번 짧게 상기시키며 마무리
10. 하단 "더 읽어보기" 섹션: 위에 제공된 네이버 블로그 참고 링크만 제목과 함께 나열 (본문 중에 이미 배치한 공식 출처 링크는 여기서 반복하지 말 것)

**중복 금지 규칙**
- 같은 사실·수치·주장을 여러 섹션에서 반복해서 다시 설명하지 마세요. 예를 들어 "왜 지금인가"에서 이미 설명한 관심도 데이터를 "그래서 어떤 의미인가"에서 같은 방식으로 다시 요약하지 말고, 각 섹션은 새로운 내용을 더해야 합니다.
- 서두(핵심 인사이트)에서 언급한 내용을 본문에서 표현만 바꿔 그대로 반복하지 마세요. 서두는 결론의 예고이고, 본문은 그 결론을 증명하는 새로운 근거·설명이어야 합니다.
- 근거로 든 법령·수치를 본문 여러 곳에서 다른 표현으로 재인용하지 말고, 한 번만 정확히 제시하세요.
- 표에 담긴 데이터를 본문 문장으로 다시 나열하지 말고, 표가 보여주는 것에 대한 해석·의미만 덧붙이세요.
- 글을 다 쓴 뒤, 앞뒤 문단에서 같은 말을 반복하고 있지 않은지 스스로 점검하고 중복되는 문장은 삭제하세요.

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



UNSPLASH_ACCESS_KEY = os.environ.get("UNSPLASH_ACCESS_KEY", "")


def generate_score_chart_base64(rows, title="키워드별 관심도 스코어"):
    """관심도 데이터를 막대그래프로 그려 base64 PNG data URI로 반환한다."""
    try:
        for font_path in ["/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
                           "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"]:
            if os.path.exists(font_path):
                fm.fontManager.addfont(font_path)
                plt.rcParams["font.family"] = fm.FontProperties(fname=font_path).get_name()
                break
        plt.rcParams["axes.unicode_minus"] = False

        keywords = [r["keyword"] for r in rows]
        scores = [r["score"] for r in rows]
        colors = ["#1F3C88" if s == max(scores) else "#9FB3D9" for s in scores]

        fig, ax = plt.subplots(figsize=(7, 3.2), dpi=150)
        bars = ax.barh(keywords, scores, color=colors)
        ax.invert_yaxis()
        ax.set_xlabel("관심도 스코어")
        ax.set_title(title, fontsize=13, color="#1F3C88", weight="bold")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        for bar, score in zip(bars, scores):
            ax.text(bar.get_width() + 0.02, bar.get_y() + bar.get_height() / 2,
                    "{:.2f}".format(score), va="center", fontsize=10, color="#333")
        fig.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        b64 = base64.b64encode(buf.read()).decode("utf-8")
        return "data:image/png;base64," + b64
    except Exception as e:
        print("차트 생성 실패: " + str(e))
        return None


def fetch_unsplash_image(query, timeout=15):
    """Unsplash에서 쿼리에 맞는 실제 사진 1장을 검색해 이미지 URL을 반환한다. 실패 시 None."""
    if not UNSPLASH_ACCESS_KEY:
        return None
    url = "https://api.unsplash.com/search/photos?" + urllib.parse.urlencode(
        {"query": query, "per_page": 1, "orientation": "landscape"}
    )
    req = urllib.request.Request(url, headers={"Authorization": "Client-ID " + UNSPLASH_ACCESS_KEY})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            data = json.load(res)
        results = data.get("results", [])
        if results:
            return results[0]["urls"]["regular"], results[0].get("user", {}).get("name", "Unsplash")
        return None
    except Exception as e:
        print("Unsplash 검색 오류(" + query + "): " + str(e))
        return None


def render_images(markdown_text, rows):
    """[이미지: 한글설명 | search: english keywords] 패턴을 실제 이미지로 치환한다.
    - '스코어'/'그래프'/'차트'/'비교' 등 데이터 시각화 요청은 직접 그린 그래프로 대체
    - 그 외에는 Unsplash에서 실사진을 검색해 삽입 (UNSPLASH_ACCESS_KEY 없으면 기존 플레이스홀더 유지)
    """
    def _replace(match):
        full_desc = match.group(1).strip()
        kor_part = full_desc.split("|")[0].strip()
        search_query = full_desc
        if "search:" in full_desc:
            search_query = full_desc.split("search:")[-1].strip()

        if any(k in kor_part for k in ["스코어", "그래프", "차트", "비교", "지수"]) and rows:
            chart = generate_score_chart_base64(rows)
            if chart:
                return ('<figure style="margin:20px 0;text-align:center;">'
                        '<img src="' + chart + '" width="430" style="width:430px;max-width:100%;border-radius:8px;" alt="' + kor_part + '">'
                        '<figcaption style="font-size:15px;color:#999;margin-top:6px;">' + kor_part + '</figcaption>'
                        '</figure>')

        photo = fetch_unsplash_image(search_query)
        if photo:
            photo_url, author = photo
            return ('<figure style="margin:20px 0;text-align:center;">'
                    '<img src="' + photo_url + '" width="430" style="width:430px;max-width:100%;border-radius:8px;" alt="' + kor_part + '">'
                    '<figcaption style="font-size:14px;color:#aaa;margin-top:6px;">사진: Unsplash / ' + author + '</figcaption>'
                    '</figure>')

        return ('<div style="margin:16px 0;padding:14px 16px;background:#f2f2f2;border:1px dashed #999;'
                'border-radius:6px;color:#666;font-size:17px;">📷 이미지 제안: ' + kor_part + '</div>')

    return re.sub(r"\[이미지:\s*(.+?)\]", _replace, markdown_text)


def build_score_table_html(rows):
    """키워드별 관심도 데이터를, 우측정렬+소수점자릿수통일+천단위콤마 규칙에 맞춘 HTML 표로 만든다.
    (붙여넣기 편집기 호환을 위해 클래스 없이 전부 인라인 style로 작성)"""
    if not rows:
        return ""
    trend_decimals = 2
    score_decimals = 2
    th_style = "border:1px solid #ddd;padding:9px 6px;background:#EEF2FB;color:#1F3C88;font-weight:700;font-size:14px;white-space:nowrap;"
    th_style_right = th_style + "text-align:right;"
    td_style = "border:1px solid #ddd;padding:9px 12px;font-size:16px;word-break:keep-all;"
    td_style_right = td_style + "text-align:right;"

    body_rows = ""
    for r in rows:
        blog_s = "{:,}".format(r["blog"])
        cafe_s = "{:,}".format(r["cafe"])
        trend_s = "{:,.{p}f}".format(r["trend"], p=trend_decimals)
        score_s = "{:.{p}f}".format(r["score"], p=score_decimals)
        body_rows += (
            '<tr><td style="' + td_style + '">' + r["keyword"] + "</td>"
            '<td style="' + td_style_right + '">&nbsp;' + blog_s + "</td>"
            '<td style="' + td_style_right + '">&nbsp;' + cafe_s + "</td>"
            '<td style="' + td_style_right + '">&nbsp;' + trend_s + "</td>"
            '<td style="' + td_style_right + '">&nbsp;' + score_s + "</td></tr>"
        )
    # 열 너비를 명시하지 않으면 네이버 편집기가 자체적으로 재계산해 키워드 열이 좁아지고 줄바꿈이 생긴다.
    # colgroup + th의 width를 모두 지정해 원본과 동일한 비율을 유지한다.
    colgroup = ('<colgroup><col style="width:40%"><col style="width:15%"><col style="width:15%">'
                '<col style="width:15%"><col style="width:15%"></colgroup>')
    return (
        '<table align="center" style="border-collapse:collapse;width:100%;table-layout:fixed;">'
        + colgroup +
        '<thead><tr>'
        '<th width="40%" style="' + th_style + 'width:40%;">키워드</th>'
        '<th width="15%" style="' + th_style_right + 'width:15%;">블로그 건수</th>'
        '<th width="15%" style="' + th_style_right + 'width:15%;">카페 건수</th>'
        '<th width="15%" style="' + th_style_right + 'width:15%;">검색트렌드지수</th>'
        '<th width="15%" style="' + th_style_right + 'width:15%;">관심도스코어</th>'
        "</tr></thead><tbody>" + body_rows + "</tbody></table>"
    )



# 붙여넣기 편집기(네이버 블로그 등)는 <style> 블록의 클래스 스타일을 대부분 걸러내고,
# 각 태그에 직접 박힌 인라인 style만 안정적으로 보존한다. 그래서 아래는 전부 인라인 방식으로 작성한다.
# 폰트 크기 전반 1.3배 확대 / margin 대신 spacer와 padding 사용 (네이버 편집기가 margin을 제거하므로)
_H1_STYLE = "font-size:29px;line-height:1.4;color:#1F3C88;font-weight:800;padding:0;"
_P_STYLE = "line-height:1.8;color:#222;font-size:21px;"
_TABLE_STYLE = "border-collapse:collapse;width:100%;font-size:18px;"
_TH_BASE = "border:1px solid #ddd;padding:10px 12px;background:#EEF2FB;color:#1F3C88;font-weight:700;"
_TD_BASE = "border:1px solid #ddd;padding:10px 12px;"
_MARK_STYLE = "background:#fff176;padding:1px 3px;border-radius:2px;"
# 근거 박스: blockquote 대신 div (네이버가 blockquote를 자체 인용구 스타일로 덮어씀)
_BLOCKQUOTE_STYLE = ("padding:12px 16px;background:#f4f6fa;border-left:5px solid #C9932F;"
                     "border-radius:6px;color:#555;font-size:17px;line-height:1.65;")
_STRONG_STYLE = "color:#B8451D;font-weight:700;"
_LI_STYLE = "line-height:1.8;margin:4px 0;font-size:21px;"

# 소제목: border-bottom(구분선)이 네이버에서 본문 폭 전체로 튀거나 사라지므로 아예 쓰지 않고,
# 배경 도형(박스) 안에 넣어 강조한다. 위쪽 간격은 margin 대신 spacer div로 확보.
_H2_BOX_STYLE = ("background:#EEF2FB;border-left:7px solid #1F3C88;border-radius:8px;"
                 "padding:12px 18px;font-size:23px;font-weight:800;color:#1F3C88;line-height:1.35;")
_H3_BOX_STYLE = ("background:#F5F6FA;border-left:5px solid #9FB3D9;border-radius:6px;"
                 "padding:9px 14px;font-size:20px;font-weight:700;color:#1F3C88;line-height:1.35;")


def _spacer(height_px):
    """네이버 편집기가 margin을 제거해도 살아남는 여백용 요소."""
    return ('<div style="height:' + str(height_px) + 'px;line-height:' + str(height_px)
            + 'px;font-size:1px;">&nbsp;</div>')


def _inline_styles(html):
    """마크다운 변환 결과의 태그들에 인라인 style을 직접 삽입한다 (클래스 대신).
    소제목(h2/h3)은 네이버에서 구분선/여백이 깨지므로 배경 도형 div로 치환한다."""
    html = re.sub(r"<h1>", '<h1 style="' + _H1_STYLE + '">', html)

    # h2 -> spacer + 박스 div (위 본문과의 간격 확보 + 강조)
    def _h2_box(m):
        return _spacer(22) + '<div style="' + _H2_BOX_STYLE + '">' + m.group(1) + "</div>" + _spacer(10)

    def _h3_box(m):
        return _spacer(16) + '<div style="' + _H3_BOX_STYLE + '">' + m.group(1) + "</div>" + _spacer(8)

    html = re.sub(r"<h2>(.*?)</h2>", _h2_box, html, flags=re.DOTALL)
    html = re.sub(r"<h3>(.*?)</h3>", _h3_box, html, flags=re.DOTALL)

    html = re.sub(r"<p>", '<p style="' + _P_STYLE + '">', html)
    # 표는 중앙 정렬 (align 속성도 함께 넣어 편집기가 style을 지워도 유지되도록)
    html = re.sub(r"<table>", '<table align="center" style="' + _TABLE_STYLE + '">', html)
    html = re.sub(r"<mark>", '<mark style="' + _MARK_STYLE + '">', html)
    # <blockquote>는 네이버 편집기가 자체 인용구 스타일로 강제 변환해 배경색이 사라지므로,
    # 대신 <div>로 치환해 우리가 지정한 배경/테두리가 그대로 유지되게 한다.
    html = re.sub(r"<blockquote>", '<div style="' + _BLOCKQUOTE_STYLE + '">', html)
    html = re.sub(r"</blockquote>", "</div>", html)
    html = re.sub(r"<strong>", '<strong style="' + _STRONG_STYLE + '">', html)
    html = re.sub(r"<li>", '<li style="' + _LI_STYLE + '">', html)

    def _merge_th(m):
        existing = (m.group(1) or "").strip()
        return '<th style="' + _TH_BASE + existing + '">'

    def _merge_td(m):
        existing = (m.group(1) or "").strip()
        return '<td style="' + _TD_BASE + existing + '">'

    html = re.sub(r'<th(?:\s+style="([^"]*)")?>', _merge_th, html)
    html = re.sub(r'<td(?:\s+style="([^"]*)")?>', _merge_td, html)
    return html


def render_naver_html(markdown_text, title="블로그 초안", category_display=None, top_keyword=None, rows=None):
    """마크다운을 네이버 블로그 붙여넣기에 적합한, 인라인 스타일 기반 HTML로 변환한다."""
    # [이미지: 설명] 표시를 실제 그래프/사진으로 치환 (실패 시 안내 박스로 대체)
    prepped = render_images(markdown_text, rows)
    body_html = md_lib.markdown(prepped, extensions=["tables", "nl2br"])
    body_html = _inline_styles(body_html)

    # "핵심 인사이트" 문단을, 단순한 구조의 색상 박스로 변환 (flexbox/의사요소 없이 -> 붙여넣기에도 안정적)
    def _wrap_insight(match):
        inner = match.group(1)
        # 글 전체에서 가장 중요한 문단이므로, 배경만이 아니라 테두리까지 두른 강조 블록으로 처리한다.
        outer_style = ("border:3px solid #1F3C88;border-radius:14px;background:#FFFFFF;"
                       "padding:0;overflow:hidden;")
        header_style = ("background:#1F3C88;color:#FFFFFF;font-size:17px;font-weight:800;"
                        "letter-spacing:1px;padding:10px 20px;")
        body_style = ("padding:18px 20px;color:#222;line-height:1.85;font-size:21px;background:#F7F9FF;")
        return (
            _spacer(14)
            + '<div style="' + outer_style + '">'
            + '<div style="' + header_style + '">🗣️ 핵심 인사이트</div>'
            + '<div style="' + body_style + '">' + inner + "</div>"
            + "</div>"
            + _spacer(14)
        )

    body_html = re.sub(
        r'<p style="[^"]*"><strong[^>]*>핵심 인사이트</strong>:?(.*?)</p>',
        _wrap_insight,
        body_html,
        count=1,
        flags=re.DOTALL,
    )

    # 제목(H1) 바로 다음에, 주제 소개 문구 + 근거 표를 자동 삽입 (Claude가 아닌 코드가 직접 생성 -> 매주 정확함)
    # 카테고리명은 블로그 카테고리로 이미 구분되므로 문장에 넣지 않고, 데이터 근거만 자연스럽게 밝힌다.
    intro_html = ""
    if top_keyword:
        table_html = build_score_table_html(rows) if rows else ""
        intro_style = "background:#F5F6FA;border-radius:10px;padding:16px 18px;color:#444;font-size:19px;line-height:1.7;"
        strong_style = "color:#1F3C88;font-weight:700;"
        intro_html = (
            '<p style="' + intro_style + '">최근 지식산업센터 관련 검색 데이터를 살펴보니, '
            '<strong style="' + strong_style + '">' + top_keyword + '</strong>을(를) 찾아보는 분들이 가장 많았습니다.<br>'
            "그래서 이번 글에서는 이 주제를 중심으로, 실제로 확인해야 할 부분과 놓치기 쉬운 고려사항을 정리해 봤습니다.</p>"
            + table_html
        )
        body_html = body_html.replace("</h1>", "</h1>\n" + intro_html, 1)

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>{title}</title>
</head>
<body style="font-family:-apple-system,'Malgun Gothic',sans-serif;font-size:21px;line-height:1.8;color:#222;max-width:820px;margin:24px auto;padding:0 16px;">
{body_html}
</body>
</html>"""
    return html


def generate_thumbnail_copy(title, category, timeout=60):
    """제목을 바탕으로 썸네일용 짧은 카피(3줄 헤드라인/강조구/보조문구)를 만든다."""
    prompt = f"""아래는 블로그 글 제목입니다.

제목: {title}
카테고리: {category}

이 글의 썸네일에 넣을 문구를 만들어 JSON으로만 출력하세요. 다른 설명은 붙이지 마세요.

{{
  "headline": "썸네일용 짧은 제목. 반드시 \\n 으로 2~3줄로 나눌 것. 각 줄은 공백 포함 12자 이내. 원제목을 짧고 강하게 압축할 것",
  "highlight": "headline 안에 실제로 들어있는 문구 중 강조할 부분. 반드시 한 줄 안에 온전히 포함되는 연속된 문자열이어야 함(줄바꿈을 걸치면 안 됨). 6자 이내 권장",
  "sub": "보조 문구 한 줄. 20자 이내"
}}"""
    url = "https://api.anthropic.com/v1/messages"
    body = json.dumps(
        {"model": MODEL, "max_tokens": 500, "messages": [{"role": "user", "content": prompt}]},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            data = json.load(res)
        text = "".join(p.get("text", "") for p in data.get("content", []) if p.get("type") == "text")
        text = text.replace("```json", "").replace("```", "").strip()
        spec = json.loads(text)
        return spec.get("headline", title), spec.get("highlight"), spec.get("sub")
    except Exception as e:
        print("썸네일 카피 생성 실패, 제목으로 대체: " + str(e))
        return title, None, None


def send_telegram_photo(filepath, caption, timeout=40):
    url = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendPhoto"
    boundary = "----WebKitFormBoundaryThumb123"
    filename = os.path.basename(filepath)
    with open(filepath, "rb") as f:
        content = f.read()
    body = b""
    body += ("--" + boundary + "\r\n").encode()
    body += b'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
    body += (TELEGRAM_CHAT_ID + "\r\n").encode()
    body += ("--" + boundary + "\r\n").encode()
    body += b'Content-Disposition: form-data; name="caption"\r\n\r\n'
    body += (caption + "\r\n").encode()
    body += ("--" + boundary + "\r\n").encode()
    body += ('Content-Disposition: form-data; name="photo"; filename="' + filename + '"\r\n').encode()
    body += b"Content-Type: image/png\r\n\r\n"
    body += content
    body += ("\r\n--" + boundary + "--\r\n").encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            print(res.read().decode("utf-8")[:200])
    except urllib.error.HTTPError as e:
        print("썸네일 전송 실패: " + e.read().decode("utf-8", errors="replace"))


def send_telegram_document(filepath, caption, timeout=30):
    url = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendDocument"
    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    filename = os.path.basename(filepath)
    content_type = "text/html" if filename.endswith(".html") else "text/plain"
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
    body += ('Content-Disposition: form-data; name="document"; filename="' + filename + '"\r\n').encode()
    body += ("Content-Type: " + content_type + "\r\n\r\n").encode()
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
    # 마크다운 원본은 내부적으로 계속 보관 (수정 요청 시 Claude에게 다시 넘길 원본)
    with open("state/draft.md", "w", encoding="utf-8") as f:
        f.write(draft)
    with open("state/collection_result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # 실제로 보여주고 검토할 파일은 서식이 살아있는 HTML로 변환
    draft_title = "[초안] " + result["category_display"] + " - " + result["top_keyword"]
    html = render_naver_html(draft, title=draft_title, category_display=result["category_display"], top_keyword=result["top_keyword"], rows=result["rows"])
    with open("draft.html", "w", encoding="utf-8") as f:
        f.write(html)

    mark_count = draft.count("<mark>")
    caption = (
        draft_title
        + "\n웹 검색으로 최신 수치를 채워 넣었습니다. 노란 하이라이트 " + str(mark_count) + "곳만 직접 확인해주세요."
        + "\n\n▶ 이 파일을 다운로드해서 브라우저로 열어보세요(파일을 눌러 크롬 등으로 열기). 서식이 그대로 보입니다."
        + "\n승인하시려면 '승인'이라고, 수정이 필요하면 원하는 내용을 답장해주세요."
    )
    send_telegram_document("draft.html", caption)

    # ── 썸네일 생성 & 전송 ──────────────────────────
    try:
        # 초안 첫 H1(제목)을 뽑아 썸네일 카피 생성의 입력으로 사용
        m = re.search(r"^#\s+(.+)$", draft, flags=re.MULTILINE)
        post_title = m.group(1).strip() if m else result["top_keyword"]

        th_headline, th_highlight, th_sub = generate_thumbnail_copy(
            post_title, result["category_display"]
        )
        thumb_path = os.path.join("state", "thumbnail.png")
        thumb.make_thumbnail(
            thumb_path,
            label=result["category_display"],
            headline=th_headline,
            highlight=th_highlight,
            sub=th_sub,
            crop_key=result["top_keyword"],
        )
        send_telegram_photo(
            thumb_path,
            "[썸네일] " + post_title + "\n블로그 발행 시 대표 이미지로 사용하세요.",
        )
    except Exception as e:
        import traceback
        print("썸네일 생성/전송 실패(본문에는 영향 없음): " + repr(e))
        traceback.print_exc()

    # 이 시점 이후의 답장부터 승인/수정 대상으로 처리하도록 baseline 기록
    baseline = get_latest_telegram_update_id()
    with open("state/last_update_id.txt", "w", encoding="utf-8") as f:
        f.write(str(baseline))


if __name__ == "__main__":
    main()
