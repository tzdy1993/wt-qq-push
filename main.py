"""战争雷霆官方资讯 → QQ 群。

用法：
  python main.py --dry-run     只打印，不发送
  python main.py --init        首次初始化：把当前全部新闻记为已推送，不发消息
  python main.py               正常轮询一次（适合 GitHub Actions）
  python main.py --loop 900    本地常驻，每 900 秒跑一次

环境变量：
  QQ_APPID / QQ_SECRET / QQ_GROUP_OPENID
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from qqbot import QQBot, QQBotError
from wtnews import NewsItem, find_new

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "state.json"
SEND_GAP = 5.0        # 两条消息之间的间隔，别把群刷了
SUMMARY_LIMIT = 150   # 摘要截断长度
IMG_GAP = 1.0         # 图片和紧跟的文字之间
SEND_IMAGE = os.environ.get("QQ_SEND_IMAGE", "1").lower() not in ("0", "false", "no")

# push 形态：
#   markdown  一条 markdown 消息 + 阅读全文按钮（推荐，链接可点）
#   both      封面图 + markdown 两条
#   text      纯文本一条，不带按钮
MODE = os.environ.get("QQ_PUSH_MODE", "markdown").lower()

# 批量合并：默认**关闭**（0），每条单独发，保留封面图 + 标题 + 摘要 + 按钮。
# 只有显式设成 QQ_BURST_THRESHOLD=3 之类才会在超过阈值时合并成一条。
# 合并会丢掉图和摘要，只剩一行标题，按需启用。
BURST_THRESHOLD = int(os.environ.get("QQ_BURST_THRESHOLD", "0"))
BURST_MAX_ITEMS = int(os.environ.get("QQ_BURST_MAX_ITEMS", "20"))

# 时间过滤默认**禁用**（0）。
# 原因：官网页面的日期只精确到「天」，算出来的 age_hours 自带 ±24 小时误差，
# 拿它判断「是不是最近发的」会误杀刚发布的新闻。防重复/防积压靠编号过滤
# （见 wtnews.find_new 里的 _max_numeric），那个是精确的。
# 真要按时间粗筛，设成 168（一周）这种量级才有意义。
MAX_AGE_HOURS = float(os.environ.get("QQ_MAX_AGE_HOURS", "0"))


def _load_env(path: Path = ROOT / ".env") -> None:
    """极简 .env 加载，省一个 python-dotenv 依赖。已存在的环境变量优先。"""
    if not path.exists():
        return
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_env()


# ---------------- state ----------------
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text("utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {"pushed": [], "updated_at": None}


def save_state(state: dict) -> None:
    state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    # 只留最近 500 条，防止无限增长
    state["pushed"] = [str(x) for x in state["pushed"]][-500:]
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------- 渲染 ----------------
def render(item: NewsItem) -> str:
    lines = [f"【战争雷霆】{item.title}"]
    summary = (item.summary or "").strip()
    if summary:
        if len(summary) > SUMMARY_LIMIT:
            summary = summary[:SUMMARY_LIMIT].rstrip() + "…"
        lines.append("")
        lines.append(summary)
    lines.append("")
    lines.append(item.url)
    return "\n".join(lines)


MD_IMG_W = os.environ.get("QQ_MD_IMG_W", "520")
MD_IMG_H = os.environ.get("QQ_MD_IMG_H", "294")
# 更新日志的列表封面是 378x213 缩略图，按原尺寸显示才不糊
MD_CL_IMG_W = os.environ.get("QQ_MD_CL_IMG_W", "378")
MD_CL_IMG_H = os.environ.get("QQ_MD_CL_IMG_H", "213")
MD_IMAGE = os.environ.get("QQ_MD_IMAGE", "1").lower() not in ("0", "false", "no")


def render_markdown(item: NewsItem, image_url: str | None = None) -> str:
    """一条 markdown 消息的正文。

    要点（都是实测出来的）：
    - 图片必须带尺寸 `![alt #600px #338px](url)`，只写 `![]()` 会渲染成空白
    - 图片放最前面，观感比夹在标题和正文之间好
    - 标题用 ##，客户端渲染成大字加粗
    - 链接靠底部按钮，正文里不再重复放裸 URL
    - 更新日志只发标题，正文是条目清单，截在半句上反而难看
    - image_url 传空字符串表示本条不带图（转存失败时降级）
    """
    parts: list[str] = []
    img = item.image if image_url is None else image_url
    if MD_IMAGE and img:
        w, h = (
            (MD_CL_IMG_W, MD_CL_IMG_H)
            if item.kind == "changelog"
            else (MD_IMG_W, MD_IMG_H)
        )
        parts.append(f"![封面 #{w}px #{h}px]({img})")
    parts.append(f"## {item.title}")

    if item.kind != "changelog":
        summary = (item.summary or "").strip()
        if summary:
            if len(summary) > 220:
                summary = summary[:220].rstrip() + "…"
            parts.append(summary)

    return "\n\n".join(parts)


def render_digest(items: list[NewsItem]) -> str:
    """一次来了多条新内容时合并成一条消息，每条标题各自可点。

    版本更新当天官网常常一次性放出好几条（完整更新日志 + 若干开发日志 +
    商店活动），逐条发会把群刷爆，所以超过 BURST_THRESHOLD 就合并。
    """
    ordered = list(reversed(items))          # 最新的排最前
    lines = [f"## 📰 战争雷霆 · {len(items)} 条新内容", ""]
    for it in ordered[:BURST_MAX_ITEMS]:
        lines.append(f"[{it.title}]({it.url})")
        lines.append("")
    if len(items) > BURST_MAX_ITEMS:
        lines.append(f"…另有 {len(items) - BURST_MAX_ITEMS} 条，请到官网查看")
    return "\n".join(lines).strip()


# ---------------- 主流程 ----------------
def run_once(dry_run: bool = False, init: bool = False, pages: int = 2) -> int:
    state = load_state()
    known = state["pushed"]
    items = find_new(known=known, pages=pages)

    if not items:
        print("没有新消息")
        if init:
            save_state(state)
        return 0

    # 时间过滤（默认关闭）：只有显式设置 QQ_MAX_AGE_HOURS 才生效
    stale: list[NewsItem] = []
    if MAX_AGE_HOURS > 0:
        stale = [i for i in items if i.age_hours is not None and i.age_hours > MAX_AGE_HOURS]
    if stale:
        for i in stale:
            state["pushed"].append(i.key)
            print(f"[stale] 跳过 {i.key}《{i.title}》（约 {i.age_hours:.0f} 小时前，精度±24h）")
        items = [i for i in items if i not in stale]
        if not dry_run:
            save_state(state)
        if not items:
            print(f"本次没有 {MAX_AGE_HOURS:.0f} 小时内的新内容，已把 {len(stale)} 条旧内容标记为已读")
            return 0

    # 官网顺序是最新在前，倒着发，让群里最后一条是最新的
    items.reverse()

    if init:
        state["pushed"].extend(i.key for i in items)
        for i in items:
            print(f"[init] 记为已推送 {i.key} {i.title}")
        save_state(state)
        return 0

    bot = None if dry_run else QQBot()

    # 批量合并（默认关闭）：BURST_THRESHOLD>0 且新内容超过阈值才启用
    if BURST_THRESHOLD > 0 and len(items) > BURST_THRESHOLD and MODE in ("markdown", "both"):
        digest = render_digest(items)
        if dry_run:
            print("-" * 60)
            print(f"[digest] 合并 {len(items)} 条")
            print(digest)
        else:
            assert bot is not None
            bot.send_group_markdown_text(digest)
            print(f"[digest] {len(items)} 条新内容合并成 1 条发送")
            for it in items:
                state["pushed"].append(it.key)
            save_state(state)
        return 0

    sent = 0
    for idx, item in enumerate(items):
        text = render_markdown(item) if MODE in ("markdown", "both") else render(item)
        if dry_run:
            print("-" * 60)
            print(f"[mode={MODE}]")
            print(text)
            sent += 1
            continue
        try:
            assert bot is not None
            # both 模式先补一张封面图，失败就跳过，不影响主消息
            if MODE == "both" and item.image:
                try:
                    bot.send_group_image_url(item.image)
                    print(f"[img] {item.id} {item.image[:70]}")
                    time.sleep(IMG_GAP)
                except QQBotError as e:
                    print(f"[img-skip] {item.id} 图片没发出去: {e}", file=sys.stderr)
            if MODE in ("markdown", "both"):
                label = "📖 查看更新日志" if item.kind == "changelog" else "📖 阅读全文"
                # 封面先转存到 QQ 素材库再引用，避免平台拉外链失败导致裂图
                md_img: str | None = None
                if MD_IMAGE and item.image:
                    try:
                        md_img = bot.upload_media_image_from_url(item.image)
                        print(f"[img-up] {item.id} 封面已转存素材库")
                    except Exception as e:  # noqa: BLE001
                        print(f"[img-skip] {item.id} 封面转存失败，本条不带图: {e}",
                              file=sys.stderr)
                        md_img = ""
                bot.send_group_news(render_markdown(item, image_url=md_img),
                                    item.url, label=label)
            else:
                bot.send_group_text(render(item))
            sent += 1
            print(f"[ok] {item.key} {item.title}")
        except QQBotError as e:
            print(f"[fail] {item.id} {item.title} -> {e}", file=sys.stderr)
            # 权限/凭证类的错误重试也没用，直接停下等修
            if e.code in (40034105, 100016, 11255):
                save_state(state)
                return 1
            continue
        state["pushed"].append(item.key)
        save_state(state)
        if idx != len(items) - 1:
            time.sleep(SEND_GAP)

    save_state(state)
    print(f"完成，发送 {sent} 条")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印不发送")
    ap.add_argument("--init", action="store_true", help="把现有新闻全部标记为已推送")
    ap.add_argument("--pages", type=int, default=2,
                    help="每个源抓取页数，默认 2 页（新闻 36 条 / 更新日志 24 条），"
                         "防 Actions 停摆后恢复时漏中间的内容")
    ap.add_argument("--loop", type=int, default=0, help="常驻轮询间隔秒数")
    args = ap.parse_args()

    if args.loop:
        while True:
            try:
                run_once(dry_run=args.dry_run, pages=args.pages)
            except Exception as e:  # noqa: BLE001
                print(f"[error] {e}", file=sys.stderr)
            time.sleep(args.loop)

    return run_once(dry_run=args.dry_run, init=args.init, pages=args.pages)


if __name__ == "__main__":
    raise SystemExit(main())
