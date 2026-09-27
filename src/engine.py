"""商店经济系统。

修复点：
1. 刷新时间按玩家 ID 相位偏移，不同玩家错开刷新，避免同时抢货。
2. 限购同时支持个人限购与全局限购，均可按物品配置（None 表示不限）。
3. 价格波动有 min/max 钳制，单次事件影响有上限。
4. 出售价格按耐久比例折算，并设最低出售价格。
5. 回购列表有最大容量，超出后最旧记录被挤出。
6. 玩家间交易由服务器端估值校验：偏离估值需二次确认，极端价格直接拒绝。
"""
from __future__ import annotations

import hashlib
import random
from collections import deque
from dataclasses import dataclass


@dataclass
class ShopItem:
    iid: str
    name: str
    price: float
    stock: int
    durability: float = 100.0
    personal_limit: int | None = None
    global_limit: int | None = None
    min_price: float | None = None
    max_price: float | None = None


@dataclass
class TradeResult:
    allowed: bool
    status: str  # "ok" / "confirm_required" / "rejected"
    reason: str = ""
    valuation: float = 0.0


class ShopSystem:
    def __init__(
        self,
        refresh_interval: int = 10,
        buyback_capacity: int = 20,
        price_min_factor: float = 0.5,
        price_max_factor: float = 2.0,
        max_event_impact: float = 0.3,
        min_sell_price: float = 1.0,
        start_gold: float = 1000.0,
        trade_tolerance: float = 0.5,
        trade_hard_floor: float = 0.01,
        trade_hard_ceiling: float = 100.0,
    ):
        self.items: dict[str, ShopItem] = {}
        self.base_prices: dict[str, float] = {}

        self.start_gold = start_gold
        self.player_gold = start_gold  # 兼容旧接口
        self.wallets: dict[str, float] = {}

        # fix5: 回购列表限容，超出后最旧记录自动挤出
        self.buyback_capacity = buyback_capacity
        self.buyback: deque = deque(maxlen=buyback_capacity)

        # fix1: 按玩家 ID 相位偏移的刷新计划
        self.refresh_interval = refresh_interval
        self.refresh_time = 0  # 全服刷新计数
        self.clock = 0
        self.last_refresh: dict[str, int] = {}
        self.next_refresh: dict[str, int] = {}

        # fix2: 个人限购 + 全局限购
        self.personal_bought: dict[str, dict[str, int]] = {}
        self.global_bought: dict[str, int] = {}

        # fix3: 价格钳制与事件影响上限
        self.price_min_factor = price_min_factor
        self.price_max_factor = price_max_factor
        self.max_event_impact = max_event_impact

        # fix4: 出售最低价
        self.min_sell_price = min_sell_price

        # fix6: 交易估值校验参数
        self.trade_tolerance = trade_tolerance
        self.trade_hard_floor = trade_hard_floor
        self.trade_hard_ceiling = trade_hard_ceiling

    # ---------- 物品 ----------

    def add_item(self, item: ShopItem) -> ShopItem:
        self.items[item.iid] = item
        self.base_prices[item.iid] = item.price
        return item

    def get_gold(self, player_id: str) -> float:
        return self.wallets.setdefault(player_id, float(self.start_gold))

    # ---------- fix1: 相位错开的刷新 ----------

    def player_phase(self, player_id: str) -> int:
        """由玩家 ID 稳定地派生 [0, refresh_interval) 内的相位偏移。"""
        digest = hashlib.sha256(str(player_id).encode("utf-8")).hexdigest()
        return int(digest[:8], 16) % self.refresh_interval

    def next_refresh_time(self, player_id: str, now: int | None = None) -> int:
        """玩家在 now 之后下一次刷新的时间点：phase + k * interval。"""
        if now is None:
            now = self.clock
        phase = self.player_phase(player_id)
        interval = self.refresh_interval
        if now < phase:
            return phase
        return phase + ((now - phase) // interval + 1) * interval

    def advance_time(self, seconds: int) -> int:
        self.clock += seconds
        return self.clock

    def refresh(self, player_id: str) -> int:
        now = self.clock
        self.last_refresh[player_id] = now
        nxt = self.next_refresh_time(player_id, now)
        self.next_refresh[player_id] = nxt
        self.refresh_time += 1
        return nxt

    # ---------- fix2: 个人 + 全局限购 ----------

    def buy(self, iid: str, player_id: str) -> bool:
        item = self.items[iid]
        if item.stock <= 0:
            return False
        if item.global_limit is not None and self.global_bought.get(iid, 0) >= item.global_limit:
            return False
        bought = self.personal_bought.setdefault(player_id, {})
        if item.personal_limit is not None and bought.get(iid, 0) >= item.personal_limit:
            return False
        price = item.price
        if self.get_gold(player_id) < price:
            return False
        item.stock -= 1
        self.global_bought[iid] = self.global_bought.get(iid, 0) + 1
        bought[iid] = bought.get(iid, 0) + 1
        self.wallets[player_id] -= price
        return True

    # ---------- fix3: 价格钳制 ----------

    def _price_bounds(self, item: ShopItem) -> tuple[float, float, float]:
        base = self.base_prices.get(item.iid, item.price)
        lo = item.min_price if item.min_price is not None else base * self.price_min_factor
        hi = item.max_price if item.max_price is not None else base * self.price_max_factor
        lo = max(0.0, lo)
        return base, lo, max(lo, hi)

    def _clamp_price(self, item: ShopItem, value: float) -> float:
        _, lo, hi = self._price_bounds(item)
        return max(lo, min(hi, value))

    def fluctuate_prices(self, rng=None) -> None:
        rng = rng or random
        for item in self.items.values():
            item.price = self._clamp_price(item, item.price * (1 + rng.uniform(-0.1, 0.1)))

    def apply_price_event(self, iid: str, impact: float) -> float:
        """对物品施加一次价格事件，impact 为相对涨跌幅（如 0.5 表示 +50%）。

        单次事件影响被钳制在 [-max_event_impact, max_event_impact]，
        结算后的价格仍受 min/max 价格钳制。
        """
        impact = max(-self.max_event_impact, min(self.max_event_impact, impact))
        item = self.items[iid]
        item.price = self._clamp_price(item, item.price * (1 + impact))
        return item.price

    # ---------- fix4: 按耐久折算的出售价 ----------

    @staticmethod
    def _durability_ratio(durability: float) -> float:
        durability = max(0.0, min(100.0, float(durability)))
        return durability / 100.0

    def sell(self, item: ShopItem, durability: float) -> float:
        base = self.base_prices.get(item.iid, item.price)
        value = base * self._durability_ratio(durability)
        return round(max(value, self.min_sell_price), 2)

    # ---------- fix5: 限容回购列表 ----------

    def add_buyback(self, item) -> None:
        self.buyback.append(item)

    # ---------- fix6: 交易价格服务器端校验 ----------

    def estimate_value(self, item: ShopItem, durability: float = 100.0) -> float:
        base = self.base_prices.get(item.iid, item.price)
        return base * self._durability_ratio(durability)

    def validate_trade(
        self, item: ShopItem, price: float, durability: float = 100.0, confirm: bool = False
    ) -> TradeResult:
        price = float(price)
        if price <= 0:
            return TradeResult(False, "rejected", "交易价格必须大于 0")
        value = self.estimate_value(item, durability)
        if value <= 0:
            return TradeResult(False, "rejected", "物品估值无效", value)
        ratio = price / value
        if ratio < self.trade_hard_floor or ratio > self.trade_hard_ceiling:
            return TradeResult(
                False,
                "rejected",
                f"价格 {price:g} 严重偏离估值 {value:g}，交易被拒绝",
                value,
            )
        if not confirm and (
            ratio < 1 - self.trade_tolerance or ratio > 1 + self.trade_tolerance
        ):
            return TradeResult(
                False,
                "confirm_required",
                f"价格 {price:g} 偏离估值 {value:g}，需要二次确认",
                value,
            )
        return TradeResult(True, "ok", "", value)

    def trade(
        self,
        buyer_id: str,
        seller_id: str,
        item: ShopItem,
        price: float,
        durability: float = 100.0,
        confirm: bool = False,
    ) -> TradeResult:
        result = self.validate_trade(item, price, durability, confirm)
        if not result.allowed:
            return result
        price = float(price)
        if self.get_gold(buyer_id) < price:
            return TradeResult(False, "rejected", "买家金币不足", result.valuation)
        self.wallets[buyer_id] = self.get_gold(buyer_id) - price
        self.wallets[seller_id] = self.get_gold(seller_id) + price
        return result
