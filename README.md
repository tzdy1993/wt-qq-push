# 战争雷霆官方资讯 → QQ 群

抓 [warthunder.com/zh/news](https://warthunder.com/zh/news/) 的最新新闻与开发日志，自动推到 QQ 群。

走 **QQ 机器人开放平台**官方 API，不需要挂 QQ 客户端、不需要小号、不需要服务器常驻。

## 它长什么样

```
【战争雷霆】[开发] H-39：夭折巨兽

德国远洋海军科技树全新顶级战舰——现实中未能完工的 H 级战列舰
(1939 年规划方案) 即将在新版本"鹰之暗影"中加入游戏！

https://warthunder.com/zh/news/2878
```

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

## 想升级成图文卡片

群聊 markdown 已对全部机器人开放，不需要申请模板权限。步骤：

1. 后台「高级设置」建 markdown 模板，拿到 `custom_template_id`
2. 模板里放 `{{.title}}` `{{.summary}}` `{{.url}}` 占位
3. 用 `qqbot.py` 里的 `send_group_markdown()` 发送

纯文本已经能把「标题 + 链接」的需求满足，卡片属于观感优化。缩略图先上传拿 `file_info`，再发 `msg_type=7` 富媒体。
