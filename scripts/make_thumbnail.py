# -*- coding: utf-8 -*-
"""
블로그 썸네일 생성 모듈 (파이프라인용).

- 배경: assets/thumbnail_bg.png (없으면 파스텔 그라데이션으로 대체)
- 제목 길이에 따라 폰트 크기 / 줄간격 / 시작 위치 자동 조절
- 키워드 해시로 크롭 구도를 매번 다르게
"""
import os
import hashlib
from PIL import Image, ImageDraw, ImageFont, ImageEnhance

# 실행 환경마다 설치된 한글 폰트가 다르므로, 후보를 순서대로 탐색해서 사용 가능한 것을 쓴다.
# (index는 TTC 컬렉션 내 한국어 폰트 위치. None이면 단일 TTF)
_FONT_CANDIDATES = {
    "black": [
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc", 1),
        ("/usr/share/fonts/truetype/noto/NotoSansCJK-Black.ttc", 1),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 1),
        ("/usr/share/fonts/truetype/nanum/NanumGothicExtraBold.ttf", None),
        ("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf", None),
        ("/usr/share/fonts/truetype/nanum/NanumBarunGothicBold.ttf", None),
    ],
    "bold": [
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 1),
        ("/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc", 1),
        ("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf", None),
        ("/usr/share/fonts/truetype/nanum/NanumBarunGothicBold.ttf", None),
        ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", None),
    ],
    "medium": [
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc", 1),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 1),
        ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", None),
        ("/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf", None),
    ],
}

_resolved = {}


def _resolve(weight):
    """해당 굵기에서 실제로 로드 가능한 폰트 (경로, index)를 찾아 캐시한다."""
    if weight in _resolved:
        return _resolved[weight]
    for path, idx in _FONT_CANDIDATES[weight]:
        if not os.path.exists(path):
            continue
        try:
            if idx is None:
                ImageFont.truetype(path, 20)
            else:
                ImageFont.truetype(path, 20, index=idx)
            _resolved[weight] = (path, idx)
            return _resolved[weight]
        except Exception:
            continue
    # 어떤 후보도 없으면, 시스템에서 한글 폰트를 한 번 더 훑는다
    for root in ("/usr/share/fonts", "/usr/local/share/fonts"):
        for dirpath, _, files in os.walk(root):
            for fn in files:
                low = fn.lower()
                if ("nanum" in low or "noto" in low) and low.endswith((".ttf", ".otf", ".ttc")):
                    p = os.path.join(dirpath, fn)
                    for try_idx in (1, 0, None):
                        try:
                            if try_idx is None:
                                ImageFont.truetype(p, 20)
                            else:
                                ImageFont.truetype(p, 20, index=try_idx)
                            _resolved[weight] = (p, try_idx)
                            return _resolved[weight]
                        except Exception:
                            continue
    _resolved[weight] = (None, None)
    return _resolved[weight]


FONT_BLACK = "black"
FONT_BOLD = "bold"
FONT_MED = "medium"

W = H = 1000

INK = (32, 54, 108)
ACCENT = (231, 126, 96)
BADGE = (255, 214, 165)
SKY_TOP = (206, 228, 246)
SKY_BOT = (247, 244, 236)

BG_PATH = os.path.join("assets", "thumbnail_bg.png")

MARGIN_X = 60
MAX_TEXT_W = W - MARGIN_X * 2


def font(weight, size, index=None):
    """weight = 'black' | 'bold' | 'medium'"""
    path, idx = _resolve(weight)
    if path is None:
        return ImageFont.load_default()
    if idx is None:
        return ImageFont.truetype(path, size)
    return ImageFont.truetype(path, size, index=idx)


def text_w(draw, s, f):
    b = draw.textbbox((0, 0), s, font=f)
    return b[2] - b[0]


def wrap_by_width(draw, text, f, max_w):
    lines, cur = [], ""
    for ch in text:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        if text_w(draw, cur + ch, f) <= max_w:
            cur += ch
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines


def fit_headline(draw, headline, max_size=92, min_size=54, max_lines=4):
    """제목 길이에 맞춰 폰트 크기를 자동으로 줄여가며 줄바꿈 결과를 확정한다."""
    for size in range(max_size, min_size - 1, -2):
        f = font(FONT_BLACK, size)
        lines = []
        for raw in headline.split("\n"):
            lines.extend(wrap_by_width(draw, raw, f, MAX_TEXT_W))
        if len(lines) <= max_lines:
            return f, lines, size
    f = font(FONT_BLACK, min_size)
    lines = []
    for raw in headline.split("\n"):
        lines.extend(wrap_by_width(draw, raw, f, MAX_TEXT_W))
    return f, lines[:max_lines], min_size


def prepare_background(bg_path=BG_PATH, shift_x=0.5, shift_y=0.5, zoom=1.0):
    """배경 이미지를 1000x1000으로 크롭·확대하고 파스텔 보정. 없으면 그라데이션."""
    if not bg_path or not os.path.exists(bg_path):
        im = Image.new("RGB", (W, H), SKY_TOP)
        d = ImageDraw.Draw(im)
        for y in range(H):
            t = y / H
            d.line([(0, y), (W, y)], fill=(
                int(SKY_TOP[0] + (SKY_BOT[0] - SKY_TOP[0]) * t),
                int(SKY_TOP[1] + (SKY_BOT[1] - SKY_TOP[1]) * t),
                int(SKY_TOP[2] + (SKY_BOT[2] - SKY_TOP[2]) * t),
            ))
        return im

    im = Image.open(bg_path).convert("RGB")
    w, h = im.size
    window = int(min(w, h) / max(1.0, zoom))
    left = max(0, min(w - window, int((w - window) * shift_x)))
    top = max(0, min(h - window, int((h - window) * shift_y)))
    im = im.crop((left, top, left + window, top + window)).resize((W, H), Image.LANCZOS)

    im = ImageEnhance.Color(im).enhance(0.78)
    im = ImageEnhance.Brightness(im).enhance(1.10)
    im = ImageEnhance.Contrast(im).enhance(0.92)
    im = Image.blend(im, Image.new("RGB", (W, H), (255, 252, 246)), 0.16)
    return im


def crop_params_from_key(key):
    """키워드 문자열로부터 매번 다른(그러나 재현 가능한) 크롭 구도를 만든다."""
    hv = int(hashlib.md5(key.encode("utf-8")).hexdigest()[:8], 16)
    zoom = 1.0 + (hv % 55) / 100.0          # 1.00 ~ 1.54
    shift_x = 0.25 + ((hv >> 8) % 50) / 100.0   # 0.25 ~ 0.74
    shift_y = 0.45 + ((hv >> 16) % 45) / 100.0  # 0.45 ~ 0.89
    return zoom, shift_x, shift_y


def make_thumbnail(out_path, label, headline, highlight=None, sub=None,
                   bg_path=BG_PATH, crop_key=None,
                   zoom=None, shift_x=None, shift_y=None):
    if crop_key and (zoom is None):
        zoom, shift_x, shift_y = crop_params_from_key(crop_key)
    zoom = 1.0 if zoom is None else zoom
    shift_x = 0.5 if shift_x is None else shift_x
    shift_y = 0.5 if shift_y is None else shift_y

    img = prepare_background(bg_path, shift_x=shift_x, shift_y=shift_y, zoom=zoom)
    d = ImageDraw.Draw(img)

    # 제목 폰트 자동 맞춤
    f_head, lines, size = fit_headline(d, headline)
    line_h = int(size * 1.26)

    # 줄 수에 따라 본문 블록 세로 위치 조정 (뱃지 아래 ~ 하단 여백 사이 배치)
    block_h = len(lines) * line_h + (74 if sub else 0)
    start_y = max(200, int((H - block_h) * 0.42))

    # 텍스트 영역만큼 화이트 스크림
    scrim_to = min(H, start_y + block_h + 90)
    overlay = Image.new("RGBA", (W, H), (255, 255, 255, 0))
    od = ImageDraw.Draw(overlay)
    for y in range(0, scrim_to):
        a = int(206 * (1 - (y / scrim_to) ** 1.7))
        od.line([(0, y), (W, y)], fill=(255, 255, 255, a))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    d = ImageDraw.Draw(img)

    # 카테고리 뱃지
    f_label = font(FONT_BOLD, 40)
    lw = text_w(d, label, f_label)
    d.rounded_rectangle([MARGIN_X, 68, MARGIN_X + lw + 52, 140], radius=14, fill=BADGE)
    d.text((MARGIN_X + 26, 84), label, font=f_label, fill=(122, 74, 40))

    # 헤드라인
    for i, line in enumerate(lines):
        y = start_y + i * line_h
        if highlight and highlight in line:
            before = line.split(highlight)[0]
            bx = MARGIN_X + text_w(d, before, f_head)
            hw = text_w(d, highlight, f_head)
            d.rounded_rectangle(
                [bx - 12, y + int(size * 0.11), bx + hw + 12, y + int(size * 1.11)],
                radius=12, fill=ACCENT,
            )
            d.text((MARGIN_X, y), before, font=f_head, fill=INK)
            d.text((bx, y), highlight, font=f_head, fill=(255, 255, 255))
            rest = line.split(highlight, 1)[1]
            if rest:
                d.text((bx + hw, y), rest, font=f_head, fill=INK)
        else:
            d.text((MARGIN_X, y), line, font=f_head, fill=INK)

    # 보조 문구
    if sub:
        f_sub = font(FONT_MED, max(34, int(size * 0.48)))
        sy = start_y + len(lines) * line_h + 24
        sw = text_w(d, sub, f_sub)
        pill = Image.new("RGBA", (W, H), (255, 255, 255, 0))
        pd = ImageDraw.Draw(pill)
        pd.rounded_rectangle([MARGIN_X - 8, sy - 8, MARGIN_X + 48 + sw, sy + 66],
                             radius=16, fill=(255, 255, 255, 222))
        img = Image.alpha_composite(img.convert("RGBA"), pill).convert("RGB")
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([MARGIN_X + 10, sy + 8, MARGIN_X + 19, sy + 58], radius=4, fill=ACCENT)
        d.text((MARGIN_X + 40, sy), sub, font=f_sub, fill=INK)

    img.save(out_path, quality=95)
    return out_path
