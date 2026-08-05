# -*- coding: utf-8 -*-
"""
텔레그램에 새 답장이 왔는지 주기적으로 확인해서:
- "승인" 계열 답장이면 -> 게시 단계로 진행 (현재는 네이버 로그인 인증 전이라 안내 메시지만 발송)
- 그 외 텍스트면 -> 수정 요청으로 간주해 Claude로 초안을 다시 생성해서 재발송
상태(state/)는 저장소에 커밋되어 다음 실행에서도 이어서 참조된다.
"""
import os
import json
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

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

MODEL = "claude-sonnet-5"
STATE_DIR = "state"
LAST_UPDATE_FILE = os.path.join(STATE_DIR, "last_update_id.txt")
DRAFT_FILE = os.path.join(STATE_DIR, "draft.md")
STATUS_FILE = os.path.join(STATE_DIR, "status.json")
COLLECTION_FILE = os.path.join(STATE_DIR, "collection_result.json")

APPROVE_WORDS = ["승인", "ok", "approve", "좋아", "게시"]



def load_collection_context():
    """generate_draft.py가 저장해둔 카테고리/키워드/표 데이터를 불러온다 (없으면 None들)."""
    if not os.path.exists(COLLECTION_FILE):
        return None, None, None
    with open(COLLECTION_FILE, "r", encoding="utf-8") as f:
        result = json.load(f)
    return result.get("category_display"), result.get("top_keyword"), result.get("rows")


def read_last_update_id():
    if os.path.exists(LAST_UPDATE_FILE):
        with open(LAST_UPDATE_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
            return int(content) if content else 0
    return 0


def write_last_update_id(update_id):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(LAST_UPDATE_FILE, "w", encoding="utf-8") as f:
        f.write(str(update_id))


def get_telegram_updates(offset, timeout=15):
    url = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/getUpdates?" + urllib.parse.urlencode(
        {"offset": offset, "timeout": 0}
    )
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as res:
        data = json.load(res)
    return data.get("result", [])


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
                        '<img src="' + chart + '" style="max-width:100%;border-radius:8px;" alt="' + kor_part + '">'
                        '<figcaption style="font-size:12px;color:#999;margin-top:4px;">' + kor_part + '</figcaption>'
                        '</figure>')

        photo = fetch_unsplash_image(search_query)
        if photo:
            photo_url, author = photo
            return ('<figure style="margin:20px 0;text-align:center;">'
                    '<img src="' + photo_url + '" style="max-width:100%;border-radius:8px;" alt="' + kor_part + '">'
                    '<figcaption style="font-size:11px;color:#aaa;margin-top:4px;">사진: Unsplash / ' + author + '</figcaption>'
                    '</figure>')

        return '<div class="img-suggest">📷 이미지 제안: ' + kor_part + '</div>'

    return re.sub(r"\[이미지:\s*(.+?)\]", _replace, markdown_text)

def build_score_table_html(rows):
    """키워드별 관심도 데이터를, 우측정렬+소수점자릿수통일+천단위콤마 규칙에 맞춘 HTML 표로 만든다."""
    if not rows:
        return ""
    trend_decimals = 2
    score_decimals = 2
    body_rows = ""
    for r in rows:
        blog_s = "{:,}".format(r["blog"])
        cafe_s = "{:,}".format(r["cafe"])
        trend_s = "{:,.{p}f}".format(r["trend"], p=trend_decimals)
        score_s = "{:.{p}f}".format(r["score"], p=score_decimals)
        body_rows += (
            "<tr><td>" + r["keyword"] + "</td>"
            '<td style="text-align:right;">&nbsp;' + blog_s + "</td>"
            '<td style="text-align:right;">&nbsp;' + cafe_s + "</td>"
            '<td style="text-align:right;">&nbsp;' + trend_s + "</td>"
            '<td style="text-align:right;">&nbsp;' + score_s + "</td></tr>"
        )
    return (
        '<table class="score-table"><thead><tr>'
        "<th>키워드</th>"
        '<th style="text-align:right;">블로그 건수</th>'
        '<th style="text-align:right;">카페 건수</th>'
        '<th style="text-align:right;">검색트렌드지수</th>'
        '<th style="text-align:right;">관심도스코어</th>'
        "</tr></thead><tbody>" + body_rows + "</tbody></table>"
    )


def render_naver_html(markdown_text, title="블로그 초안", category_display=None, top_keyword=None, rows=None):
    """마크다운을 네이버 블로그 붙여넣기에 적합한, 시각적으로 변화를 준 HTML로 변환한다."""
    # [이미지: 설명] 표시를 눈에 띄는 박스로 변환
    prepped = render_images(markdown_text, rows)
    body_html = md_lib.markdown(prepped, extensions=["tables", "nl2br"])

    # "핵심 인사이트" 문단을 말풍선(사람이 설명하는 느낌) 박스로 변환
    def _wrap_insight(match):
        inner = match.group(1)
        return (
            '<div class="insight-box">'
            '<div class="insight-avatar">🗣️</div>'
            '<div class="insight-bubble"><span class="insight-label">핵심 인사이트</span>' + inner + "</div>"
            "</div>"
        )

    body_html = re.sub(
        r"<p><strong>핵심 인사이트</strong>:?(.*?)</p>",
        _wrap_insight,
        body_html,
        count=1,
        flags=re.DOTALL,
    )

    # 제목(H1) 바로 다음에, 이번 주 주제를 소개하는 고정 문구 + 근거 표를 자동 삽입 (Claude가 아닌 코드가 직접 생성 -> 매주 정확함)
    intro_html = ""
    if category_display and top_keyword:
        table_html = build_score_table_html(rows) if rows else ""
        intro_html = (
            '<p class="context-intro">이번주는 <strong>' + category_display + '</strong>에 대해 알아보는 시간인데요.<br>'
            + category_display + ' 관련 검색 키워드 분석결과 <strong>' + top_keyword + '</strong>가 1위로 확인되어, '
            "이에 대한 인사이트와 고려사항 등을 전달해 드리고자 합니다.</p>"
            + table_html
        )
        body_html = body_html.replace("</h1>", "</h1>\n" + intro_html, 1)

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<style>
  body {{ font-family: -apple-system, "Malgun Gothic", sans-serif; font-size: 16px; line-height: 1.8; color: #222; max-width: 720px; margin: 24px auto; padding: 0 16px; }}
  h1 {{ font-size: 24px; margin-top: 32px; color: #1F3C88; }}
  h2 {{ font-size: 20px; margin-top: 30px; color: #1F3C88; border-bottom: 3px solid #C9932F; padding-bottom: 6px; display: inline-block; }}
  h3 {{ font-size: 18px; margin-top: 22px; color: #1F3C88; }}
  table {{ border-collapse: collapse; width: 100%; margin: 16px 0; font-size: 15px; }}
  th, td {{ border: 1px solid #ddd; padding: 8px 12px; }}
  th {{ background: #EEF2FB; color: #1F3C88; }}
  mark {{ background: #fff176; padding: 1px 3px; border-radius: 2px; }}
  blockquote {{ margin: 6px 0 18px 0; padding: 8px 14px; background: #f7f8fa; border-left: 3px solid #C9932F; color: #555; font-size: 14px; }}
  strong {{ color: #B8451D; }}
  .context-intro {{ background: #F5F6FA; border-radius: 10px; padding: 14px 16px; color: #444; font-size: 15px; margin: 14px 0 20px 0; }}
  .context-intro strong {{ color: #1F3C88; }}
  .insight-box {{ display: flex; align-items: flex-start; gap: 12px; background: #EEF2FB; border-radius: 16px; padding: 16px 18px; margin: 20px 0 26px 0; position: relative; }}
  .insight-avatar {{ font-size: 28px; line-height: 1; flex-shrink: 0; }}
  .insight-bubble {{ flex: 1; background: #fff; border-radius: 12px; padding: 12px 14px; position: relative; }}
  .insight-bubble:before {{ content: ""; position: absolute; left: -8px; top: 14px; border-width: 8px 8px 8px 0; border-style: solid; border-color: transparent #fff transparent transparent; }}
  .insight-bubble strong {{ color: #1F3C88; }}
  .insight-label {{ display: block; font-size: 12px; font-weight: 700; color: #C9932F; letter-spacing: 1px; margin-bottom: 4px; }}
  .img-suggest {{ margin: 16px 0; padding: 14px 16px; background: #f2f2f2; border: 1px dashed #999; border-radius: 6px; color: #666; font-size: 14px; }}
  .score-table {{ font-size: 13.5px; }}
  .score-table th, .score-table td {{ padding: 6px 10px; }}
  .score-table td:first-child {{ text-align: left; }}
</style>
</head>
<body>
{body_html}
</body>
</html>"""
    return html

def send_telegram_message(text, timeout=20):
    url = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendMessage"
    body = urllib.parse.urlencode({"chat_id": TELEGRAM_CHAT_ID, "text": text}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as res:
        res.read()






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
        url, data=body, method="POST",
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            res.read()
    except urllib.error.HTTPError as e:
        print("텔레그램 문서 전송 실패: " + e.read().decode("utf-8", errors="replace"))
        raise


def call_claude_revise(previous_draft, instruction, timeout=280):
    prompt = f"""아래는 네이버 블로그용으로 작성 중인 마크다운 초안입니다.

[기존 초안]
{previous_draft}

[수정 지시사항]
{instruction}

위 지시사항을 반영해서 초안 전체를 다시 작성해주세요. 다음 규칙은 계속 지켜주세요:
- 이 글은 정보 나열이 아니라, 서두에 명확한 핵심 인사이트(이 글의 결론)를 1~2문장으로 먼저 제시하고, 본문은 "왜 지금인가 → 무엇이 근거인가 → 그래서 어떤 의미인가 → 어떻게 대응할 것인가" 순서로 단계별로 전개할 것. 각 섹션은 앞 섹션의 결론을 이어받아 다음 섹션으로 자연스럽게 연결될 것
- 확실하지 않거나 확인이 필요한 수치/사실은 <mark>이 태그</mark>로 감싸기
- 근거 링크는 글 하단에 모으지 말고, 해당 수치·법령·정책을 언급한 문단 바로 아래에 인용부호(>) 형식으로 배치할 것. 예: > 근거: 국세청 「OOO 안내」 (https://...)
- 이미지는 정확히 2곳: 첫 번째는 `[이미지: 관심도스코어 비교]` (실제 막대그래프 자동 생성), 두 번째는 `[이미지: 한글설명 | search: 영어검색키워드]` 형식(실제 스톡사진 자동 검색) 유지. 하단에는 네이버 블로그 참고 링크만 "더 읽어보기"로 남길 것
- 가독성: 한 문단은 최대 3~4줄로 끊고, 나열되는 정보(조건·비율·체크항목)는 반드시 불릿(-)으로 정리하며, 각 섹션의 핵심 결론 문장은 볼드 처리할 것
- 쉬운 언어: 전문 용어·업계 은어는 첫 등장 시 괄호로 쉬운 설명을 병기하고(예: 무피(프리미엄 없이 분양가에 되파는 매물), LTV(집값 대비 대출 한도 비율)), 어려운 개념은 일상 비유로 한 번 풀어줄 것
- 실행 가능성: "임장하세요", "전문가와 상담하세요" 같은 추상적 조언 금지. 확인할 항목·질문 문장·조회 사이트명처럼 바로 실행 가능한 형태의 체크리스트를 포함할 것
- 숫자 예시: 수익률·세금·대출을 다룰 경우 구체적 금액을 넣은 계산 예시를 최소 1개 포함할 것
- 출처 신뢰도: 근거 링크는 국가법령정보센터·국세청·지자체·통계청·언론 등 공식 출처를 사용하고, 나무위키·위키백과·개인 블로그는 근거로 쓰지 말 것
- 표 서식: 숫자 열은 마크다운 우측 정렬(`---:`)을 쓰고, 숫자 앞에 한 칸 공백을 넣으며, 소수점 자릿수를 열 전체에서 통일해 소수점 위치가 세로로 맞도록 할 것. 천 단위는 쉼표 표기
- 포지셔닝: 글쓴이는 향후 지식산업센터 전문 중개사로 개업 예정이므로, 현장 감각이 묻어나는 서술과 타이밍의 중요성을 은근하게 녹여 독자가 잠재 고객이 되도록 유도하되, "문의 주세요"·"상담 환영"·특정 매물 추천·상담 유도 문장 같은 노골적 영업 표현은 절대 쓰지 말 것. 마무리는 정보 요약으로 끝낼 것
- 마크다운 텍스트만 출력하고 다른 설명은 붙이지 마세요."""

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
        url, data=body, method="POST",
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
    print("Claude stop_reason: " + str(data.get("stop_reason")) + " / 길이: " + str(len(text)))
    return text


def main():
    last_id = read_last_update_id()
    updates = get_telegram_updates(offset=last_id + 1)

    # 우리 챗봇 채팅방에서 온, 텍스트가 있는 메시지만 필터링
    relevant = [
        u for u in updates
        if u.get("message", {}).get("chat", {}).get("id") == int(TELEGRAM_CHAT_ID)
        and u.get("message", {}).get("text")
    ]

    if not relevant:
        print("새 메시지 없음. 종료합니다.")
        return

    # 가장 최근 메시지 하나만 처리 (여러 개 밀려있어도 최신 지시를 기준으로 처리)
    latest = relevant[-1]
    text = latest["message"]["text"].strip()
    max_update_id = max(u["update_id"] for u in updates)

    if text.lower() in [w.lower() for w in APPROVE_WORDS]:
        print("승인 메시지 감지: " + text)
        status = {"state": "approved", "message": text}
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False, indent=2)

        if not os.path.exists(DRAFT_FILE):
            send_telegram_message("승인 확인했으나 초안 파일을 찾을 수 없습니다. 관리자에게 문의해주세요.")
        else:
            with open(DRAFT_FILE, "r", encoding="utf-8") as f:
                final_md = f.read()

            cat, kw, rows = load_collection_context()
            final_html = render_naver_html(final_md, title="최종 발행본", category_display=cat, top_keyword=kw, rows=rows)
            final_path = os.path.join(STATE_DIR, "final_post.html")
            with open(final_path, "w", encoding="utf-8") as f:
                f.write(final_html)

            send_telegram_document(
                final_path,
                "승인 확인했습니다! 네이버 블로그 글쓰기 API는 정책상 제공되지 않아 자동 게시는 불가능합니다 (2020년 종료).\n\n"
                "▶ 이 파일을 다운로드해서 브라우저로 열고, 전체 선택(Ctrl+A) → 복사(Ctrl+C) 한 뒤 "
                "네이버 블로그 글쓰기 화면에 붙여넣으면(Ctrl+V) 표·볼드·하이라이트가 그대로 유지됩니다.\n"
                "노란색으로 표시된 부분은 발행 전 최신 수치인지 한 번 더 확인해주세요.",
            )
    else:
        print("수정 요청 감지: " + text)
        if not os.path.exists(DRAFT_FILE):
            send_telegram_message("이전 초안 파일을 찾을 수 없어서 수정할 수 없습니다. 관리자에게 문의해주세요.")
        else:
            with open(DRAFT_FILE, "r", encoding="utf-8") as f:
                previous_draft = f.read()
            revised = call_claude_revise(previous_draft, text)
            if not revised or not revised.strip():
                send_telegram_message("수정본 생성에 실패했습니다(빈 응답). 다시 한 번 요청해주세요.")
                write_last_update_id(max_update_id)
                return
            with open(DRAFT_FILE, "w", encoding="utf-8") as f:
                f.write(revised)
            mark_count = revised.count("<mark>")
            revised_html_path = os.path.join(STATE_DIR, "draft.html")
            with open(revised_html_path, "w", encoding="utf-8") as f:
                cat, kw, rows = load_collection_context()
                f.write(render_naver_html(revised, title="수정된 초안", category_display=cat, top_keyword=kw, rows=rows))
            send_telegram_document(
                revised_html_path,
                "[수정된 초안] 요청하신 내용을 반영했습니다. 노란 하이라이트 " + str(mark_count) + "곳 확인해주세요.\n"
                "▶ 파일을 브라우저로 열면 서식이 그대로 보입니다.\n"
                "승인하시려면 '승인'이라고 답장해주세요.",
            )

    write_last_update_id(max_update_id)


if __name__ == "__main__":
    main()
