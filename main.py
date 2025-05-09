from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import requests
from web3 import Web3
import time
import re
from decimal import Decimal
import os

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
NFT_API = "https://explorer-pepe-unchained-gupg0lo9wf.t.conduit.xyz/api/v2/addresses/{}/nft?type=ERC-721%2CERC-404%2CERC-1155"
BATCH_PRICE_API = "https://api.geckoterminal.com/api/v2/simple/networks/pepe-unchained/token_price/{}?include_24hr_vol=true&include_24hr_price_change=true&include_total_reserve_in_usd=true"
TOKEN_INFO_API = "https://api.geckoterminal.com/api/v2/networks/pepe-unchained/tokens/{}"
STAKING_CONTRACT = "0xf0163C18F8D3fC8D5b4cA15e07D0F9f75460335F"
LP_MANAGER_ADDRESS = "0x5e7cda0b5f1d239e6ea03beaee12008ba4184782"

web3 = Web3(Web3.HTTPProvider(RPC_URL))

staking_abi = [
    {
        "name": "poolStakers",
        "outputs": [
            {"name": "", "type": "uint256"},
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

lp_manager_abi = [{
    "name": "positions",
    "type": "function",
    "stateMutability": "view",
    "inputs": [{"name": "tokenId", "type": "uint256"}],
    "outputs": [
        {"name": "nonce", "type": "uint96"},
        {"name": "operator", "type": "address"},
        {"name": "token0", "type": "address"},
        {"name": "token1", "type": "address"},
        {"name": "fee", "type": "uint24"},
        {"name": "tickLower", "type": "int24"},
        {"name": "tickUpper", "type": "int24"},
        {"name": "liquidity", "type": "uint128"},
        {"name": "feeGrowthInside0LastX128", "type": "uint256"},
        {"name": "feeGrowthInside1LastX128", "type": "uint256"},
        {"name": "tokensOwed0", "type": "uint128"},
        {"name": "tokensOwed1", "type": "uint128"},
    ]
}]

erc20_abi = [{
    "constant": True,
    "inputs": [{"name": "owner", "type": "address"}],
    "name": "balanceOf",
    "outputs": [{"name": "", "type": "uint256"}],
    "payable": False,
    "stateMutability": "view",
    "type": "function"
}]

staking_contract = web3.eth.contract(address=STAKING_CONTRACT, abi=staking_abi)
lp_contract = web3.eth.contract(address=Web3.to_checksum_address(LP_MANAGER_ADDRESS), abi=lp_manager_abi)

pepu_cache = {"price": None, "icon": None, "timestamp": 0}
token_cache = {
    "0x4200000000000000000000000000000000000006": {
        "icon_url": "https://coin-images.coingecko.com/coins/images/52681/large/wn6wNj1C_400x400.jpg?1734021973"
    }
}
CACHE_TTL = 300

def populate_icon_cache(token_addrs, now, retries=1, delay=1.5):
    remaining = list(token_addrs)
    for attempt in range(retries):
        next_try = []
        for addr in remaining:
            if addr not in token_cache:
                token_cache[addr] = {}
            if "icon_url" not in token_cache[addr]:
                try:
                    url = TOKEN_INFO_API.format(addr)
                    print(f"[ICON] Fetching: {url}")
                    res = requests.get(url).json()["data"]["attributes"]
                    token_cache[addr]["icon_url"] = res.get("image_url")
                except:
                    next_try.append(addr)
        if not next_try:
            break
        time.sleep(delay)
        remaining = next_try

def populate_price_cache(token_addrs, now, retries=1, delay=1.5):
    remaining = list(token_addrs)
    for attempt in range(retries):
        next_try = []
        for i in range(0, len(remaining), 30):
            batch = remaining[i:i+30]
            try:
                url = BATCH_PRICE_API.format("%2C".join(batch))
                print(f"[PRICE] Fetching: {url}")
                res = requests.get(url).json()["data"]["attributes"]
                price_data = res["token_prices"]
                liq_data = res["total_reserve_in_usd"]
                vol_data = res.get("h24_volume_usd", {})
                change_data = res.get("h24_price_change_percentage", {})
                for addr in batch:
                    if addr not in token_cache:
                        token_cache[addr] = {}
                    token_cache[addr]["price_usd"] = float(price_data.get(addr, 0.0) or 0.0)
                    token_cache[addr]["liquidity"] = float(liq_data.get(addr, 0.0) or 0.0)
                    token_cache[addr]["volume_24h_usd"] = float(vol_data.get(addr, 0.0) or 0.0)
                    token_cache[addr]["price_change_24h_percentage"] = float(change_data.get(addr, 0.0) or 0.0)
                    token_cache[addr]["timestamp"] = now
            except:
                next_try.extend(batch)
        if not next_try:
            break
        time.sleep(delay)
        remaining = next_try


def tick_to_sqrt_price(tick):
    return int((1.0001 ** tick) ** 0.5 * (2 ** 96))

def get_amounts_from_liquidity(liquidity, sqrtPriceX96, sqrtLowerX96, sqrtUpperX96):
    if sqrtPriceX96 <= sqrtLowerX96:
        amount0 = liquidity * (sqrtUpperX96 - sqrtLowerX96) // (sqrtUpperX96 * sqrtLowerX96)
        amount1 = 0
    elif sqrtPriceX96 < sqrtUpperX96:
        amount0 = liquidity * (sqrtUpperX96 - sqrtPriceX96) // (sqrtUpperX96 * sqrtPriceX96)
        amount1 = liquidity * (sqrtPriceX96 - sqrtLowerX96) // (2 ** 96)
    else:
        amount0 = 0
        amount1 = liquidity * (sqrtUpperX96 - sqrtLowerX96) // (2 ** 96)
    return amount0, amount1

@app.get("/portfolio")
def get_portfolio(wallet: str = Query(..., min_length=42, max_length=42), log_mode: bool = Query(False)):
    now = time.time()
    
    try:
        checksum_wallet = Web3.to_checksum_address(wallet)
    except:
        return {"error": "Invalid wallet address format."}
        
    native = int(requests.get(NATIVE_BALANCE_API.format(wallet)).json().get("coin_balance", 0)) / 1e18
    try:
        staked_raw = staking_contract.functions.poolStakers(checksum_wallet).call()
        staked = staked_raw / 1e18 if isinstance(staked_raw, int) else staked_raw[0] / 1e18
    except:
        staked = 0
    try:
        rewards = staking_contract.functions.getRewards(checksum_wallet).call() / 1e18
    except:
        rewards = 0

    if now - pepu_cache["timestamp"] > CACHE_TTL:
        for attempt in range(3):
            try:
                res = requests.get(PEPU_ETH_INFO, timeout=5)
                data = res.json().get("data", {})
                attributes = data.get("attributes", {})
    
                # Only update cache if data exists
                if "price_usd" in attributes and "image_url" in attributes:
                    pepu_cache["price"] = float(attributes["price_usd"])
                    pepu_cache["icon"] = attributes["image_url"]
                    pepu_cache["timestamp"] = now
                    break
            except Exception as e:
                if attempt == 1:
                    print(f"[Warning] PEPU price fetch failed: {repr(e)}")
            time.sleep(1.5)

    pepu_price = pepu_cache.get("price", 0.0)
    pepu_icon = pepu_cache.get("icon", "https://placehold.co/32x32")


    pepu_price = pepu_cache["price"]
    pepu_icon = pepu_cache["icon"]
    
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

    total = result["native_pepu"]["total_usd"] + result["staked_pepu"]["total_usd"] + result["unclaimed_rewards"]["total_usd"]

    
    tokens = requests.get(TOKEN_BALANCE_API.format(wallet)).json()
    tokens = [t for t in tokens if t["token"]["address"].lower() != LP_MANAGER_ADDRESS.lower()]    #Exclude LP tokens
    token_addrs = [t["token"]["address"].lower() for t in tokens]
    
    # Determine and fetch missing icons
    missing_icons = [addr for addr in token_addrs if token_cache.get(addr, {}).get("icon_url") is None]
    if not log_mode:
        populate_icon_cache(missing_icons, now)
    
    
    # Determine tokens that still need fresh price data
    needs_price_update = [
        addr for addr in token_addrs
        if addr not in token_cache or (now - token_cache[addr].get("timestamp", 0)) > CACHE_TTL
    ]
    
    # Fetch fresh price+liquidity in batches of 30
    if not log_mode:
        populate_price_cache(needs_price_update, now)
    else:
        populate_price_cache(needs_price_update, now, retries=12, delay=15)
    
    # Now use the populated cache to build the response
    for t in tokens:
        tok = t["token"]
        addr = tok["address"].lower()
        symbol = tok.get("symbol", "")
        name = tok.get("name", "Unknown")
        decimals = int(tok.get("decimals", 18)) if tok.get("decimals") else 18
        amount = int(t["value"]) / (10 ** decimals)
    
        info = token_cache.get(addr, {})
        price = info.get("price_usd", 0.0)
        liquidity = info.get("liquidity", 0.0)
        volume_24h_usd = info.get("volume_24h_usd", 0.0)
        price_change_24h_percentage = info.get("price_change_24h_percentage", 0.0)
        icon = info.get("icon_url") or "https://placehold.co/32x32"
    
        warning = None
        if price == 0.0:
            warning = "Error fetching price data"
        elif liquidity < 1000:
            warning = "Low liquidity pool"
            price = 0.0
    
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
            "volume_24h_usd": volume_24h_usd,
            "price_change_24h_percentage": price_change_24h_percentage,
            "icon_url": icon,
            "warning": warning
        })
        
    result["tokens"].sort(key=lambda x: x["total_usd"], reverse=True)
    result["total_value_usd"] = round(total, 2)
    return result

@app.get("/lp-positions")
def get_lp_positions(wallet: str = Query(..., min_length=42, max_length=42), log_mode: bool = Query(False)):
    now = time.time()

    total_lp = 0.0
    
    try:
        checksum_wallet = Web3.to_checksum_address(wallet)
    except:
        return {"error": "Invalid wallet address format."}
        
    # LP NFT positions
    try:
        nft_data = requests.get(NFT_API.format(wallet), timeout=15).json()
        lp_items = [
            item for item in nft_data.get("items", [])
            if item.get("token", {}).get("address", "").lower() == LP_MANAGER_ADDRESS.lower()
        ]
    
        lp_price_update = set()
        
        result = {
            "lp_positions": [],
            "total_value_usd": 0.0
        }
    
        def process_lp(item):
            try:
                token_id = int(item["id"])
                pos = lp_contract.functions.positions(token_id).call()
                token0 = Web3.to_checksum_address(pos[2])
                token1 = Web3.to_checksum_address(pos[3])
                liquidity = pos[7]

                if liquidity == 0:
                    return None
                
                pool_match = re.search(r"Pool Address: (0x[a-fA-F0-9]{40})", item.get("metadata", {}).get("description", ""))
                pool_address = pool_match.group(1) if pool_match else None
                
                symbol0_match = re.search(rf"([\S]+) Address: {re.escape(token0)}", item.get("metadata", {}).get("description", ""), re.IGNORECASE)
                symbol0 = symbol0_match.group(1) if symbol0_match else "?"
                
                symbol1_match = re.search(rf"([\S]+) Address: {re.escape(token1)}", item.get("metadata", {}).get("description", ""), re.IGNORECASE)
                symbol1 = symbol1_match.group(1) if symbol1_match else "?"

    
                amount0 = amount1 = 0
                if pool_address:
                    pool_address_checksum = Web3.to_checksum_address(pool_address)
                    slot0_data = web3.eth.call({
                        "to": pool_address_checksum,
                        "data": "0x3850c7bd"
                    })
                    sqrtPriceX96 = int.from_bytes(bytes.fromhex(slot0_data.hex()[2:66]), "big")
    
                    sqrt_ratio = sqrtPriceX96 / (2 ** 96)
                    ratio = sqrt_ratio ** 2
                    tick_lower = pos[5]
                    tick_upper = pos[6]
    
                    sqrt_lower = 1.0001 ** (tick_lower / 2)
                    sqrt_upper = 1.0001 ** (tick_upper / 2)
    
                    if sqrtPriceX96 <= sqrt_lower * (2 ** 96):
                        amount0 = liquidity * (sqrt_upper - sqrt_lower) / (sqrt_upper * sqrt_lower)
                        amount1 = 0
                    elif sqrtPriceX96 < sqrt_upper * (2 ** 96):
                        amount0 = liquidity * (sqrt_upper - sqrt_ratio) / (sqrt_upper * sqrt_ratio)
                        amount1 = liquidity * (sqrt_ratio - sqrt_lower)
                    else:
                        amount0 = 0
                        amount1 = liquidity * (sqrt_upper - sqrt_lower)
    
                    amount0 /= 1e18
                    amount1 /= 1e18
    
                    token0_lower = token0.lower()
                    token1_lower = token1.lower()
    
                    # Icons
                    if not log_mode:
                        for addr in [token0_lower, token1_lower]:
                            if token_cache.get(addr, {}).get("icon_url") is None:
                                populate_icon_cache([addr], now)
    
                    # Price check
                    if token0_lower not in token_cache or (now - token_cache[token0_lower].get("timestamp", 0)) > CACHE_TTL:
                        lp_price_update.update([token0_lower])
                    if token1_lower not in token_cache or (now - token_cache[token1_lower].get("timestamp", 0)) > CACHE_TTL:
                        lp_price_update.update([token1_lower])

                    icon0 = token_cache.get(token0_lower, {}).get("icon_url", "https://placehold.co/32x32")
                    icon1 = token_cache.get(token1_lower, {}).get("icon_url", "https://placehold.co/32x32")

    
                    return {
                        "token_id": token_id,
                        "token0": token0,
                        "token1": token1,
                        "symbol0": symbol0,
                        "symbol1": symbol1,
                        "pool_address": pool_address,
                        "lp_name": item.get("metadata", {}).get("name", "Unknown LP"),
                        "amount0": amount0,
                        "amount1": amount1,
                        "amount0_usd": 0,
                        "amount1_usd": 0,
                        "token0_icon": icon0,
                        "token1_icon": icon1,
                        "warning": None
                    }
    
            except Exception as e:
                return {
                    "token_id": item.get("id"),
                    "token0": item.get("token0", "Unknown"),
                    "token1": item.get("token1", "Unknown"),
                    "symbol0": item.get("symbol0", "Unknown"),
                    "symbol1": item.get("symbol1", "Unknown"),
                    "pool_address": item.get("metadata", {}).get("description", "Unknown"),
                    "lp_name": item.get("metadata", {}).get("name", "Unknown LP"),
                    "amount0": 0,
                    "amount1": 0,
                    "amount0_usd": 0,
                    "amount1_usd": 0,
                    "warning": f"Failed to get LP data: {repr(e)}"
                }
    
        with ThreadPoolExecutor(max_workers=10) as executor:
            lp_results = list(executor.map(process_lp, lp_items))
    
        result["lp_positions"].extend(lp for lp in lp_results if lp)
    
        # Fetch fresh price+liquidity
        if not log_mode:
            populate_price_cache(list(lp_price_update), now)
        else:
            populate_price_cache(lp_price_update, now, retries=12, delay=15)
    
        # Final price + USD calc
        for lp in result["lp_positions"]:
            token0 = lp["token0"].lower()
            token1 = lp["token1"].lower()
            price0 = token_cache.get(token0, {}).get("price_usd", 0.0)
            price1 = token_cache.get(token1, {}).get("price_usd", 0.0)
    
            if price0 == 0.0 or price1 == 0.0:
                if not lp.get("warning"):
                    lp["warning"] = "Error fetching price data"
                lp["amount0_usd"] = 0
                lp["amount1_usd"] = 0
            else:
                lp["amount0_usd"] = lp["amount0"] * price0
                lp["amount1_usd"] = lp["amount1"] * price1
                total_lp += lp["amount0_usd"] + lp["amount1_usd"]
    
    except Exception as e:
        result["lp_positions"].append({"error": f"Failed to fetch LPs: {str(e)}"})

    result["lp_positions"].sort(key=lambda x: x.get("amount0_usd", 0) + x.get("amount1_usd", 0), reverse=True)
    result["total_value_usd"] = round(total_lp, 2)
    return result




# Structure: (staking_contract_address, pool_id, token_label)
STAKING_POOLS = [
    {
        "contract_address": Web3.to_checksum_address("0x99618fE301BabC0929D956018d8cfB9938810AeC"),
        "pool_id": 0,
        "token_label": "$777 Pool 0"
    },
    {
        "contract_address": Web3.to_checksum_address("0x99618fE301BabC0929D956018d8cfB9938810AeC"),
        "pool_id": 1,
        "token_label": "$777 Pool 1"
    }
]

multi_staking_abi = [
    {
        "name": "pools",
        "inputs": [{"name": "id", "type": "uint256"}],
        "outputs": [
            {"name": "stakingToken", "type": "address"},
            {"name": "rewardToken", "type": "address"},
            {"name": "apyBasisPoints", "type": "uint256"},
            {"name": "maxRewardDuration", "type": "uint256"},
            {"name": "lockDuration", "type": "uint256"},
            {"name": "lockStaking", "type": "bool"},
            {"name": "allowRestake", "type": "bool"},
            {"name": "totalStaked", "type": "uint256"},
            {"name": "active", "type": "bool"}
        ],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "name": "stakes",
        "inputs": [{"name": "poolId", "type": "uint256"}, {"name": "user", "type": "address"}],
        "outputs": [
            {"name": "amount", "type": "uint256"},
            {"name": "timestamp", "type": "uint256"},
            {"name": "unclaimed", "type": "uint256"}
        ],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "name": "pendingRewards",
        "inputs": [{"name": "poolId", "type": "uint256"}, {"name": "user", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    }
]

@app.get("/staking")
def get_staking(wallet: str = Query(..., min_length=42, max_length=42), log_mode: bool = Query(False)):
    try:
        checksum_wallet = Web3.to_checksum_address(wallet)
    except:
        return {"error": "Invalid wallet address format."}

    now = time.time()
    staking_results = []

    for entry in STAKING_POOLS:
        try:
            contract = web3.eth.contract(address=entry["contract_address"], abi=multi_staking_abi)
            pool_id = entry["pool_id"]
            label = entry["token_label"]

            pool = contract.functions.pools(pool_id).call()
            stake = contract.functions.stakes(pool_id, checksum_wallet).call()
            pending = contract.functions.pendingRewards(pool_id, checksum_wallet).call()

            staking_token = Web3.to_checksum_address(pool[0])
            apy = pool[2] / 100
            lock_duration = pool[4]
            amount_staked = stake[0] / 1e18
            timestamp = stake[1]
            pending_rewards = pending / 1e18

            token_key = staking_token.lower()

            if not log_mode:
                if token_cache.get(token_key, {}).get("icon_url") is None:
                    populate_icon_cache([token_key], now)

            if token_key not in token_cache or now - token_cache[token_key].get("timestamp", 0) > CACHE_TTL:
                if not log_mode:
                    populate_price_cache([token_key], now)
                else:
                    populate_price_cache([token_key], now, retries=12, delay=15)

            info = token_cache.get(token_key, {})
            price_usd = info.get("price_usd", 0.0)
            icon_url = info.get("icon_url", "https://placehold.co/32x32")

            remaining_lock = max(0, lock_duration - (int(now) - timestamp)) if timestamp else 0
            total_value = (amount_staked + pending_rewards) * price_usd

            staking_results.append({
                "contract": entry["contract_address"],
                "pool_id": pool_id,
                "pool_name": label,
                "token_address": staking_token,
                "icon_url": icon_url,
                "price_usd": price_usd,
                "apy": apy,
                "lock_duration": lock_duration,
                "remaining_lock_time": remaining_lock,
                "staked_amount": amount_staked,
                "pending_rewards": pending_rewards,
                "total_value_usd": round(total_value, 4)
            })

        except Exception as e:
            staking_results.append({
                "contract": entry["contract_address"],
                "pool_id": entry["pool_id"],
                "pool_name": entry["token_label"],
                "error": f"Failed to fetch staking data: {str(e)}"
            })

    total_usd = sum(p.get("total_value_usd", 0) for p in staking_results if isinstance(p, dict) and "total_value_usd" in p)
    return {
        "staking_pools": staking_results,
        "total_value_usd": round(total_usd, 2)
    }




PESW_PRESALE_CA = Web3.to_checksum_address("0xcE4268fB5908dAf59c198Ef26ef3f78949bf772C")
PESW_STAKING_MANAGER_CA = Web3.to_checksum_address("0xDd6f17b253eDc9e3D7329FB6EA293DbF8b4c214d")

# ABIs
pesw_presale_abi = [
    {"name": "getUserDeposits", "inputs": [{"name": "user", "type": "bytes"}], "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"name": "currentStep", "inputs": [], "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
    {
        "name": "rounds",
        "inputs": [
            {"name": "", "type": "uint256"},
            {"name": "", "type": "uint256"}
        ],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    }
]

pesw_staking_abi = [
    {"name": "getPoolStakers", "inputs": [{"name": "user", "type": "bytes"}], "outputs": [
        {"type": "uint256"}, {"type": "uint256"}, {"type": "uint256"}, {"type": "uint256"}, {"type": "uint256"}
    ], "stateMutability": "view", "type": "function"},
    {"name": "getRewards", "inputs": [{"name": "_user", "type": "bytes"}], "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
]

pesw_presale_contract = web3.eth.contract(address=PESW_PRESALE_CA, abi=pesw_presale_abi)
pesw_staking_contract = web3.eth.contract(address=PESW_STAKING_MANAGER_CA, abi=pesw_staking_abi)

@app.get("/presales")
def get_presales(wallet: str = Query(..., min_length=42, max_length=42), log_mode: bool = Query(False)):
    try:
        wallet_bytes = bytes.fromhex(wallet[2:])
        # Deposits
        deposits = pesw_presale_contract.functions.getUserDeposits(wallet_bytes).call() / 1e18

        # Staking info
        staked_info = pesw_staking_contract.functions.getPoolStakers(wallet_bytes).call()
        staked_amount = staked_info[0] / 1e18
        pending_rewards = pesw_staking_contract.functions.getRewards(wallet_bytes).call() / 1e18

        # Price info
        current_step = pesw_presale_contract.functions.currentStep().call()
        current_price = pesw_presale_contract.functions.rounds(1, current_step).call() / 1e18

        pesw_total_value_usd = (deposits + staked_amount + pending_rewards) * current_price

        return {
            "pesw": {
                "icon": "https://www.pepesquid.world/_next/image?url=%2Ficons%2Fpesw_icon-72.png&w=256&q=75",
                "deposited_tokens": deposits,
                "staked_tokens": staked_amount,
                "pending_rewards": pending_rewards,
                "current_price_usd": current_price,
                "launch_price_usd": 0.01155
            },
            "total_value_usd": pesw_total_value_usd
        }
    except Exception as e:
        return {"error": str(e)}


# --- History Integration ---
from history import record_wallet_history, get_wallet_history
import asyncio

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(record_wallet_history())

@app.get("/wallet-history")
async def wallet_history(
    wallets: str = Query(...),
    message: str = Query(...),
    signature: str = Query(...)
):
    return await get_wallet_history(wallets, message, signature)

@app.get("/track-wallet")
async def track_wallet(wallet: str = Query(...)):
    import asyncpg
    DB_URL = os.getenv("DATABASE_URL")
    async with asyncpg.create_pool(DB_URL) as pool:
        async with pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS tracked_wallets (
                    id SERIAL PRIMARY KEY,
                    wallet TEXT UNIQUE NOT NULL
                )
            """)
            await conn.execute("INSERT INTO tracked_wallets (wallet) VALUES ($1) ON CONFLICT DO NOTHING", wallet.lower())
    return {"status": "added", "wallet": wallet.lower()}

