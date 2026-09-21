"""
Hyperliquid Info MCP Client & Market Discovery Service with Polars Engine.
Re-exports HyperliquidInfoClient and Polars calculation utilities from src.scanner.mcp_client.
"""

from src.scanner.mcp_client import HyperliquidInfoClient, compute_candle_features

__all__ = ["HyperliquidInfoClient", "compute_candle_features"]
