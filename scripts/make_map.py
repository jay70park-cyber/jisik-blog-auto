# -*- coding: utf-8 -*-
"""
지역 현안 위치 개념도

네이버 정적지도는 NCP 콘솔에서 Maps 상품을 신청해야 쓸 수 있다
(2026-09-17 확인: Permission Denied 210 — 인증은 맞고 상품 미신청).
신청 전까지는 이 모듈이 좌표만으로 개념도를 그린다.

실제 지도가 아니다. 축척과 도로 형태가 정확하지 않다.
"이 사안이 동탄 어느 쪽이고 테크노밸리에서 어느 방향인가"를
전달하는 것이 목적이다. 그래서 캡션에 개념도임을 반드시 밝힌다.

좌표는 두 군데에서 읽는다.

  점 하나  data/local_agenda.csv 의 위도·경도 열
  선       data/agenda_lines.csv 에 같은 id 로 두 점 이상

도로·철도처럼 선으로 놓인 현안은 핀 하나로는 뜻이 안 통한다.
"가까운데 산이 가로막아 못 간다" 같은 이야기는 선을 그어야 보인다.
선을 쓰는 현안이 하나뿐이라 local_agenda.csv 의 열을 늘리는 대신
별도 파일로 뒀다. 점이 없는 현안은 지금까지처럼 핀 하나로 그린다.

값이 없으면 지도를 그리지 않는다. 틀린 핀은 없느니만 못하다.
같은 이유로 좌표를 모르는 지형지물(산 따위)은 핀을 찍지 않고
선 가운데에 글자로만 얹는다.

Maps 를 신청한 뒤에는 draw_map() 만 정적지도 호출로 바꾸면 되고,
부르는 쪽은 손대지 않아도 된다.
"""
import io
import os
import csv
import math
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
    "rail":  dict(color="#B8451D", marker="s", size=90),
    "biz":   dict(color="#1F3C88", marker="^", size=95),
    "park":  dict(color="#4C8C4A", marker="o", size=70),
    "town":  dict(color="#9FB3D9", marker="o", size=70),
    "target": dict(color="#C9932F", marker="*", size=420),
    "route": dict(color="#C9932F", marker="o", size=150),
}

ROUTE_COLOR = "#C9932F"
ROUTE_EDGE = "#8A6410"

FONT_PATHS = [
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",
]

LINE_CSV = os.path.join("data", "agenda_lines.csv")


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
    """한 행에서 (위도, 경도)를 꺼낸다. 없으면 None.

    local_agenda.csv 와 agenda_lines.csv 가 열 이름을 공유한다.
    """
    try:
        lat = float(str(row.get("위도", "")).strip())
        lng = float(str(row.get("경도", "")).strip())
    except (TypeError, ValueError):
        return None
    # 한반도 밖이면 잘못 들어간 값이다
    if not (33.0 <= lat <= 39.0 and 124.0 <= lng <= 132.0):
        return None
    return lat, lng


def load_line(agenda_id, path=LINE_CSV):
    """선형 현안의 지점들을 순번대로 돌려준다.

    반환  ([(지점명, 위도, 경도), ...], 구간설명)
          파일이 없거나 해당 id 가 없으면 ([], "")

    구간설명은 그 id 의 행 중 처음 채워진 값을 쓴다.
    선 가운데에 얹을 글자다. 산 이름처럼 좌표를 모르는 것을
    여기에 적는다.
    """
    if not agenda_id or not os.path.exists(path):
        return [], ""
    want = str(agenda_id).strip()
    rows, label = [], ""
    try:
        with open(path, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if (r.get("id") or "").strip() != want:
                    continue
                if not label:
                    label = (r.get("구간설명") or "").strip()
                pt = parse_point(r)
                if not pt:
                    continue
                try:
                    seq = int(str(r.get("순번", "")).strip() or 0)
                except ValueError:
                    seq = 0
                rows.append((seq, (r.get("지점명") or "").strip(),
                             pt[0], pt[1]))
    except Exception as e:
        print("선형 좌표를 읽지 못했습니다: {}".format(e))
        return [], ""
    rows.sort(key=lambda x: x[0])
    return [(n, la, ln) for _, n, la, ln in rows], label


def draw_map(targets, title="", line=None, line_label="",
             width=7.0, height=5.0, dpi=150):
    """개념도를 그려 base64 data URI 로 돌려준다.

    targets     [(이름, 위도, 경도), ...]  점으로 찍을 대상
    line        [(이름, 위도, 경도), ...]  순서대로 이을 지점들
    line_label  선 가운데에 얹을 글자 (없으면 생략)
    반환         data:image/png;base64,... 또는 None
    """
    pts = [(n, la, ln) for n, la, ln in (targets or [])
           if la is not None and ln is not None]
    seg = [(n, la, ln) for n, la, ln in (line or [])
           if la is not None and ln is not None]
    if len(seg) < 2:
        seg = []
    if not pts and not seg:
        return None

    if not _use_korean_font():
        print("한글 폰트를 못 찾아 지도를 건너뜁니다.")
        return None
    plt.rcParams["axes.unicode_minus"] = False

    focus = pts + seg
    all_lat = [la for _, la, _ in focus] + [l for _, l, _, _ in LANDMARKS]
    all_lng = [ln for _, _, ln in focus] + [g for _, _, g, _ in LANDMARKS]
    # 선형일 때는 양끝 이름표가 바깥으로 나가므로 여백을 더 둔다
    grow = 0.36 if seg else 0.28
    pad_lat = max(0.014, (max(all_lat) - min(all_lat)) * grow)
    pad_lng = max(0.022, (max(all_lng) - min(all_lng)) * grow)

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
    # 선형일 때는 시점 하나만 잰다. 양끝을 다 이으면 그림이 어지럽다.
    hub = next(((la, ln) for n, la, ln, _ in LANDMARKS if n == "동탄역"), None)
    far_pts = pts if pts else seg[:1]
    if hub:
        for name, la, ln in far_pts:
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

    # 선형 현안 — 구간을 긋고 양끝에 이름을 단다
    if seg:
        xs = [ln for _, _, ln in seg]
        ys = [la for _, la, _ in seg]
        ax.plot(xs, ys, color=ROUTE_COLOR, linewidth=3.6, zorder=5,
                solid_capstyle="round", alpha=0.95)
        # 이름표를 선 방향 바깥으로 민다. 가운데에 몰아 두면
        # 양끝 이름과 구간설명이 서로 겹친다.
        # 화면상 기울기는 위도 보정(1/0.8) 때문에 데이터 기울기와 다르다.
        sx = seg[-1][2] - seg[0][2]
        sy = (seg[-1][1] - seg[0][1]) / 0.8
        norm = math.hypot(sx, sy) or 1.0
        ux, uy = sx / norm, sy / norm

        st = STYLE["route"]
        for i, (name, la, ln) in enumerate(seg):
            ax.scatter(ln, la, s=st["size"], c=st["color"],
                       marker=st["marker"], zorder=6,
                       edgecolors="white", linewidths=1.8)
            if i == 0:
                ox, oy = -ux * 38, -uy * 38
            elif i == len(seg) - 1:
                ox, oy = ux * 38, uy * 38
            else:
                ox, oy = 0, 18          # 중간 경유지는 위로
            ha = "right" if ox < -8 else ("left" if ox > 8 else "center")
            ax.annotate(name, (ln, la), xytext=(ox, oy),
                        textcoords="offset points", ha=ha, va="center",
                        fontsize=10.5, fontweight="bold", color=ROUTE_EDGE,
                        zorder=7,
                        bbox=dict(boxstyle="round,pad=0.3", fc="#FFF8E7",
                                  ec=ROUTE_COLOR, lw=0.8))
        if line_label:
            m = len(seg) // 2
            if len(seg) % 2 == 0:
                a, b = seg[m - 1], seg[m]
                mx, my = (a[2] + b[2]) / 2.0, (a[1] + b[1]) / 2.0
            else:
                mx, my = seg[m][2], seg[m][1]
            # 선과 직각으로 비켜 놓는다
            ax.annotate(line_label, (mx, my), xytext=(-uy * 62, ux * 62),
                        textcoords="offset points", ha="center", va="center",
                        fontsize=9.5, color="#7A5A12", zorder=7,
                        bbox=dict(boxstyle="round,pad=0.28", fc="white",
                                  ec=ROUTE_COLOR, lw=0.8))

    # 이번 글의 대상 (점)
    st = STYLE["target"]
    for name, la, ln in pts:
        ax.scatter(ln, la, s=st["size"], c=st["color"], marker=st["marker"],
                   zorder=6, edgecolors=ROUTE_EDGE, linewidths=1.0)
        ax.annotate(name, (ln, la), xytext=(0, 16),
                    textcoords="offset points", ha="center",
                    fontsize=11, fontweight="bold", color=ROUTE_EDGE, zorder=7,
                    bbox=dict(boxstyle="round,pad=0.3", fc="#FFF8E7",
                              ec=ROUTE_COLOR, lw=0.8))

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

    agenda_lines.csv 에 두 점 이상이 있으면 구간도를,
    없으면 지금까지처럼 위도·경도 한 쌍으로 위치도를 그린다.
    둘 다 없으면 빈 문자열을 돌려준다. 부르는 쪽은 그대로 붙이면 된다.
    """
    if not agenda:
        return ""
    name = (agenda.get("현안명") or "").strip() or "현안 위치"
    aid = (agenda.get("id") or agenda.get("ID") or "")

    seg, seg_label = load_line(aid)
    if len(seg) >= 2:
        uri = draw_map([], title=name + " 구간",
                       line=seg, line_label=seg_label)
        cap = caption or "{} 구간 개념도 (동탄권 주요 지점 대비)".format(name)
    else:
        pt = parse_point(agenda)
        if not pt:
            return ""
        uri = draw_map([(name, pt[0], pt[1])], title=name + " 위치")
        cap = caption or "{} 위치 개념도 (동탄권 주요 지점 대비)".format(name)

    if not uri:
        return ""
    return ('<figure style="margin:20px 0;text-align:center;">'
            '<img src="' + uri + '" width="560" '
            'style="width:560px;max-width:100%;border-radius:8px;" '
            'alt="' + cap + '">'
            '<figcaption style="font-size:14px;color:#999;margin-top:6px;">'
            + cap + '</figcaption></figure>')


if __name__ == "__main__":
    # 점 하나
    uri = draw_map([("동탄 주택공급", 37.2100, 127.0730)],
                   title="동탄 주택공급 위치")
    print("점 하나:", "성공" if uri else "실패")
    if uri:
        raw = base64.b64decode(uri.split(",", 1)[1])
        with open("map_sample.png", "wb") as f:
            f.write(raw)
        print("  map_sample.png 저장 ({:,}바이트)".format(len(raw)))

    # 선
    uri2 = draw_map([], title="용인 남사~화성 신동 연결도로 구간",
                    line=[("동탄 신동(시점)", 37.1777, 127.1420),
                          ("남사읍 완장리(종점)", 37.1525, 127.1736)],
                    line_label="함봉산 관통 — 터널 포함 구간")
    print("선:", "성공" if uri2 else "실패")
    if uri2:
        raw = base64.b64decode(uri2.split(",", 1)[1])
        with open("map_line_sample.png", "wb") as f:
            f.write(raw)
        print("  map_line_sample.png 저장 ({:,}바이트)".format(len(raw)))
