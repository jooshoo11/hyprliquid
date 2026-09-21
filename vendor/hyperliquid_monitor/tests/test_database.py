import pytest
import os
import threading
from pathlib import Path
from hyperliquid_monitor.database import TradeDatabase, init_database

def test_database_initialization(temp_db_path):
    db = TradeDatabase(temp_db_path)
    assert db is not None
    db.close()

def test_init_database_default():
    db_path = init_database()
    assert os.path.exists(db_path)
    assert db_path.endswith("trades.db")
    os.remove(db_path)  # Cleanup

def test_init_database_custom_path(tmp_path):
    custom_path = tmp_path / "custom" / "path" / "db.sqlite"
    db_path = init_database(str(custom_path))
    assert os.path.exists(db_path)
    assert str(custom_path) == db_path
    assert os.path.exists(custom_path.parent)  # Parent directories should be created

def test_database_tables(temp_db_path):
    db = TradeDatabase(temp_db_path)
    cursor = db.conn.cursor()
    
    # Check fills table
    cursor.execute("""
        SELECT name FROM sqlite_master 
        WHERE type='table' AND name='fills'
    """)
    assert cursor.fetchone() is not None
    
    # Check orders table
    cursor.execute("""
        SELECT name FROM sqlite_master 
        WHERE type='table' AND name='orders'
    """)
    assert cursor.fetchone() is not None
    
    # Check indexes
    cursor.execute("""
        SELECT name FROM sqlite_master 
        WHERE type='index' AND sql IS NOT NULL
    """)
    indexes = [row[0] for row in cursor.fetchall()]
    assert "idx_fills_address" in indexes
    assert "idx_fills_timestamp" in indexes
    assert "idx_orders_address" in indexes
    assert "idx_orders_timestamp" in indexes
    
    db.close()

def test_store_fill(temp_db_path, sample_fill_data):
    db = TradeDatabase(temp_db_path)
    
    # Add address to fill data
    fill_data = {**sample_fill_data, "address": "0x123..."}
    
    # Store the fill
    db.store_fill(fill_data)
    
    # Query the stored fill
    cursor = db.conn.cursor()
    cursor.execute("SELECT * FROM fills")
    row = cursor.fetchone()
    
    assert row is not None
    assert row[2] == "0x123..."  # address
    assert row[3] == "ETH"      # coin
    assert row[4] == "BUY"      # side
    assert float(row[5]) == 0.5  # size
    assert float(row[6]) == 1850.5  # price
    
    db.close()

def test_store_order(temp_db_path, sample_order_data):
    db = TradeDatabase(temp_db_path)
    
    # Add address to order data
    order_data = {**sample_order_data, "address": "0x123..."}
    
    # Store the order
    db.store_order(order_data, "placed")
    
    # Query the stored order
    cursor = db.conn.cursor()
    cursor.execute("SELECT * FROM orders")
    row = cursor.fetchone()
    
    assert row is not None
    assert row[2] == "0x123..."  # address
    assert row[3] == "BTC"      # coin
    assert row[4] == "placed"   # action
    assert row[5] == "SELL"     # side
    assert float(row[6]) == 0.1  # size
    assert float(row[7]) == 35000.5  # price
    assert row[8] == 54321      # order_id
    
    db.close()

def test_database_reuse(temp_db_path, sample_fill_data):
    # First instance
    db1 = TradeDatabase(temp_db_path)
    fill_data = {**sample_fill_data, "address": "0x123..."}
    db1.store_fill(fill_data)
    db1.close()
    
    # Second instance should be able to access the same data
    db2 = TradeDatabase(temp_db_path)
    cursor = db2.conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM fills")
    count = cursor.fetchone()[0]
    assert count == 1
    db2.close()

def test_init_database_invalid_path(tmp_path):
    # Using a file as directory to simulate invalid path
    invalid_file = tmp_path / "file.txt"
    invalid_file.write_text("")  # Create a file
    invalid_path = str(invalid_file / "db.sqlite")  # Try to create DB inside a file
    
    with pytest.raises((ValueError, OSError)):
        init_database(invalid_path)

def test_database_connection_thread_safety(temp_db_path):
    db = TradeDatabase(temp_db_path)
    connections = []
    
    def create_connection():
        conn = db.conn
        connections.append(conn)
    
    # Create connections in different threads
    thread1 = threading.Thread(target=create_connection)
    thread2 = threading.Thread(target=create_connection)
    
    thread1.start()
    thread2.start()
    
    thread1.join()
    thread2.join()
    
    # Verify we got different connections
    assert len(connections) == 2
    assert connections[0] != connections[1]
    
    db.close()