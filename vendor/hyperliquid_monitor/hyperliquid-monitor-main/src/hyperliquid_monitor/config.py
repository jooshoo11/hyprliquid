import os
from dotenv import load_dotenv
from hyperliquid_monitor.database import init_database

# Load environment variables
load_dotenv()

# Get addresses from environment
ADDRESSES = [addr.strip() for addr in os.getenv("MONITORED_ADDRESSES", "").split(",") if addr.strip()]

# Initialize database and get path
DB_PATH = init_database(os.getenv("DB_PATH", "trades.db"))