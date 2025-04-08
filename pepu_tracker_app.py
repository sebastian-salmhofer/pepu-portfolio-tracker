import streamlit as st
import requests
import concurrent.futures
from web3 import Web3

# === CONFIG ===
RPC_URL = "https://rpc-pepe-unchained-gupg0lo9wf.t.conduit.xyz"
PEPU_ETH_INFO = "https://api.geckoterminal.com/api/v2/networks/eth/tokens/0xadd39272e83895e7d3f244f696b7a25635f34234"
TOKEN_BALANCE_API = "https://explorer-pepe-unchained-gupg0lo9wf.t.conduit.xyz/api/v2/addresses/{}/token-balances"
NATIVE_BALANCE_API = "https://explorer-pepe-unchained-gupg0lo9wf.t.conduit.xyz/api/v2/addresses/{}"
TOKEN_INFO_API = "https://api.geckoterminal.com/api/v2/networks/pepe-unchained/tokens/{}"
STAKING_CONTRACT = "0xf0163C18F8D3fC8D5b4cA15e07D0F9f75460335F"

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

web3 = Web3(Web3.HTTPProvider(RPC_URL))
contract = web3.eth.contract(address=STAKING_CONTRACT, abi=staking_abi)

# === PAGE SETUP ===
st.set_page_config(page_title="Pepe Unchained Portfolio", layout="wide")

# === GLOBAL STYLE ===
st.markdown("""
<style>
body {
    background-color: #f2f2f2;
}
.token-card {
    border: 2px solid #cccccc;
    background-color: #ffffff;
    border-radius: 12px;
    padding: 15px;
    margin-bottom: 10px;
}
.token-header {
    font-size: 16px;
    font-weight: bold;
    color: #333333;
}
.token-sub {
    font-size: 12px;
    color: #777777;
}
.token-label {
    margin-top: 8px;
    color: #444444;
}
.token-warning {
    color: #ffa500;
    font-size: 12px;
    margin-top: 6px;
}

/* Hide Streamlit UI elements */
#MainMenu, header, footer {
    visibility: hidden;
}
.block-container {
    padding-top: 1rem;
}
</style>
""", unsafe_allow_html=True)

# === TITLE ===
st.title("🐸 Pepe Unchained Portfolio Tracker")

# === FUNCTIONS ===
def get_native_balance(address):
    res = requests.get(NATIVE_BALANCE_API.format(address)).json()
    return int(res.get("coin_balance", 0)) / 1e18

def get_staked_and_rewards(address):
    staked = contract.functions.poolStakers(address).call()[0] / 1e18
    rewards = contract.functions.getRewards(address).call() / 1e18
    return staked, rewards

def get_pepu_price_icon():
    res = requests.get(PEPU_ETH_INFO).json()["data"]["attributes"]
    return float(res["price_usd"]), res["image_url"]

def get_token_balances(address):
    return requests.get(TOKEN_BALANCE_API.format(address)).json()

def get_token_info(address):
    try:
        res = requests.get(TOKEN_INFO_API.format(address)).json()
        return address, res["data"]["attributes"]
    except:
        return address, {}

def fetch_token_info_parallel(addresses):
    with concurrent.futures.ThreadPoolExecutor() as executor:
        results = list(executor.map(get_token_info, addresses))
    return dict(results)

def render_pepu_card(label, amount, price, icon):
    value = amount * price
    st.markdown(f"""
        <div class='token-card'>
            <div style='display:flex; align-items:center; gap:10px;'>
                <img src="{icon}" width="32" height="32" />
                <div class='token-header'>{label}</div>
            </div>
            <div class='token-label'>
                <div>Amount: <strong>{amount:,.4f}</strong></div>
                <div>Price: <strong>${price:,.6f}</strong></div>
                <div>Total: <strong>${value:,.2f}</strong></div>
            </div>
        </div>
    """, unsafe_allow_html=True)
    return value

def render_token_card(name, symbol, amount, price, value, icon_url, contract_address, warning=None):
    price_display = f"${price:,.6f}" if price else "N/A"
    value_display = f"${value:,.2f}" if price else "N/A"
    token_link = f"https://www.geckoterminal.com/pepe-unchained/pools/{contract_address}"
    warning_html = f"<div class='token-warning'>{warning}</div>" if warning else ""

    st.markdown(f"""
        <div class='token-card'>
            <div style='display:flex; align-items:center; gap:10px;'>
                <img src="{icon_url}" width="32" height="32" style="border-radius:4px;" />
                <div>
                    <a href="{token_link}" target="_blank" class="token-header">
                        {name} ({symbol})
                    </a><br/>
                    <div class="token-sub">{contract_address}</div>
                </div>
            </div>
            <div class='token-label'>
                <div>Amount: <strong>{amount:,.4f}</strong></div>
                <div>Price: <strong>{price_display}</strong></div>
                <div>Total: <strong>{value_display}</strong></div>
            </div>
            {warning_html}
        </div>
    """, unsafe_allow_html=True)

# === MAIN APP ===
wallet = st.text_input("Enter your wallet address", placeholder="0x...")

if wallet and wallet.startswith("0x") and len(wallet) == 42:
    with st.spinner("Fetching data..."):
        total = 0.0
        pepu_price, pepu_icon = get_pepu_price_icon()
        native = get_native_balance(wallet)
        staked, rewards = get_staked_and_rewards(wallet)

        st.subheader("💚 Pepe Unchained")
        pepu_cols = st.columns(3)
        with pepu_cols[0]:
            total += render_pepu_card("Wallet Balance", native, pepu_price, pepu_icon)
        with pepu_cols[1]:
            total += render_pepu_card("Staked PEPU", staked, pepu_price, pepu_icon)
        with pepu_cols[2]:
            total += render_pepu_card("Unclaimed Rewards", rewards, pepu_price, pepu_icon)

        st.markdown("---")
        st.subheader("📦 Other Tokens")

        tokens = get_token_balances(wallet)
        addresses = [t["token"]["address"] for t in tokens]
        token_info_map = fetch_token_info_parallel(addresses)

        token_cards = []
        for t in tokens:
            token = t["token"]
            address = token["address"]
            name = token.get("name", "Unknown")
            symbol = token.get("symbol", "")
            decimals = int(token.get("decimals", 18)) if token.get("decimals") else 18
            amount = int(t["value"]) / (10 ** decimals)

            info = token_info_map.get(address, {})
            price = float(info.get("price_usd", 0.0) or 0.0)
            icon_url = info.get("image_url", "https://placehold.co/32x32")
            liquidity = float(info.get("total_reserve_in_usd", 0.0) or 0.0)

            if liquidity < 1000:
                price = 0.0
                value = 0.0
                warning = "⚠️ Low liquidity pool"
            else:
                value = amount * price
                total += value
                warning = None

            token_cards.append({
                "name": name,
                "symbol": symbol,
                "amount": amount,
                "price": price,
                "value": value,
                "icon": icon_url,
                "address": address,
                "warning": warning
            })

        token_cards.sort(key=lambda x: x["value"], reverse=True)
        cols = st.columns(3)
        for i, t in enumerate(token_cards):
            with cols[i % 3]:
                render_token_card(
                    t["name"], t["symbol"], t["amount"], t["price"],
                    t["value"], t["icon"], t["address"], t["warning"]
                )

        st.markdown(f"""
            <h2 style="text-align:center; margin-top:30px;">
                💰 Total Portfolio Value: <span style="color:#4CAF50">${total:,.2f}</span>
            </h2>
        """, unsafe_allow_html=True)
else:
    st.info("Enter a valid wallet address to continue.")
