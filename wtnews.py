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
from datetime import datetime, timezone
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
# 新闻 og:title 尾巴形如「 新闻-War Thuner」，更新日志形如「 -  更新-游戏-War Thunder」
# 注意官网把 Thunder 拼成了 Thuner，用 \w* 兜住
_TITLE_TAIL = re.compile(
    r"\s*(?:-\s*)?(?:更新|新闻|News)\s*(?:-\s*游戏)?\s*-\s*War\s*Thun\w*\s*$", re.I
)


@dataclass
class NewsItem:
    id: str          # 官网编号
    title: str
    url: str         # 用于按钮跳转
    image: str       # 封面图（更新日志为空）
    summary: str
    kind: str = "news"   # news | changelog
    age_hours: float | None = None   # 距抓取时的发布时间（小时）；更新日志无日期字段，为 None

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


_AGE_PATTERNS = [
    (r"刚刚|(\d+)\s*秒", 1 / 3600),      # 刚刚 / X 秒以前
    (r"(\d+)\s*分钟", 1 / 60),
    (r"(\d+)\s*小时", 1),
    (r"(\d+)\s*天", 24),
    (r"(\d+)\s*周", 24 * 7),
    (r"(\d+)\s*个月", 24 * 30),
    (r"(\d+)\s*年", 24 * 365),
]


def parse_age_hours(text: str) -> float | None:
    """把官网的相对时间（'7 小时以前'、'刚刚'、'2 天以前'）换算成小时数。"""
    text = (text or "").strip()
    if not text:
        return None
    if "刚刚" in text:
        return 0.0
    for pat, unit in _AGE_PATTERNS:
        m = re.search(pat, text)
        if m:
            if m.group(1) is None:      # 「刚刚」这类无数字的
                return 0.0
            return int(m.group(1)) * unit
    return None


def _max_numeric(known: set[str], prefix: str = "") -> int:
    """known 里同一编号体系的最大值。新闻传 prefix=''，更新日志传 'cl:'。"""
    nums: list[int] = []
    for k in known:
        if prefix:
            if not k.startswith(prefix):
                continue
            tail = k[len(prefix):]
        else:
            if k.startswith(CL_PREFIX):
                continue
            tail = k
        try:
            nums.append(int(tail))
        except ValueError:
            continue
    return max(nums) if nums else 0


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

    date_el = soup.select_one(".date")
    age = parse_age_hours(date_el.get_text(strip=True)) if date_el else None

    return NewsItem(
        id=news_id,
        title=title or f"战争雷霆新闻 #{news_id}",
        url=f"{BASE}/zh/news/{news_id}",
        image=image,
        summary=summary,
        kind="news",
        age_hours=age,
    )


# ---------------- 更新日志 ----------------
def parse_abs_date(text: str) -> datetime | None:
    """'2026 年 9 月 16 日' -> datetime。"""
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text or "")
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=None)
    except ValueError:
        return None


def fetch_changelog_items(s: requests.Session, pages: int = 1) -> list["NewsItem"]:
    """从列表页直接解析更新日志：标题 / 摘要 / 封面 / 日期全在卡片里。

    不用再逐个访问详情页，省一轮请求，而且列表页才有专属封面和日期
    （详情页 og:image 是站点默认图，也没有日期字段）。
    """
    items: list[NewsItem] = []
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for p in range(1, pages + 1):
        url = CHANGELOG_LIST if p == 1 else f"{BASE}/zh/game/changelog/page/{p}/"
        try:
            soup = BeautifulSoup(_get(s, url), "lxml")
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 更新日志第 {p} 页抓取失败: {e}")
            break
        before = len(items)
        for card in soup.select(".widget"):
            a = card.select_one('a[href*="/game/changelog/current/"]')
            if not a:
                continue
            m = _CHANGELOG_HREF.search(a.get("href", ""))
            if not m:
                continue
            cid = m.group(1)
            if any(i.id == cid for i in items):
                continue

            title_el = card.select_one(".widget__title")
            title = re.sub(r"\s+", " ", title_el.get_text(" ", strip=True)) if title_el else ""
            comment_el = card.select_one(".widget__comment")
            comment = re.sub(r"\s+", " ", comment_el.get_text(" ", strip=True)) if comment_el else ""

            img_el = card.select_one("img")
            image = ""
            if img_el:
                image = img_el.get("data-src") or img_el.get("src") or ""
                if image.startswith("//"):
                    image = "https:" + image

            date_el = card.select_one(".widget-meta__item--right") or card.select_one(".widget__meta li")
            age_hours = None
            if date_el:
                dt = parse_abs_date(date_el.get_text(strip=True))
                if dt:
                    age_hours = max(0.0, (now - dt).total_seconds() / 3600)

            items.append(
                NewsItem(
                    id=cid,
                    title=title or f"更新日志 #{cid}",
                    url=f"{BASE}/zh/game/changelog/current/{cid}",
                    image=image,
                    summary=comment,
                    kind="changelog",
                    age_hours=age_hours,
                )
            )
        if len(items) == before:
            break
    return items


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
    allow_backfill: bool = False,
) -> list[NewsItem]:
    """返回 known 里没有的新条目。新闻在前，更新日志在后。

    allow_backfill=False 时，编号小于「已知最大值」的条目会被判为历史积压并跳过，
    这样翻多页、或者 Actions 停摆很久之后恢复，都不会把一堆旧内容补发进群。
    """
    s = s or _session()
    known_set = {str(k) for k in known}
    out: list[NewsItem] = []

    news_max = _max_numeric(known_set, "")
    for nid in fetch_list_ids(s, pages=pages):
        if nid in known_set:
            continue
        if not allow_backfill and int(nid) <= news_max:
            print(f"[skip] 新闻 {nid} 不晚于已知最新 {news_max}，判为历史积压")
            continue
        try:
            out.append(fetch_detail(s, nid))
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 新闻详情抓取失败 {nid}: {e}")

    if include_changelog:
        cl_max = _max_numeric(known_set, CL_PREFIX)
        for it in fetch_changelog_items(s, pages=pages):
            if it.key in known_set:
                continue
            if not allow_backfill and int(it.id) <= cl_max:
                print(f"[skip] 更新日志 {it.id} 不晚于已知最新 {cl_max}，判为历史积压")
                continue
            out.append(it)

    return out


if __name__ == "__main__":
    sess = _session()
    print("新闻编号:", fetch_list_ids(sess)[:6])
    print()
    for it in fetch_changelog_items(sess, pages=1)[:5]:
        print("-" * 60)
        print("key:", it.key)
        print("标题:", it.title)
        print("日期:", f"{it.age_hours:.0f} 小时前" if it.age_hours is not None else "未知")
        print("封面:", it.image[:95] or "（无）")
        print("摘要:", it.summary[:90])
