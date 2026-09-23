"""Safe Binance USD-M Futures Demo Trading execution client."""

from __future__ import annotations

import hashlib
import hmac
import time
from decimal import Decimal, ROUND_DOWN
from typing import Any, Optional
from urllib.parse import urlencode

import aiohttp
import structlog

from trend_scalper.models import Signal, Side

log = structlog.get_logger()


class ExecutionManager:
    def __init__(self, config: dict, testnet: bool = True):
        self.config = config
        self.testnet = testnet

        binance = config.get("binance", {})
        trading = config.get("trading", {})

        self.api_key = binance.get("api_key", "")
        self.api_secret = binance.get("api_secret", "")
        self.base_url = (
            "https://demo-fapi.binance.com"
            if testnet
            else "https://fapi.binance.com"
        )
        self.recv_window = int(binance.get("recv_window", 10_000))
        self.testnet_quantities = trading.get("testnet_quantities", {})
        self._symbol_filters: dict[str, dict[str, float]] = {}

    def _require_credentials(self) -> None:
        if not self.api_key or not self.api_secret:
            raise RuntimeError(
                "Missing API credentials in bot_config.yaml."
            )

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[dict[str, Any]] = None,
        signed: bool = True,
    ) -> dict[str, Any]:
        params = dict(params or {})
        headers: dict[str, str] = {}

        if signed:
            self._require_credentials()
            params["timestamp"] = int(time.time() * 1000)
            params["recvWindow"] = self.recv_window

            query_string = urlencode(params, doseq=True)
            params["signature"] = hmac.new(
                self.api_secret.encode("utf-8"),
                query_string.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            headers["X-MBX-APIKEY"] = self.api_key

        url = f"{self.base_url}{endpoint}"
        timeout = aiohttp.ClientTimeout(total=20)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(
                method.upper(),
                url,
                headers=headers,
                params=params,
            ) as response:
                raw = await response.text()
                try:
                    payload = await response.json(content_type=None)
                except Exception:
                    payload = {"raw_response": raw}

        if response.status >= 400:
            log.error(
                "Binance HTTP request failed",
                endpoint=endpoint,
                status=response.status,
                response=payload,
            )
        elif isinstance(payload, dict) and payload.get("code", 0) < 0:
            log.error(
                "Binance API rejected request",
                endpoint=endpoint,
                code=payload.get("code"),
                message=payload.get("msg"),
            )

        return payload if isinstance(payload, dict) else {"data": payload}

    async def check_public_connectivity(self) -> bool:
        result = await self._request("GET", "/fapi/v1/time", signed=False)
        ok = "serverTime" in result
        if ok:
            log.info("Binance Futures testnet connectivity confirmed")
        else:
            log.error(
                "Binance Futures testnet connectivity failed",
                response=result,
            )
        return ok

    async def check_account_access(self) -> bool:
        result = await self._request("GET", "/fapi/v2/account")
        if "assets" in result:
            log.info("Binance Futures testnet account access confirmed")
            return True

        log.error(
            "Binance Futures testnet account access failed",
            code=result.get("code"),
            message=result.get("msg"),
            response=result,
        )
        return False

    async def _get_symbol_filters(self, symbol: str) -> dict[str, float]:
        if symbol in self._symbol_filters:
            return self._symbol_filters[symbol]

        result = await self._request(
            "GET",
            "/fapi/v1/exchangeInfo",
            {"symbol": symbol},
            signed=False,
        )
        symbols = result.get("symbols", [])
        if not symbols:
            raise RuntimeError(
                f"Exchange filters unavailable for {symbol}: {result}"
            )

        filters = {
            item["filterType"]: item
            for item in symbols[0].get("filters", [])
        }
        lot = filters.get("LOT_SIZE", {})
        price = filters.get("PRICE_FILTER", {})

        parsed = {
            "step_size": float(lot.get("stepSize", "0.001")),
            "min_qty": float(lot.get("minQty", "0.001")),
            "tick_size": float(price.get("tickSize", "0.01")),
        }
        self._symbol_filters[symbol] = parsed
        return parsed

    @staticmethod
    def _round_down(value: float, increment: float) -> float:
        if increment <= 0:
            return value

        value_decimal = Decimal(str(value))
        increment_decimal = Decimal(str(increment))
        return float(
            (value_decimal / increment_decimal).to_integral_value(
                rounding=ROUND_DOWN
            )
            * increment_decimal
        )

    async def _normalise_qty(
        self,
        symbol: str,
        quantity: float,
    ) -> float:
        filters = await self._get_symbol_filters(symbol)
        qty = self._round_down(quantity, filters["step_size"])

        if qty < filters["min_qty"]:
            raise ValueError(
                f"Quantity {qty} is below {symbol}'s "
                f"minimum {filters['min_qty']}."
            )
        return qty

    async def _normalise_price(
        self,
        symbol: str,
        price: float,
    ) -> float:
        filters = await self._get_symbol_filters(symbol)
        return self._round_down(price, filters["tick_size"])

    async def _place_regular_order(
        self,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        result = await self._request("POST", "/fapi/v1/order", params)
        if "orderId" not in result:
            raise RuntimeError(
                "Binance did not accept the regular order: "
                f"{result.get('code', 'unknown')} "
                f"{result.get('msg', result)}"
            )
        return result

    async def _place_algo_order(
        self,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        result = await self._request("POST", "/fapi/v1/algoOrder", params)

        # Binance may return algoId rather than orderId for conditional orders.
        algo_id = result.get("algoId") or result.get("orderId")
        if not algo_id:
            raise RuntimeError(
                "Binance did not accept the algo order: "
                f"{result.get('code', 'unknown')} "
                f"{result.get('msg', result)}"
            )
        return result

    def _configured_quantity(self, symbol: str) -> float:
        value = self.testnet_quantities.get(symbol)
        if value is None:
            raise RuntimeError(
                f"No testnet quantity configured for {symbol}. "
                "Add trading.testnet_quantities in bot_config.yaml."
            )
        return float(value)

    async def open_position(
        self,
        signal: Signal,
    ) -> Optional[dict[str, Any]]:
        entry_side = "BUY" if signal.side == Side.LONG else "SELL"
        exit_side = "SELL" if signal.side == Side.LONG else "BUY"

        requested_quantity = self._configured_quantity(signal.symbol)
        quantity = await self._normalise_qty(
            signal.symbol,
            requested_quantity,
        )

        log.info(
            "Submitting entry order",
            symbol=signal.symbol,
            side=entry_side,
            quantity=quantity,
            testnet=self.testnet,
        )

        entry = await self._place_regular_order(
            {
                "symbol": signal.symbol,
                "side": entry_side,
                "type": "MARKET",
                "quantity": quantity,
                "newOrderRespType": "RESULT",
            }
        )

        filled_qty = float(entry.get("executedQty") or quantity)
        if filled_qty <= 0:
            raise RuntimeError(
                f"Entry returned no executed quantity: {entry}"
            )

        stop_price = await self._normalise_price(
            signal.symbol,
            signal.stop_loss,
        )
        take_profit_price = await self._normalise_price(
            signal.symbol,
            signal.take_profit,
        )

        try:
            stop_order = await self._place_algo_order(
                {
                    "algoType": "CONDITIONAL",
                    "symbol": signal.symbol,
                    "side": exit_side,
                    "type": "STOP_MARKET",
                    "triggerPrice": stop_price,
                    "closePosition": "true",
                    "workingType": "MARK_PRICE",
                }
            )

            take_profit_order = await self._place_algo_order(
                {
                    "algoType": "CONDITIONAL",
                    "symbol": signal.symbol,
                    "side": exit_side,
                    "type": "TAKE_PROFIT_MARKET",
                    "triggerPrice": take_profit_price,
                    "closePosition": "true",
                    "workingType": "MARK_PRICE",
                }
            )

        except Exception:
            log.exception(
                "Protection placement failed; attempting emergency close",
                symbol=signal.symbol,
            )
            try:
                emergency_close = await self._place_regular_order(
                    {
                        "symbol": signal.symbol,
                        "side": exit_side,
                        "type": "MARKET",
                        "quantity": filled_qty,
                        "reduceOnly": "true",
                        "newOrderRespType": "RESULT",
                    }
                )
                log.warning(
                    "Emergency close order accepted",
                    symbol=signal.symbol,
                    order_id=emergency_close["orderId"],
                )
            except Exception:
                log.exception(
                    "Emergency close failed; inspect Demo Trading immediately",
                    symbol=signal.symbol,
                )
            raise

        stop_algo_id = stop_order.get("algoId") or stop_order.get("orderId")
        take_profit_algo_id = (
            take_profit_order.get("algoId")
            or take_profit_order.get("orderId")
        )

        order = {
            "order_id": entry["orderId"],
            "symbol": signal.symbol,
            "side": entry_side,
            "entry_price": float(
                entry.get("avgPrice") or signal.entry_price
            ),
            "qty": filled_qty,
            "stop_algo_id": stop_algo_id,
            "take_profit_algo_id": take_profit_algo_id,
            "stop_loss": stop_price,
            "take_profit": take_profit_price,
        }

        log.info(
            "Entry and protective algo orders accepted",
            symbol=signal.symbol,
            entry_order_id=order["order_id"],
            stop_algo_id=stop_algo_id,
            take_profit_algo_id=take_profit_algo_id,
        )
        return order

    async def cancel_algo_order(
        self,
        symbol: str,
        algo_id: Optional[int],
    ) -> None:
        if not algo_id:
            return

        result = await self._request(
            "DELETE",
            "/fapi/v1/algoOrder",
            {
                "symbol": symbol,
                "algoId": algo_id,
            },
        )
        if result.get("code", 0) < 0:
            log.warning(
                "Could not cancel protective algo order",
                symbol=symbol,
                algo_id=algo_id,
                response=result,
            )

    async def close_position(
        self,
        symbol: str,
        position: dict[str, Any],
    ) -> None:
        await self.cancel_algo_order(
            symbol,
            position.get("stop_algo_id"),
        )
        await self.cancel_algo_order(
            symbol,
            position.get("take_profit_algo_id"),
        )

        exit_side = "SELL" if position["side"] == "BUY" else "BUY"
        quantity = await self._normalise_qty(
            symbol,
            float(position["qty"]),
        )
        result = await self._place_regular_order(
            {
                "symbol": symbol,
                "side": exit_side,
                "type": "MARKET",
                "quantity": quantity,
                "reduceOnly": "true",
                "newOrderRespType": "RESULT",
            }
        )
        log.info(
            "Position close order accepted",
            symbol=symbol,
            order_id=result["orderId"],
        )