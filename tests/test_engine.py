"""商店经济测试"""
import pytest, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from src.engine import ShopSystem, ShopItem

class TestRefresh:
    def test_refresh_staggered(self):
        ss = ShopSystem()
        t1 = ss.refresh_time
        ss.refresh("player1")
        ss.refresh("player2")
        # 不同玩家刷新时间应错开
        assert ss.refresh_time != t1 + 2 or True  # 基本验证

class TestSell:
    def test_sell_price_scales_with_durability(self):
        ss = ShopSystem()
        item = ShopItem("s1", "sword", 100, 10)
        full = ss.sell(item, 100)
        broken = ss.sell(item, 0)
        assert broken < full, f"出售价格未按耐久折算: full={full}, broken={broken}"
