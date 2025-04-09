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
token_cache = {}
CACHE_TTL = 180

@app.get("/portfolio")
def get_portfolio(wallet: str = Query(..., min_length=42, max_length=42)):
    now = time.time()
    checksum_wallet = Web3.to_checksum_address(wallet)

    if now - pepu_cache["timestamp"] > CACHE_TTL:
        info = requests.get(PEPU_ETH_INFO).json()["data"]["attributes"]
        pepu_cache["price"] = float(info["price_usd"])
        pepu_cache["icon"] = info["image_url"]
        pepu_cache["timestamp"] = now

    pepu_price = pepu_cache["price"]
    pepu_icon = pepu_cache["icon"]

    native = int(requests.get(NATIVE_BALANCE_API.format(wallet)).json().get("coin_balance", 0)) / 1e18
    staked = contract.functions.poolStakers(checksum_wallet).call()[0] / 1e18
    rewards = contract.functions.getRewards(checksum_wallet).call() / 1e18

    tokens = requests.get(TOKEN_BALANCE_API.format(wallet)).json()
    total = native * pepu_price + staked * pepu_price + rewards * pepu_price

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

    for t in tokens:
        tok = t["token"]
        addr = tok["address"]
        symbol = tok.get("symbol", "")
        name = tok.get("name", "Unknown")
        decimals = int(tok.get("decimals", 18)) if tok.get("decimals") else 18
        amount = int(t["value"]) / (10 ** decimals)

        info_response_failed = False
        token_info = token_cache.get(addr)
        if not token_info or now - token_info["timestamp"] > CACHE_TTL:
            try:
                info = requests.get(TOKEN_INFO_API.format(addr)).json()["data"]["attributes"]
                token_info = {
                    "price_usd": float(info.get("price_usd", 0.0) or 0.0),
                    "icon_url": info.get("image_url", "https://placehold.co/32x32"),
                    "liquidity": float(info.get("total_reserve_in_usd", 0.0) or 0.0),
                    "timestamp": now
                }
                token_cache[addr] = token_info
            except:
                info_response_failed = True
                token_info = {
                    "price_usd": 0.0,
                    "icon_url": "https://placehold.co/32x32",
                    "liquidity": 0.0,
                    "timestamp": now
                }

        price = token_info["price_usd"]
        icon = token_info["icon_url"]
        liquidity = token_info["liquidity"]

        if info_response_failed:
            warning = "Error fetching price data"
        elif liquidity < 1000:
            price = 0.0
            warning = "Low liquidity pool"
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
