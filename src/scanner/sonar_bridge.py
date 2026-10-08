"""
CryptoSonar to Hyperliquid Bridge (src/scanner/sonar_bridge.py)

Directly links jooshoo11/Crypto_Sonar with jooshoo11/hyprliquid:
1. Ingests sentiment and social velocity from Crypto_Sonar's SQLite database.
2. Vetoes Hyperliquid trade candidates with toxic keywords (exploit, hack, rug, sec).
3. Boosts conviction on candidates with high positive social sentiment and velocity.
"""

import os
import sys
import sqlite3
from typing import Dict, Any, List, Optional
from pathlib import Path

# Path to Crypto_Sonar repo
SONAR_DIR = Path(__file__).resolve().parents[3] / "Crypto_Sonar"
SONAR_DB_PATH = SONAR_DIR / "crypto_sonar.db"


class SonarBridge:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = str(db_path or SONAR_DB_PATH)
        self.sonar_available = os.path.exists(self.db_path) or os.path.exists(str(SONAR_DIR))

    def get_token_sentiment(self, coin: str) -> Dict[str, Any]:
        """
        Queries Crypto_Sonar database for recent mentions and sentiment of a specific token.
        """
        coin_clean = coin.upper().replace("$", "")
        result = {
            "coin": coin_clean,
            "mention_count": 0,
            "avg_sentiment": 0.0,
            "toxic_catalyst": False,
            "toxic_reason": "",
            "has_data": False,
        }

        if not os.path.exists(self.db_path):
            return result

        try:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            cursor = conn.cursor()

            # Query analyzed_tokens for mentions and sentiment
            cursor.execute("""
                SELECT mention_count, average_sentiment
                FROM analyzed_tokens
                WHERE ticker = ?
                LIMIT 1
            """, (coin_clean,))
            token_row = cursor.fetchone()

            if token_row:
                result["has_data"] = True
                result["mention_count"] = int(token_row[0] or 0)
                result["avg_sentiment"] = float(token_row[1] or 0.0)

            # Query alerts for recent scam / toxic warnings
            cursor.execute("""
                SELECT reason, gemini_scam_likelihood, gemini_summary
                FROM alerts
                WHERE ticker = ?
                ORDER BY id DESC
                LIMIT 5
            """, (coin_clean,))
            alert_rows = cursor.fetchall()
            conn.close()

            toxic_keywords = ["exploit", "hack", "hacked", "rugpull", "rug", "sec", "delist", "scam"]

            for reason, scam_like, narrative in alert_rows:
                combined_txt = f"{reason or ''} {scam_like or ''} {narrative or ''}".lower()
                if scam_like == "HIGH" or any(kw in combined_txt for kw in toxic_keywords):
                    result["toxic_catalyst"] = True
                    result["toxic_reason"] = f"Sonar Alert flagged: {reason or scam_like}"
                    break

        except Exception as e:
            # Non-blocking on database locks
            result["toxic_reason"] = f"Sonar read error: {e}"

        return result

    def vet_candidates(self, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Enriches a list of candidate trade setups with CryptoSonar narrative intelligence.
        """
        vetted_candidates = []

        for cand in candidates:
            coin = cand.get("coin", "")
            sentiment_data = self.get_token_sentiment(coin)

            enriched = dict(cand)
            enriched["sonar_mentions"] = sentiment_data["mention_count"]
            enriched["sonar_sentiment"] = sentiment_data["avg_sentiment"]

            # Toxic Veto: If Sonar flagged an exploit or hack, veto the trade!
            if sentiment_data["toxic_catalyst"]:
                enriched["validated"] = False
                enriched["veto_reason"] = sentiment_data["toxic_reason"]
                enriched["adjusted_conviction"] = 0
            elif sentiment_data["has_data"] and sentiment_data["avg_sentiment"] > 0.4:
                # Positive Narrative Boost
                current_conv = enriched.get("adjusted_conviction", enriched.get("conviction", 50))
                enriched["adjusted_conviction"] = min(100, int(current_conv * 1.25))
                enriched["sonar_boost"] = True

            vetted_candidates.append(enriched)

        return vetted_candidates
