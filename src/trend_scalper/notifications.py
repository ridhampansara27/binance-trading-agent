from __future__ import annotations

import asyncio
from typing import Optional

import httpx
import structlog

from .config import TelegramConfig

logger = structlog.get_logger()


class TelegramNotifier:
    def __init__(self, cfg: TelegramConfig, client: Optional[httpx.AsyncClient] = None):
        self.cfg = cfg
        self.enabled = cfg.enabled and bool(cfg.bot_token) and bool(cfg.chat_id)
        self.client = client or httpx.AsyncClient(timeout=10.0)

        if self.enabled:
            logger.info("Telegram notifications enabled")
        else:
            logger.debug("Telegram notifications disabled")

    async def send_message(self, text: str) -> bool:
        if not self.enabled:
            return False

        url = f"https://api.telegram.org/bot{self.cfg.bot_token}/sendMessage"
        payload = {"chat_id": self.cfg.chat_id, "text": text, "parse_mode": "Markdown"}

        try:
            resp = await self.client.post(url, json=payload)
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.exception("Failed to send Telegram message", error=str(e))
            return False

    async def notify_trade(self, action: str, symbol: str, side: str, price: float, pnl: Optional[float] = None) -> None:
        if action == "open":
            text = f"🟢 *Position Opened*\nSymbol: `{symbol}`\nSide: {side}\nEntry: `{price:.6f}`"
        elif action == "close":
            pnl_text = f"PnL: `{pnl:.2f}`" if pnl is not None else "PnL: N/A"
            text = f"🔴 *Position Closed*\nSymbol: `{symbol}`\nSide: {side}\nExit: `{price:.6f}`\n{pnl_text}"
        else:
            text = f"🔔 *Trade Update*\nSymbol: `{symbol}`\nAction: {action}"

        await self.send_message(text)

    async def notify_error(self, message: str) -> None:
        text = f"⚠️ *Error*\n{message}"
        await self.send_message(text)

    async def close(self) -> None:
        await self.client.aclose()
