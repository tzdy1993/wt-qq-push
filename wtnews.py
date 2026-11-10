"""战争雷霆官网新闻/开发日志抓取。

数据源：https://warthunder.com/zh/news/
- 列表页只取「新闻编号 + slug」，编号是天然的去重键与排序键
- 详情页取 og:title / og:image / og:description，一次只抓新增的，开销小
- 短链 https://warthunder.com/zh/news/<编号> 实测可直接访问（200），OG url 也是它
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, asdict
from typing import Iterable

import requests
from bs4 import BeautifulSoup

BASE = "https://warthunder.com"
LIST_URL = f"{BASE}/zh/news/"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}

_NEWS_HREF = re.compile(r"^/zh/news/(\d+)-")


@dataclass
class NewsItem:
    id: str          # 官网新闻编号，如 "2878"
    title: str
    url: str         # 短链，适合塞进 QQ 消息
    image: str       # 封面图（可能为空）
    summary: str     # 首段摘要（可能为空）

    @property
    def key(self) -> str:
        return self.id

    def to_dict(self) -> dict:
        return asdict(self)


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def _get(s: requests.Session, url: str, retries: int = 3) -> str:
    last: Exception | None = None
    for i in range(retries):
        try:
            r = s.get(url, timeout=25)
            r.raise_for_status()
            r.encoding = "utf-8"   # 官网是 UTF-8，requests 自动猜会猜错导致中文乱码
            return r.text
        except Exception as e:      # noqa: BLE001
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"抓取失败 {url}: {last}")


def fetch_list_ids(s: requests.Session, pages: int = 1) -> list[str]:
    """按页面顺序返回新闻编号，最新在前。"""
    ids: list[str] = []
    for p in range(1, pages + 1):
        url = LIST_URL if p == 1 else f"{BASE}/zh/news/page/{p}/"
        soup = BeautifulSoup(_get(s, url), "lxml")
        for a in soup.select('a[href^="/zh/news/"]'):
            m = _NEWS_HREF.match(a.get("href", ""))
            if m and m.group(1) not in ids:
                ids.append(m.group(1))
        if not ids:
            break
    return ids


def fetch_detail(s: requests.Session, news_id: str) -> NewsItem:
    url = f"{BASE}/zh/news/{news_id}"
    soup = BeautifulSoup(_get(s, url), "lxml")

    def meta(prop: str) -> str:
        el = soup.find("meta", attrs={"property": prop}) or soup.find(
            "meta", attrs={"name": prop}
        )
        return (el.get("content") or "").strip() if el else ""

    title = meta("og:title") or meta("twitter:title")
    # og:title 带站名尾巴，例如「[开发] H-39：夭折巨兽 新闻-War Thuner」
    title = re.sub(r"\s*(新闻|News)\s*-\s*War\s*Thun\w*\s*$", "", title, flags=re.I).strip()

    image = meta("og:image") or meta("twitter:image")
    if image.startswith("//"):
        image = "https:" + image

    summary = meta("description")
    # 官网 description 前面是句固定的宣传语，去掉更有信息量
    summary = re.sub(r"^和您的朋友一起在最拟真的免费游戏中畅玩！\s*", "", summary).strip()

    return NewsItem(
        id=news_id,
        title=title or f"战争雷霆新闻 #{news_id}",
        url=f"{BASE}/zh/news/{news_id}",
        image=image,
        summary=summary,
    )


def find_new(
    s: requests.Session | None = None, known: Iterable[str] = (), pages: int = 1
) -> list[NewsItem]:
    """返回 known 里没有的新条目（按官网顺序，最新在前）。"""
    s = s or _session()
    known_set = {str(k) for k in known}
    fresh_ids = [i for i in fetch_list_ids(s, pages=pages) if i not in known_set]
    out: list[NewsItem] = []
    for nid in fresh_ids:
        try:
            out.append(fetch_detail(s, nid))
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 详情抓取失败 {nid}: {e}")
    return out


if __name__ == "__main__":
    sess = _session()
    print("列表编号:", fetch_list_ids(sess)[:8])
    items = find_new(sess, known=[])
    for it in items[:5]:
        print("-" * 60)
        print("编号:", it.id)
        print("标题:", it.title)
        print("链接:", it.url)
        print("封面:", it.image[:90])
        print("摘要:", it.summary[:100])
