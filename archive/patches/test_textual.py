from textual.app import App, ComposeResult
from textual.widgets import DataTable, Header, Footer
import time
import threading

class NodeApp(App):
    BINDINGS = [("d", "toggle_dark", "Toggle dark mode"), ("q", "quit", "Quit")]

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable()
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("Symbol", "Price", "5M Gain", "30M Gain", "24H Gain", "Positions")
        self.update_timer = self.set_interval(1.0, self.update_table)

    def update_table(self) -> None:
        table = self.query_one(DataTable)
        table.clear()
        table.add_row("BTC", "$50000", "+0.5%", "+1.0%", "+2.5%", "FLAT")
        table.add_row("ETH", "$3000", "+0.1%", "+0.5%", "+1.5%", "LONG 1.0")

if __name__ == "__main__":
    app = NodeApp()
    app.run()
