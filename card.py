"""把一条新闻渲染成一张卡片图（封面 + 标题 + 摘要）。

起因：QQ 群机器人 API 里 msg_type=7 发图时 content 会被忽略，markdown 群客户端不渲染，
结构化卡片(Ark)群聊「发 ❌」，所以图文没法放同一条消息。把字画进图里，一条图片消息就够。
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

# 折行单位：英文/数字串整体、任意连续非 ASCII 字符（中文）整体、空白
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\.\-_:/]*|\s+|[^\x00-\x7f]|.", re.S)

W = 940
COVER_H = 530
PAD = 40
BG = (22, 24, 30)
TITLE_COLOR = (255, 255, 255)
SUMMARY_COLOR = (188, 195, 208)
FOOT_COLOR = (120, 130, 148)

FONT_BOLD_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJKsc-Bold.otf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
]
FONT_REG_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
]


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for p in FONT_BOLD_CANDIDATES if bold else FONT_REG_CANDIDATES:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default(size)


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    """按像素宽度折行。以「词」为单位，避免把 1939 拆成 1 / 939、把 H-39 拆散。"""
    tokens = _TOKEN_RE.findall(text)
    lines: list[str] = []
    cur = ""
    for tk in tokens:
        if tk == "\n":
            lines.append(cur.rstrip())
            cur = ""
            continue
        trial = cur + tk
        if not cur or font.getlength(trial.rstrip()) <= max_w:
            cur = trial
        else:
            lines.append(cur.rstrip())
            cur = tk.lstrip()
    if cur.strip():
        lines.append(cur.rstrip())
    return [ln for ln in lines if ln]


def _ellipsis(lines: list[str], keep: int, font: ImageFont.FreeTypeFont) -> list[str]:
    """超出 keep 行就截断并补省略号。"""
    if len(lines) <= keep:
        return lines
    out = lines[:keep]
    last = out[-1]
    while last and font.getlength(last + "…") > (W - PAD * 2):
        last = last[:-1]
    out[-1] = last + "…"
    return out


def _fetch_image(url: str) -> Image.Image | None:
    if not url:
        return None
    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        return Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception:  # noqa: BLE001
        return None


def render_card(item, out_dir: Path | None = None) -> Path:
    """item 需要 .id / .title / .summary / .url / .image。返回生成的 jpg 路径。"""
    title_font = _font(44, bold=True)
    sum_font = _font(27)
    foot_font = _font(22)

    title_lines = _ellipsis(_wrap(item.title, title_font, W - PAD * 2), 2, title_font)
    sum_lines = _ellipsis(_wrap(item.summary or "", sum_font, W - PAD * 2), 2, sum_font)

    lh_title = int(title_font.size * 1.35)
    lh_sum = int(sum_font.size * 1.55)

    text_h = PAD + lh_title * len(title_lines)
    if sum_lines:
        text_h += 14 + lh_sum * len(sum_lines)
    text_h += 22 + int(foot_font.size * 1.4) + PAD

    cover = _fetch_image(item.image)
    if cover:
        cover = cover.resize((W, COVER_H), Image.LANCZOS)
        total_h = COVER_H + text_h
    else:
        total_h = text_h + PAD

    canvas = Image.new("RGB", (W, total_h), BG)
    if cover:
        canvas.paste(cover, (0, 0))
        # 封面底部压一层渐变，和文字区自然衔接
        grad = Image.new("RGBA", (W, 120), (0, 0, 0, 0))
        gd = ImageDraw.Draw(grad)
        for i in range(120):
            gd.line([(0, i), (W, i)], fill=(BG[0], BG[1], BG[2], int(255 * (i / 120) ** 1.6)))
        canvas.paste(Image.new("RGB", (W, 120), BG), (0, COVER_H - 120),
                     grad.split()[3])

    d = ImageDraw.Draw(canvas)
    y = cover and COVER_H or PAD
    y += PAD

    for ln in title_lines:
        d.text((PAD, y), ln, font=title_font, fill=TITLE_COLOR)
        y += lh_title

    if sum_lines:
        y += 14
        for ln in sum_lines:
            d.text((PAD, y), ln, font=sum_font, fill=SUMMARY_COLOR)
            y += lh_sum

    y += 22
    d.text((PAD, y), item.url, font=foot_font, fill=FOOT_COLOR)

    out_dir = out_dir or Path(__file__).resolve().parent / "cards"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{item.id}.jpg"
    canvas.save(path, "JPEG", quality=92, optimize=True)
    return path


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from wtnews import _session, fetch_detail

    s = _session()
    item = fetch_detail(s, sys.argv[1] if len(sys.argv) > 1 else "2878")
    p = render_card(item)
    print(f"{p}  ({p.stat().st_size / 1024:.0f} KB, {Image.open(p).size})")
