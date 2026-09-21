![image](./assets/head-image.png)

# Hyperliquid Monitor

A Python package for monitoring trades and orders on Hyperliquid DEX in real-time. This package allows you to track specific addresses and receive notifications when trades are executed or orders are placed/cancelled.

## Features

- Real-time monitoring of trades and orders
- Support for multiple addresses
- Optional SQLite database storage
- Callback system for custom notifications
- Clean shutdown handling
- Proper trade type definitions using dataclasses

## Installation

### Using Poetry (recommended)

```bash
poetry add hyperliquid-monitor
```

### Using pip

```bash
pip install hyperliquid-monitor
```

## Quick Start

### Simple Console Notification

Here's a basic example that monitors an address and prints trades to the console:

```python
from hyperliquid_monitor import HyperliquidMonitor
from hyperliquid_monitor.types import Trade
from datetime import datetime

def print_trade(trade: Trade):
    """Print trade information to console with colors"""
    timestamp = trade.timestamp.strftime('%Y-%m-%d %H:%M:%S')
    
    # Color codes
    GREEN = '\033[92m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    RESET = '\033[0m'
    
    # Choose color based on trade type and side
    color = GREEN if trade.side == "BUY" else RED
    
    print(f"\n{BLUE}[{timestamp}]{RESET} New {trade.trade_type}:")
    print(f"Address: {trade.address}")
    print(f"Coin: {trade.coin}")
    print(f"{color}Side: {trade.side}{RESET}")
    print(f"Size: {trade.size}")
    print(f"Price: {trade.price}")
    
    if trade.trade_type == "FILL":
        print(f"Direction: {trade.direction}")
        if trade.closed_pnl:
            pnl_color = GREEN if trade.closed_pnl > 0 else RED
            print(f"PnL: {pnl_color}{trade.closed_pnl:.2f}{RESET}")
        print(f"Hash: {trade.tx_hash}")

def main():
    # List of addresses to monitor
    addresses = [
        "0x010461C14e146ac35Fe42271BDC1134EE31C703a"  # Example address
    ]

    # Create monitor with console notifications and optional database
    monitor = HyperliquidMonitor(
        addresses=addresses,
        db_path="trades.db",  # Optional: remove to disable database
        callback=print_trade
    )

    try:
        print("Starting monitor... Press Ctrl+C to exit")
        monitor.start()
    except KeyboardInterrupt:
        monitor.stop()

if __name__ == "__main__":
    main()
```

### Trade Object Structure

The `Trade` object contains the following information:

```python
@dataclass
class Trade:
    timestamp: datetime      # When the trade occurred
    address: str            # The address that made the trade
    coin: str              # The traded coin/token
    side: Literal["BUY", "SELL"]  # Trade side
    size: float            # Trade size
    price: float           # Trade price
    trade_type: Literal["FILL", "ORDER_PLACED", "ORDER_CANCELLED"]
    direction: Optional[str] = None  # e.g., "Open Long", "Close Short"
    tx_hash: Optional[str] = None    # Transaction hash for fills
    fee: Optional[float] = None      # Trading fee
    fee_token: Optional[str] = None  # Fee token (e.g., "USDC")
    start_position: Optional[float] = None  # Position size before trade
    closed_pnl: Optional[float] = None     # Realized PnL for closing trades
    order_id: Optional[int] = None         # Order ID for orders
```

## Database Storage

If you provide a `db_path`, trades will be stored in an SQLite database with two tables:

### Fills Table
- timestamp: When the trade occurred
- address: Trader's address
- coin: Traded asset
- side: BUY/SELL
- size: Trade size
- price: Trade price
- direction: Trade direction
- tx_hash: Transaction hash
- fee: Trading fee
- fee_token: Fee token
- start_position: Position before trade
- closed_pnl: Realized PnL

### Orders Table
- timestamp: When the order was placed/cancelled
- address: Trader's address
- coin: Asset
- action: placed/cancelled
- side: BUY/SELL
- size: Order size
- price: Order price
- order_id: Unique order ID

## Database Recording Modes

The monitor supports different modes of operation for recording trades:

### 1. Full Monitoring with Notifications
```python
# Records to database and sends notifications via callback
monitor = HyperliquidMonitor(
    addresses=addresses,
    db_path="trades.db",
    callback=print_trade
)
```

### 2. Silent Database Recording
```python
# Only records to database, no notifications
monitor = HyperliquidMonitor(
    addresses=addresses,
    db_path="trades.db",
    silent=True  # Suppresses all notifications and console output
)
```

### 3. Notification-Only Mode
```python
# Only sends notifications, no database recording
monitor = HyperliquidMonitor(
    addresses=addresses,
    callback=print_trade
)
```

The silent mode is particularly useful for:
- Background monitoring and data collection
- Reducing system resource usage
- Running multiple monitors concurrently
- Long-term trade data accumulation
- Server-side deployments where notifications aren't needed

Note: Silent mode requires a database path to be specified since it's meant for data recording.

## Development

### Setting up the Development Environment

1. Clone the repository:
```bash
git clone https://github.com/your-username/hyperliquid-monitor.git
cd hyperliquid-monitor
```

2. Install poetry if you haven't already:
```bash
curl -sSL https://install.python-poetry.org | python3 -
```

3. Install dependencies:
```bash
poetry install
```

### Running Tests

The package includes a comprehensive test suite using pytest. To run the tests:

```bash
# Run all tests
poetry run pytest

# Run with coverage report
poetry run pytest --cov

# Run specific test file
poetry run pytest tests/test_monitor.py

# Run tests with output
poetry run pytest -v
```

### Test Structure

Tests are organized in the following structure:
```
tests/
├── __init__.py
├── conftest.py          # Shared fixtures
├── test_monitor.py      # Monitor tests
├── test_database.py     # Database tests
└── test_types.py        # Type validation tests
```

Key test areas:
- Monitor functionality (subscriptions, event handling)
- Database operations (storage, retrieval)
- Type validation (trade object validation)
- Event processing (fills, orders)

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request. Make sure to:

1. Add tests for any new functionality
2. Update documentation as needed
3. Follow the existing code style
4. Run the test suite before submitting

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Acknowledgments

Built on top of the official Hyperliquid Python SDK