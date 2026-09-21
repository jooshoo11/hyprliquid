import re

with open("src/execution/node_runner.py", "r") as f:
    code = f.read()

# 1. Fetch ctx map at the start of create_surveillance_table
fetch_code = """
def create_surveillance_table(
    top_coins: List[str],
    continuation_strat: Optional[TrendContinuationSMC],
    funding_strat: Optional[HourlyFundingFade],
    scalp_strat: Optional[OrderBookImbalance],
    vwap_strat: Optional[VwapOiMomentum],
    guard: PortfolioGuard,
    info_client: HyperliquidInfoClient,
    node: Optional[TradingNode],
    wallet_address: str,
    is_ephemeral: bool,
) -> Table:
    try:
        meta, asset_ctxs = info_client.get_meta_and_asset_ctxs()
        ctx_map = {m.get("name"): ctx for m, ctx in zip(meta.get("universe", []), asset_ctxs)}
    except Exception:
        ctx_map = {}
"""

code = re.sub(
    r"def create_surveillance_table\(.*?\) -> Table:\n    \"\"\"Generate the Rich interactive dashboard for multi-strategy node state\.\"\"\"",
    fetch_code.strip() + '\n    """Generate the Rich interactive dashboard for multi-strategy node state."""',
    code,
    flags=re.DOTALL
)


# 2. Add columns
col_replace = """
    table.add_column("Symbol", justify="left", style="bold white")
    table.add_column("Last Price", justify="right", style="cyan")
    table.add_column("5M Gain", justify="right")
    table.add_column("30M Gain", justify="right")
    table.add_column("24H Gain", justify="right")
    table.add_column("4H Trend", justify="center")
    table.add_column("30M Zones", justify="center")
    table.add_column("5M Structure", justify="left")
    table.add_column("Funding APR", justify="right")
    table.add_column("Active Position", justify="center")
"""

code = re.sub(
    r"    table\.add_column\(\"Symbol\".*?table\.add_column\(\"Active Position\", justify=\"center\"\)",
    col_replace.strip(),
    code,
    flags=re.DOTALL
)

# 3. Calculate gains and append rows
loop_replace = """
        # Price and Context
        ctx = ctx_map.get(coin, {})
        current_px = float(ctx.get("oraclePx", 0.0))
        prev_day_px = float(ctx.get("prevDayPx", 0.0))
        
        price_str = f"${current_px:,.4f}" if current_px else "Awaiting"
        
        # 24H Gain (resetting at midnight UTC per Hyperliquid prevDayPx)
        gain_24h = ((current_px - prev_day_px) / prev_day_px) * 100 if prev_day_px and current_px else 0.0
        g24_color = "green" if gain_24h >= 0 else "red"
        g24_sign = "+" if gain_24h >= 0 else ""
        g24_str = f"[{g24_color}]{g24_sign}{gain_24h:.2f}%[/{g24_color}]"

        # 5M and 30M Gain
        gain_5m_str = "[dim]-[/dim]"
        gain_30m_str = "[dim]-[/dim]"
        if cont_state and current_px:
            if cont_state.last_5m_bar:
                open_5m = cont_state.last_5m_bar.open.as_double()
                g5 = ((current_px - open_5m) / open_5m) * 100
                g5_color = "green" if g5 >= 0 else "red"
                gain_5m_str = f"[{g5_color}]{'+' if g5 >= 0 else ''}{g5:.2f}%[/{g5_color}]"
            
            if cont_state.recent_30m_bars:
                open_30m = cont_state.recent_30m_bars[-1].open.as_double()
                g30 = ((current_px - open_30m) / open_30m) * 100
                g30_color = "green" if g30 >= 0 else "red"
                gain_30m_str = f"[{g30_color}]{'+' if g30 >= 0 else ''}{g30:.2f}%[/{g30_color}]"

        # 4H Trend
"""

code = re.sub(
    r"        # Price extraction.*?# 4H Trend",
    loop_replace.strip() + "\n",
    code,
    flags=re.DOTALL
)

# 4. Modify table.add_row
add_row_replace = """
        table.add_row(
            coin,
            price_str,
            gain_5m_str,
            gain_30m_str,
            g24_str,
            trend_str,
            zones_str,
            struct_str,
            f"[{funding_color}]{funding_str}[/{funding_color}]",
            f"[{pos_color}]{pos_str}[/{pos_color}]",
        )
"""
code = re.sub(
    r"        table\.add_row\([\s\S]*?f\"\[\{pos_color\}\]\{pos_str\}\[/\{pos_color\}\]\",\n        \)",
    add_row_replace.strip(),
    code
)

with open("src/execution/node_runner.py", "w") as f:
    f.write(code)

print("node_runner patched!")
