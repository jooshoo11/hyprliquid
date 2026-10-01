import sqlite3
import threading
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

def init_database(db_path: Optional[str] = None) -> str:
    """
    Initialize a new database for the Hyperliquid monitor or validate an existing one.
    
    Args:
        db_path: Optional path to the database. If None, creates a default 'trades.db'
                in the current directory.
    
    Returns:
        str: The absolute path to the initialized database
        
    Raises:
        sqlite3.Error: If there's an error creating or accessing the database
        ValueError: If the provided path is invalid
    """
    if db_path is None:
        db_path = "trades.db"
    
    # Convert to Path object for easier manipulation
    db_path = Path(db_path).resolve()
    
    # Create parent directories if they don't exist
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        # Create and test the database connection
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        
        # Create the required tables
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS fills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME,
            address TEXT,
            coin TEXT,
            side TEXT,
            size REAL,
            price REAL,
            direction TEXT,
            tx_hash TEXT,
            fee REAL,
            fee_token TEXT,
            start_position REAL,
            closed_pnl REAL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME,
            address TEXT,
            coin TEXT,
            action TEXT,
            side TEXT,
            size REAL,
            price REAL,
            order_id INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        # Create indexes for better query performance
        cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_fills_address ON fills(address)
        ''')
        cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_fills_timestamp ON fills(timestamp)
        ''')
        cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_orders_address ON orders(address)
        ''')
        cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_orders_timestamp ON orders(timestamp)
        ''')
        
        conn.commit()
        conn.close()
        
        return str(db_path)
        
    except sqlite3.Error as e:
        raise sqlite3.Error(f"Failed to initialize database at {db_path}: {str(e)}")
    except Exception as e:
        raise ValueError(f"Error creating database at {db_path}: {str(e)}")

class TradeDatabase:
    def __init__(self, db_path: str):
        """Initialize the database connection and create tables if they don't exist."""
        self.db_path = init_database(db_path)  # Use the init_database function
        self._local = threading.local()
        
    @property
    def conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, 'conn'):
            self._local.conn = sqlite3.connect(self.db_path)
        return self._local.conn

    def store_fill(self, fill: Dict) -> None:
        """Store a fill in the database."""
        cursor = self.conn.cursor()
        timestamp = datetime.fromtimestamp(int(fill.get("time", 0)) / 1000)
        
        cursor.execute('''
        INSERT INTO fills (
            timestamp, address, coin, side, size, price, direction, tx_hash, 
            fee, fee_token, start_position, closed_pnl
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            timestamp,
            fill.get("address", "Unknown"),
            fill.get("coin", "Unknown"),
            "BUY" if fill.get("side", "B") == "A" else "SELL",
            float(fill.get("sz", 0)),
            float(fill.get("px", 0)),
            fill.get("dir", "Unknown"),
            fill.get("hash", "Unknown"),
            float(fill.get("fee", 0)),
            fill.get("feeToken", "Unknown"),
            float(fill.get("startPosition", 0)),
            float(fill.get("closedPnl", 0))
        ))
        
        self.conn.commit()

    def store_order(self, order: Dict, action: str) -> None:
        """Store an order in the database."""
        cursor = self.conn.cursor()
        timestamp = datetime.fromtimestamp(int(order.get("time", 0)) / 1000)
        
        # Get the placed or canceled order details
        order_details = order.get("placed", {}) if action == "placed" else order.get("canceled", {})
        
        cursor.execute('''
        INSERT INTO orders (timestamp, address, coin, action, side, size, price, order_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            timestamp,
            order.get("address", "Unknown"),
            order.get("coin", "Unknown"),
            action,
            "BUY" if order_details.get("side", "B") == "A" else "SELL",
            float(order_details.get("sz", 0)),
            float(order_details.get("px", 0)),
            int(order_details.get("oid", 0))
        ))
        
        self.conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        if hasattr(self._local, 'conn'):
            self._local.conn.close()
            delattr(self._local, 'conn')