import os
import json
import time
import requests
from collections import defaultdict
from datetime import datetime, timezone

HELIUS_API_KEY = os.environ.get("HELIUS_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

WALLETS_FILE = "/app/spirit2_wallets.txt"
LOOKBACK_SECONDS = 1800  # 30 minutes
MIN_WHALE_COUNT = 3       # minimum wallets buying same token to trigger signal

def load_wallets():
    with open(WALLETS_FILE) as f:
        return [line.strip() for line in f if line.strip()]

def get_recent_swaps(wallet, api_key):
    """Get recent SWAP transactions for a wallet"""
    url = f"https://api.helius.xyz/v0/addresses/{wallet}/transactions"
    params = {
        "api-key": api_key,
        "limit": 10,
        "type": "SWAP"
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            return r.json()
        return []
    except Exception as e:
        return []

def extract_token_bought(tx, wallet):
    """Extract the token mint that this wallet bought (received)"""
    now = int(time.time())
    ts = tx.get("timestamp", 0)
    
    if now - ts > LOOKBACK_SECONDS:
        return None
    
    if tx.get("type") != "SWAP":
        return None
    
    transfers = tx.get("tokenTransfers", [])
    SOL_MINT = "So11111111111111111111111111111111111111112"
    
    for t in transfers:
        # Token received by our wallet (bought)
        if t.get("toUserAccount") == wallet:
            mint = t.get("mint", "")
            if mint and mint != SOL_MINT:
                return {
                    "mint": mint,
                    "amount": t.get("tokenAmount", 0),
                    "timestamp": ts,
                    "signature": tx.get("signature", "")
                }
    return None

def get_token_info(mint):
    """Get token name/symbol from DexScreener"""
    try:
        r = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=8)
        if r.status_code == 200:
            data = r.json()
            pairs = data.get("pairs", [])
            if pairs:
                p = pairs[0]
                return {
                    "symbol": p.get("baseToken", {}).get("symbol", "UNKNOWN"),
                    "name": p.get("baseToken", {}).get("name", ""),
                    "mcap": p.get("marketCap", 0),
                    "price_usd": p.get("priceUsd", "0"),
                    "volume_24h": p.get("volume", {}).get("h24", 0),
                    "change_1h": p.get("priceChange", {}).get("h1", 0),
                    "dex_url": p.get("url", f"https://dexscreener.com/solana/{mint}")
                }
    except:
        pass
    return {"symbol": mint[:8], "name": "", "mcap": 0, "price_usd": "0", "volume_24h": 0, "change_1h": 0, "dex_url": f"https://dexscreener.com/solana/{mint}"}

def send_telegram(message):
    """Send message to Telegram"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[TELEGRAM SKIPPED - no token/chat_id]: {message[:100]}")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        r = requests.post(url, json=data, timeout=10)
        return r.json()
    except Exception as e:
        print(f"Telegram error: {e}")

def run():
    wallets = load_wallets()
    print(f"Loaded {len(wallets)} whale wallets")
    
    # Track which wallets bought which tokens
    token_buyers = defaultdict(list)  # mint -> [wallet, ...]
    token_txs = {}  # mint -> tx info
    
    # Sample first 50 wallets per run to stay within rate limits
    # Rotate through all 415 over time
    import random
    sample = random.sample(wallets, min(50, len(wallets)))
    
    checked = 0
    for wallet in sample:
        txs = get_recent_swaps(wallet, HELIUS_API_KEY)
        for tx in txs:
            token = extract_token_bought(tx, wallet)
            if token:
                mint = token["mint"]
                token_buyers[mint].append(wallet)
                if mint not in token_txs:
                    token_txs[mint] = token
        checked += 1
        if checked % 10 == 0:
            print(f"Checked {checked}/{len(sample)} wallets...")
        time.sleep(0.1)  # rate limit
    
    # Find tokens bought by multiple whales
    signals = []
    for mint, buyers in token_buyers.items():
        if len(buyers) >= MIN_WHALE_COUNT:
            info = get_token_info(mint)
            signals.append({
                "mint": mint,
                "whale_count": len(buyers),
                "buyers": buyers,
                "token_info": info,
                "tx": token_txs[mint]
            })
    
    signals.sort(key=lambda x: x["whale_count"], reverse=True)
    
    print(f"\nFound {len(signals)} whale signals:")
    
    for s in signals:
        info = s["token_info"]
        msg = f"""🐋 <b>WHALE SIGNAL — Open Claw 🦀</b>

<b>${info['symbol']}</b> {info['name']}
👥 {s['whale_count']} SPIRIT2.0 whales just bought this

📊 MCap: ${info['mcap']:,.0f}
📈 1h: {info['change_1h']:+.1f}%
💰 Vol 24h: ${info['volume_24h']:,.0f}

🔗 {info['dex_url']}

<i>Want signals like this? → t.me/ficlawcryptobot</i>"""
        
        print(msg)
        send_telegram(msg)
    
    if not signals:
        print("No whale convergence signals found in this batch.")
    
    return json.dumps({
        "wallets_checked": len(sample),
        "signals_found": len(signals),
        "signals": [{"mint": s["mint"], "symbol": s["token_info"]["symbol"], "whale_count": s["whale_count"]} for s in signals]
    })

if __name__ == "__main__":
    result = run()
    print(f"\nResult: {result}")
