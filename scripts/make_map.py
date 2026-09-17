# -*- coding: utf-8 -*-
"""
지역 현안 위치 개념도

네이버 정적지도는 NCP 콘솔에서 Maps 상품을 신청해야 쓸 수 있다
(2026-09-17 확인: Permission Denied 210 — 인증은 맞고 상품 미신청).
신청 전까지는 이 모듈이 좌표만으로 개념도를 그린다.

실제 지도가 아니다. 축척과 도로 형태가 정확하지 않다.
"이 사안이 동탄 어느 쪽이고 테크노밸리에서 어느 방향인가"를
전달하는 것이 목적이다. 그래서 캡션에 개념도임을 반드시 밝힌다.

좌표는 data/local_agenda.csv 의 위도·경도 열에서 읽는다.
값이 없으면 지도를 그리지 않는다. 틀린 핀은 없느니만 못하다.

Maps 를 신청한 뒤에는 draw_map() 만 정적지도 호출로 바꾸면 되고,
부르는 쪽은 손대지 않아도 된다.
"""
import io
import os
import base64

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from matplotlib.patches import FancyBboxPatch

# ── 동탄권 기준점 ────────────────────────────────
# 글에서 위치를 가늠하는 잣대로 쓴다.
# 값은 대략치다. 정확한 좌표가 필요하면 여기서 고친다.
LANDMARKS = [
    ("동탄역",          37.2007, 127.0966, "rail"),
    ("동탄테크노밸리",   37.2200, 127.1060, "biz"),
    ("동탄일반산업단지", 37.1750, 127.1200, "biz"),
    ("동탄호수공원",     37.1880, 127.1260, "park"),
    ("동탄1신도시",      37.2050, 127.0750, "town"),
    ("동탄2신도시",      37.1950, 127.1100, "town"),
]

STYLE = {
    "rail": dict(color="#B8451D", marker="s", size=90),
    "biz":  dict(color="#1F3C88", marker="^", size=95),
    "park": dict(color="#4C8C4A", marker="o", size=70),
    "town": dict(color="#9FB3D9", marker="o", size=70),
    "target": dict(color="#C9932F", marker="*", size=420),
}

FONT_PATHS = [
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",
]


def _use_korean_font():
    for p in FONT_PATHS:
        if os.path.exists(p):
            try:
                fm.fontManager.addfont(p)
                plt.rcParams["font.family"] = fm.FontProperties(
                    fname=p).get_name()
                return True
            except Exception:
                continue
    return False


FAR_KM = 4.0   # 이보다 멀면 거리를 표시한다


def dist_km(lat1, lng1, lat2, lng2):
    """대략 거리. 위도 37도 기준으로 경도를 보정한다."""
    dlat = (lat2 - lat1) * 111.0
    dlng = (lng2 - lng1) * 111.0 * 0.8
    return (dlat ** 2 + dlng ** 2) ** 0.5


def parse_point(row):
    """local_agenda.csv 한 행에서 (위도, 경도)를 꺼낸다. 없으면 None."""
    try:
        lat = float(str(row.get("위도", "")).strip())
        lng = float(str(row.get("경도", "")).strip())
    except (TypeError, ValueError):
        return None
    # 한반도 밖이면 잘못 들어간 값이다
    if not (33.0 <= lat <= 39.0 and 124.0 <= lng <= 132.0):
        return None
    return lat, lng


def draw_map(targets, title="", width=7.0, height=5.0, dpi=150):
    """개념도를 그려 base64 data URI 로 돌려준다.

    targets  [(이름, 위도, 경도), ...]  이번 글의 대상
    반환      data:image/png;base64,... 또는 None
    """
    pts = [(n, la, ln) for n, la, ln in targets
           if la is not None and ln is not None]
    if not pts:
        return None

    if not _use_korean_font():
        print("한글 폰트를 못 찾아 지도를 건너뜁니다.")
        return None
    plt.rcParams["axes.unicode_minus"] = False

    all_lat = [la for _, la, _ in pts] + [l for _, l, _, _ in LANDMARKS]
    all_lng = [ln for _, _, ln in pts] + [g for _, _, g, _ in LANDMARKS]
    pad_lat = max(0.012, (max(all_lat) - min(all_lat)) * 0.28)
    pad_lng = max(0.015, (max(all_lng) - min(all_lng)) * 0.28)

    fig, ax = plt.subplots(figsize=(width, height), dpi=dpi)
    ax.set_facecolor("#F7F9FC")

    # 기준점
    for name, la, ln, kind in LANDMARKS:
        st = STYLE[kind]
        ax.scatter(ln, la, s=st["size"], c=st["color"], marker=st["marker"],
                   zorder=3, edgecolors="white", linewidths=1.2)
        ax.annotate(name, (ln, la), xytext=(0, -15),
                    textcoords="offset points", ha="center",
                    fontsize=8.5, color="#555", zorder=4)

    # 동탄역에서 멀리 떨어진 대상은 거리를 함께 보여준다.
    # 개념도는 축척이 없어서, 멀다는 사실만으로는 감이 안 온다.
    hub = next(((la, ln) for n, la, ln, _ in LANDMARKS if n == "동탄역"), None)
    if hub:
        for name, la, ln in pts:
            km = dist_km(hub[0], hub[1], la, ln)
            if km < FAR_KM:
                continue
            ax.plot([hub[1], ln], [hub[0], la], linestyle="--",
                    color="#B0B8C8", linewidth=1.2, zorder=2)
            ax.annotate("동탄역에서 약 {:.0f}km".format(km),
                        ((hub[1] + ln) / 2, (hub[0] + la) / 2),
                        xytext=(0, 6), textcoords="offset points",
                        ha="center", fontsize=9, color="#7A8394", zorder=5,
                        bbox=dict(boxstyle="round,pad=0.25", fc="white",
                                  ec="#D5DBE5", lw=0.7))

    # 이번 글의 대상
    st = STYLE["target"]
    for name, la, ln in pts:
        ax.scatter(ln, la, s=st["size"], c=st["color"], marker=st["marker"],
                   zorder=6, edgecolors="#8A6410", linewidths=1.0)
        ax.annotate(name, (ln, la), xytext=(0, 16),
                    textcoords="offset points", ha="center",
                    fontsize=11, fontweight="bold", color="#8A6410", zorder=7,
                    bbox=dict(boxstyle="round,pad=0.3", fc="#FFF8E7",
                              ec="#C9932F", lw=0.8))

    ax.set_xlim(min(all_lng) - pad_lng, max(all_lng) + pad_lng)
    ax.set_ylim(min(all_lat) - pad_lat, max(all_lat) + pad_lat)
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_color("#DDD")
    ax.grid(True, color="#E8EDF5", linewidth=0.8, zorder=0)
    # 위도 1도와 경도 1도의 실제 거리가 달라 그대로 두면 남북으로 눌린다.
    # 위도 37도 기준으로 보정한다.
    ax.set_aspect(1.0 / 0.8, adjustable="box")

    if title:
        ax.set_title(title, fontsize=13, color="#1F3C88",
                     weight="bold", pad=12)
    ax.text(0.5, -0.06,
            "위치 개념도 — 실제 축척·도로 형태와 다릅니다",
            transform=ax.transAxes, ha="center",
            fontsize=8.5, color="#999")

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight",
                facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return "data:image/png;base64," + base64.b64encode(buf.read()).decode()


def build_map_figure(agenda, caption=""):
    """collection_result.json 의 agenda 로 지도 HTML 조각을 만든다.

    좌표가 없으면 빈 문자열을 돌려준다. 부르는 쪽에서 그대로 붙이면 된다.
    """
    if not agenda:
        return ""
    pt = parse_point(agenda)
    if not pt:
        return ""
    name = (agenda.get("현안명") or "").strip() or "현안 위치"
    uri = draw_map([(name, pt[0], pt[1])], title=name + " 위치")
    if not uri:
        return ""
    cap = caption or "{} 위치 개념도 (동탄권 주요 지점 대비)".format(name)
    return ('<figure style="margin:20px 0;text-align:center;">'
            '<img src="' + uri + '" width="560" '
            'style="width:560px;max-width:100%;border-radius:8px;" '
            'alt="' + cap + '">'
            '<figcaption style="font-size:14px;color:#999;margin-top:6px;">'
            + cap + '</figcaption></figure>')


if __name__ == "__main__":
    uri = draw_map([("동탄 주택공급", 37.2100, 127.0730)],
                   title="동탄 주택공급 위치")
    print("생성 결과:", "성공" if uri else "실패")
    if uri:
        raw = base64.b64decode(uri.split(",", 1)[1])
        with open("map_sample.png", "wb") as f:
            f.write(raw)
        print("map_sample.png 저장 ({:,}바이트)".format(len(raw)))
