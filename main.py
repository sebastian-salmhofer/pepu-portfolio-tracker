from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import requests
from web3 import Web3
import time

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

RPC_URL = "https://rpc-pepe-unchained-gupg0lo9wf.t.conduit.xyz"
PEPU_ETH_INFO = "https://api.geckoterminal.com/api/v2/networks/eth/tokens/0xadd39272e83895e7d3f244f696b7a25635f34234"
TOKEN_BALANCE_API = "https://explorer-pepe-unchained-gupg0lo9wf.t.conduit.xyz/api/v2/addresses/{}/token-balances"
NATIVE_BALANCE_API = "https://explorer-pepe-unchained-gupg0lo9wf.t.conduit.xyz/api/v2/addresses/{}"
BATCH_PRICE_API = "https://api.geckoterminal.com/api/v2/simple/networks/pepe-unchained/token_price/{}?include_total_reserve_in_usd=true"
TOKEN_INFO_API = "https://api.geckoterminal.com/api/v2/networks/pepe-unchained/tokens/{}"
STAKING_CONTRACT = "0xf0163C18F8D3fC8D5b4cA15e07D0F9f75460335F"

web3 = Web3(Web3.HTTPProvider(RPC_URL))

staking_abi = [
    {
        "name": "poolStakers",
        "outputs": [
            {"name": "amount", "type": "uint256"},
            {"name": "rewardDebt", "type": "uint256"},
            {"name": "lastRewardMultiplier", "type": "uint256"},
            {"name": "lastUpdateTime", "type": "uint256"},
        ],
        "inputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "name": "getRewards",
        "outputs": [{"name": "", "type": "uint256"}],
        "inputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    }
]

contract = web3.eth.contract(address=STAKING_CONTRACT, abi=staking_abi)

pepu_cache = {"price": None, "icon": None, "timestamp": 0}
token_cache = {}  # key: address, value: {"price_usd", "liquidity", "icon_url", "timestamp"}
CACHE_TTL = 300  # 5 minutes for price/liquidity

@app.get("/portfolio")
def get_portfolio(wallet: str = Query(..., min_length=42, max_length=42)):
    now = time.time()

    # PEPU price/icon cache
    if now - pepu_cache["timestamp"] > CACHE_TTL:
        info = requests.get(PEPU_ETH_INFO).json()["data"]["attributes"]
        pepu_cache["price"] = float(info["price_usd"])
        pepu_cache["icon"] = info["image_url"]
        pepu_cache["timestamp"] = now

    pepu_price = pepu_cache["price"]
    pepu_icon = pepu_cache["icon"]

    try:
        checksum_wallet = Web3.to_checksum_address(wallet)
    except:
        return {"error": "Invalid wallet address format."}

    native = int(requests.get(NATIVE_BALANCE_API.format(wallet)).json().get("coin_balance", 0)) / 1e18
    staked = contract.functions.poolStakers(checksum_wallet).call()[0] / 1e18
    rewards = contract.functions.getRewards(checksum_wallet).call() / 1e18

    tokens = requests.get(TOKEN_BALANCE_API.format(wallet)).json()
    token_addrs = [t["token"]["address"].lower() for t in tokens]

    # BATCH price + liquidity
    prices = {}
    for i in range(0, len(token_addrs), 30):
        batch = token_addrs[i:i+30]
        url = BATCH_PRICE_API.format("%2C".join(batch))
        try:
            resp = requests.get(url).json()["data"]["attributes"]
            price_data = resp["token_prices"]
            liq_data = resp["total_reserve_in_usd"]
            for addr in batch:
                prices[addr] = {
                    "price_usd": float(price_data.get(addr, 0.0) or 0.0),
                    "liquidity": float(liq_data.get(addr, 0.0) or 0.0),
                    "timestamp": now
                }
        except:
            continue

    result = {
        "native_pepu": {
            "label": "Wallet Balance",
            "amount": native,
            "price_usd": pepu_price,
            "total_usd": native * pepu_price,
            "icon": pepu_icon
        },
        "staked_pepu": {
            "label": "Staked PEPU",
            "amount": staked,
            "price_usd": pepu_price,
            "total_usd": staked * pepu_price,
            "icon": pepu_icon
        },
        "unclaimed_rewards": {
            "label": "Unclaimed Rewards",
            "amount": rewards,
            "price_usd": pepu_price,
            "total_usd": rewards * pepu_price,
            "icon": pepu_icon
        },
        "tokens": [],
        "total_value_usd": 0.0
    }

    total = native * pepu_price + staked * pepu_price + rewards * pepu_price

    for t in tokens:
        tok = t["token"]
        addr = tok["address"].lower()
        symbol = tok.get("symbol", "")
        name = tok.get("name", "Unknown")
        decimals = int(tok.get("decimals", 18)) if tok.get("decimals") else 18
        amount = int(t["value"]) / (10 ** decimals)

        fresh = prices.get(addr)
        cached = token_cache.get(addr)

        if fresh:
            if addr not in token_cache:
                # Try fetching the token icon. If it fails, do not store any icon.
                try:
                    icon = requests.get(TOKEN_INFO_API.format(addr)).json()["data"]["attributes"]["image_url"]
                except:
                    icon = None
                token_cache[addr] = {
                    "price_usd": fresh["price_usd"],
                    "liquidity": fresh["liquidity"],
                    "timestamp": now
                }
                if icon:
                    token_cache[addr]["icon_url"] = icon
            else:
                # If the token is cached and no icon is stored, try fetching the icon.
                if "icon_url" not in token_cache[addr]:
                    try:
                        icon = requests.get(TOKEN_INFO_API.format(addr)).json()["data"]["attributes"]["image_url"]
                        if icon:
                            token_cache[addr]["icon_url"] = icon
                    except:
                        pass
                token_cache[addr]["price_usd"] = fresh["price_usd"]
                token_cache[addr]["liquidity"] = fresh["liquidity"]
                token_cache[addr]["timestamp"] = now

        info = token_cache.get(addr, {})
        price = info.get("price_usd", 0.0)
        liquidity = info.get("liquidity", 0.0)
        # Only return the placeholder if the icon has never been set
        icon = info.get("icon_url", "https://placehold.co/32x32")

        if fresh is None:
            warning = "Error fetching price data"
            price = 0.0
        elif liquidity < 1000:
            warning = "Low liquidity pool"
            price = 0.0
        else:
            warning = None

        total_usd = amount * price if price else 0.0
        if price:
            total += total_usd

        result["tokens"].append({
            "name": name,
            "symbol": symbol,
            "contract": addr,
            "amount": amount,
            "price_usd": price,
            "total_usd": total_usd,
            "icon_url": icon,
            "warning": warning
        })

    result["tokens"].sort(key=lambda x: x["total_usd"], reverse=True)
    result["total_value_usd"] = round(total, 2)

    return result
