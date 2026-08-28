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
from content_rules import build_rules

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
            "max_tokens": 16000,
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

def load_realprice_summary(max_chars=1800):
    """실거래 분석 요약을 읽어온다. 파일이 없으면 빈 문자열."""
    path = os.path.join("data", "analysis_summary.txt")
    if not os.path.exists(path):
        print("실거래 요약 파일 없음 - 시세 없이 진행합니다.")
        return ""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read().strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "\n(이하 생략)"
    print("실거래 요약 로드: {}자".format(len(text)))
    return text

def load_local_headlines(keyword="", max_items=25):
    """지역 트랙일 때 쓸 최근 뉴스 제목. 키워드와 관련된 것을 앞에 둔다."""
    path = os.path.join("data", "local_headlines.json")
    if not os.path.exists(path):
        print("뉴스 제목 파일 없음 - 검색으로 대체합니다.")
        return "", ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print("뉴스 제목 읽기 실패: " + str(e))
        return "", ""

    titles = data.get("headlines", [])
    if not titles:
        return "", ""

    # 이번 키워드의 낱말이 들어간 제목을 앞으로 끌어올린다
    words = [w for w in keyword.replace("동탄", "").split() if len(w) >= 2]
    hit = [t for t in titles if any(w in t for w in words)]
    rest = [t for t in titles if t not in hit]
    picked = (hit + rest)[:max_items]

    print("뉴스 제목 로드: {}건 (키워드 관련 {}건)".format(len(picked), len(hit)))
    return "\n".join("- " + t for t in picked), data.get("updated", "")
    
def build_prompt(result, refs, today, plan=None, category="jisik"):
    plan = plan or {}
    rules = build_rules(category)
    realprice = load_realprice_summary()
    headlines, hl_date = load_local_headlines(result.get("top_keyword", "")) if category == "local" else ("", "")
    refs_desc = "\n".join("- {t} ({l})".format(t=r["title"], l=r["link"]) for r in refs) or "(참고 자료 없음)"
    kw = result["top_keyword"]

    reader = plan.get("reader", "지식산업센터 투자 또는 입주를 검토 중인 사람")
    output_type = plan.get("output_type", "② 합격/불합격 기준")
    conclusion = plan.get("conclusion", "")
    criteria = plan.get("criteria", [])
    calc_tab = plan.get("calc_tab", "없음")
    calc_url = plan.get("calc_url", "")
    criteria_desc = "\n".join("   " + str(i) + ". " + s for i, s in enumerate(criteria[:3], 1)) or "   (미정)"

    calc_block = ""
    if calc_tab and calc_tab != "없음" and calc_url:
        calc_block = f"""
6. **"내 경우 계산해보기" 섹션 (필수)**: 독자가 자기 숫자를 넣어볼 수 있도록 계산기를 안내하세요.
   - 계산기 주소: {calc_url}
   - 이 글에서 연결할 탭: "{calc_tab}"
   - 마크다운 링크로 넣으세요. 예: [실투자금 계산기에서 내 숫자로 확인하기]({calc_url})
   - 링크 앞에 어떤 값을 넣으면 무엇을 알 수 있는지 한 줄로 안내하세요.
   - 계산기 결과는 참고용이며 실제 조건은 다를 수 있다는 점을 한 문장으로 덧붙이세요."""

    realprice_block = ""
    if realprice:
        realprice_block = f"""[실거래 데이터 — 직접 수집한 자료]
{realprice}

이 수치는 국토교통부 실거래가 공개시스템에서 직접 수집·집계한 것입니다.
- 본문에서 시세를 언급할 때 이 데이터를 우선 사용하고, 출처를 "국토교통부 실거래가 공개시스템(직접 집계)"로 밝히세요.
- 평당가는 모두 건물면적(계약면적) 기준입니다. 전용면적 기준이 아니라는 점을 한 번은 명시하세요.
- 이 데이터에 없는 수치는 웹 검색으로 찾되, 그래도 확인이 안 되면 자리표시자로 남기세요.
- 거래 건수가 적은 항목(10건 미만)은 참고치임을 밝히세요.
"""

    news_block = ""
    if headlines:
        news_block = f"""[최근 지역 뉴스 제목 — {hl_date} 수집]
{headlines}

- 위 제목들은 최근 3주간 동탄 관련 부동산·산업 기사에서 모은 것입니다.
- 이 중 이번 주제와 맞닿은 것을 골라 글의 출발점으로 삼으세요.
  무엇이 실제로 진행되고 있는지가 여기 드러납니다.
- 제목만 있으므로 구체적 내용은 웹 검색으로 확인하고, 확인된 것만 쓰세요.
- 제목을 그대로 나열하지 마세요. 흐름을 읽고 하나의 이야기로 엮으세요.
"""

    prompt = f"""당신은 경기도 동탄 지역 지식산업센터 전문 공인중개사의 블로그 글을 씁니다.
오늘 날짜는 {today} 입니다.

이번 글의 기획은 이미 확정되어 있습니다. 반드시 이 기획대로 쓰세요.

[확정 기획]
- 독자: {reader} (이 한 사람만 겨냥합니다. 다른 독자층을 위한 내용은 넣지 마세요.)
- 산출물 유형: {output_type}
- 핵심 결론: {conclusion}
- 판단 기준 3가지:
{criteria_desc}
- 검색 관심도 1위 키워드: {kw}

[참고 링크 후보 (네이버 블로그)]
{refs_desc}

{realprice_block}
{news_block}
──────────────────────────────
가장 중요한 원칙: **이 글은 설명문이 아니라 판단 도구입니다.**
독자가 다 읽고 나서 "그래서 나는 무엇을 하면 되는가"에 스스로 답할 수 있어야 합니다.
사실을 나열하고 "확인해보세요"로 끝내는 글은 실패입니다.

**웹 검색 활용**
- 웹 검색 도구로 이 주제의 최신 법령·세율·정책·시세를 오늘 날짜 기준으로 직접 조사해 실제 숫자로 쓰세요.
- 검색으로 확인한 내용만 숫자로 쓰고, 지어내지 마세요.
- 확인이 어렵거나 변경 가능성이 높은 부분은 <mark>이 태그</mark>로 감싸세요. 확실한 정보에는 쓰지 마세요.
- 근거 링크는 해당 내용 바로 아래에 인용부호(>)로 배치하세요. 예: > 근거: 국세청 「OOO」 (https://...)
- 공식 출처만 사용하세요(국가법령정보센터, 국세청, 지자체, 통계청, 언론). 나무위키·위키백과·개인 블로그는 근거로 쓰지 마세요.

**글 구조 — 아래 순서를 소제목으로 그대로 사용**

1. 제목(H1): 독자가 얻어갈 산출물이 드러나게. 낚시성 표현은 쓰지 마세요.

2. ## 3초 요약
   - 핵심 결론을 **한 문장**으로 먼저 씁니다.
   - 이어서 "이 글이 필요한 사람"과 "필요 없는 사람"을 각각 한 줄씩 씁니다.
   - 필요 없는 사람을 명시하는 것은 독자의 시간을 아껴주기 위한 것이니 솔직하게 쓰세요.

3. ## 무슨 일이 있었나
   - 배경 사실을 씁니다. **한 문장에 하나의 정보만** 담으세요.
   - 법조문을 그대로 옮기지 말고, 쪼개서 일상 언어로 풀어 쓰세요.

4. ## {output_type.split(" — ")[0]} (실제 소제목은 내용에 맞게 구체적으로)
   - 이 글의 핵심입니다. 위에 정한 산출물 유형에 맞는 도구를 만드세요.
   - 표, 체크리스트, 순서도 중 형식은 내용에 맞게 고르되, 독자가 자기 상황을 대입할 수 있어야 합니다.
   - 남의 사례를 읽고 끝나는 게 아니라, 독자가 자기 숫자·자기 조건을 넣어볼 수 있는 형태로 만드세요.

5. ## 판단 기준 3가지
   - 위 [확정 기획]의 판단 기준 3가지를 그대로 사용하되, 각 항목마다 왜 그것이 중요한지 2~3문장으로 풀어 쓰세요.
   - "수익률 4% 이상이면 매수" 같은 단정적 투자 권유는 하지 마세요.
     다만 "전용률 50% 미만이면 실사용에 부적합" 같은 사실 기반의 판단 기준은 제시하세요.
   - 대신 "이것이 정리되지 않으면 결론을 내리기 이르다"는 톤을 유지하세요.
{calc_block}

7. ## 동탄은 어떤가
   - 동탄 지역에 한정한 내용을 한 단락 이상 씁니다. 이 블로그를 봐야 할 이유가 되는 부분입니다.
   - 검색으로 확인된 동탄 관련 사실이 없으면, 지역 특성상 어떤 점을 따로 확인해야 하는지라도 구체적으로 쓰세요.

8. ## 이 판단이 틀릴 수 있는 경우
   - 위 결론이 적용되지 않는 상황, 반대 견해, 이 글의 한계를 솔직하게 2~3가지 씁니다.
   - 이 섹션은 신뢰를 만드는 부분이니 형식적으로 쓰지 마세요.

9. ## 더 읽어보기
   - 위에 제공된 네이버 블로그 참고 링크만 제목과 함께 나열하세요.

10. ## 이 주제를 고른 이유
   - 이 주제를 다루게 된 배경을 2~3문장으로 짧게 씁니다. (관심도 데이터 표는 시스템이 이 섹션 아래에 자동으로 붙입니다. 표를 직접 만들지 마세요.)

**이미지 삽입**: 본문 중 정확히 2곳에 `[이미지: 한글설명 | search: 영어검색키워드]` 형식으로 넣으세요.
`| search:` 뒤의 영어 키워드는 필수입니다. 이것이 없으면 사진이 삽입되지 않습니다.
영어 키워드는 지역명 없이 보편적인 장면으로 쓰세요. 스톡사진 사이트에서 검색되는 말이어야 합니다.
  좋은 예: modern office interior / industrial building exterior / business meeting
  나쁜 예: dongtan technovalley (한국 지역명은 스톡사진에 없습니다)
독립된 문단으로 배치하고, 관심도 수치를 그래프로 만들지 마세요.

**중복 금지**: 같은 사실·수치를 여러 섹션에서 표현만 바꿔 반복하지 마세요. 각 섹션은 새로운 내용을 더해야 합니다.

**가독성**: 한 문단 최대 3~4줄. 나열 정보는 반드시 불릿. 각 섹션의 핵심 결론 문장은 볼드.

**쉬운 언어**: 전문 용어는 첫 등장 시 괄호로 쉬운 설명을 붙이세요. 예: LTV(집값 대비 대출 한도 비율), 무피(프리미엄 없이 분양가에 되파는 매물).

**포지셔닝**: 글쓴이는 향후 이 분야 중개사로 개업할 예정입니다. 현장 감각이 묻어나는 서술을 한두 군데 자연스럽게 넣되, "문의 주세요", "상담 환영", 특정 매물 추천, 글 말미 상담 유도 같은 노골적 영업 표현은 절대 쓰지 마세요. 마무리는 정보 요약으로 끝내세요.

**SEO 메인 키워드는 "{kw}" 입니다.** 아래 SEO 규칙의 "메인 키워드"는 모두 이것을 가리킵니다.

{rules}

**금지 표현**: "~할 수 있습니다", "~에 영향을 줍니다", "~가 중요합니다" 같은 조건부·당위 서술로 문단을 끝내지 마세요. 반드시 구체적 숫자나 확인 방법으로 끝내세요. 근거 없는 별점(★)이나 추천도 표도 쓰지 마세요.

**시세 수치**: 웹 검색으로 확인되지 않은 시세는 임의로 지어내지 말고 <mark>[평당 분양가 확인 필요]</mark> 형태의 자리표시자로 남기세요.

전체 분량 2000~3000자, 전문성 있으면서 친근한 톤.
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


def render_naver_html(markdown_text, title="블로그 초안", category_display=None, top_keyword=None, rows=None, table_position="top"):
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
        if table_position == "bottom":
            # 관심도 표는 독자 의사결정에 직접 쓰이지 않으므로 글 맨 아래로 보낸다
            body_html = body_html + table_html
            table_html = ""
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

    plan = {}
    plan_path = os.path.join("state", "plan.json")
    if os.path.exists(plan_path):
        with open(plan_path, "r", encoding="utf-8") as f:
            plan = json.load(f)
        print("기획안 로드: " + json.dumps(plan, ensure_ascii=False)[:300])
    else:
        print("기획안 파일이 없어 기본 구조로 진행합니다.")

    refs = fetch_reference_links(result["top_keyword"])
    track = result.get("track", "jisik")
    prompt = build_prompt(result, refs, today, plan=plan, category=track)

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
    html = render_naver_html(draft, title=draft_title, category_display=result["category_display"], top_keyword=result["top_keyword"], rows=result["rows"], table_position="bottom")
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
