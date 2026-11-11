"""官方论坛中文区（分类 86）官方帖抓取。

只认 National Coordinator（primary_group_id = 47）创建的帖子，也就是
Monika_in_action 和 Autumn_Maple秋枫 两个账号。

抓取走 `topics/created-by/{用户名}` 接口而不是分类列表，原因：
分类列表按「最后回复时间」排序，几个月前的老帖被回复就会浮到顶部，
导致老公告被误判成新内容重复推送。created-by 按创建时间排序，没有这个问题。

判断新旧用主题 id：Discourse 的主题 id 严格自增，比创建时间更可靠。
与官网新闻重复的（中文区大部分是官网内容副本）用标题相似度剔除。
"""

from __future__ import annotations

import difflib
import re
from typing import Iterable
from urllib.parse import quote

import requests

from wtnews import NewsItem

FORUM = "https://forum.warthunder.com"
CATEGORY_ID = 86                 # 中文分论坛
OFFICIAL_GID = 47                # National Coordinator

# 中文区负责发布官方消息的账号。换人就在这里加一个，不用改逻辑。
OFFICIAL_USERS = ["Monika_in_action", "Autumn_Maple秋枫"]

# 标题相似度 >= 这个值就判为「官网内容的论坛副本」而跳过。
# 实测：真副本最低 0.70（H 级战列舰 h-39 型 / H-39：夭折巨兽），
# 论坛独家最高 0.42（计划中的分房权重变动 / 计划中的服务器维护）。
DUP_THRESHOLD = 0.55

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept": "application/json", "Accept-Language": "zh-CN,zh;q=0.9"}

_NORM_RE = re.compile(r"[\[\]【】（）()：:·，,。.\-—\s\"'“”‘’!！?？]")


def _norm(text: str) -> str:
    return _NORM_RE.sub("", (text or "")).lower()


def is_duplicate(title: str, news_titles: Iterable[str], threshold: float = DUP_THRESHOLD) -> bool:
    """这条论坛帖是不是官网内容的副本。"""
    a = _norm(title)
    if not a:
        return False
    for t in news_titles:
        b = _norm(t)
        if b and difflib.SequenceMatcher(None, a, b).ratio() >= threshold:
            return True
    return False


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def fetch_official_topics(pages: int = 1, session: requests.Session | None = None) -> list[NewsItem]:
    """抓两个官方账号创建的中文区主题。

    每人默认取 1 页（30 条），两天内绝不会有 30 条新公告，足够。
    """
    s = session or _session()
    found: dict[str, NewsItem] = {}

    for user in OFFICIAL_USERS:
        for p in range(pages):
            url = f"{FORUM}/topics/created-by/{quote(user)}.json"
            if p:
                url += f"?page={p}"
            try:
                data = s.get(url, timeout=30).json()
            except Exception as e:  # noqa: BLE001
                print(f"[warn] 论坛 {user} 第 {p + 1} 页抓取失败: {e}")
                break

            topics = (data.get("topic_list") or {}).get("topics") or []
            if not topics:
                break

            for t in topics:
                tid = str(t.get("id"))
                if tid in found:
                    continue
                if t.get("category_id") != CATEGORY_ID:
                    continue
                op = next(
                    (x for x in (t.get("posters") or [])
                     if "Original Poster" in (x.get("description") or "")),
                    None,
                )
                if not op or op.get("primary_group_id") != OFFICIAL_GID:
                    continue

                image = t.get("image_url") or ""
                if image.startswith("//"):
                    image = "https:" + image

                found[tid] = NewsItem(
                    id=tid,
                    title=(t.get("title") or "").strip(),
                    url=f"{FORUM}/t/{tid}",
                    image=image,
                    summary="",
                    kind="forum",
                )

    return sorted(found.values(), key=lambda i: int(i.id))


def find_new_forum(
    known: Iterable[str],
    dedupe_titles: list[str],
    pages: int = 1,
    session: requests.Session | None = None,
) -> tuple[list[NewsItem], list[str]]:
    """返回 (要推送的论坛帖, 判为官网副本而跳过的 key 列表)。

    新旧判断：主题 id 大于 state 里已知最大 f: 编号才算新的。
    Discourse 的主题 id 严格自增，这个判断是可靠的。
    """
    known_set = {str(k) for k in known}
    forum_max = max([int(k[2:]) for k in known_set if k.startswith("f:")] or [0])

    fresh: list[NewsItem] = []
    mirrored: list[str] = []
    for t in fetch_official_topics(pages=pages, session=session):
        if t.key in known_set:
            continue
        if int(t.id) <= forum_max:
            continue
        if is_duplicate(t.title, dedupe_titles):
            mirrored.append(t.key)
            continue
        fresh.append(t)

    return fresh, mirrored


if __name__ == "__main__":
    topics = fetch_official_topics(pages=1)
    print(f"官方主题 {len(topics)} 条（id 升序）")
    for t in topics[-8:]:
        print(f"  f:{t.id}  {t.title[:56]}")
