"""
Strategy: HighConvictionMomentum (high_conviction_momentum.py)
Autonomous High-Conviction Momentum Trading Strategy:
- Computes microstructure features (RVOL, funding z-score, OBI spread proxy, ATR expansion, RSI, MACD hist, ADX).
- Runs trained XGBoost classifier with early stopping and asymmetric precision penalty.
- Dynamic position sizing and confidence gating via DynamicPositionGuard.
- Places triple-barrier take-profit and stop-loss bracket orders.
"""

from dataclasses import dataclass, field
from decimal import Decimal
import os
import json
import time
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

import polars as pl
import numpy as np

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId, Venue
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.trading.strategy import Strategy

from hyperliquid_nautilus.features.microstructure_features import FeatureEngineer
from hyperliquid_nautilus.risk.position_guard import DynamicPositionGuard
from hyperliquid_nautilus.models.trainer import XGBoostTrainer
from src.utils.instruments import get_coin_max_leverage


class HighConvictionMomentumConfig(StrategyConfig, kw_only=True):
    """Configuration for HighConvictionMomentum strategy."""
    venue: str = "HYPERLIQUID"
    min_confidence_prob: float = 0.55
    max_active_positions: int = 3
    risk_per_trade_pct: float = 0.02
    pt_mult: float = 2.5
    sl_mult: float = 1.2
    model_path: str = "config/momentum_xgboost.json"


@dataclass
class InstrumentBarHistory:
    """Maintains rolling bar history for feature engineering."""
    bars: List[Dict[str, float]] = field(default_factory=list)
    max_bars: int = 150

    def add_bar(self, bar: Bar) -> None:
        dt = datetime.fromtimestamp(bar.ts_event / 1_000_000_000, tz=timezone.utc)
        self.bars.append({
            "datetime": dt,
            "open": bar.open.as_double(),
            "high": bar.high.as_double(),
            "low": bar.low.as_double(),
            "close": bar.close.as_double(),
            "volume": bar.volume.as_double(),
        })
        if len(self.bars) > self.max_bars:
            self.bars.pop(0)

    def to_dataframe(self) -> Any:
        if not self.bars:
            return pl.DataFrame().to_pandas()
        df_pl = pl.DataFrame(self.bars)
        df = df_pl.to_pandas()
        if "datetime" in df.columns:
            df.set_index("datetime", inplace=True)
        return df


class HighConvictionMomentumStrategy(Strategy):
    """
    NautilusTrader Strategy implementing High-Conviction Momentum XGBoost pipeline.
    """

    def __init__(self, config: HighConvictionMomentumConfig, portfolio_guard: Optional[Any] = None) -> None:
        super().__init__(config)
        self.momentum_config: HighConvictionMomentumConfig = config
        self.portfolio_guard = portfolio_guard
        self.instruments_map: Dict[str, Instrument] = {}
        self.histories: Dict[str, InstrumentBarHistory] = {}
        self.trainer: Optional[XGBoostTrainer] = None
        self._last_order_ts: Dict[str, int] = {}

    def on_start(self) -> None:
        """Initialize XGBoost model and subscribe to bars."""
        # Load pre-trained model if available
        model_file = self.momentum_config.model_path
        if os.path.exists(model_file):
            try:
                self.trainer = XGBoostTrainer(scale_pos_weight=2.0)
                self.trainer.load_model(model_file)
                self.log.info(f"Loaded XGBoost momentum model from {model_file}")
            except Exception as e:
                self.log.warning(f"Could not load pre-trained model: {e}")
                self.trainer = None
        else:
            self.log.warning(f"Model file {model_file} not found; initializing trainer fallback.")
            self.trainer = XGBoostTrainer(scale_pos_weight=2.0)

        instruments = [i for i in self.cache.instruments() if str(i.id.venue) == self.momentum_config.venue]
        self.log.info(f"HighConvictionMomentumStrategy active on {len(instruments)} instruments")

        for instrument in instruments:
            instr_str = str(instrument.id)
            self.instruments_map[instr_str] = instrument
            self.histories[instr_str] = InstrumentBarHistory()
            self.subscribe_bars(BarType.from_str(f"{instr_str}-15-MINUTE-LAST-EXTERNAL"))
            if not getattr(self.cache, "is_backtest", False):
                self.subscribe_quote_ticks(instrument.id)

    def _get_asset_type(self, coin: str) -> str:
        """Classify coin into MAJOR, MEME, or ALT for DynamicPositionGuard."""
        coin = coin.upper()
        if coin in ("BTC", "ETH", "SOL"):
            return "MAJOR"
        elif coin in ("DOGE", "PEPE", "KPEPE", "WIF", "FARTCOIN", "PUMP", "TRUMP", "MEME", "SHIB", "BONK", "FLOKI"):
            return "MEME"
        else:
            return "ALT"

    def _get_account_equity(self) -> float:
        """Determine account equity from portfolio or portfolio_guard."""
        if hasattr(self, "portfolio") and self.portfolio:
            try:
                eq = self.portfolio.equity(USD)
                if eq is not None:
                    val = float(eq.as_double())
                    if val > 0:
                        return val
            except Exception:
                pass
            try:
                account = self.portfolio.account(self.venue)
                if account is not None:
                    bal = account.balance_total(USD)
                    if bal is not None:
                        val = float(bal.as_double())
                        if val > 0:
                            return val
            except Exception:
                pass
        if self.portfolio_guard and hasattr(self.portfolio_guard, "current_equity"):
            eq = float(self.portfolio_guard.current_equity)
            if eq > 0:
                return eq
        return 200.0  # Fallback default

    def on_bar(self, bar: Bar) -> None:
        """Compute microstructure features, score XGBoost model, gate sizing, and execute."""
        instr_str = str(bar.bar_type.instrument_id)
        instrument = self.instruments_map.get(instr_str)
        history = self.histories.get(instr_str)
        if not instrument or not history:
            return

        history.add_bar(bar)
        if len(history.bars) < 35:
            return  # Need sufficient bars for rolling indicators (ATR 30, SMA 20, etc.)

        # Check existing open position or pending order
        if not self.portfolio.is_flat(instrument.id):
            return
        if len(self.cache.orders_open(instrument_id=instrument.id)) > 0:
            return

        active_count = self.cache.positions_open_count()
        if active_count >= self.momentum_config.max_active_positions:
            return

        # Rate limit order submissions per instrument
        now_ns = bar.ts_event
        if (now_ns - self._last_order_ts.get(instr_str, 0)) < 60_000_000_000:
            return

        # 1. Feature Engineering
        try:
            df = history.to_dataframe()
            engineer = FeatureEngineer(df)
            feat_df = engineer.generate_features()
            if feat_df.empty:
                return
        except Exception as fe_err:
            self.log.debug(f"Feature calculation error on {instr_str}: {fe_err}")
            return

        feature_cols = ['rvol', 'funding_zscore', 'obi', 'atr_expansion', 'rsi_14', 'macd_hist', 'adx_14']
        latest_row = feat_df.iloc[-1]
        X = latest_row[feature_cols].values.reshape(1, -1)

        # 2. XGBoost Model Probability
        prob = 0.5
        if self.trainer is not None:
            try:
                prob = float(self.trainer.predict_proba(X)[0, 1])
            except Exception as inf_err:
                self.log.debug(f"Inference error on {instr_str}: {inf_err}")
                prob = 0.5

        # 3. Dynamic Sizing & Confidence Gating
        equity = self._get_account_equity()
        coin = instr_str.split("-")[0].split(".")[0].upper()
        asset_type = self._get_asset_type(coin)

        position_guard = DynamicPositionGuard(account_balance=equity, asset_type=asset_type)
        sizing_decision = position_guard.calculate_trade_size(prediction_prob=prob)

        if sizing_decision.get("status") != "Approved":
            return  # Vetoed / Confidence below 0.55 threshold

        risk_pct = sizing_decision.get("risk_pct", 0.01)
        max_lev = float(get_coin_max_leverage(coin))
        conviction = sizing_decision.get("conviction_tier", "Base")

        # 4. Entry & Triple-Barrier Calculation
        close_px = bar.close.as_double()
        atr_14 = float(latest_row.get("atr_5", close_px * 0.015))  # Use ATR proxy
        if atr_14 <= 0:
            atr_14 = close_px * 0.015

        pt_mult = self.momentum_config.pt_mult
        sl_mult = self.momentum_config.sl_mult

        entry_price = close_px
        tp_price = entry_price + (pt_mult * atr_14)
        sl_price = entry_price - (sl_mult * atr_14)

        # Deploy $100 base margin per position (50% of account base) scaled by coin's max exchange leverage
        max_positions = float(getattr(self.portfolio_guard, "max_total_open_positions", 2) if self.portfolio_guard else 2)
        base_margin = (equity / max_positions) if max_positions > 0 else (equity * 0.50)
        target_notional = base_margin * max_lev
        # Risk budget: dollar loss at the stop must stay within 6% of equity (matches UnifiedEngine.max_risk_pct_per_trade)
        sl_frac = abs(entry_price - sl_price) / entry_price if entry_price > 0 else 0.0
        if sl_frac > 0 and target_notional * sl_frac > equity * 0.06:
            target_notional = (equity * 0.06) / sl_frac
        target_notional = max(11.0, target_notional)
        qty_val = target_notional / entry_price

        step = Decimal(str(instrument.size_increment.as_double()))
        qty_dec = (Decimal(str(round(qty_val, instrument.size_precision))) // step) * step
        if qty_dec <= 0:
            return

        quantity = instrument.make_qty(qty_dec)
        if quantity.as_double() <= 0:
            return

        notional_usd = float(quantity.as_double()) * entry_price
        if notional_usd < 11.0:
            return

        # PortfolioGuard check
        if self.portfolio_guard is not None:
            self.portfolio_guard.update_equity(equity)
            can_trade, reason = self.portfolio_guard.can_open_position(
                strategy_name=self.__class__.__name__,
                instrument_id=instrument.id,
                side=OrderSide.BUY,
                proposed_notional_usd=notional_usd,
                current_open_positions_count=self.cache.positions_open_count(),
                leverage=max_lev,
            )
            if not can_trade:
                return

        entry_obj = instrument.make_price(Decimal(str(round(entry_price, instrument.price_precision))))
        sl_obj = instrument.make_price(Decimal(str(round(sl_price, instrument.price_precision))))
        tp_obj = instrument.make_price(Decimal(str(round(tp_price, instrument.price_precision))))

        self._last_order_ts[instr_str] = now_ns
        msg = (
            f"🚀 [HighConvictionMomentum] BUY {instrument.id}: Prob={prob:.2%} | Conviction={conviction} | "
            f"Qty={quantity} @ {entry_obj} | SL={sl_obj} | TP={tp_obj} | Notional=${notional_usd:.2f}"
        )
        self.log.info(msg)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

        try:
            bracket_list = self.order_factory.bracket(
                instrument_id=instrument.id,
                order_side=OrderSide.BUY,
                quantity=quantity,
                entry_order_type=OrderType.MARKET,
                entry_price=entry_obj,
                time_in_force=TimeInForce.IOC,
                tp_order_type=OrderType.LIMIT,
                tp_price=tp_obj,
                sl_order_type=OrderType.STOP_MARKET,
                sl_trigger_price=sl_obj,
            )
            self.submit_order_list(bracket_list)
            if self.portfolio_guard is not None and bracket_list.first is not None:
                self.portfolio_guard.register_order_submitted(
                    order=bracket_list.first,
                    strategy_name=self.__class__.__name__,
                    notional_usd=notional_usd,
                )
        except Exception as ord_err:
            self.log.warning(f"Error submitting HighConvictionMomentum order: {ord_err}")
