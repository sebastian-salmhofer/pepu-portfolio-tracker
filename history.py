# === history.py ===

import os
import time
import asyncio
import asyncpg
from datetime import datetime
import httpx
from eth_account.messages import encode_defunct
from eth_account import Account
from web3 import Web3

DB_URL = os.getenv("DATABASE_URL")
MIN_REQUIRED_PBTC = 2_000_000
PBTC_CONTRACT = "0x73d070ec589d9f889fdf3b16fb1b828cecef320b"

# Run every 1 hour to log wallet data
async def log_loop():
    while True:
        try:
            async with asyncpg.create_pool(DB_URL) as pool:
                async with pool.acquire() as conn:
                    await conn.execute("""
                    CREATE TABLE IF NOT EXISTS wallet_history (
                        id SERIAL PRIMARY KEY,
                        wallet TEXT NOT NULL,
                        timestamp TIMESTAMPTZ DEFAULT NOW(),
                        pepu_usd DOUBLE PRECISION,
                        l2_usd DOUBLE PRECISION,
                        lp_usd DOUBLE PRECISION,
                        presale_usd DOUBLE PRECISION
                    )
                    """)

                    await conn.execute("""
                    CREATE TABLE IF NOT EXISTS tracked_wallets (
                        wallet TEXT PRIMARY KEY
                    )
                    """)

                    rows = await conn.fetch("SELECT wallet FROM tracked_wallets")
                    wallets = [r["wallet"] for r in rows]

                    async with httpx.AsyncClient(timeout=30) as client:
                        for wallet in wallets:
                            try:
                                res1 = await client.get(f"https://pepu-portfolio-tracker-test.onrender.com/portfolio?wallet={wallet}&log_mode=true")
                                res2 = await client.get(f"https://pepu-portfolio-tracker-test.onrender.com/lp-positions?wallet={wallet}&log_mode=true")
                                res3 = await client.get(f"https://pepu-portfolio-tracker-test.onrender.com/presales?wallet={wallet}")
                                res4 = await client.get(f"https://pepu-portfolio-tracker-test.onrender.com/staking?wallet={wallet}&log_mode=true")

                                portfolio = res1.json()
                                lps = res2.json()
                                presales = res3.json()
                                staking = res4.json()

                                pepu_usd = round(portfolio['native_pepu']['total_usd'] + portfolio['staked_pepu']['total_usd'] + portfolio['unclaimed_rewards']['total_usd'], 2)
                                l2_usd = round(sum(t['total_usd'] for t in portfolio['tokens']) + staking.get("total_value_usd", 0), 2)
                                lp_usd = round(lps.get("total_value_usd", 0), 2)
                                presale_usd = round(presales.get("total_value_usd", 0), 2)

                                await conn.execute("""
                                    INSERT INTO wallet_history (wallet, pepu_usd, l2_usd, lp_usd, presale_usd)
                                    VALUES ($1, $2, $3, $4, $5)
                                """, wallet, pepu_usd, l2_usd, lp_usd, presale_usd)

                            except Exception as e:
                                print(f"[ERROR] Logging wallet {wallet}: {e}")

        except Exception as e:
            print("[DB ERROR]", e)

        await asyncio.sleep(1 * 60 * 60)  # every hour


def record_wallet_history():
    return log_loop()


async def get_wallet_history(wallets_str: str, message: str, signature: str):
    wallets = [w.strip().lower() for w in wallets_str.split(",") if w.strip().startswith("0x")]
    if not wallets:
        return {"error": "No valid wallet addresses provided."}

    try:
        encoded = encode_defunct(text=message)
        recovered = Account.recover_message(encoded, signature=signature)
    except Exception as e:
        return {"error": f"Signature verification failed: {str(e)}"}

    # Verify PBTC balance from /portfolio
    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.get(f"https://pepu-portfolio-tracker-test.onrender.com/portfolio?wallet={recovered}")
        if res.status_code != 200:
            return {"error": "Failed to verify wallet PBTC balance."}
        data = res.json()
        tokens = data.get("tokens", [])

        pbtc_token = next((t for t in tokens if t.get("contract", "").lower() == PBTC_CONTRACT), None)
        total_pbtc = pbtc_token.get("amount", 0) if pbtc_token else 0

    if total_pbtc < MIN_REQUIRED_PBTC:
        return {"error": f"Minimum {MIN_REQUIRED_PBTC:,} PBTC required to view history."}

    # Add all requested wallets to tracked_wallets
    async with asyncpg.create_pool(DB_URL) as pool:
        async with pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS tracked_wallets (
                    wallet TEXT PRIMARY KEY
                )
            """)
            for w in wallets:
                await conn.execute("INSERT INTO tracked_wallets (wallet) VALUES ($1) ON CONFLICT DO NOTHING", w)

    async with asyncpg.create_pool(DB_URL) as pool:
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT wallet, timestamp, pepu_usd, l2_usd, lp_usd, presale_usd
                FROM wallet_history
                WHERE wallet = ANY($1::text[])
                ORDER BY timestamp ASC
            """, wallets)

    history = {}
    for row in rows:
        wallet = row["wallet"]
        if wallet not in history:
            history[wallet] = []
        history[wallet].append({
            "timestamp": row["timestamp"].isoformat(),
            "pepu_usd": row["pepu_usd"],
            "l2_usd": row["l2_usd"],
            "lp_usd": row["lp_usd"],
            "presale_usd": row["presale_usd"]
        })

    return history

    return {"status": "ok"}
