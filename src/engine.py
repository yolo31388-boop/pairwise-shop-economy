"""商店经济系统"""
import zlib
from collections import deque
from dataclasses import dataclass, field


@dataclass
class ShopItem:
    iid: str
    name: str
    price: float
    stock: int
    durability: float = 100.0
    max_durability: float = 100.0
    base_price: float = 0.0  # 价格波动钳制基准，0 表示以当前 price 为基准


@dataclass
class PurchaseLimit:
    per_player: int | None = None   # 个人限购，None 表示不限
    global_limit: int | None = None  # 全局限购，None 表示不限


@dataclass
class TradeResult:
    status: str  # "ok" | "confirmed" | "needs_confirmation" | "rejected"
    estimate: float
    deviation: float


class ShopSystem:
    def __init__(self, *, refresh_interval: int = 100,
                 price_min_ratio: float = 0.5, price_max_ratio: float = 2.0,
                 event_impact_cap: float = 0.3, sell_min_ratio: float = 0.1,
                 buyback_capacity: int = 20, trade_tolerance: float = 0.2,
                 trade_hard_reject_ratio: float = 10.0):
        self.items: dict[str, ShopItem] = {}
        self.player_gold = 1000.0
        self.refresh_interval = refresh_interval
        self.refresh_time = 0
        self._player_next_refresh: dict[str, int] = {}
        self.limits: dict[str, PurchaseLimit] = {}
        self._global_bought: dict[str, int] = {}
        self._player_bought: dict[str, dict[str, int]] = {}
        self.price_min_ratio = price_min_ratio
        self.price_max_ratio = price_max_ratio
        self.event_impact_cap = event_impact_cap
        self.sell_min_ratio = sell_min_ratio
        self.buyback: deque = deque(maxlen=buyback_capacity)
        self.trade_tolerance = trade_tolerance
        self.trade_hard_reject_ratio = trade_hard_reject_ratio

    # bug1: 刷新时间按玩家ID做相位偏移，不同玩家错开
    def _phase(self, player_id) -> int:
        return zlib.crc32(str(player_id).encode("utf-8")) % self.refresh_interval

    def refresh(self, player_id):
        self.refresh_time += 1
        next_tick = self.refresh_time + self._phase(player_id)
        self._player_next_refresh[player_id] = next_tick
        return next_tick

    def next_refresh(self, player_id) -> int:
        return self._player_next_refresh.get(player_id, self.refresh_time + self._phase(player_id))

    # bug2: 限购分个人限购和全局限购，均可配置
    def set_limit(self, iid: str, per_player: int | None = None,
                  global_limit: int | None = None):
        self.limits[iid] = PurchaseLimit(per_player, global_limit)

    def buy(self, iid, player_id):
        item = self.items[iid]
        if item.stock <= 0:
            return False
        limit = self.limits.get(iid)
        if limit is not None:
            if limit.global_limit is not None and \
                    self._global_bought.get(iid, 0) >= limit.global_limit:
                return False
            if limit.per_player is not None and \
                    self._player_bought.get(player_id, {}).get(iid, 0) >= limit.per_player:
                return False
        item.stock -= 1
        self._global_bought[iid] = self._global_bought.get(iid, 0) + 1
        self._player_bought.setdefault(player_id, {})[iid] = \
            self._player_bought.setdefault(player_id, {}).get(iid, 0) + 1
        self.player_gold -= item.price
        return True

    # bug3: 价格波动有 min-max 钳制，事件影响有上限
    def _base(self, item: ShopItem) -> float:
        return item.base_price if item.base_price else item.price

    def adjust_price(self, iid: str, delta_ratio: float, *, is_event: bool = False) -> float:
        item = self.items[iid]
        if is_event:
            delta_ratio = max(-self.event_impact_cap,
                              min(self.event_impact_cap, delta_ratio))
        base = self._base(item)
        lo, hi = base * self.price_min_ratio, base * self.price_max_ratio
        item.price = max(lo, min(hi, item.price * (1 + delta_ratio)))
        return item.price

    # bug4: 出售价格按耐久比例折算，且有最低价格
    def sell(self, item, durability):
        max_dur = item.max_durability or 100.0
        ratio = max(0.0, min(1.0, durability / max_dur))
        return max(item.price * ratio, item.price * self.sell_min_ratio)

    # bug5: 回购列表有最大容量，超出后最旧的被挤出
    def add_buyback(self, item):
        self.buyback.append(item)  # deque(maxlen) 自动挤出最旧

    # bug6: 交易价格有服务器端估值校验，异常价格需二次确认或拒绝
    def estimate(self, item: ShopItem) -> float:
        return self.sell(item, item.durability)

    def trade(self, seller_id, buyer_id, item: ShopItem, price: float,
              *, confirmed: bool = False) -> TradeResult:
        estimate = self.estimate(item)
        if estimate <= 0:
            deviation = 0.0 if price == 0 else float("inf")
        else:
            deviation = abs(price - estimate) / estimate
        if estimate > 0 and (price > estimate * self.trade_hard_reject_ratio
                             or price < estimate / self.trade_hard_reject_ratio):
            return TradeResult("rejected", estimate, deviation)
        if deviation <= self.trade_tolerance:
            return TradeResult("ok", estimate, deviation)
        if confirmed:
            return TradeResult("confirmed", estimate, deviation)
        return TradeResult("needs_confirmation", estimate, deviation)
