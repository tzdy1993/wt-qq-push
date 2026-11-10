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
        self.group_openid = group_openid or os.environ.get("QQ_GROUP_OPENID", "")
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

    def whoami(self) -> dict:
        r = requests.get(f"{self.base}/users/@me", headers=self._headers(), timeout=20)
        return r.json()


if __name__ == "__main__":
    bot = QQBot()
    print(bot.whoami())
    print(bot.send_group_text("【测试】QQBot 封装自检"))
