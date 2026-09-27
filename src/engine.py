"""商店经济 - 带6个bug"""
from dataclasses import dataclass, field

@dataclass
class ShopItem:
    iid: str
    name: str
    price: float
    stock: int
    durability: float = 100.0

class ShopSystem:
    def __init__(self):
        self.items: dict[str, ShopItem] = {}
        self.player_gold = 1000.0
        self.buyback: list = []  # bug5: 无限制
        self.refresh_time = 0  # bug1: 固定刷新
        self.global_limit: dict[str, int] = {}  # bug2: 全局限购

    def refresh(self, player_id):
        # bug1: 所有玩家同时刷新
        self.refresh_time += 1

    def buy(self, iid, player_id):
        item = self.items[iid]
        if item.stock <= 0:
            return False
        if self.global_limit.get(iid, 999) <= 0:
            return False
        item.stock -= 1
        self.global_limit[iid] = self.global_limit.get(iid, 999) - 1
        self.player_gold -= item.price
        return True

    def sell(self, item, durability):
        # bug4: 不看耐久
        return item.price

    def add_buyback(self, item):
        # bug5: 无数量限制
        self.buyback.append(item)
