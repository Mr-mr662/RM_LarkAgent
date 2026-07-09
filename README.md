# RM Lark Agent

一个基于飞书 Web 账号 Cookie 的个人 AI 助手原型。它会监听飞书消息、记录聊天到 MySQL，并在私聊、`/run` 指令或群聊触发词下调用大模型和 MCP 工具回复。

当前版本用于快速验证类似 Aily 的飞书助手体验，不是正式的飞书开放平台应用。

## 当前能力

- 监听飞书私聊和群聊新消息
- 将文本消息存入 MySQL
- 私聊默认自动回复
- 群聊默认只在 `/run` 或触发词命中时回复
- 回复时带当前会话最近若干条上下文
- 支持大模型自动选择 MCP 工具
- 支持群聊总结、待办提取、历史消息搜索
- 支持天气、时间、运势、发消息等示例工具

## 重要说明

这个项目使用 `LARK_COOKIE` 模拟飞书 Web 登录态：

- Cookie 等同于登录凭证，不要提交到 GitHub，不要发给别人。
- Cookie 会过期，失效后需要重新从飞书网页版复制。
- 该方案适合个人原型验证，不适合团队正式生产环境。
- 如果要做成长期稳定产品，建议迁移到飞书开放平台应用、OAuth、事件订阅和官方 OpenAPI。

## 目录结构

```text
.
├── app/
│   ├── api/                # 飞书账号接口与认证
│   ├── config/             # 环境变量配置
│   ├── core/               # LLM、MCP、消息处理
│   ├── db/                 # SQLAlchemy 模型和会话
│   └── utils/
├── builder/                # 飞书 protobuf 请求构造
├── extension/              # 扩展能力，例如天气
├── static/                 # proto、解密脚本和资源
├── docker-compose.yml
├── Dockerfile
├── main.py
├── requirements.txt
└── .env.example
```

## 快速复现

推荐使用 Docker Compose。mentor 或其他复现者不需要使用你的 `.env`，每个人都应该配置自己的 Cookie 和 API Key。

### 1. 克隆仓库

```bash
git clone <repo-url>
cd RM_LarkAgent
```

如果需要使用当前 MVP 分支：

```bash
git checkout codex/lark-assistant-mvp
```

### 2. 创建配置文件

```bash
cp .env.example .env
```

编辑 `.env`：

```env
DB_HOST=db
DB_PORT=3306
DB_USER=lark_user
DB_PASSWORD=lark_password_123
DB_NAME=lark_messages

LARK_COOKIE='从飞书网页版 Network 请求头中复制的 Cookie'

FUNCTION_TRIGGER_FLAG="/run"
AUTO_REPLY_PRIVATE="true"
AUTO_REPLY_GROUP="false"
GROUP_TRIGGER_KEYWORDS="@助手,@AI Bot,助手"
CONTEXT_MESSAGE_LIMIT="12"

AI_BOT_PREFIX="Lark AI Bot:"

OPENAI_API_KEY="你的 OpenAI 兼容 API Key"
OPENAI_API_BASE_URL="https://api.deepseek.com"
OPENAI_API_MODEL="deepseek-chat"
```

注意：`LARK_COOKIE` 建议用英文单引号包起来，避免 Cookie 里的 `$` 被 Docker Compose 当成环境变量解析。

### 3. 获取 LARK_COOKIE

1. 打开飞书网页版并登录。
2. 打开浏览器开发者工具，进入 Network。
3. 勾选 Disable cache，刷新页面。
4. 找到 `robotega.feishu.cn` 或 `internal-api-lark-api.feishu.cn` 的请求。
5. 在 Headers 的 Request Headers 里复制 `Cookie` 右侧完整值。
6. 粘贴到 `.env` 的 `LARK_COOKIE`。

不要把 Cookie 发给别人，也不要提交到仓库。

### 4. 启动

```bash
docker compose up -d --build
```

如果你的环境使用旧版 Compose：

```bash
docker-compose up -d --build
```

查看日志：

```bash
docker compose logs -f app
```

正常日志会包含：

```text
初始化数据库...
数据库初始化成功.
初始化认证...
认证初始化成功.
创建 Lark 客户端...
Lark 客户端创建成功.
连接到 Lark WebSocket...
开始接收消息...
```

## 使用方式

私聊 Cookie 对应的飞书账号：

```text
你好
```

群聊或私聊使用指令：

```text
/run 现在几点
/run 总结最近聊天
/run 提取最近聊天里的待办
/run 搜索一下 报名
```

群聊默认不会自动回复全部消息。可以使用触发词：

```text
@助手 总结一下刚才讨论
助手 提取最近待办
```

如果 `AUTO_REPLY_GROUP="true"`，群聊所有文本消息都会触发助手回复，谨慎开启。

## 内置工具

| 工具名 | 说明 |
| --- | --- |
| `list_tools` | 列出可用工具 |
| `get_time` | 获取当前时间 |
| `get_weather` | 查询城市天气 |
| `tell_joke` | 讲一个随机笑话 |
| `fortune` | 抽取随机运势 |
| `count_daily_speakers` | 统计今天发言人数 |
| `get_top_speaker_today` | 查询今天发言最多的人 |
| `send_message` | 给指定用户发送私信 |
| `extra_order_from_content` | 提取订单信息 |
| `summarize_recent_chat` | 总结最近聊天 |
| `extract_todos_from_recent_chat` | 从最近聊天提取待办 |
| `search_messages` | 搜索历史入库消息 |

## 配置项

| 变量 | 说明 |
| --- | --- |
| `DB_HOST` | Docker Compose 下使用 `db` |
| `DB_PORT` | MySQL 端口 |
| `DB_USER` | MySQL 用户名 |
| `DB_PASSWORD` | MySQL 密码 |
| `DB_NAME` | MySQL 数据库名 |
| `LARK_COOKIE` | 飞书 Web Cookie |
| `FUNCTION_TRIGGER_FLAG` | 指令触发前缀，默认 `/run` |
| `AUTO_REPLY_PRIVATE` | 私聊是否自动回复 |
| `AUTO_REPLY_GROUP` | 群聊是否自动回复所有消息 |
| `GROUP_TRIGGER_KEYWORDS` | 群聊触发关键词，英文逗号分隔 |
| `CONTEXT_MESSAGE_LIMIT` | 发送给大模型的最近上下文条数 |
| `AI_BOT_PREFIX` | 机器人回复前缀 |
| `OPENAI_API_KEY` | OpenAI 兼容 API Key |
| `OPENAI_API_BASE_URL` | OpenAI 兼容接口地址 |
| `OPENAI_API_MODEL` | 模型名 |

## 开发自定义工具

在 `app/core/mcp_server.py` 中使用 `@register_tool`：

```python
@register_tool(name="get_time", description="Get the current time")
def get_time() -> str:
    now = datetime.datetime.now()
    return f"当前时间是 {now.strftime('%Y-%m-%d %H:%M:%S')}"
```

工具的 `description` 会提供给大模型，用于自动判断何时调用。

## 常见问题

### Docker 提示 5000 端口占用

当前项目不需要暴露 5000 端口，`docker-compose.yml` 已去掉 app 端口映射。如果仍遇到端口冲突，确认使用的是最新配置。

### Docker 提示 `g0 variable is not set`

这是 Cookie 中的 `$g0` 被 Compose 解析成变量。把 `LARK_COOKIE` 改成单引号：

```env
LARK_COOKIE='...$g0...'
```

### 日志显示认证失败

通常是 Cookie 不完整或已过期。重新登录飞书网页版，再复制新的 Request Headers Cookie。

### 收不到回复

检查：

- `docker compose logs -f app` 是否显示开始接收消息
- 消息是否发给 Cookie 对应账号，或发在该账号所在群
- 群聊是否使用 `/run` 或触发词
- `OPENAI_API_KEY`、`OPENAI_API_BASE_URL`、`OPENAI_API_MODEL` 是否正确

## 后续方向

- 接入本地个人知识库
- 为不同群绑定不同知识库
- 增加定时总结和主动提醒
- 从 Cookie 账号助手迁移到飞书开放平台应用
- 使用官方 OAuth 和事件订阅实现多用户授权
