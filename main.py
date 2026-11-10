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


# ---------------- 主流程 ----------------
def run_once(dry_run: bool = False, init: bool = False, pages: int = 1) -> int:
    state = load_state()
    known = state["pushed"]
    items = find_new(known=known, pages=pages)

    if not items:
        print("没有新消息")
        if init:
            save_state(state)
        return 0

    # 官网顺序是最新在前，倒着发，让群里最后一条是最新的
    items.reverse()

    if init:
        state["pushed"].extend(i.id for i in items)
        for i in items:
            print(f"[init] 记为已推送 {i.id} {i.title}")
        save_state(state)
        return 0

    bot = None if dry_run else QQBot()
    sent = 0
    for idx, item in enumerate(items):
        text = render(item)
        if dry_run:
            print("-" * 60)
            print(text)
            sent += 1
            continue
        try:
            assert bot is not None
            # 先发封面图，再发文字。图片失败不影响文字，降级继续
            if SEND_IMAGE and item.image:
                try:
                    bot.send_group_image_url(item.image)
                    print(f"[img] {item.id} {item.image[:70]}")
                    time.sleep(IMG_GAP)
                except QQBotError as e:
                    print(f"[img-skip] {item.id} 图片没发出去: {e}", file=sys.stderr)
            bot.send_group_text(text)
            sent += 1
            print(f"[ok] {item.id} {item.title}")
        except QQBotError as e:
            print(f"[fail] {item.id} {item.title} -> {e}", file=sys.stderr)
            # 权限/凭证类的错误重试也没用，直接停下等修
            if e.code in (40034105, 100016, 11255):
                save_state(state)
                return 1
            continue
        state["pushed"].append(item.id)
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
    ap.add_argument("--pages", type=int, default=1, help="列表抓取页数")
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
