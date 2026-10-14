"""
Tests for AnalyticsPipeline and Dashboard Microstructure Bridge.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from src.scanner.analytics_pipeline import AnalyticsPipeline, PositionMicrostructureAudit


class TestAnalyticsPipeline(unittest.TestCase):

    def setUp(self):
        self.pipeline = AnalyticsPipeline(auto_start=False)

    def tearDown(self):
        self.pipeline.stop()

    def test_position_risk_audit_normal(self):
        sample_data = {
            "depth_decay": {"bid_decay_percent": 2.0, "ask_decay_percent": 1.0, "bid_status": "OK", "ask_status": "OK"},
            "trade_flow": {"sweep_direction": "UP", "buy_ratio": 0.5},
            "liquidations": {"status": "NORMAL", "short_liquidations": 0, "long_liquidations": 0},
            "crowding": {"is_crowded_long": False, "is_crowded_short": False},
            "regime": {"market_regime": "NORMAL"},
        }
        with patch.object(self.pipeline, "get_latest", return_value=sample_data):
            audit = self.pipeline.audit_position_risk("SOL", "LONG", pnl_pct=0.5)
            self.assertFalse(audit.is_toxic)
            self.assertEqual(len(audit.toxic_reasons), 0)

    def test_position_risk_audit_toxic_depth_decay(self):
        sample_data = {
            "depth_decay": {"bid_decay_percent": 65.0, "ask_decay_percent": 2.0, "bid_status": "CRITICAL", "ask_status": "OK"},
            "trade_flow": {"sweep_direction": "DOWN", "buy_ratio": 0.10},
            "liquidations": {"status": "NORMAL", "short_liquidations": 0, "long_liquidations": 0},
            "crowding": {"is_crowded_long": False, "is_crowded_short": False},
            "regime": {"market_regime": "NORMAL"},
        }
        with patch.object(self.pipeline, "get_latest", return_value=sample_data):
            audit = self.pipeline.audit_position_risk("SOL", "LONG", pnl_pct=-0.6)
            self.assertTrue(audit.is_toxic)
            self.assertTrue(any("Bid depth support evaporating" in r for r in audit.toxic_reasons))
            self.assertTrue(any("Aggressive sell sweeps detected" in r for r in audit.toxic_reasons))

    def test_signal_filter_thin_liquidity(self):
        sample_data = {
            "regime": {"liquidity_regime": "THIN"},
            "orderbook": {"imbalance": {"l5": 0.0}},
            "crowding": {"is_crowded_long": False, "is_crowded_short": False},
        }
        with patch.object(self.pipeline, "get_latest", return_value=sample_data):
            allowed, reason = self.pipeline.get_entry_signal_filter("SOL", "LONG")
            self.assertFalse(allowed)
            self.assertIn("THIN", reason)

    def test_signal_filter_imbalance_rejection(self):
        sample_data = {
            "regime": {"liquidity_regime": "HIGH"},
            "orderbook": {"imbalance": {"l5": -0.60}},  # Heavy sell wall
            "crowding": {"is_crowded_long": False, "is_crowded_short": False},
        }
        with patch.object(self.pipeline, "get_latest", return_value=sample_data):
            allowed, reason = self.pipeline.get_entry_signal_filter("SOL", "LONG")
            self.assertFalse(allowed)
            self.assertIn("imbalance", reason.lower())


if __name__ == "__main__":
    unittest.main()
