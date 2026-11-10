"""官方论坛中文区（分类 86）的官方主题抓取。

只认 National Coordinator（group_id = 47）发起的主题——中文区只有
Monika_in_action 和 Autumn_Maple秋枫 两个人是这个身份，玩家帖一律不碰。

判断依据是论坛 JSON 里 posters[].primary_group_id，不是用户名白名单，
这样以后换人负责中文区，只要工牌还是 47 就自动生效。

中文区最大的坑：94% 的内容是官网新闻的翻译，会和官网源重复。
去重靠标题相似度（见 find_new_forum 的 news_titles 参数）。
"""

from __future__ import annotations

import difflib
import re
from typing import Iterable

import requests

from wtnews import NewsItem

FORUM = "https://forum.warthunder.com"
CATEGORY = "/c/national-communities/86-category/86"
OFFICIAL_GID = 47          # National Coordinator：中文区官方协调员
DUP_THRESHOLD = 0.45       # 标题相似度超过这个值，判为官网新闻的翻译

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept": "application/json", "Accept-Language": "zh-CN,zh;q=0.9"}

_NORM_RE = re.compile(r"[\[\]【】（）()：:·，,。.\-—\s\"'“”‘’!！?？]")


def _norm(text: str) -> str:
    return _NORM_RE.sub("", (text or "")).lower()


def is_duplicate(title: str, news_titles: Iterable[str], threshold: float = DUP_THRESHOLD) -> bool:
    """这条论坛帖是不是官网新闻的中文翻译。"""
    a = _norm(title)
    if not a:
        return False
    for t in news_titles:
        b = _norm(t)
        if not b:
            continue
        if difflib.SequenceMatcher(None, a, b).ratio() >= threshold:
            return True
    return False


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def fetch_official_topics(pages: int = 3, session: requests.Session | None = None) -> list[NewsItem]:
    """抓中文区官方（gid=47）发起的主题。返回 kind='forum' 的 NewsItem。

    每页 30 条，单页失败只告警不中断；某页没有新增官方帖即停止翻页。
    """
    s = session or _session()
    items: list[NewsItem] = []
    for p in range(pages):
        url = f"{FORUM}{CATEGORY}.json" + (f"?page={p}" if p else "")
        try:
            data = s.get(url, timeout=30).json()
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 论坛第 {p + 1} 页抓取失败: {e}")
            break

        topics = (data.get("topic_list") or {}).get("topics") or []
        if not topics:
            break

        before = len(items)
        for t in topics:
            op = next(
                (x for x in (t.get("posters") or [])
                 if "Original Poster" in (x.get("description") or "")),
                None,
            )
            if not op or op.get("primary_group_id") != OFFICIAL_GID:
                continue

            tid = str(t.get("id"))
            if any(i.id == tid for i in items):
                continue

            image = t.get("image_url") or ""
            if image.startswith("//"):
                image = "https:" + image

            items.append(
                NewsItem(
                    id=tid,
                    title=(t.get("title") or "").strip(),
                    url=f"{FORUM}/t/{tid}",
                    image=image,
                    summary="",
                    kind="forum",
                )
            )
        if len(items) == before:      # 本页没有新的官方帖，翻到底了
            break
    return items


def find_new_forum(
    known: Iterable[str],
    dedupe_titles: list[str],
    pages: int = 3,
    session: requests.Session | None = None,
) -> tuple[list[NewsItem], list[str]]:
    """返回 (要推送的论坛帖, 被判为官网镜像而跳过的 key 列表)。

    dedupe_titles 必须是**新闻 + 更新日志**的全部标题。只传新闻标题的话，
    论坛里那 20 多条「2.59.0.92 更新」会全部当成新内容推一遍。
    """
    known_set = {str(k) for k in known}
    fresh: list[NewsItem] = []
    mirrored: list[str] = []
    for t in fetch_official_topics(pages=pages, session=session):
        if t.key in known_set:
            continue
        if is_duplicate(t.title, dedupe_titles):
            mirrored.append(t.key)
            continue
        fresh.append(t)
    return fresh, mirrored


if __name__ == "__main__":
    all_topics = fetch_official_topics(pages=3)
    print(f"中文区官方主题：{len(all_topics)} 条\n")
    for t in all_topics[:12]:
        print(f"  {t.key:<12} {t.title[:56]}")
    print("\n（与官网新闻的重复判断需要 news_titles，由主流程传入）")
