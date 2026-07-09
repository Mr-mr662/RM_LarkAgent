# -*- coding: utf-8 -*-
import os
import sys
import random
import datetime
from loguru import logger
from sqlalchemy import func, desc
from mcp.server.fastmcp import FastMCP
from typing_extensions import Annotated

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app.core.llm_service import LLMService
from app.db.session import get_db_session, close_db_session
from app.db.models import Message
from app.api.auth import get_auth
from app.api.lark_client import LarkClient

mcp = FastMCP("LARK_MCP_SERVER")
registered_tools = []
llm_service = LLMService()


def _clamp_limit(limit, default=20, max_value=100):
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, max_value))


def _format_messages(messages):
    lines = []
    for message in messages:
        timestamp = message.message_time.strftime("%Y-%m-%d %H:%M:%S") if message.message_time else ""
        source = f"群聊:{message.group_name}" if message.is_group_chat and message.group_name else "私聊"
        lines.append(f"{timestamp} [{source}] {message.user_name}: {message.content}")
    return "\n".join(lines)


def _get_recent_messages(chat_id="", limit=30):
    db = get_db_session()
    try:
        query = db.query(Message)
        if chat_id:
            query = query.filter(Message.chat_id == str(chat_id))
        rows = (
            query.order_by(Message.message_time.desc(), Message.id.desc())
            .limit(_clamp_limit(limit, default=30))
            .all()
        )
        rows.reverse()
        return rows
    finally:
        close_db_session(db)


def _summarize_with_llm(system_prompt, content):
    if not llm_service.is_available():
        return "未在配置中设置 OPENAI_API_KEY，无法使用大模型生成结果。"
    res = llm_service.chat_completion(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ],
        tools=None,
    )
    if res and res.choices:
        answer = res.choices[0].message.content
        if answer:
            return answer
    return "未能生成结果。"


def register_tool(name: str, description: str):
    def decorator(func):
        mcp.tool(name=name, description=description)(func)
        registered_tools.append((name, description))
        return func
    return decorator

@register_tool(name="list_tools", description="List all available tools and their descriptions")
def list_tools() -> str:
    result = "🛠️ 当前可用功能列表：\n"
    for name, desc in registered_tools:
        result += f"- `{name}`：{desc}\n"
    return result

@register_tool(name="get_weather", description="获取城市天气")
def get_weather(
    city: Annotated[str, "城市名称"] = "北京"
):
    """
    获取城市天气
    :param city: 城市名称
    :return: 城市天气
    """
    from extension.weather_api.api import get_city_weather
    return get_city_weather(city)

@register_tool(name="extra_order_from_content", description="提取文字中的订单信息，包括订单号、商品名称、数量等，以json格式返回")
def extra_order_from_content(content: str) -> str:
    """
    提取订单信息
    :param content: 消息内容
    :return: 提取的订单信息
    """
    res = llm_service.chat_completion(
        messages=[
            {"role": "user", "content": content},
            {"role": "system", "content": "请提取订单信息，包括订单号、商品名称、数量等，以json格式返回"},
        ],
        tools=None,
        model="qwen-plus"
    )
    if res and res.choices:
        content = res.choices[0].message.content
        if content:
            return content
    return "未能提取到订单信息，请检查消息内容是否包含有效的订单信息。"


@register_tool(
    name="summarize_recent_chat",
    description="总结最近聊天内容。参数 chat_id 可使用当前会话 chat_id，limit 表示最近消息数量"
)
def summarize_recent_chat(
    chat_id: Annotated[str, "会话ID，默认使用当前会话"] = "",
    limit: Annotated[int, "最近消息数量，最多100条"] = 50
) -> str:
    messages = _get_recent_messages(chat_id=chat_id, limit=limit)
    if not messages:
        return "没有找到可总结的聊天记录。"
    content = _format_messages(messages)
    return _summarize_with_llm(
        "你是飞书群聊总结助手。请用中文总结聊天内容，包含：核心结论、重要进展、待确认问题。保持简洁。",
        content
    )


@register_tool(
    name="extract_todos_from_recent_chat",
    description="从最近聊天中提取待办事项。参数 chat_id 可使用当前会话 chat_id，limit 表示最近消息数量"
)
def extract_todos_from_recent_chat(
    chat_id: Annotated[str, "会话ID，默认使用当前会话"] = "",
    limit: Annotated[int, "最近消息数量，最多100条"] = 50
) -> str:
    messages = _get_recent_messages(chat_id=chat_id, limit=limit)
    if not messages:
        return "没有找到可提取待办的聊天记录。"
    content = _format_messages(messages)
    return _summarize_with_llm(
        "你是飞书待办提取助手。请从聊天中提取待办，按「事项 / 负责人 / 截止时间 / 来源」输出。没有明确负责人或时间就写「未明确」。",
        content
    )


@register_tool(
    name="search_messages",
    description="搜索历史聊天消息。keyword 为关键词，chat_id 可限制在当前会话，limit 表示返回条数"
)
def search_messages(
    keyword: Annotated[str, "要搜索的关键词"],
    chat_id: Annotated[str, "会话ID，默认不限制"] = "",
    limit: Annotated[int, "返回条数，最多50条"] = 10
) -> str:
    db = get_db_session()
    try:
        query = db.query(Message).filter(Message.content.ilike(f"%{keyword}%"))
        if chat_id:
            query = query.filter(Message.chat_id == str(chat_id))
        rows = (
            query.order_by(Message.message_time.desc(), Message.id.desc())
            .limit(_clamp_limit(limit, default=10, max_value=50))
            .all()
        )
        rows.reverse()
        if not rows:
            return f"没有搜索到包含「{keyword}」的消息。"
        return _format_messages(rows)
    except Exception as e:
        logger.error(f"搜索历史消息时出错: {str(e)}")
        return f"搜索失败: {str(e)}"
    finally:
        close_db_session(db)


@register_tool(name="tell_joke", description="Tell a random joke")
def tell_joke() -> str:
    jokes = [
        "为什么程序员都喜欢黑色？因为他们不喜欢 bug 光。",
        "Python 和蛇有什么共同点？一旦缠上你就放不下了。",
        "为什么 Java 开发者很少被邀去派对？因为他们总是抛出异常。",
    ]
    return random.choice(jokes)


@register_tool(name="get_time", description="Get the current time")
def get_time() -> str:
    now = datetime.datetime.now()
    return f"当前时间是 {now.strftime('%Y-%m-%d %H:%M:%S')}"


@register_tool(name="fortune", description="Draw a random fortune")
def fortune() -> str:
    fortunes = [
        "大吉：今天适合尝试新事物！✨",
        "中吉：平稳的一天，保持专注。",
        "小吉：会有小惊喜出现～",
        "凶：注意不要过度疲劳。",
        "大凶：小心电子设备出问题 🧯"
    ]
    return random.choice(fortunes)


@register_tool(name="count_daily_speakers", description="获取今天发言的人数统计")
def count_daily_speakers() -> str:
    """查询数据库统计今天有多少人发言"""
    db = get_db_session()
    try:
        today = datetime.datetime.now().date()
        today_start = datetime.datetime.combine(today, datetime.time.min)
        today_end = datetime.datetime.combine(today, datetime.time.max)
        speaker_count = db.query(func.count(func.distinct(Message.user_id)))\
            .filter(Message.message_time >= today_start)\
            .filter(Message.message_time <= today_end)\
            .scalar()
        message_count = db.query(func.count(Message.id))\
            .filter(Message.message_time >= today_start)\
            .filter(Message.message_time <= today_end)\
            .scalar()

        return f"今天已有 {speaker_count} 人发言，共发送了 {message_count} 条消息。"
    except Exception as e:
        logger.error(f"查询今日发言人数时出错: {str(e)}")
        return f"查询失败: {str(e)}"
    finally:
        close_db_session(db)

@register_tool(name="get_top_speaker_today", description="获取今天发言最多的用户")
def get_top_speaker_today() -> str:
    """查询数据库统计今天谁的发言最多"""
    db = get_db_session()
    try:
        today = datetime.datetime.now().date()
        today_start = datetime.datetime.combine(today, datetime.time.min)
        today_end = datetime.datetime.combine(today, datetime.time.max)
        result = db.query(
                Message.user_name,
                Message.user_id,
                func.count(Message.id).label('message_count')
            )\
            .filter(Message.message_time >= today_start)\
            .filter(Message.message_time <= today_end)\
            .group_by(Message.user_id, Message.user_name)\
            .order_by(desc('message_count'))\
            .first()
        if not result:
            return "今天还没有人发言。"
        user_name, user_id, message_count = result
        return f"今日话题王: {user_name}，共发送了 {message_count} 条消息。"
    except Exception as e:
        logger.error(f"查询今日最多发言用户时出错: {str(e)}")
        return f"查询失败: {str(e)}"
    finally:
        close_db_session(db)

@register_tool(name="send_message", description="给指定用户发送消息 {user:用户名称 content:消息内容}")
def send_message(user: str, content: str) -> str:
    """给指定用户发送私信"""
    lark_client = LarkClient(get_auth())
    SearchResponsePacket, userAndGroupIds = lark_client.search_some(user)
    if not userAndGroupIds:
        return f"未找到用户 '{user}'。"
    user_or_group_id = userAndGroupIds[0]
    if user_or_group_id['type'] == 'user':
        logger.info(f'搜索到用户: {user}')
        userId = user_or_group_id['id']
        PutChatResponsePacket, chatId = lark_client.create_chat(userId)
        found_user_name = lark_client.get_other_user_all_name(userId, chatId)
        logger.info(f'用户名称: {found_user_name}')
    else:
        logger.info('搜索到群组')
        chatId = user_or_group_id['id']
        group_name = lark_client.get_group_name(chatId)
        logger.info(f'群组名称: {group_name}')
        return f"'{user}' 是一个群组，不是用户，无法发送私信。"

    _ = lark_client.send_msg(content, chatId)
    return f"成功向 {user} 发送了私信: '{content}'"

if __name__ == "__main__":
    mcp.run(transport="stdio")
