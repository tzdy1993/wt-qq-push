"""QQ 机器人开放平台 API 封装（群聊场景）。

只依赖 requests。AppID / AppSecret 从环境变量读，不要硬编码进仓库。
"""

from __future__ import annotations

import os
import time

import requests

TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
API_PROD = "https://api.sgroup.qq.com"
API_SANDBOX = "https://sandbox.api.sgroup.qq.com"

# 常见错误码 -> 人话
ERROR_HINTS = {
    40034105: "主动消息无权限：群主需在「群机器人 → 设置权限」里开启「允许主动推送消息」",
    11255: "群 openid 无效：请确认传入的是 group_openid，不是群号",
    22009: "消息发送超频：降低发送频率",
    100016: "AppID 或 AppSecret 不正确",
}


def link_button(label: str, url: str, visited: str | None = None,
                unsupport_tips: str | None = None) -> dict:
    """构造一个跳转按钮（keyboard 结构）。

    注意：文档要求按钮模版「申请使用」/ 自定义按钮「内邀开通」。这里直接下发，
    接口可能接受但客户端不一定渲染，属于实验性能力，用之前要在真机上验证。
    """
    return {
        "content": {
            "rows": [
                {
                    "buttons": [
                        {
                            "id": "link",
                            "render_data": {
                                "label": label,
                                "visited_label": visited or label,
                                "style": 1,
                            },
                            "action": {
                                "type": 0,              # 0 = 跳转
                                "permission": {"type": 2},   # 2 = 所有人可点
                                "data": url,
                                "unsupport_tips": unsupport_tips or url,
                            },
                        }
                    ]
                }
            ]
        }
    }


class QQBotError(RuntimeError):
    def __init__(self, code: int | None, message: str, raw: dict | None = None):
        self.code = code
        self.message = message
        self.raw = raw or {}
        hint = ERROR_HINTS.get(code or 0)
        super().__init__(f"[{code}] {message}" + (f" | {hint}" if hint else ""))


class QQBot:
    def __init__(
        self,
        appid: str | None = None,
        secret: str | None = None,
        group_openid: str | None = None,
        sandbox: bool = False,
    ) -> None:
        self.appid = appid or os.environ.get("QQ_APPID", "")
        self.secret = secret or os.environ.get("QQ_SECRET", "")
        # 支持逗号分隔的多个群
        raw = group_openid or os.environ.get("QQ_GROUP_OPENID", "")
        self.group_openids = [g.strip() for g in raw.split(",") if g.strip()]
        self.group_openid = self.group_openids[0] if self.group_openids else ""
        self.base = API_SANDBOX if sandbox else API_PROD
        self._token = ""
        self._expire_at = 0.0
        if not self.appid or not self.secret:
            raise ValueError("缺少 QQ_APPID / QQ_SECRET 环境变量")

    # ---------------- token ----------------
    def token(self, force: bool = False) -> str:
        if not force and self._token and time.time() < self._expire_at - 60:
            return self._token
        r = requests.post(
            TOKEN_URL,
            json={"appId": self.appid, "clientSecret": self.secret},
            timeout=20,
        )
        data = r.json()
        tok = data.get("access_token")
        if not tok:
            raise QQBotError(data.get("code"), data.get("message", "获取 token 失败"), data)
        self._token = tok
        self._expire_at = time.time() + int(data.get("expires_in", 7200))
        return tok

    def _headers(self) -> dict:
        return {"Authorization": f"QQBot {self.token()}", "Content-Type": "application/json"}

    # ---------------- 发送 ----------------
    def send_group(self, payload: dict, group_openid: str | None = None) -> dict:
        gid = group_openid or self.group_openid
        if not gid:
            raise ValueError("缺少 group_openid")
        r = requests.post(
            f"{self.base}/v2/groups/{gid}/messages",
            headers=self._headers(),
            json=payload,
            timeout=20,
        )
        try:
            data = r.json()
        except Exception:  # noqa: BLE001
            data = {"raw": r.text}
        if r.status_code != 200 or "id" not in data:
            raise QQBotError(data.get("code"), data.get("message", r.text[:200]), data)
        return data

    def send_group_text(self, content: str, group_openid: str | None = None) -> dict:
        return self.send_group(
            {"content": content, "msg_type": 0}, group_openid=group_openid
        )

    def send_group_markdown(
        self,
        template_id: str,
        params: list[dict],
        content: str = " ",
        group_openid: str | None = None,
    ) -> dict:
        """markdown 需要先在后台「高级设置」建好模板，拿 custom_template_id。"""
        return self.send_group(
            {
                "msg_type": 2,
                "content": content,
                "markdown": {"custom_template_id": template_id, "params": params},
            },
            group_openid=group_openid,
        )

    # ---------------- 富媒体（图片） ----------------
    def upload_group_image(self, url: str, group_openid: str | None = None) -> str:
        """用公网 URL 直传图片，返回 file_info。

        省掉下载/分片，平台自己去拉这个 URL。file_info 有 ttl，拿到就立刻发。
        """
        gid = group_openid or self.group_openid
        if not gid:
            raise ValueError("缺少 group_openid")
        r = requests.post(
            f"{self.base}/v2/groups/{gid}/files",
            headers=self._headers(),
            json={"file_type": 1, "url": url},   # file_type=1 图片
            timeout=60,
        )
        try:
            data = r.json()
        except Exception:  # noqa: BLE001
            data = {"raw": r.text}
        fi = data.get("file_info")
        if not fi:
            raise QQBotError(data.get("code"), data.get("message", r.text[:200]), data)
        return fi

    def send_group_image(self, file_info: str, group_openid: str | None = None) -> dict:
        """msg_type=7 富媒体。content 必须给个占位值，但不展示。"""
        return self.send_group(
            {"msg_type": 7, "content": " ", "media": {"file_info": file_info}},
            group_openid=group_openid,
        )

    def send_group_image_url(self, url: str, group_openid: str | None = None) -> dict:
        """上传 + 发送一步到位。"""
        return self.send_group_image(
            self.upload_group_image(url, group_openid), group_openid
        )

    def upload_group_image_bytes(
        self, data: bytes, file_name: str = "card.jpg", group_openid: str | None = None
    ) -> str:
        """上传本地图片字节流（分片上传四步），返回 file_info。

        本地生成的图没有公网 URL，URL 直传用不了，只能走：
        upload_prepare → 逐片 PUT → part_finish → files 合并
        """
        import hashlib

        gid = group_openid or self.group_openid
        if not gid:
            raise ValueError("缺少 group_openid")

        md5 = hashlib.md5(data).hexdigest()
        sha1 = hashlib.sha1(data).hexdigest()
        md5_10m = hashlib.md5(data[:10002432]).hexdigest()

        # 1) 预上传
        r = requests.post(
            f"{self.base}/v2/groups/{gid}/upload_prepare",
            headers=self._headers(),
            json={
                "file_type": 1,
                "file_size": str(len(data)),
                "file_name": file_name,
                "md5": md5,
                "sha1": sha1,
                "md5_10m": md5_10m,
            },
            timeout=30,
        )
        prep = r.json()
        if not prep.get("upload_id"):
            raise QQBotError(prep.get("code"), prep.get("message", r.text[:200]), prep)

        upload_id = prep["upload_id"]
        parts = sorted(prep.get("parts") or [], key=lambda p: p["index"])
        fallback_bs = int(prep.get("block_size") or len(data))

        # 2)+3) 逐片 PUT，然后通知分片完成
        offset = 0
        for p in parts:
            bs = int(p.get("block_size") or fallback_bs)
            chunk = data[offset : offset + bs]
            offset += bs
            pr = requests.put(p["presigned_url"], data=chunk, timeout=120)
            if pr.status_code not in (200, 201):
                raise QQBotError(None, f"分片 {p['index']} PUT 失败 HTTP {pr.status_code}")
            fr = requests.post(
                f"{self.base}/v2/groups/{gid}/upload_part_finish",
                headers=self._headers(),
                json={
                    "upload_id": upload_id,
                    "part_index": p["index"],
                    "block_size": str(len(chunk)),
                    "md5": hashlib.md5(chunk).hexdigest(),
                },
                timeout=30,
            )
            if fr.status_code != 200:
                raise QQBotError(fr.json().get("code"), f"分片 {p['index']} 确认失败: {fr.text[:150]}")

        # 4) 合并，拿 file_info
        r = requests.post(
            f"{self.base}/v2/groups/{gid}/files",
            headers=self._headers(),
            json={"file_type": 1, "upload_id": upload_id, "file_name": file_name},
            timeout=60,
        )
        data_json = r.json()
        fi = data_json.get("file_info")
        if not fi:
            raise QQBotError(
                data_json.get("code"), data_json.get("message", r.text[:200]), data_json
            )
        return fi

    def send_group_image_file(self, path, group_openid: str | None = None) -> dict:
        """发本地图片文件。"""
        from pathlib import Path

        p = Path(path)
        fi = self.upload_group_image_bytes(
            p.read_bytes(), p.name, group_openid=group_openid
        )
        return self.send_group_image(fi, group_openid=group_openid)

    def send_group_card(self, path, url: str, group_openid: str | None = None) -> dict:
        """发一张本地卡片图，底部挂「阅读全文」跳转按钮。

        图 + 可点按钮压在同一条消息里。按钮是否渲染取决于客户端和账号权限，
        渲染不出来时图仍然正常显示，属于可接受的降级。
        """
        from pathlib import Path

        p = Path(path)
        fi = self.upload_group_image_bytes(
            p.read_bytes(), p.name, group_openid=group_openid
        )
        return self.send_group(
            {
                "msg_type": 7,
                "content": " ",
                "media": {"file_info": fi},
                "keyboard": link_button("📖 阅读全文", url),
            },
            group_openid=group_openid,
        )

    def send_group_news(self, markdown_text: str, url: str, label: str = "📖 阅读全文",
                        group_openid: str | None = None) -> dict:
        """一条 markdown 消息 + 底部跳转按钮。目前 QQ 群能同时满足
        「单条消息 / 图文排版 / 链接可点」的唯一形态。
        """
        return self.send_group(
            {
                "msg_type": 2,
                "content": " ",
                "markdown": {"content": markdown_text},
                "keyboard": link_button(label, url),
            },
            group_openid=group_openid,
        )

    def send_group_markdown_text(self, markdown_text: str,
                                 group_openid: str | None = None) -> dict:
        """纯 markdown（不带按钮），用于批量合并消息。"""
        return self.send_group(
            {"msg_type": 2, "content": " ", "markdown": {"content": markdown_text}},
            group_openid=group_openid,
        )

    def upload_media_image(self, data: bytes, file_name: str,
                          content_type: str = "image/jpeg") -> str:
        """把图片预上传到 QQ 素材库，返回 qqbot-media:// 引用。

        相比在 markdown 里直接写公网 URL，这条路径不依赖平台去外网拉取，
        不会出现「转存失败 -> 裂图」。素材 90 天滑动过期，单机器人上限 100 张。
        """
        import hashlib

        md5 = hashlib.md5(data).hexdigest()
        sha1 = hashlib.sha1(data).hexdigest()

        r = requests.post(
            f"{self.base}/v2/media-images/prepare",
            headers=self._headers(),
            json={
                "file_name": file_name,
                "file_size": len(data),
                "content_type": content_type,
                "md5": md5,
                "sha1": sha1,
            },
            timeout=30,
        )
        prep = r.json()
        if prep.get("code") == 40035007:          # 素材数量超上限，清理最旧的再试
            self.purge_media_images(keep=80)
            r = requests.post(
                f"{self.base}/v2/media-images/prepare",
                headers=self._headers(),
                json={
                    "file_name": file_name,
                    "file_size": len(data),
                    "content_type": content_type,
                    "md5": md5,
                    "sha1": sha1,
                },
                timeout=30,
            )
            prep = r.json()
        if "upload_id" not in prep:
            raise QQBotError(prep.get("code"), prep.get("message", r.text[:200]), prep)

        pr = requests.put(
            prep["presigned_url"],
            data=data,
            headers={"Content-Type": content_type},   # 必须与 prepare 声明一致，否则 COS 403
            timeout=120,
        )
        if pr.status_code not in (200, 201):
            raise QQBotError(None, f"素材 PUT 失败 HTTP {pr.status_code}")
        etag = (pr.headers.get("ETag") or "").strip('"')

        r = requests.post(
            f"{self.base}/v2/media-images/complete",
            headers=self._headers(),
            json={"upload_id": prep["upload_id"], "md5": etag or md5},
            timeout=30,
        )
        info = r.json()
        url = info.get("url")
        if not url:
            raise QQBotError(info.get("code"), info.get("message", r.text[:200]), info)
        return url

    def upload_media_image_from_url(self, image_url: str) -> str:
        """下载公网图片 -> 上传素材库 -> 返回 qqbot-media:// 引用。"""
        r = requests.get(image_url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        ctype = (r.headers.get("Content-Type") or "image/jpeg").split(";")[0].strip()
        if ctype not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
            ctype = "image/jpeg"
        ext = {"image/jpeg": "jpg", "image/png": "png",
               "image/gif": "gif", "image/webp": "webp"}[ctype]
        return self.upload_media_image(r.content, f"cover.{ext}", ctype)

    def list_media_images(self) -> dict:
        r = requests.get(f"{self.base}/v2/media-images", headers=self._headers(), timeout=20)
        return r.json()

    def purge_media_images(self, keep: int = 80) -> int:
        """素材超过上限时删掉最旧的一批，返回删除数量。"""
        try:
            data = self.list_media_images()
        except Exception:  # noqa: BLE001
            return 0
        images = sorted(data.get("images") or [], key=lambda x: x.get("created_at") or 0)
        removed = 0
        for img in images[: max(0, len(images) - keep)]:
            try:
                requests.delete(
                    f"{self.base}/v2/media-images/{img['image_id']}/delete",
                    headers=self._headers(),
                    timeout=20,
                )
                removed += 1
            except Exception:  # noqa: BLE001
                continue
        return removed

    def whoami(self) -> dict:
        r = requests.get(f"{self.base}/users/@me", headers=self._headers(), timeout=20)
        return r.json()


if __name__ == "__main__":
    bot = QQBot()
    print(bot.whoami())
    print(bot.send_group_text("【测试】QQBot 封装自检"))
