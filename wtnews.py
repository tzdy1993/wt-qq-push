"""战争雷霆官网内容抓取：新闻 + 更新日志。

两个源的列表页卡片结构完全一致，标题/摘要/封面/日期都在卡片里，
所以统一走列表页解析，不再逐条访问详情页（更快，而且日期是权威的）。

- 新闻/开发日志   /zh/news/                        编号 2878，短链 /zh/news/2878 可直接访问
- 更新日志        /zh/game/changelog/              编号 729，必须 /current/729

去重 key：新闻 "2878"，更新日志 "cl:729"。

注意：详情页里的 .date 是**评论区时间**，不是发布时间，别拿它做时间过滤。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
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
_TITLE_TAIL = re.compile(
    r"\s*(?:-\s*)?(?:更新|新闻|News)\s*(?:-\s*游戏)?\s*-\s*War\s*Thun\w*\s*$", re.I
)
_DATE_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")


@dataclass
class NewsItem:
    id: str
    title: str
    url: str
    image: str
    summary: str
    kind: str = "news"              # news | changelog
    # 官网页面的日期只精确到「天」。这个值由「日期 00:00」推算，误差最大 ±24 小时，
    # 不能用来判断「几小时前发布」。新旧判断一律看 self.id 编号，不要用这个字段。
    age_hours: float | None = None

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


def _clean_title(raw: str) -> str:
    return _TITLE_TAIL.sub("", raw or "").strip()


def parse_abs_date(text: str) -> datetime | None:
    """'2026 年 9 月 16 日' -> naive datetime。"""
    m = _DATE_RE.search(text or "")
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


# 官网卡片日期只精确到「天」，且按北京时间显示；换算 UTC 要减 8 小时。
# 因为精度只有天，算出来的 age_hours 自带 ±24 小时误差，只能做粗筛，
# 绝不能当成精确发布时间用。
TZ_OFFSET_HOURS = 8


def age_from_date(date_text: str, now_utc: datetime) -> float | None:
    dt = parse_abs_date(date_text)
    if dt is None:
        return None
    dt_utc = dt - timedelta(hours=TZ_OFFSET_HOURS)
    return max(0.0, (now_utc - dt_utc).total_seconds() / 3600)


def _parse_card(
    card: BeautifulSoup, href_re: re.Pattern, kind: str, url_fmt: str,
    now: datetime,
) -> NewsItem | None:
    """从列表页卡片里一次性取齐所有字段。新闻和更新日志共用。"""
    a = card.select_one("a[href]")
    if not a:
        return None
    m = href_re.search(a.get("href", ""))
    if not m:
        return None
    item_id = m.group(1)

    title_el = card.select_one(".widget__title")
    title = _clean_title(_squash(title_el.get_text(" ", strip=True))) if title_el else ""

    comment_el = card.select_one(".widget__comment")
    summary = _squash(comment_el.get_text(" ", strip=True)) if comment_el else ""

    image = ""
    img_el = card.select_one("img")
    if img_el:
        image = img_el.get("data-src") or img_el.get("src") or ""
        if image.startswith("//"):
            image = "https:" + image

    age_hours = None
    date_el = (
        card.select_one(".widget-meta__item--right")
        or card.select_one(".widget__meta li:last-child")
    )
    if date_el:
        age_hours = age_from_date(date_el.get_text(strip=True), now)

    return NewsItem(
        id=item_id,
        title=title or f"{'更新日志' if kind == 'changelog' else '新闻'} #{item_id}",
        url=url_fmt.format(id=item_id),
        image=image,
        summary=summary,
        kind=kind,
        age_hours=age_hours,
    )


def _collect(
    s: requests.Session, first_url: str, page_fmt: str, href_re: re.Pattern,
    kind: str, url_fmt: str, pages: int, label: str,
) -> list[NewsItem]:
    items: list[NewsItem] = []
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for p in range(1, pages + 1):
        url = first_url if p == 1 else page_fmt.format(page=p)
        try:
            soup = BeautifulSoup(_get(s, url), "lxml")
        except Exception as e:  # noqa: BLE001
            print(f"[warn] {label}第 {p} 页抓取失败: {e}")
            break
        before = len(items)
        for card in soup.select(".widget"):
            item = _parse_card(card, href_re, kind, url_fmt, now)
            if item and all(i.id != item.id for i in items):
                items.append(item)
        if len(items) == before:      # 本页没有新增，说明翻到底了
            break
    return items


def fetch_news_items(s: requests.Session, pages: int = 1) -> list[NewsItem]:
    return _collect(
        s, NEWS_LIST, f"{BASE}/zh/news/page/{{page}}/", _NEWS_HREF,
        "news", f"{BASE}/zh/news/{{id}}", pages, "新闻",
    )


def fetch_changelog_items(s: requests.Session, pages: int = 1) -> list[NewsItem]:
    return _collect(
        s, CHANGELOG_LIST, f"{BASE}/zh/game/changelog/page/{{page}}/", _CHANGELOG_HREF,
        "changelog", f"{BASE}/zh/game/changelog/current/{{id}}", pages, "更新日志",
    )


def _max_numeric(known: set[str], prefix: str = "") -> int:
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


def find_new(
    s: requests.Session | None = None,
    known: Iterable[str] = (),
    pages: int = 1,
    include_changelog: bool = True,
    allow_backfill: bool = False,
) -> list[NewsItem]:
    """返回 known 里没有的新条目。新闻在前，更新日志在后。

    allow_backfill=False 时，编号小于「已知最大值」的条目会被判为历史积压并跳过，
    这样翻多页、或 Actions 停摆很久之后恢复，都不会把一堆旧内容补发进群。
    """
    s = s or _session()
    known_set = {str(k) for k in known}
    out: list[NewsItem] = []

    news_max = _max_numeric(known_set, "")
    for it in fetch_news_items(s, pages=pages):
        if it.key in known_set:
            continue
        if not allow_backfill and int(it.id) <= news_max:
            print(f"[skip] 新闻 {it.id} 不晚于已知最新 {news_max}，判为历史积压")
            continue
        out.append(it)

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
    print("=== 新闻（只按编号判断新旧，不看日期）===")
    for it in fetch_news_items(sess, pages=1)[:5]:
        print(f"  {it.id}  {it.title[:52]}")
    print("\n=== 更新日志 ===")
    for it in fetch_changelog_items(sess, pages=1)[:5]:
        print(f"  {it.key}  {it.title[:52]}")
