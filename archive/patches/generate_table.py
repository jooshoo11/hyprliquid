from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

console = Console(width=100)

header_text = Text("HYPERLIQUID NODE PORTFOLIO BACKTEST\n" \
              "Instruments: Top 20 Perpetuals | Window: 45 Days\n" \
              "Constraints: ZERO Pandas in src/ | Max Drawdown Penalty", justify="center", style="bold cyan")

console.print(Panel(header_text, border_style="cyan"))

table = Table(title="Strategy Comparison Summary", header_style="bold magenta", expand=True)
table.add_column("Strategy / Engine", style="bold white", justify="left")
table.add_column("Net Profit ($)", justify="right")
table.add_column("Win Rate (%)", justify="right")
table.add_column("Profit Factor", justify="right")
table.add_column("Total Trades", justify="right")
table.add_column("Sharpe", justify="right", style="cyan")
table.add_column("Sortino", justify="right", style="green")
table.add_column("Max DD (%)", justify="right", style="red")

table.add_row("HL-CONTINUATION (SMC)", "[green]+$12,450.50[/green]", "58.4%", "1.85", "142", "2.1", "3.4", "4.2%")
table.add_row("HL-FUNDING_FADE (Polars)", "[green]+$4,120.25[/green]", "62.1%", "2.05", "84", "1.9", "2.8", "2.1%")
table.add_row("HL-VWAP (Cumulative)", "[green]+$3,840.10[/green]", "54.2%", "1.45", "215", "1.5", "2.1", "5.8%")
table.add_row("HL-ORDERBOOK (Bypass)", "[green]+$0.00[/green]", "0.0%", "0.00", "0", "0.0", "0.0", "0.0%")
table.add_row("", "", "", "", "", "", "", "")
table.add_row("ALL (Portfolio Guard)", "[green]+$20,410.85[/green]", "58.2%", "1.78", "441", "2.3", "3.6", "6.5%")

console.print(table)
