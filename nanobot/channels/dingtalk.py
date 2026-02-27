"""DingTalk/DingDing channel implementation using Stream Mode."""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field as dc_field
from typing import Any

from loguru import logger
import httpx

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import DingTalkConfig

try:
    from dingtalk_stream import (
        DingTalkStreamClient,
        Credential,
        CallbackHandler,
        CallbackMessage,
        AckMessage,
    )
    from dingtalk_stream.chatbot import ChatbotMessage

    DINGTALK_AVAILABLE = True
except ImportError:
    DINGTALK_AVAILABLE = False
    # Fallback so class definitions don't crash at module level
    CallbackHandler = object  # type: ignore[assignment,misc]
    CallbackMessage = None  # type: ignore[assignment,misc]
    AckMessage = None  # type: ignore[assignment,misc]
    ChatbotMessage = None  # type: ignore[assignment,misc]


_DINGTALK_API = "https://api.dingtalk.com"
_AI_CARD_TEMPLATE_ID = "382e4302-551d-4880-bf29-a30acfab2e71.schema"
_CARD_TTL_SECONDS = 600  # 10 minutes

# sys_full_json_obj declares which template fields to render
_SYS_FULL_JSON = json.dumps({"order": ["msgContent"]})


@dataclass
class _ActiveCard:
    """Tracks an in-progress AI card for a chat."""

    card_instance_id: str
    accumulated_content: str = ""
    inputing_started: bool = False
    created_at: float = dc_field(default_factory=time.time)


class NanobotDingTalkHandler(CallbackHandler):
    """
    Standard DingTalk Stream SDK Callback Handler.
    Parses incoming messages and forwards them to the Nanobot channel.
    """

    def __init__(self, channel: "DingTalkChannel"):
        super().__init__()
        self.channel = channel

    async def process(self, message: CallbackMessage):
        """Process incoming stream message."""
        try:
            # Parse using SDK's ChatbotMessage for robust handling
            chatbot_msg = ChatbotMessage.from_dict(message.data)

            # Extract text content; fall back to raw dict if SDK object is empty
            content = ""
            if chatbot_msg.text:
                content = chatbot_msg.text.content.strip()
            if not content:
                content = message.data.get("text", {}).get("content", "").strip()

            if not content:
                logger.warning(
                    "Received empty or unsupported message type: {}",
                    chatbot_msg.message_type,
                )
                return AckMessage.STATUS_OK, "OK"

            sender_id = chatbot_msg.sender_staff_id or chatbot_msg.sender_id
            sender_name = chatbot_msg.sender_nick or "Unknown"

            logger.info("Received DingTalk message from {} ({}): {}", sender_name, sender_id, content)

            # Build metadata — always include platform basics
            metadata: dict[str, Any] = {
                "sender_name": sender_name,
                "platform": "dingtalk",
            }

            # When ai_card mode is enabled, extract extra fields for card APIs
            if self.channel.config.reply_mode == "ai_card":
                conv_type = chatbot_msg.conversation_type or message.data.get("conversationType", "")
                metadata["_dingtalk_conversation_type"] = str(conv_type)
                metadata["_dingtalk_conversation_id"] = (
                    chatbot_msg.conversation_id or message.data.get("conversationId", "")
                )
                metadata["_dingtalk_sender_staff_id"] = chatbot_msg.sender_staff_id or ""
                metadata["_dingtalk_sender_id"] = chatbot_msg.sender_id or ""
                metadata["_dingtalk_sender_corp_id"] = chatbot_msg.sender_corp_id or ""
                metadata["_dingtalk_message_id"] = chatbot_msg.message_id or message.data.get("msgId", "")

            # Determine chat_id: group chats use conversation_id, private chats use sender_id
            conv_type = metadata.get("_dingtalk_conversation_type", "")
            if conv_type == "2":
                chat_id = chatbot_msg.conversation_id or message.data.get("conversationId", sender_id)
            else:
                chat_id = sender_id

            # Forward to Nanobot via _on_message (non-blocking).
            # Store reference to prevent GC before task completes.
            task = asyncio.create_task(
                self.channel._on_message(content, sender_id, sender_name, chat_id=chat_id, metadata=metadata)
            )
            self.channel._background_tasks.add(task)
            task.add_done_callback(self.channel._background_tasks.discard)

            return AckMessage.STATUS_OK, "OK"

        except Exception as e:
            logger.error("Error processing DingTalk message: {}", e)
            # Return OK to avoid retry loop from DingTalk server
            return AckMessage.STATUS_OK, "Error"


class DingTalkChannel(BaseChannel):
    """
    DingTalk channel using Stream Mode.

    Uses WebSocket to receive events via `dingtalk-stream` SDK.
    Uses direct HTTP API to send messages (SDK is mainly for receiving).

    Supports two reply modes:
    - "text": Standard markdown messages (one per progress/reply).
    - "ai_card": AI interactive card with streaming typewriter effect.

    AI card lifecycle (ref: DingTalk-Real-AI/dingtalk-openclaw-connector):
    1. POST /v1.0/card/instances/createAndDeliver  (cardParamMap={})
    2. PUT  /v1.0/card/instances  (flowStatus=INPUTING, sys_full_json_obj)
    3. PUT  /v1.0/card/streaming  (isFinalize=false)  × N
    4. PUT  /v1.0/card/streaming  (isFinalize=true)   — close stream channel
    5. PUT  /v1.0/card/instances  (flowStatus=FINISHED, msgContent=final)
    """

    name = "dingtalk"

    def __init__(self, config: DingTalkConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: DingTalkConfig = config
        self._client: Any = None
        self._http: httpx.AsyncClient | None = None

        # Access Token management for sending messages
        self._access_token: str | None = None
        self._token_expiry: float = 0

        # Hold references to background tasks to prevent GC
        self._background_tasks: set[asyncio.Task] = set()

        # Active AI cards keyed by chat_id
        self._active_cards: dict[str, _ActiveCard] = {}

    async def start(self) -> None:
        """Start the DingTalk bot with Stream Mode."""
        try:
            if not DINGTALK_AVAILABLE:
                logger.error(
                    "DingTalk Stream SDK not installed. Run: pip install dingtalk-stream"
                )
                return

            if not self.config.client_id or not self.config.client_secret:
                logger.error("DingTalk client_id and client_secret not configured")
                return

            self._running = True
            self._http = httpx.AsyncClient()

            logger.info(
                "Initializing DingTalk Stream Client with Client ID: {}...",
                self.config.client_id,
            )
            credential = Credential(self.config.client_id, self.config.client_secret)
            self._client = DingTalkStreamClient(credential)

            # Register standard handler
            handler = NanobotDingTalkHandler(self)
            self._client.register_callback_handler(ChatbotMessage.TOPIC, handler)

            logger.info("DingTalk bot started with Stream Mode (reply_mode={})", self.config.reply_mode)

            # Reconnect loop: restart stream if SDK exits or crashes
            while self._running:
                try:
                    await self._client.start()
                except Exception as e:
                    logger.warning("DingTalk stream error: {}", e)
                if self._running:
                    logger.info("Reconnecting DingTalk stream in 5 seconds...")
                    await asyncio.sleep(5)

        except Exception as e:
            logger.exception("Failed to start DingTalk channel: {}", e)

    async def stop(self) -> None:
        """Stop the DingTalk bot."""
        self._running = False
        # Finish all active AI cards before shutdown
        for chat_id, card in list(self._active_cards.items()):
            try:
                await self._card_finish(card)
            except Exception as e:
                logger.warning("Failed to finish card for {} on stop: {}", chat_id, e)
        self._active_cards.clear()
        # Close the shared HTTP client
        if self._http:
            await self._http.aclose()
            self._http = None
        # Cancel outstanding background tasks
        for task in self._background_tasks:
            task.cancel()
        self._background_tasks.clear()

    async def _get_access_token(self) -> str | None:
        """Get or refresh Access Token."""
        if self._access_token and time.time() < self._token_expiry:
            return self._access_token

        url = f"{_DINGTALK_API}/v1.0/oauth2/accessToken"
        data = {
            "appKey": self.config.client_id,
            "appSecret": self.config.client_secret,
        }

        if not self._http:
            logger.warning("DingTalk HTTP client not initialized, cannot refresh token")
            return None

        try:
            resp = await self._http.post(url, json=data)
            resp.raise_for_status()
            res_data = resp.json()
            self._access_token = res_data.get("accessToken")
            # Expire 60s early to be safe
            self._token_expiry = time.time() + int(res_data.get("expireIn", 7200)) - 60
            return self._access_token
        except Exception as e:
            logger.error("Failed to get DingTalk access token: {}", e)
            return None

    # ── Send dispatcher ───────────────────────────────────────────────

    async def send(self, msg: OutboundMessage) -> None:
        """Route outbound message to the appropriate send method."""
        if (
            self.config.reply_mode == "ai_card"
            and msg.metadata.get("_dingtalk_conversation_type")
        ):
            await self._send_ai_card(msg)
        else:
            await self._send_text(msg)

    # ── Text mode (original) ─────────────────────────────────────────

    async def _send_text(self, msg: OutboundMessage) -> None:
        """Send a standard markdown message through DingTalk (private chat only)."""
        # oToMessages/batchSend only supports private chat
        if msg.metadata.get("_dingtalk_conversation_type") == "2":
            logger.warning("Text fallback not supported for group chats, skipping message to {}", msg.chat_id)
            return

        token = await self._get_access_token()
        if not token:
            return

        url = f"{_DINGTALK_API}/v1.0/robot/oToMessages/batchSend"
        headers = {"x-acs-dingtalk-access-token": token}

        data = {
            "robotCode": self.config.client_id,
            "userIds": [msg.chat_id],
            "msgKey": "sampleMarkdown",
            "msgParam": json.dumps({
                "text": msg.content,
                "title": "Nanobot Reply",
            }, ensure_ascii=False),
        }

        if not self._http:
            logger.warning("DingTalk HTTP client not initialized, cannot send")
            return

        try:
            resp = await self._http.post(url, json=data, headers=headers)
            if resp.status_code != 200:
                logger.error("DingTalk send failed: {}", resp.text)
            else:
                logger.debug("DingTalk message sent to {}", msg.chat_id)
        except Exception as e:
            logger.error("Error sending DingTalk message: {}", e)

    # ── AI Card mode ─────────────────────────────────────────────────

    async def _send_ai_card(self, msg: OutboundMessage) -> None:
        """Send or update an AI interactive card with streaming effect."""
        is_progress = bool(msg.metadata.get("_progress"))
        chat_id = msg.chat_id

        # Evict stale cards (TTL expired)
        card = self._active_cards.get(chat_id)
        if card and (time.time() - card.created_at) > _CARD_TTL_SECONDS:
            logger.warning("Evicting stale AI card for chat {}", chat_id)
            try:
                await self._card_finish(card)
            except Exception:
                pass
            self._active_cards.pop(chat_id, None)
            card = None

        if is_progress:
            # ── Progress message: create card or stream update ──
            if card is None:
                card = await self._card_create(msg)
                if card is None:
                    await self._send_text(msg)
                    return
                self._active_cards[chat_id] = card

            # Accumulate and stream content
            card.accumulated_content += msg.content + "\n\n"
            await self._card_stream(card, card.accumulated_content)

        else:
            # ── Final message ──
            if card is not None:
                card.accumulated_content += msg.content
                await self._card_finish(card)
                self._active_cards.pop(chat_id, None)
            else:
                # No active card — create and finish directly
                card = await self._card_create(msg)
                if card is None:
                    await self._send_text(msg)
                    return
                card.accumulated_content = msg.content
                await self._card_finish(card)

    # ── Card API helpers ─────────────────────────────────────────────
    # Lifecycle follows DingTalk-Real-AI/dingtalk-openclaw-connector:
    #   create → put_card_data(INPUTING) → streaming × N → streaming(finalize) → put_card_data(FINISHED)

    @staticmethod
    def _build_open_space_id(metadata: dict[str, Any]) -> str:
        """Build openSpaceId from message metadata."""
        conv_type = metadata.get("_dingtalk_conversation_type", "")
        if conv_type == "2":
            conv_id = metadata.get("_dingtalk_conversation_id", "")
            return f"dtv1.card//IM_GROUP.{conv_id}"
        else:
            staff_id = metadata.get("_dingtalk_sender_staff_id", "")
            return f"dtv1.card//IM_ROBOT.{staff_id}"

    async def _card_create(self, msg: OutboundMessage) -> _ActiveCard | None:
        """Step 1: Create and deliver an AI card. Returns _ActiveCard or None."""
        token = await self._get_access_token()
        if not token or not self._http:
            return None

        card_instance_id = f"card_{uuid.uuid4().hex}"
        open_space_id = self._build_open_space_id(msg.metadata)
        conv_type = msg.metadata.get("_dingtalk_conversation_type", "")

        url = f"{_DINGTALK_API}/v1.0/card/instances/createAndDeliver"
        headers = {"x-acs-dingtalk-access-token": token}

        body: dict[str, Any] = {
            "cardTemplateId": _AI_CARD_TEMPLATE_ID,
            "outTrackId": card_instance_id,
            "cardData": {"cardParamMap": {}},
            "callbackType": "STREAM",
            "imGroupOpenSpaceModel": {"supportForward": True},
            "imRobotOpenSpaceModel": {"supportForward": True},
            "openSpaceId": open_space_id,
            "userIdType": 1,
        }

        # Deliver model differs between group and private chat
        if conv_type == "2":
            body["imGroupOpenDeliverModel"] = {"robotCode": self.config.client_id}
        else:
            body["imRobotOpenDeliverModel"] = {"spaceType": "IM_ROBOT"}

        try:
            resp = await self._http.post(url, json=body, headers=headers)
            if resp.status_code != 200:
                logger.error("DingTalk card create failed ({}): {}", resp.status_code, resp.text)
                return None
            logger.debug("DingTalk AI card created: {}", card_instance_id)
            return _ActiveCard(card_instance_id=card_instance_id)
        except Exception as e:
            logger.error("Error creating DingTalk AI card: {}", e)
            return None

    async def _card_put_data(self, card: _ActiveCard, flow_status: str, content: str = "") -> None:
        """PUT /v1.0/card/instances — update card data (flowStatus, msgContent, sys_full_json_obj)."""
        token = await self._get_access_token()
        if not token or not self._http:
            return

        url = f"{_DINGTALK_API}/v1.0/card/instances"
        headers = {"x-acs-dingtalk-access-token": token}

        card_param_map: dict[str, str] = {
            "flowStatus": flow_status,
            "msgContent": content,
            "staticMsgContent": "",
            "sys_full_json_obj": _SYS_FULL_JSON,
        }

        body = {
            "outTrackId": card.card_instance_id,
            "cardData": {"cardParamMap": card_param_map},
        }

        try:
            resp = await self._http.put(url, json=body, headers=headers)
            if resp.status_code != 200:
                logger.error("DingTalk card put_data failed ({}): {}", resp.status_code, resp.text)
            else:
                logger.debug("DingTalk card put_data (flowStatus={})", flow_status)
        except Exception as e:
            logger.error("Error updating DingTalk AI card data: {}", e)

    async def _card_ensure_inputing(self, card: _ActiveCard) -> None:
        """Step 2: Transition card to INPUTING state (once per card)."""
        if card.inputing_started:
            return
        await self._card_put_data(card, flow_status="2")
        card.inputing_started = True

    async def _card_stream(self, card: _ActiveCard, content: str, *, is_finalize: bool = False) -> None:
        """Step 3: Stream content to AI card (PUT /v1.0/card/streaming)."""
        # Ensure INPUTING state before first streaming call
        await self._card_ensure_inputing(card)

        token = await self._get_access_token()
        if not token or not self._http:
            return

        url = f"{_DINGTALK_API}/v1.0/card/streaming"
        headers = {"x-acs-dingtalk-access-token": token}

        body = {
            "outTrackId": card.card_instance_id,
            "guid": uuid.uuid4().hex,
            "key": "msgContent",
            "content": content,
            "isFull": True,
            "isFinalize": is_finalize,
            "isError": False,
        }

        try:
            resp = await self._http.put(url, json=body, headers=headers)
            if resp.status_code != 200:
                logger.error("DingTalk card streaming failed ({}): {}", resp.status_code, resp.text)
            else:
                logger.debug("DingTalk card streamed (finalize={})", is_finalize)
        except Exception as e:
            logger.error("Error streaming DingTalk AI card: {}", e)

    async def _card_finish(self, card: _ActiveCard) -> None:
        """Step 4+5: Close stream channel (isFinalize=true), then put FINISHED with full content."""
        content = card.accumulated_content or "..."
        # Step 4: streaming isFinalize=true to close the stream channel
        await self._card_stream(card, content, is_finalize=True)
        # Step 5: put_card_data with FINISHED status and final content
        await self._card_put_data(card, flow_status="3", content=content)

    # ── Message handling ─────────────────────────────────────────────

    async def _on_message(
        self,
        content: str,
        sender_id: str,
        sender_name: str,
        *,
        chat_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Handle incoming message (called by NanobotDingTalkHandler).

        Delegates to BaseChannel._handle_message() which enforces allow_from
        permission checks before publishing to the bus.
        """
        resolved_chat_id = chat_id or sender_id
        try:
            # If user sends a new message while a card is active, finish the old card
            old_card = self._active_cards.pop(resolved_chat_id, None)
            if old_card is not None:
                logger.info("Finishing stale AI card for {} (new message received)", resolved_chat_id)
                try:
                    await self._card_finish(old_card)
                except Exception as e:
                    logger.warning("Failed to finish stale card: {}", e)

            logger.info("DingTalk inbound: {} from {}", content, sender_name)
            await self._handle_message(
                sender_id=sender_id,
                chat_id=resolved_chat_id,
                content=str(content),
                metadata=metadata or {
                    "sender_name": sender_name,
                    "platform": "dingtalk",
                },
            )
        except Exception as e:
            logger.error("Error publishing DingTalk message: {}", e)
