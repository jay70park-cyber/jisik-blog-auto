# -*- coding: utf-8 -*-
"""
지역 현안 위치도

배경은 네이버 정적지도(NCP Maps Static Map)를 쓴다.
핀과 이름표, 선은 그 위에 직접 그린다.

  배경만 가져오는 이유
    정적지도의 마커 label 은 한글을 제대로 받지 못하고,
    선 그리기(path) 문법은 버전마다 다르다.
    배경만 받으면 보낼 값이 center·level·w·h 뿐이라 깨질 데가 없고,
    이름표 배치는 아래 코드가 이미 다듬어 둔 것을 그대로 쓴다.

  키가 없거나 호출이 실패하면
    예전처럼 좌표만으로 개념도를 그린다. 글은 나와야 한다.
    이때는 캡션이 '개념도'로 바뀐다. 실제 지도가 아니라고 밝히는
    문장은 배경이 가짜일 때만 붙어야 한다.

환경변수 (GitHub Actions Secrets)
  NCP_MAP_CLIENT_ID      NCP 콘솔 Application 의 Client ID
  NCP_MAP_CLIENT_SECRET  같은 Application 의 Client Secret

좌표는 두 군데에서 읽는다.

  점 하나  data/local_agenda.csv 의 위도·경도 열
  선       data/agenda_lines.csv 에 같은 id 로 두 점 이상

도로·철도처럼 선으로 놓인 현안은 핀 하나로는 뜻이 안 통한다.
"가까운데 산이 가로막아 못 간다" 같은 이야기는 선을 그어야 보인다.

값이 없으면 지도를 그리지 않는다. 틀린 핀은 없느니만 못하다.
같은 이유로 좌표를 모르는 지형지물(산 따위)은 핀을 찍지 않고
선 가운데에 글자로만 얹는다.
"""
import io
import os
import csv
import math
import base64
import urllib.error
import urllib.parse
import urllib.request

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import matplotlib.image as mpimg

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

# 굵은 글씨용. 이것까지 등록해야 bold 가 진짜 굵게 나온다.
# 등록하지 않으면 matplotlib 이 흉내만 내고 경고를 쏟는다
# (findfont: Failed to find font weight bold). 핀 이름표가 전부
# bold 라서 실제 지도 위에서 또렷함 차이가 난다.
BOLD_PATHS = {
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf":
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf":
        "/usr/share/fonts/truetype/nanum/NanumBarunGothicBold.ttf",
}

LINE_CSV = os.path.join("data", "agenda_lines.csv")

# ── 정적지도 ────────────────────────────────────
# 엔드포인트는 콘솔 이전(2025) 전후로 호스트가 다르다.
# 어느 쪽 계정인지 코드가 알 수 없으니 순서대로 시도한다.
STATIC_URLS = [
    "https://maps.apigw.ntruss.com/map-static/v2/raster",
    "https://naveropenapi.apigw.ntruss.com/map-static/v2/raster",
]
MAP_W = 640            # CSS 픽셀. scale=2 라서 받는 그림은 이 두 배다
MAP_H = 440
MAP_SCALE = 2
MAP_TIMEOUT = 12
TILE = 256             # 레벨 0 에서 세계 한 바퀴의 픽셀 수
LEVEL_MIN, LEVEL_MAX = 6, 18
MAP_TYPE = "basic"     # basic | traffic | terrain | satellite

_FETCH_FAILED = False  # 한 번 실패하면 같은 실행에서 다시 묻지 않는다


def _use_korean_font():
    for p in FONT_PATHS:
        if os.path.exists(p):
            try:
                fm.fontManager.addfont(p)
                bold = BOLD_PATHS.get(p)
                if bold and os.path.exists(bold):
                    # 같은 집안의 굵은 글꼴로 등록된다. 없으면 그냥 넘어간다.
                    fm.fontManager.addfont(bold)
                plt.rcParams["font.family"] = fm.FontProperties(
                    fname=p).get_name()
                return True
            except Exception:
                continue
    return False


FAR_KM = 4.0        # 이보다 멀면 거리를 표시한다
LABEL_GAP_KM = 0.9  # 대상과 이보다 가까운 기준점은 이름을 생략한다

# 실제 지도 배경일 때 남길 기준점 종류.
# 지도는 역·공원·신도시를 이미 글자로 적어 준다. 그 위에 같은 이름을
# 또 찍으면 겹치기만 한다. 지도가 잘 안 적어 주면서 이 블로그 독자에게
# 필요한 것은 산업 쪽 지점이다. 개념도일 때는 여섯 개를 다 쓴다.
MAP_KEEP = {"biz", "rail"}


def dist_km(lat1, lng1, lat2, lng2):
    """대략 거리. 위도 37도 기준으로 경도를 보정한다."""
    dlat = (lat2 - lat1) * 111.0
    dlng = (lng2 - lng1) * 111.0 * 0.8
    return (dlat ** 2 + dlng ** 2) ** 0.5


# ── 메르카토르 ──────────────────────────────────
# 정적지도는 웹 메르카토르로 그려진 그림이다.
# 위도·경도를 그대로 얹으면 핀이 남북으로 밀린다.
# 그림과 같은 좌표계로 바꿔서 얹는다. 세계를 0~1 로 본다.

def merc_x(lng):
    return (lng + 180.0) / 360.0


def merc_y(lat):
    lat = max(-85.0, min(85.0, lat))
    s = math.sin(math.radians(lat))
    return 0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)


def _level_for(span_x, span_y, w=MAP_W, h=MAP_H):
    """메르카토르 폭·높이(0~1)가 w·h 픽셀에 들어가는 가장 큰 레벨."""
    for lvl in range(LEVEL_MAX, LEVEL_MIN - 1, -1):
        world = TILE * (2 ** lvl)
        if span_x * world <= w and span_y * world <= h:
            return lvl
    return LEVEL_MIN


def map_keys():
    """(client_id, client_secret). 둘 중 하나라도 없으면 (None, None)."""
    cid = (os.environ.get("NCP_MAP_CLIENT_ID") or "").strip()
    sec = (os.environ.get("NCP_MAP_CLIENT_SECRET") or "").strip()
    if not cid or not sec:
        return None, None
    return cid, sec


def fetch_basemap(center_lat, center_lng, level,
                  w=MAP_W, h=MAP_H, scale=MAP_SCALE):
    """정적지도 배경 그림(PNG 바이트)을 받아온다. 실패하면 None.

    실패 사유를 반드시 찍는다. 본문을 읽지 않아 진단이 늦은 적이 있다.
    """
    global _FETCH_FAILED
    if _FETCH_FAILED:
        return None
    cid, sec = map_keys()
    if not cid:
        print("지도 키가 없어 개념도로 그립니다 "
              "(NCP_MAP_CLIENT_ID / NCP_MAP_CLIENT_SECRET).")
        _FETCH_FAILED = True
        return None

    q = urllib.parse.urlencode({
        "w": int(w),
        "h": int(h),
        "center": "{:.6f},{:.6f}".format(center_lng, center_lat),
        "level": int(level),
        "scale": int(scale),
        "maptype": MAP_TYPE,
        "format": "png",
        "lang": "ko",
    })
    headers = {
        "X-NCP-APIGW-API-KEY-ID": cid,
        "X-NCP-APIGW-API-KEY": sec,
        "Accept": "image/png",
    }

    last = ""
    for base in STATIC_URLS:
        url = base + "?" + q
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=MAP_TIMEOUT) as r:
                body = r.read()
            if len(body) < 1000 or not body.startswith(b"\x89PNG"):
                last = "{} — PNG 가 아닙니다 ({}바이트): {}".format(
                    base, len(body), body[:200])
                continue
            return body
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                detail = ""
            last = "{} — HTTP {} {}".format(base, e.code, detail)
        except Exception as e:
            last = "{} — {}".format(base, e)

    print("정적지도를 받지 못해 개념도로 그립니다: {}".format(last))
    _FETCH_FAILED = True
    return None


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
             dpi=150, return_mode=False):
    """지도를 그려 base64 data URI 로 돌려준다.

    targets     [(이름, 위도, 경도), ...]  점으로 찍을 대상
    line        [(이름, 위도, 경도), ...]  순서대로 이을 지점들
    line_label  선 가운데에 얹을 글자 (없으면 생략)
    return_mode True 면 (uri, 'map'|'sketch') 를 돌려준다.
                'sketch' 는 배경이 실제 지도가 아니라는 뜻이다.
                부르는 쪽이 캡션 문구를 그걸로 고른다.
    반환         data:image/png;base64,... 또는 None
    """
    pts = [(n, la, ln) for n, la, ln in (targets or [])
           if la is not None and ln is not None]
    seg = [(n, la, ln) for n, la, ln in (line or [])
           if la is not None and ln is not None]
    if len(seg) < 2:
        seg = []
    if not pts and not seg:
        return (None, "") if return_mode else None

    if not _use_korean_font():
        print("한글 폰트를 못 찾아 지도를 건너뜁니다.")
        return (None, "") if return_mode else None
    plt.rcParams["axes.unicode_minus"] = False

    focus = pts + seg
    all_lat = [la for _, la, _ in focus] + [l for _, l, _, _ in LANDMARKS]
    all_lng = [ln for _, _, ln in focus] + [g for _, _, g, _ in LANDMARKS]
    # 선형일 때는 양끝 이름표가 바깥으로 나가므로 여백을 더 둔다
    grow = 0.36 if seg else 0.28
    pad_lat = max(0.014, (max(all_lat) - min(all_lat)) * grow)
    pad_lng = max(0.022, (max(all_lng) - min(all_lng)) * grow)
    lat0, lat1 = min(all_lat) - pad_lat, max(all_lat) + pad_lat
    lng0, lng1 = min(all_lng) - pad_lng, max(all_lng) + pad_lng

    # 담아야 할 범위를 그림 좌표계로 바꾼 뒤 레벨을 고른다
    bx0, bx1 = merc_x(lng0), merc_x(lng1)
    by0, by1 = merc_y(lat1), merc_y(lat0)      # y 는 북쪽이 작다
    center_lat = (lat0 + lat1) / 2.0
    center_lng = (lng0 + lng1) / 2.0
    level = _level_for(bx1 - bx0, by1 - by0)

    png = fetch_basemap(center_lat, center_lng, level)
    mode = "map" if png else "sketch"

    # 그림이 실제로 덮는 범위. 레벨이 정해지면 화면 크기로 정해진다.
    world = TILE * (2 ** level)
    half_x = (MAP_W / 2.0) / world
    half_y = (MAP_H / 2.0) / world
    cx, cy = merc_x(center_lng), merc_y(center_lat)
    x_lo, x_hi = cx - half_x, cx + half_x
    y_lo, y_hi = cy - half_y, cy + half_y       # y_lo 가 북쪽

    # 북쪽을 위로 두기 위해 y 부호를 뒤집어 쓴다
    def px(lng):
        return merc_x(lng)

    def py(lat):
        return -merc_y(lat)

    fig, ax = plt.subplots(figsize=(MAP_W / 100.0, MAP_H / 100.0), dpi=dpi)

    if png:
        try:
            img = mpimg.imread(io.BytesIO(png), format="png")
            ax.imshow(img, extent=(x_lo, x_hi, -y_hi, -y_lo),
                      aspect="auto", zorder=0, interpolation="bilinear")
        except Exception as e:
            print("배경 그림을 못 읽어 개념도로 그립니다: {}".format(e))
            png, mode = None, "sketch"

    if not png:
        ax.set_facecolor("#F7F9FC")
        ax.grid(True, color="#E8EDF5", linewidth=0.8, zorder=0)

    # 실제 지도 위에서는 기준점 글자가 지도 글자와 겹친다.
    # 배경이 있을 때는 흰 테를 둘러 읽히게 한다.
    halo = dict(boxstyle="round,pad=0.18", fc="white", ec="none",
                alpha=0.78) if png else None

    # 기준점
    # 이번 글의 대상과 거의 같은 자리에 있는 기준점은 이름을 생략한다.
    # 핀은 남긴다. 둘 다 적으면 글자끼리 겹쳐 둘 다 못 읽는다.
    # 대상 이름이 이겨야 한다. 기준점은 잣대일 뿐이다.
    for name, la, ln, kind in LANDMARKS:
        if png and kind not in MAP_KEEP:
            continue
        st = STYLE[kind]
        ax.scatter(px(ln), py(la), s=st["size"], c=st["color"],
                   marker=st["marker"], zorder=3,
                   edgecolors="white", linewidths=1.2)
        near = any(dist_km(la, ln, tla, tln) < LABEL_GAP_KM
                   for _, tla, tln in focus)
        if near:
            continue
        ax.annotate(name, (px(ln), py(la)), xytext=(0, -15),
                    textcoords="offset points", ha="center",
                    fontsize=8.5, color="#444" if png else "#555",
                    zorder=4, bbox=halo)

    # 동탄역에서 멀리 떨어진 대상은 거리를 함께 보여준다.
    # 선형일 때는 시점 하나만 잰다. 양끝을 다 이으면 그림이 어지럽다.
    hub = next(((la, ln) for n, la, ln, _ in LANDMARKS if n == "동탄역"), None)
    far_pts = pts if pts else seg[:1]
    if hub:
        for name, la, ln in far_pts:
            km = dist_km(hub[0], hub[1], la, ln)
            if km < FAR_KM:
                continue
            hx, hy = px(hub[1]), py(hub[0])
            tx, ty = px(ln), py(la)
            ax.plot([hx, tx], [hy, ty],
                    linestyle="--", color="#8A93A5" if png else "#B0B8C8",
                    linewidth=1.4, zorder=2)
            # 가운데에 두면 동탄역 이름표와 붙는다. 조금 당겨 놓고
            # 선과 직각으로 비켜 놓는다.
            dx, dy = tx - hx, ty - hy
            dn = math.hypot(dx, dy) or 1.0
            ax.annotate("동탄역 약 {:.0f}km".format(km),
                        (hx + dx * 0.45, hy + dy * 0.45),
                        xytext=(-dy / dn * 16, dx / dn * 16),
                        textcoords="offset points",
                        ha="center", va="center",
                        fontsize=9, color="#5E6878", zorder=5,
                        bbox=dict(boxstyle="round,pad=0.25", fc="white",
                                  ec="#D5DBE5", lw=0.7, alpha=0.92))

    # 선형 현안 — 구간을 긋고 양끝에 이름을 단다
    if seg:
        xs = [px(ln) for _, _, ln in seg]
        ys = [py(la) for _, la, _ in seg]
        ax.plot(xs, ys, color=ROUTE_COLOR, linewidth=4.2, zorder=5,
                solid_capstyle="round", alpha=0.95)
        # 이름표를 선 방향 바깥으로 민다. 가운데에 몰아 두면
        # 양끝 이름과 구간설명이 서로 겹친다.
        # 이제 x·y 가 같은 축척이라 기울기를 보정할 필요가 없다.
        sx = xs[-1] - xs[0]
        sy = ys[-1] - ys[0]
        norm = math.hypot(sx, sy) or 1.0
        ux, uy = sx / norm, sy / norm

        # 미는 거리를 구간 길이에 맞춘다. 늘 38pt 씩 밀었더니
        # 짧은 구간에서는 이름표가 구간 밖 엉뚱한 곳까지 날아갔다.
        span_x = (x_hi - x_lo) if png else (px(lng1) - px(lng0))
        pts_per_unit = (fig.get_size_inches()[0] * 72.0) / (span_x or 1.0)
        push = max(14.0, min(22.0, norm * pts_per_unit * 0.30))

        st = STYLE["route"]
        for i, (name, la, ln) in enumerate(seg):
            ax.scatter(px(ln), py(la), s=st["size"], c=st["color"],
                       marker=st["marker"], zorder=6,
                       edgecolors="white", linewidths=1.8)
            if i == 0:
                ox, oy = -ux * push, -uy * push
            elif i == len(seg) - 1:
                ox, oy = ux * push, uy * push
            else:
                ox, oy = 0, 18          # 중간 경유지는 위로
            ha = "right" if ox < -8 else ("left" if ox > 8 else "center")
            ax.annotate(name, (px(ln), py(la)), xytext=(ox, oy),
                        textcoords="offset points", ha=ha, va="center",
                        fontsize=10.5, fontweight="bold", color=ROUTE_EDGE,
                        zorder=7,
                        bbox=dict(boxstyle="round,pad=0.3", fc="#FFF8E7",
                                  ec=ROUTE_COLOR, lw=0.8, alpha=0.95))
        if line_label:
            m = len(seg) // 2
            if len(seg) % 2 == 0:
                mx = (xs[m - 1] + xs[m]) / 2.0
                my = (ys[m - 1] + ys[m]) / 2.0
            else:
                mx, my = xs[m], ys[m]
            # 선과 직각으로 비켜 놓는다
            ax.annotate(line_label, (mx, my), xytext=(-uy * 62, ux * 62),
                        textcoords="offset points", ha="center", va="center",
                        fontsize=9.5, color="#7A5A12", zorder=7,
                        bbox=dict(boxstyle="round,pad=0.28", fc="white",
                                  ec=ROUTE_COLOR, lw=0.8, alpha=0.95))

    # 이번 글의 대상 (점)
    st = STYLE["target"]
    for name, la, ln in pts:
        ax.scatter(px(ln), py(la), s=st["size"], c=st["color"],
                   marker=st["marker"], zorder=6,
                   edgecolors=ROUTE_EDGE, linewidths=1.0)
        ax.annotate(name, (px(ln), py(la)), xytext=(0, 16),
                    textcoords="offset points", ha="center",
                    fontsize=11, fontweight="bold", color=ROUTE_EDGE,
                    zorder=7,
                    bbox=dict(boxstyle="round,pad=0.3", fc="#FFF8E7",
                              ec=ROUTE_COLOR, lw=0.8, alpha=0.95))

    if png:
        # 배경이 덮는 범위를 그대로 쓴다. 잘라내면 네이버 로고가 사라진다.
        ax.set_xlim(x_lo, x_hi)
        ax.set_ylim(-y_hi, -y_lo)
    else:
        ax.set_xlim(px(lng0), px(lng1))
        ax.set_ylim(py(lat0), py(lat1))
        ax.set_aspect("equal", adjustable="box")
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_color("#DDD")

    if title:
        ax.set_title(title, fontsize=13, color="#1F3C88",
                     weight="bold", pad=12)
    # 개념도라는 안내는 캡션 한 줄로만 붙인다.
    # 그림 안에도 넣었더니 figcaption 과 두 줄로 겹쳤다.

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight",
                facecolor="white")
    plt.close(fig)
    buf.seek(0)
    uri = "data:image/png;base64," + base64.b64encode(buf.read()).decode()
    return (uri, mode) if return_mode else uri


def build_map_figure(agenda, caption=""):
    """collection_result.json 의 agenda 로 지도 HTML 조각을 만든다.

    agenda_lines.csv 에 두 점 이상이 있으면 구간도를,
    없으면 지금까지처럼 위도·경도 한 쌍으로 위치도를 그린다.
    둘 다 없으면 빈 문자열을 돌려준다. 부르는 쪽은 그대로 붙이면 된다.

    캡션은 한 줄이다. 배경이 실제 지도일 때와 개념도일 때 문구가 다르다.
    """
    if not agenda:
        return ""
    name = (agenda.get("현안명") or "").strip() or "현안 위치"
    aid = (agenda.get("id") or agenda.get("ID") or "")

    seg, seg_label = load_line(aid)
    if len(seg) >= 2:
        uri, mode = draw_map([], title=name + " 구간",
                             line=seg, line_label=seg_label,
                             return_mode=True)
        what = "구간"
    else:
        pt = parse_point(agenda)
        if not pt:
            return ""
        uri, mode = draw_map([(name, pt[0], pt[1])], title=name + " 위치",
                             return_mode=True)
        what = "위치"

    if not uri:
        return ""
    if caption:
        cap = caption
    elif mode == "map":
        cap = "{} {} (네이버 지도 · 동탄권 주요 지점 대비)".format(name, what)
    else:
        cap = ("{} {} 개념도 — 실제 축척·도로 형태와 다릅니다"
               .format(name, what))

    return ('<figure style="margin:20px 0;text-align:center;">'
            '<img src="' + uri + '" width="560" '
            'style="width:560px;max-width:100%;border-radius:8px;" '
            'alt="' + cap + '">'
            '<figcaption style="font-size:14px;color:#999;margin-top:6px;">'
            + cap + '</figcaption></figure>')


def probe():
    """키가 실제로 통하는지만 확인한다. 성공하면 받은 그림을 저장한다.

    이 컨테이너 밖에서 한 번은 돌려봐야 한다.
    호스트가 두 가지고 계정에 따라 통하는 쪽이 다르다.
    """
    cid, _ = map_keys()
    print("키:", "있음" if cid else "없음")
    if not cid:
        return 1
    png = fetch_basemap(37.2007, 127.0966, 13)
    if not png:
        print("정적지도 호출 실패 — 위 사유를 보세요.")
        return 1
    with open("map_probe.png", "wb") as f:
        f.write(png)
    print("정적지도 호출 성공 — map_probe.png ({:,}바이트)".format(len(png)))
    return 0


if __name__ == "__main__":
    import sys
    if "--probe" in sys.argv:
        raise SystemExit(probe())

    cid, _ = map_keys()
    print("지도 키:", "있음" if cid else "없음 (개념도로 그립니다)")

    # 메르카토르 변환 점검 — 위도가 커지면 y 는 작아져야 한다
    assert merc_y(37.22) < merc_y(37.17), "메르카토르 y 방향이 뒤집혔습니다"
    assert merc_x(127.09) < merc_x(127.17), "경도 방향이 뒤집혔습니다"
    assert 0 < merc_y(37.2) < 1 and 0 < merc_x(127.1) < 1
    lvl = _level_for(merc_x(127.18) - merc_x(127.05),
                     merc_y(37.23) - merc_y(37.15))
    print("동탄권 전체 레벨:", lvl)
    assert LEVEL_MIN <= lvl <= LEVEL_MAX
    print("좌표 변환 점검: 통과")

    # 점 하나
    uri, mode = draw_map([("동탄 주택공급", 37.2100, 127.0730)],
                         title="동탄 주택공급 위치", return_mode=True)
    print("점 하나:", "성공" if uri else "실패", "/ 배경:", mode)
    if uri:
        raw = base64.b64decode(uri.split(",", 1)[1])
        with open("map_sample.png", "wb") as f:
            f.write(raw)
        print("  map_sample.png 저장 ({:,}바이트)".format(len(raw)))

    # 선
    uri2, mode2 = draw_map([], title="용인 남사~화성 신동 연결도로 구간",
                           line=[("동탄 신동(시점)", 37.1777, 127.1420),
                                 ("남사읍 완장리(종점)", 37.1525, 127.1736)],
                           line_label="함봉산 관통 — 터널 포함 구간",
                           return_mode=True)
    print("선:", "성공" if uri2 else "실패", "/ 배경:", mode2)
    if uri2:
        raw = base64.b64decode(uri2.split(",", 1)[1])
        with open("map_line_sample.png", "wb") as f:
            f.write(raw)
        print("  map_line_sample.png 저장 ({:,}바이트)".format(len(raw)))

    # 캡션은 한 줄인지
    html = build_map_figure({"현안명": "테스트 현안", "id": "ZZZ",
                             "위도": "37.21", "경도": "127.073"})
    print("캡션 줄 수:", html.count("<figcaption"))
    assert html.count("<figcaption") == 1
    assert "개념도" not in html or mode == "sketch"
