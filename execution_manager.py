"""Safe Binance USD-M Futures Demo Trading execution client."""

from __future__ import annotations

import hashlib
import hmac
import time
from decimal import ROUND_DOWN, Decimal
from typing import Any
from urllib.parse import urlencode

import aiohttp
import structlog

from journal import TradeJournal
from trend_scalper.models import Side, Signal

log = structlog.get_logger()


class ExecutionManager:
    def __init__(self, config: dict, testnet: bool = True, journal: TradeJournal | None = None):
        self.config = config
        self.testnet = testnet
        if not self.testnet:
            raise RuntimeError("Production execution is disabled in this implementation.")

        binance = config.get("binance", {})
        trading = config.get("trading", {})

        self.api_key = str(binance.get("api_key", ""))
        self.api_secret = str(binance.get("api_secret", ""))
        self.base_url = "https://demo-fapi.binance.com"
        self.recv_window = int(binance.get("recv_window", 10_000))
        self.testnet_quantities = trading.get("testnet_quantities", {})
        self._symbol_filters: dict[str, dict[str, float]] = {}
        self._clock_offset_ms = 0
        self.journal = journal

    def _require_credentials(self) -> None:
        if not self.api_key or not self.api_secret:
            raise RuntimeError("Missing Demo API credentials. Set BINANCE_API_KEY/BINANCE_API_SECRET.")

    async def _public_time(self) -> dict[str, Any]:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session, session.get(
            f"{self.base_url}/fapi/v1/time"
        ) as response:
            payload = await response.json(content_type=None)
            if response.status >= 400:
                raise RuntimeError(f"Time endpoint failed: HTTP {response.status}")
            return payload if isinstance(payload, dict) else {}

    async def sync_server_time(self) -> None:
        result = await self._public_time()
        server_ms = int(result["serverTime"])
        local_ms = int(time.time() * 1000)
        self._clock_offset_ms = server_ms - local_ms
        log.info("Server time synchronized", offset_ms=self._clock_offset_ms)

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        signed: bool = True,
    ) -> dict[str, Any]:
        params = dict(params or {})
        headers: dict[str, str] = {}

        if signed:
            self._require_credentials()
            if self._clock_offset_ms == 0:
                await self.sync_server_time()
            params["timestamp"] = int(time.time() * 1000) + self._clock_offset_ms
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

        async with aiohttp.ClientSession(timeout=timeout) as session, session.request(
            method.upper(),
            url,
            headers=headers,
            params=params,
        ) as response:
            raw = await response.text()
            try:
                payload = await response.json(content_type=None)
            except (TypeError, ValueError):
                payload = {"raw_response": raw}

        if response.status >= 400:
            safe_payload = payload if isinstance(payload, dict) else {"response": str(payload)}
            log.error("Binance HTTP request failed", endpoint=endpoint, status=response.status, response=safe_payload)
            if response.status == 400 and "timestamp" in str(safe_payload):
                await self.sync_server_time()
        elif isinstance(payload, dict) and payload.get("code", 0) < 0:
            log.error(
                "Binance API rejected request",
                endpoint=endpoint,
                code=payload.get("code"),
                message=payload.get("msg"),
            )
            if payload.get("code") in (-1021,):
                await self.sync_server_time()

        return payload if isinstance(payload, dict) else {"data": payload}

    async def check_public_connectivity(self) -> bool:
        result = await self._request("GET", "/fapi/v1/time", signed=False)
        ok = "serverTime" in result
        if ok:
            await self.sync_server_time()
            log.info("Binance Futures demo connectivity confirmed")
        else:
            log.error("Binance Futures demo connectivity failed", response=result)
        return ok

    async def check_account_access(self) -> bool:
        result = await self._request("GET", "/fapi/v2/account")
        if "assets" in result:
            log.info("Binance Futures demo account access confirmed")
            return True

        log.error("Binance Futures demo account access failed", code=result.get("code"), message=result.get("msg"))
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
            raise RuntimeError(f"Exchange filters unavailable for {symbol}.")

        info = symbols[0]
        filters = {item["filterType"]: item for item in info.get("filters", [])}
        lot = filters.get("LOT_SIZE", {})
        price = filters.get("PRICE_FILTER", {})
        min_notional = filters.get("MIN_NOTIONAL", filters.get("NOTIONAL", {}))

        parsed = {
            "step_size": float(lot.get("stepSize", "0.001")),
            "min_qty": float(lot.get("minQty", "0.001")),
            "tick_size": float(price.get("tickSize", "0.01")),
            "min_notional": float(min_notional.get("notional", min_notional.get("minNotional", "5"))),
            "quantity_precision": float(info.get("quantityPrecision", 3)),
            "price_precision": float(info.get("pricePrecision", 2)),
        }
        self._symbol_filters[symbol] = parsed
        return parsed

    @staticmethod
    def _round_down(value: float, increment: float) -> float:
        if increment <= 0:
            return value

        value_decimal = Decimal(str(value))
        increment_decimal = Decimal(str(increment))
        return float((value_decimal / increment_decimal).to_integral_value(rounding=ROUND_DOWN) * increment_decimal)

    async def _normalise_qty(self, symbol: str, quantity: float) -> float:
        filters = await self._get_symbol_filters(symbol)
        qty = self._round_down(quantity, filters["step_size"])

        if qty < filters["min_qty"]:
            raise ValueError(f"Quantity {qty} is below {symbol} minimum {filters['min_qty']}.")
        return qty

    async def _normalise_price(self, symbol: str, price: float) -> float:
        filters = await self._get_symbol_filters(symbol)
        return self._round_down(price, filters["tick_size"])

    async def _enforce_min_notional(self, symbol: str, qty: float, ref_price: float) -> None:
        filters = await self._get_symbol_filters(symbol)
        notional = qty * ref_price
        if notional < filters["min_notional"]:
            raise ValueError(
                f"Notional {notional:.4f} is below {symbol} minimum notional {filters['min_notional']:.4f}."
            )

    async def _place_regular_order(self, params: dict[str, Any]) -> dict[str, Any]:
        result = await self._request("POST", "/fapi/v1/order", params)
        if "orderId" not in result:
            raise RuntimeError(f"Regular order rejected: {result.get('code', 'unknown')} {result.get('msg', result)}")
        return result

    async def _place_algo_order(self, params: dict[str, Any]) -> dict[str, Any]:
        result = await self._request("POST", "/fapi/v1/algoOrder", params)
        algo_id = result.get("algoId") or result.get("orderId")
        if not algo_id:
            raise RuntimeError(f"Algo order rejected: {result.get('code', 'unknown')} {result.get('msg', result)}")
        return result

    def _configured_quantity(self, symbol: str) -> float:
        value = self.testnet_quantities.get(symbol)
        if value is None:
            raise RuntimeError(f"No testnet quantity configured for {symbol}.")
        return float(value)

    async def validate_symbol_quantities(self, symbols: list[str], mark_prices: dict[str, float]) -> None:
        for symbol in symbols:
            qty = await self._normalise_qty(symbol, self._configured_quantity(symbol))
            await self._enforce_min_notional(symbol, qty, mark_prices[symbol])

    async def get_mark_prices(self, symbols: list[str]) -> dict[str, float]:
        marks: dict[str, float] = {}
        for symbol in symbols:
            result = await self._request("GET", "/fapi/v1/premiumIndex", {"symbol": symbol}, signed=False)
            marks[symbol] = float(result["markPrice"])
        return marks

    async def reconcile_account_state(self) -> dict[str, Any]:
        positions = await self._request("GET", "/fapi/v2/positionRisk")
        open_positions = [p for p in positions if float(p.get("positionAmt", "0")) != 0] if isinstance(positions, list) else []
        open_orders = await self._request("GET", "/fapi/v1/openOrders", signed=True)
        algo_orders = await self._request("GET", "/fapi/v1/algoOrders", {"onlyActive": "true"}, signed=True)
        return {
            "positions": open_positions,
            "open_orders": open_orders if isinstance(open_orders, list) else [],
            "open_algo_orders": algo_orders.get("orders", []) if isinstance(algo_orders, dict) else [],
        }

    async def open_position(self, signal: Signal) -> dict[str, Any] | None:
        entry_side = "BUY" if signal.side == Side.LONG else "SELL"
        exit_side = "SELL" if signal.side == Side.LONG else "BUY"

        requested_quantity = self._configured_quantity(signal.symbol)
        quantity = await self._normalise_qty(signal.symbol, requested_quantity)
        await self._enforce_min_notional(signal.symbol, quantity, signal.entry_price)

        log.info("Submitting entry order", symbol=signal.symbol, side=entry_side, quantity=quantity, testnet=True)

        entry = await self._place_regular_order(
            {
                "symbol": signal.symbol,
                "side": entry_side,
                "type": "MARKET",
                "quantity": quantity,
                "newOrderRespType": "RESULT",
            }
        )
        if self.journal:
            self.journal.log_order(signal.symbol, "entry", str(entry.get("orderId", "")), entry)

        filled_qty = float(entry.get("executedQty") or quantity)
        fill_price = float(entry.get("avgPrice") or signal.entry_price)
        if filled_qty <= 0:
            raise RuntimeError(f"Entry returned no executed quantity: {entry}")

        stop_price = await self._normalise_price(signal.symbol, signal.stop_loss)
        take_profit_price = await self._normalise_price(signal.symbol, signal.take_profit)

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
            if self.journal:
                self.journal.log_order(signal.symbol, "stop_loss", str(stop_order.get("algoId", "")), stop_order)
                self.journal.log_order(
                    signal.symbol,
                    "take_profit",
                    str(take_profit_order.get("algoId", "")),
                    take_profit_order,
                )
        except Exception as error:
            if self.journal:
                self.journal.log_incident(
                    severity="critical",
                    code="protection-placement-failed",
                    message="Protective algo order placement failed; attempting emergency close.",
                    payload={"symbol": signal.symbol, "error": str(error)},
                )
            log.exception("Protection placement failed; attempting emergency close", symbol=signal.symbol)
            emergency_close_result: dict[str, Any] | None = None
            try:
                emergency_close_result = await self._place_regular_order(
                    {
                        "symbol": signal.symbol,
                        "side": exit_side,
                        "type": "MARKET",
                        "quantity": filled_qty,
                        "reduceOnly": "true",
                        "newOrderRespType": "RESULT",
                    }
                )
                log.warning("Emergency close order accepted", symbol=signal.symbol, order_id=emergency_close_result["orderId"])
            finally:
                if self.journal and emergency_close_result:
                    self.journal.log_order(
                        signal.symbol,
                        "emergency_close",
                        str(emergency_close_result.get("orderId", "")),
                        emergency_close_result,
                    )
            raise

        stop_algo_id = stop_order.get("algoId") or stop_order.get("orderId")
        take_profit_algo_id = take_profit_order.get("algoId") or take_profit_order.get("orderId")

        order = {
            "order_id": entry["orderId"],
            "symbol": signal.symbol,
            "side": entry_side,
            "entry_price": fill_price,
            "qty": filled_qty,
            "stop_algo_id": stop_algo_id,
            "take_profit_algo_id": take_profit_algo_id,
            "stop_loss": stop_price,
            "take_profit": take_profit_price,
            "protected": True,
        }
        return order

    async def cancel_algo_order(self, symbol: str, algo_id: int | None) -> None:
        if not algo_id:
            return
        result = await self._request(
            "DELETE",
            "/fapi/v1/algoOrder",
            {"symbol": symbol, "algoId": algo_id},
        )
        if result.get("code", 0) < 0:
            log.warning("Could not cancel protective algo order", symbol=symbol, algo_id=algo_id)

    async def close_position(self, symbol: str, position: dict[str, Any]) -> dict[str, Any]:
        await self.cancel_algo_order(symbol, position.get("stop_algo_id"))
        await self.cancel_algo_order(symbol, position.get("take_profit_algo_id"))

        exit_side = "SELL" if position["side"] == "BUY" else "BUY"
        quantity = await self._normalise_qty(symbol, float(position["qty"]))
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
        if self.journal:
            self.journal.log_order(symbol, "manual_close", str(result.get("orderId", "")), result)
        return result
