"""战争雷霆官网内容抓取：新闻 + 更新日志。

两个数据源：
- 新闻/开发日志   https://warthunder.com/zh/news/      编号如 2878，短链 /zh/news/2878 可直接访问
- 更新日志        https://warthunder.com/zh/game/changelog/ 编号如 729，必须用完整路径 /current/729

两者编号体系不同，去重 key 用前缀区分：新闻是 "2878"，更新日志是 "cl:729"。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, asdict
from typing import Iterable

import requests
from bs4 import BeautifulSoup

BASE = "https://warthunder.com"
NEWS_LIST = f"{BASE}/zh/news/"
CHANGELOG_LIST = f"{BASE}/zh/game/changelog/"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}

CL_PREFIX = "cl:"
_NEWS_HREF = re.compile(r"^/zh/news/(\d+)-")
_CHANGELOG_HREF = re.compile(r"/zh/game/changelog/current/(\d+)")
_TITLE_TAIL = re.compile(r"\s*-\s*(更新|新闻|News)\s*-\s*游戏?\s*-?\s*War\s*Thun\w*\s*$", re.I)


@dataclass
class NewsItem:
    id: str          # 官网编号
    title: str
    url: str         # 用于按钮跳转
    image: str       # 封面图（更新日志为空）
    summary: str
    kind: str = "news"   # news | changelog

    @property
    def key(self) -> str:
        return self.id if self.kind == "news" else f"{CL_PREFIX}{self.id}"

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


def _meta(soup: BeautifulSoup, prop: str) -> str:
    el = soup.find("meta", attrs={"property": prop}) or soup.find(
        "meta", attrs={"name": prop}
    )
    return (el.get("content") or "").strip() if el else ""


def _clean_title(raw: str) -> str:
    return _TITLE_TAIL.sub("", raw).strip()


# ---------------- 新闻 ----------------
def fetch_list_ids(s: requests.Session, pages: int = 1) -> list[str]:
    """新闻列表编号，页面顺序，最新在前。

    单页抓取失败只告警不中断；某页没有新增编号说明已翻到底，提前结束。
    """
    ids: list[str] = []
    for p in range(1, pages + 1):
        url = NEWS_LIST if p == 1 else f"{BASE}/zh/news/page/{p}/"
        try:
            soup = BeautifulSoup(_get(s, url), "lxml")
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 新闻第 {p} 页抓取失败: {e}")
            break
        before = len(ids)
        for a in soup.select('a[href^="/zh/news/"]'):
            m = _NEWS_HREF.match(a.get("href", ""))
            if m and m.group(1) not in ids:
                ids.append(m.group(1))
        if len(ids) == before:
            break
    return ids


def fetch_detail(s: requests.Session, news_id: str) -> NewsItem:
    url = f"{BASE}/zh/news/{news_id}"
    soup = BeautifulSoup(_get(s, url), "lxml")

    title = _clean_title(_meta(soup, "og:title") or _meta(soup, "twitter:title"))
    image = _meta(soup, "og:image") or _meta(soup, "twitter:image")
    if image.startswith("//"):
        image = "https:" + image
    summary = re.sub(
        r"^和您的朋友一起在最拟真的免费游戏中畅玩！\s*", "", _meta(soup, "description")
    ).strip()

    return NewsItem(
        id=news_id,
        title=title or f"战争雷霆新闻 #{news_id}",
        url=f"{BASE}/zh/news/{news_id}",
        image=image,
        summary=summary,
        kind="news",
    )


# ---------------- 更新日志 ----------------
def fetch_changelog_ids(s: requests.Session, pages: int = 1) -> list[str]:
    """更新日志编号。列表页每条有多个链接指向同一 id，需要去重。

    每页 12 条，单页失败只告警不中断，某页无新增即结束。
    """
    ids: list[str] = []
    for p in range(1, pages + 1):
        url = CHANGELOG_LIST if p == 1 else f"{BASE}/zh/game/changelog/page/{p}/"
        try:
            soup = BeautifulSoup(_get(s, url), "lxml")
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 更新日志第 {p} 页抓取失败: {e}")
            break
        before = len(ids)
        for a in soup.select('a[href*="/game/changelog/current/"]'):
            m = _CHANGELOG_HREF.search(a.get("href", ""))
            if m and m.group(1) not in ids:
                ids.append(m.group(1))
        if len(ids) == before:
            break
    return ids


def fetch_changelog_detail(s: requests.Session, cid: str, summary_len: int = 160) -> NewsItem:
    """更新日志没有专属封面图（og:image 是站点默认图），摘要从 .article 正文抠。"""
    url = f"{BASE}/zh/game/changelog/current/{cid}"
    soup = BeautifulSoup(_get(s, url), "lxml")

    title = _clean_title(_meta(soup, "og:title")) or f"更新日志 #{cid}"

    body = soup.select_one(".article")
    if body:
        # 正文是改动条目清单，换行压成空格，取开头一段
        summary = re.sub(r"\s+", " ", body.get_text(" ", strip=True)).strip()
        if len(summary) > summary_len:
            summary = summary[:summary_len].rstrip() + "…"
    else:
        summary = ""

    return NewsItem(
        id=cid,
        title=title,
        url=url,
        image="",          # 不拿默认图冒充封面
        summary=summary,
        kind="changelog",
    )


# ---------------- 汇总 ----------------
def find_new(
    s: requests.Session | None = None,
    known: Iterable[str] = (),
    pages: int = 1,
    include_changelog: bool = True,
) -> list[NewsItem]:
    """返回 known 里没有的新条目。新闻在前，更新日志在后。"""
    s = s or _session()
    known_set = {str(k) for k in known}
    out: list[NewsItem] = []

    for nid in fetch_list_ids(s, pages=pages):
        if nid in known_set:
            continue
        try:
            out.append(fetch_detail(s, nid))
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 新闻详情抓取失败 {nid}: {e}")

    if include_changelog:
        for cid in fetch_changelog_ids(s, pages=pages):
            if f"{CL_PREFIX}{cid}" in known_set:
                continue
            try:
                out.append(fetch_changelog_detail(s, cid))
            except Exception as e:  # noqa: BLE001
                print(f"[warn] 更新日志抓取失败 {cid}: {e}")

    return out


if __name__ == "__main__":
    sess = _session()
    print("新闻编号:", fetch_list_ids(sess)[:6])
    print("更新日志编号:", fetch_changelog_ids(sess)[:6])
    print()
    for it in find_new(sess, known=[]):
        if it.kind != "changelog":
            continue
        print("-" * 60)
        print("key:", it.key)
        print("标题:", it.title)
        print("链接:", it.url)
        print("摘要:", it.summary[:120])
