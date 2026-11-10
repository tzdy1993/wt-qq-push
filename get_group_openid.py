"""拿 group_openid 用的一次性脚本。

group_openid 不是群号，必须从事件里取。让机器人先进群，然后在群里 @ 它一次，
本脚本会把事件里的 group_openid 打出来。

用法：
  python get_group_openid.py            # 监听 240 秒
  python get_group_openid.py --timeout 600

环境变量：QQ_APPID / QQ_SECRET
"""

from __future__ import annotations

import argparse
import json
import os
import time

import requests
import websocket

INTENTS_GROUP_AND_C2C = 1 << 25


def get_token(appid: str, secret: str) -> str:
    r = requests.post(
        "https://bots.qq.com/app/getAppAccessToken",
        json={"appId": appid, "clientSecret": secret},
        timeout=20,
    )
    return r.json()["access_token"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=240)
    args = ap.parse_args()

    appid = os.environ.get("QQ_APPID", "")
    secret = os.environ.get("QQ_SECRET", "")
    if not appid or not secret:
        print("请先设置 QQ_APPID / QQ_SECRET")
        return 1

    tok = get_token(appid, secret)
    print("token OK，连接网关…")

    ws = websocket.create_connection("wss://api.sgroup.qq.com/websocket", timeout=30)
    hello = json.loads(ws.recv())
    hb = hello["d"]["heartbeat_interval"] / 1000.0
    ws.send(
        json.dumps(
            {
                "op": 2,
                "d": {
                    "token": f"QQBot {tok}",
                    "intents": INTENTS_GROUP_AND_C2C,
                    "shard": [0, 1],
                    "properties": {},
                },
            }
        )
    )
    print("已连接。现在去群里 @ 机器人 发一句话。\n")

    seq, last_hb = None, time.time()
    deadline = time.time() + args.timeout
    ws.settimeout(1.0)
    while time.time() < deadline:
        if time.time() - last_hb > hb:
            ws.send(json.dumps({"op": 1, "d": seq}))
            last_hb = time.time()
        try:
            raw = ws.recv()
        except websocket.WebSocketTimeoutException:
            continue
        if not raw:
            continue
        ev = json.loads(raw)
        if "s" in ev:
            seq = ev["s"]
        t = ev.get("t")
        if t in ("GROUP_AT_MESSAGE_CREATE", "GROUP_ADD_ROBOT"):
            d = ev.get("d", {})
            gid = d.get("group_openid")
            print(f"事件: {t}")
            print(f"group_openid = {gid}")
            print(f"原始: {json.dumps(d, ensure_ascii=False)[:400]}")
            if t == "GROUP_ADD_ROBOT" and gid:
                print("\n把它写进 .env：QQ_GROUP_OPENID=" + gid)
                return 0
    print("超时，没收到群事件。确认机器人已在群里，并且你 @ 了它。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
