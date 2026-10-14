import os
import json
import numpy as np
import pandas as pd
import pytest

from hyperliquid_nautilus.features.microstructure_features import FeatureEngineer
from hyperliquid_nautilus.backtesting.triple_barrier_labeler import apply_triple_barrier
from hyperliquid_nautilus.models.trainer import XGBoostTrainer
from hyperliquid_nautilus.backtesting.walk_forward import WalkForwardValidator
from hyperliquid_nautilus.risk.position_guard import DynamicPositionGuard


@pytest.fixture
def sample_ohlcv_df():
    """Generates 200 bars of synthetic OHLCV data."""
    np.random.seed(42)
    n = 200
    dates = pd.date_range("2025-01-01", periods=n, freq="15min")
    price = 100.0 + np.cumsum(np.random.randn(n) * 0.5)
    high = price + np.random.uniform(0.1, 1.0, n)
    low = price - np.random.uniform(0.1, 1.0, n)
    volume = np.random.uniform(100.0, 1000.0, n)

    df = pd.DataFrame(
        {
            "open": price,
            "high": high,
            "low": low,
            "close": price + np.random.uniform(-0.2, 0.2, n),
            "volume": volume,
            "funding_rate": np.random.uniform(-0.0005, 0.0005, n),
            "bid_vol": np.random.uniform(50.0, 500.0, n),
            "ask_vol": np.random.uniform(50.0, 500.0, n),
        },
        index=dates,
    )
    return df


def test_feature_engineering_with_funding_and_l2(sample_ohlcv_df):
    engineer = FeatureEngineer(sample_ohlcv_df)
    features_df = engineer.generate_features()

    expected_cols = [
        "rvol",
        "funding_zscore",
        "obi",
        "atr_expansion",
        "rsi_14",
        "macd_hist",
        "adx_14",
    ]
    for col in expected_cols:
        assert col in features_df.columns
        assert not features_df[col].isna().any()
        assert not np.isinf(features_df[col]).any()

    assert len(features_df) > 0


def test_feature_engineering_fallbacks():
    np.random.seed(42)
    n = 100
    dates = pd.date_range("2025-01-01", periods=n, freq="15min")
    df = pd.DataFrame(
        {
            "open": np.full(n, 100.0),
            "high": np.full(n, 102.0),
            "low": np.full(n, 98.0),
            "close": np.full(n, 101.0),
            "volume": np.full(n, 500.0),
        },
        index=dates,
    )

    engineer = FeatureEngineer(df)
    features_df = engineer.generate_features()

    assert "funding_zscore" in features_df.columns
    assert (features_df["funding_zscore"] == 0.0).all()

    # OBI fallback to (high - low) / close
    assert "obi" in features_df.columns
    expected_obi = (102.0 - 98.0) / 101.0
    np.testing.assert_allclose(features_df["obi"].values, expected_obi, rtol=1e-5)


def test_triple_barrier_labeler(sample_ohlcv_df):
    df_labeled = apply_triple_barrier(sample_ohlcv_df, pt_mult=2.5, sl_mult=1.2, max_hold=12)

    assert "target" in df_labeled.columns
    assert "sample_weight" in df_labeled.columns

    # Targets should be binary 0 or 1
    assert set(df_labeled["target"].unique()).issubset({0, 1})

    # Sample weights should be strictly positive
    assert (df_labeled["sample_weight"] > 0).all()
    assert len(df_labeled) > 0


def test_xgboost_trainer():
    np.random.seed(42)
    X = np.random.randn(100, 5)
    y = np.random.randint(0, 2, 100)
    w = np.random.uniform(0.5, 1.5, 100)

    X_train, y_train, w_train = X[:70], y[:70], w[:70]
    X_val, y_val, w_val = X[70:], y[70:], w[70:]

    trainer = XGBoostTrainer(scale_pos_weight=2.0)
    trainer.train(X_train, y_train, w_train, X_val, y_val, w_val)

    metrics = trainer.evaluate(X_val, y_val)
    assert "accuracy" in metrics
    assert "precision" in metrics
    assert "auc" in metrics
    assert 0.0 <= metrics["accuracy"] <= 1.0
    assert 0.0 <= metrics["auc"] <= 1.0

    preds = trainer.predict(X_val)
    assert len(preds) == len(X_val)
    probs = trainer.predict_proba(X_val)
    assert probs.shape == (len(X_val), 2)


def test_walk_forward_validator():
    np.random.seed(42)
    # Generate 60 days of data to test multiple folds
    n = 60 * 96  # 96 15m bars per day
    dates = pd.date_range("2025-01-01", periods=n, freq="15min")
    price = 100.0 + np.cumsum(np.random.randn(n) * 0.2)
    df = pd.DataFrame(
        {
            "open": price,
            "high": price + 1.0,
            "low": price - 1.0,
            "close": price + 0.1,
            "volume": np.random.uniform(100, 1000, n),
        },
        index=dates,
    )

    engineer = FeatureEngineer(df)
    features_df = engineer.generate_features()
    labeled_df = apply_triple_barrier(features_df, pt_mult=2.5, sl_mult=1.2, max_hold=12)

    features = ["rvol", "funding_zscore", "obi", "atr_expansion", "rsi_14", "macd_hist", "adx_14"]
    trainer = XGBoostTrainer(scale_pos_weight=2.0)

    validator = WalkForwardValidator(train_days=14, test_days=7)
    metrics = validator.run(labeled_df, features, trainer)

    assert len(metrics) > 0
    for fold in metrics:
        assert "fold_start" in fold
        assert "accuracy" in fold
        assert "precision" in fold
        assert "auc" in fold

    # Check exported file
    assert os.path.exists("config/optimized_params.json")
    with open("config/optimized_params.json", "r") as f:
        saved_data = json.load(f)
    assert "metrics" in saved_data
    assert "top_features" in saved_data
    assert saved_data["top_features"] == features


def test_dynamic_position_guard():
    # MAJOR asset (e.g. BTC, ETH, SOL)
    guard_major = DynamicPositionGuard(account_balance=10000.0, asset_type="MAJOR")

    # High conviction: >= 0.75
    high_res = guard_major.calculate_trade_size(0.80)
    assert high_res["status"] == "Approved"
    assert high_res["conviction_tier"] == "High"
    assert high_res["risk_pct"] == 0.020
    assert high_res["max_leverage_allowed"] == 5.0

    # Standard conviction: 0.65 <= prob < 0.75
    std_res = guard_major.calculate_trade_size(0.70)
    assert std_res["status"] == "Approved"
    assert std_res["conviction_tier"] == "Standard"
    assert std_res["risk_pct"] == 0.015
    assert std_res["max_leverage_allowed"] == 5.0

    # Base conviction: 0.55 <= prob < 0.65
    base_res = guard_major.calculate_trade_size(0.58)
    assert base_res["status"] == "Approved"
    assert base_res["conviction_tier"] == "Base"
    assert base_res["risk_pct"] == 0.010
    assert base_res["max_leverage_allowed"] == 5.0

    # Vetoed: prob < 0.55
    veto_res = guard_major.calculate_trade_size(0.50)
    assert veto_res["status"] == "Vetoed"
    assert veto_res["risk_pct"] == 0.0

    # ALT asset leverage cap
    guard_alt = DynamicPositionGuard(account_balance=10000.0, asset_type="ALT")
    alt_res = guard_alt.calculate_trade_size(0.80)
    assert alt_res["max_leverage_allowed"] == 3.0

    # MEME asset leverage cap
    guard_meme = DynamicPositionGuard(account_balance=10000.0, asset_type="MEME")
    meme_res = guard_meme.calculate_trade_size(0.80)
    assert meme_res["max_leverage_allowed"] == 2.0


def test_high_conviction_momentum_strategy_init():
    from src.strategies.high_conviction_momentum import (
        HighConvictionMomentumStrategy,
        HighConvictionMomentumConfig,
        InstrumentBarHistory,
    )

    config = HighConvictionMomentumConfig(venue="HYPERLIQUID", risk_per_trade_pct=0.01)
    strat = HighConvictionMomentumStrategy(config=config)
    assert strat._get_asset_type("BTC") == "MAJOR"
    assert strat._get_asset_type("SOL") == "MAJOR"
    assert strat._get_asset_type("DOGE") == "MEME"
    assert strat._get_asset_type("PEPE") == "MEME"
    assert strat._get_asset_type("AVAX") == "ALT"

    # Test bar history buffering
    history = InstrumentBarHistory()
    assert history.to_dataframe().empty
