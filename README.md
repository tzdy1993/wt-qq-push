# 战争雷霆官方资讯 → QQ 群

抓 [warthunder.com/zh/news](https://warthunder.com/zh/news/) 的最新新闻与开发日志，自动推到 QQ 群。

走 **QQ 机器人开放平台**官方 API，不需要挂 QQ 客户端、不需要小号、不需要服务器常驻。

## 它长什么样

每条新闻发两条：封面图 + 文字。

```
[图片] 官网封面（940x530）

【战争雷霆】[开发] H-39：夭折巨兽

德国远洋海军科技树全新顶级战舰——现实中未能完工的 H 级战列舰
(1939 年规划方案) 即将在新版本"鹰之暗影"中加入游戏！

https://warthunder.com/zh/news/2878
```

图片走 `msg_type=7` 富媒体，用官网封面图的公网 URL 直传（`/v2/groups/{openid}/files` 带 `file_type=1`），不用下载、不用分片、不用本地渲染。文字走 `msg_type=0`。

设 `QQ_SEND_IMAGE=0` 可关掉图片，只发文字。

## 文件

| 文件 | 作用 |
|---|---|
| `wtnews.py` | 抓官网列表 + 详情，解析标题 / 封面 / 摘要 / 短链 |
| `qqbot.py` | QQ 机器人 API 封装：token 管理、群消息、markdown |
| `main.py` | 主流程：抓取 → 去重 → 渲染 → 发送 → 记账 |
| `get_group_openid.py` | 一次性脚本，拿群的 `group_openid` |
| `state.json` | 已推送的新闻编号（去重靠它） |

## 本地跑

```powershell
pip install -r requirements.txt

# 看效果，不发消息
python main.py --dry-run

# 首次：把现有新闻全部标记为已推送，避免一上线就刷屏
python main.py --init

# 正常推送一次
python main.py

# 常驻，每 15 分钟一轮
python main.py --loop 900
```

凭证走环境变量：

```powershell
$env:QQ_APPID="..."
$env:QQ_SECRET="..."
$env:QQ_GROUP_OPENID="..."
```

## 部署到 GitHub Actions（零服务器）

1. 把项目推到 GitHub 私有仓库
2. `Settings → Secrets and variables → Actions`，加三个 secret：
   - `QQ_APPID`
   - `QQ_SECRET`
   - `QQ_GROUP_OPENID`
3. `Settings → Actions → General → Workflow permissions` 选 **Read and write permissions**（要提交 `state.json` 回去）
4. 先手动跑一次 `WT News → QQ`（`workflow_dispatch`）验证，之后每 15 分钟自动跑

> 私有仓库每月 2000 Actions 分钟。15 分钟一轮、每轮约 30 秒，月耗约 1440 分钟，够用。
> 想更省就把 cron 改成 `*/20` 或 `*/30`。

## 首次接入 checklist

1. 在 [QQ 开放平台](https://q.qq.com/) 创建机器人，完成个人主体认证
2. 手机 QQ 把机器人拉进群
3. **群主**在手机 QQ「群机器人 → 设置权限」里开启 **允许主动推送消息**
   —— 不开会一直返回 `40034105 主动消息失败, 无权限`
4. 群里 @ 机器人一句话，用 `get_group_openid.py` 抓 `group_openid`
5. 发一条测试消息验证

## 踩过的坑

| 现象 | 原因 |
|---|---|
| `100016 invalid appid or secret` | AppSecret 复制不全。标准长度 **32 位**，去后台重置后走复制按钮 |
| `11255 请求的资源不存在(用户/群已注销)` | 把群号当 `group_openid` 传了。两者不是一个东西 |
| `40034105 主动消息失败, 无权限` | 群主没开「允许主动推送消息」 |
| 中文抓出来是乱码 | 官网是 UTF-8，`requests` 自动猜会猜错，必须 `r.encoding = "utf-8"` |
| 首次上线刷屏 | 忘了跑 `--init` |

## 已知限制

- **个人主体机器人只能在「自己是群主 / 管理员」的群里用**，不支持公开添加、不能上架机器人市场
- 主动消息频控：单群 20 条/分钟、1000 条/群/天，对新闻推送完全够
- 链接实测**不需要**配 URL 白名单就可以直接发（`warthunder.com`）
- 官网改版会让 `.widget` / `og:` 这类选择器失效，`wtnews.py` 里集中改一处即可

## 为什么不用 markdown

`msg_type=2` 在文档里标着群聊可用，但 **QQ 群客户端不渲染 markdown**，发出去要么是原始文本要么干脆不显示。所以图文卡片走的是「封面图（`msg_type=7`）+ 文字（`msg_type=0`）」两条消息的组合。

封面图直接用官网 `og:image`，尺寸 940x530，在手机 QQ 里显示效果正好。

## 消息条数预算

一条新闻 = 2 条主动消息。群聊主动消息限 1000 条/群/天，够发 500 条新闻，战争雷霆一天最多十几条，绰绰有余。

如果介意一个更新季连发好几条占版面，可以用 `state.json` 的 `pushed` 只推「开发日志」类（官网 `?tags=开发` 支持按标签过滤）。
