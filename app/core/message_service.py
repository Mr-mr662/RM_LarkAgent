"""
Service for processing and storing Lark messages
"""
import json
import os
import re

from loguru import logger
from datetime import datetime
from app.db.models import Message
from app.db.session import get_db_session, close_db_session
from app.config.settings import settings
from app.core.llm_service import LLMService
from fastmcp import Client
from fastmcp.client.transports import PythonStdioTransport

class MessageService:
    """Service for processing and storing messages"""
    
    def __init__(self, lark_client):
        self.lark_client = lark_client
        self.db = get_db_session()
        self.llm_service = LLMService()
        self.mcp_transport = PythonStdioTransport("app/core/mcp_server.py", env={"PATHEXT": os.environ.get("PATHEXT", "")})
        self.system_prompt = (
            "你是一个类似 Aily 的飞书 AI 工作助手，运行在飞书聊天中。"
            "你可以根据用户问题回答、总结聊天、提取待办、查询历史消息，并在需要时调用 tools 中定义的函数。"
            "回答要简洁、可靠、适合直接发回飞书。"
            "如果用户要求你操作飞书或查询聊天记录，优先使用工具。"
        )
    
    async def process_message(self, user_name, user_id, content, is_group_chat, group_name, chat_id):
        """Process and store a message in the database"""
        try:
            message_source = f"群聊 {group_name}" if is_group_chat else "私聊"
            logger.info(f"收到{message_source}消息 - 用户: {user_name}, 内容: {content}")
            if not content or not content.strip():
                logger.warning("收到非纯文本消息，跳过存储")
                return
            message = Message(
                user_name=user_name,
                user_id=user_id,
                content=content,
                is_group_chat=is_group_chat,
                group_name=group_name,
                chat_id=chat_id,
                message_time=datetime.now()
            )
            self.db.add(message)
            self.db.commit()

            if self._should_ignore_message(user_id):
                return

            should_reply, query = self._resolve_reply_query(content, is_group_chat)
            if should_reply:
                await self._handle_assistant_request(user_name, query, chat_id, is_group_chat)
        except Exception as e:
            self.db.rollback()
            logger.error(f"存储消息时出错: {str(e)}")

    def _should_ignore_message(self, user_id):
        """Avoid replying to messages sent by the account that runs the assistant."""
        return str(user_id) == str(getattr(self.lark_client, "me_id", ""))

    def _resolve_reply_query(self, content, is_group_chat):
        """Decide whether the assistant should respond and normalize the user query."""
        stripped = content.strip()
        if stripped.startswith(settings.FUNCTION_TRIGGER_FLAG):
            query = stripped[len(settings.FUNCTION_TRIGGER_FLAG):].strip()
            return True, query or "请根据当前聊天上下文提供帮助。"

        if not is_group_chat:
            return settings.AUTO_REPLY_PRIVATE, stripped

        if settings.AUTO_REPLY_GROUP:
            return True, stripped

        matched_keyword = next(
            (keyword for keyword in settings.GROUP_TRIGGER_KEYWORDS if keyword in stripped),
            None
        )
        if matched_keyword:
            query = stripped.replace(matched_keyword, "", 1).strip()
            query = re.sub(r"^[:,，：\s]+", "", query)
            return True, query or "请根据当前群聊上下文提供帮助。"

        return False, stripped

    def _get_recent_context(self, chat_id):
        """Return recent messages in the same chat as compact text context."""
        try:
            limit = max(settings.CONTEXT_MESSAGE_LIMIT, 1)
            rows = (
                self.db.query(Message)
                .filter(Message.chat_id == str(chat_id))
                .order_by(Message.message_time.desc(), Message.id.desc())
                .limit(limit)
                .all()
            )
            rows.reverse()
            lines = []
            for row in rows:
                timestamp = row.message_time.strftime("%Y-%m-%d %H:%M:%S") if row.message_time else ""
                lines.append(f"{timestamp} {row.user_name}: {row.content}")
            return "\n".join(lines)
        except Exception as e:
            logger.warning(f"读取最近聊天上下文失败: {str(e)}")
            return ""

    def _build_messages(self, user_name, query, chat_id, is_group_chat):
        context = self._get_recent_context(chat_id)
        chat_type = "群聊" if is_group_chat else "私聊"
        system_content = (
            f"{self.system_prompt}\n"
            f"当前会话类型: {chat_type}\n"
            f"当前 chat_id: {chat_id}\n"
            f"当前用户: {user_name}\n"
            "当工具需要 chat_id 时，默认使用当前 chat_id。"
        )
        messages = [{"role": "system", "content": system_content}]
        if context:
            messages.append({
                "role": "system",
                "content": f"最近聊天上下文如下，供理解当前问题使用：\n{context}"
            })
        messages.append({"role": "user", "content": query})
        return messages

    @staticmethod
    def _tool_output_to_text(output):
        if isinstance(output, str):
            return output
        if hasattr(output, "content"):
            parts = []
            for item in output.content:
                text = getattr(item, "text", None)
                if text:
                    parts.append(text)
            if parts:
                return "\n".join(parts)
        return str(output)

    async def _handle_assistant_request(self, user_name, query, chat_id, is_group_chat):
        """Handle an assistant request and send a response back to Lark."""
        try:
            logger.info(f"触发助手请求 - 用户: {user_name}, 内容: {query}")
            if not self.llm_service.is_available():
                error_msg = settings.AI_BOT_PREFIX + " 未在配置中设置 OPENAI_API_KEY"
                logger.error(error_msg)
                self.lark_client.send_msg(error_msg, chat_id)
                return
            
            async with Client(self.mcp_transport) as mcp_client:
                tools = []
                for tool in await mcp_client.list_tools():
                    tools.append({
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description":  tool.description,
                            "parameters": tool.inputSchema,
                        }
                    })
                messages = self._build_messages(user_name, query, chat_id, is_group_chat)
                resp = self.llm_service.chat_completion(messages, tools)
                msg = resp.choices[0].message
                if msg.tool_calls:
                    call = msg.tool_calls[0]
                    fn_name = call.function.name
                    args = json.loads(call.function.arguments)
                    output = await mcp_client.call_tool(fn_name, args)
                    output_text = self._tool_output_to_text(output)
                    logger.info(f"调用函数 {fn_name} -> {output_text}")
                    messages.append(msg)
                    messages.append({
                        "role": "tool",
                        "content": output_text,
                        "tool_call_id": call.id
                    })
                    summary = self.llm_service.chat_completion(messages)
                    response = summary.choices[0].message.content
                else:
                    response = msg.content
                response = f'{settings.AI_BOT_PREFIX} {response}'
                self.lark_client.send_msg(response, chat_id)
        except Exception as e:
            logger.error(f"处理助手请求时出错: {str(e)}")
            error_msg = f"{settings.AI_BOT_PREFIX} 处理请求时出错: {str(e)}"
            try:
                self.lark_client.send_msg(error_msg, chat_id)
            except Exception as send_err:
                logger.error(f"发送错误消息失败: {str(send_err)}")
    
    def close(self):
        """Close database session"""
        close_db_session(self.db) 
