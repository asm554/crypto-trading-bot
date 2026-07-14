import os
import json
from dotenv import load_dotenv

load_dotenv()

# ==========================================
# SECRETS (.env)
# ==========================================
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
CLOB_API_KEY = os.getenv("CLOB_API_KEY")
CLOB_SECRET = os.getenv("CLOB_SECRET")
CLOB_PASSPHRASE = os.getenv("CLOB_PASSPHRASE")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
VISUAL_CROSSING_KEY = os.getenv("VISUAL_CROSSING_KEY")
POLYGON_RPC_URL = os.getenv("POLYGON_RPC_URL")
KRAKEN_API_KEY = os.getenv("KRAKEN_API_KEY")
KRAKEN_API_SECRET = os.getenv("KRAKEN_API_SECRET")

PAPER_MODE = os.getenv("PAPER_MODE", "true").lower() == "true"
POLYGON_CHAIN_ID = 137
HOST = "https://clob.polymarket.com"

# ==========================================
# PARAMS (config.json)
# ==========================================
try:
    with open(os.path.join(os.path.dirname(__file__), "config.json"), "r") as f:
        _settings = json.load(f)
except Exception as e:
    _settings = {}
    
BOT_NAME = _settings.get("bot_name", "V3-Nash-Weather")
FEE_MODEL_ENABLED = _settings.get("fee_model_enabled", True)
WEATHER_TAKER_FEE_RATE = float(_settings.get("weather_taker_fee_rate", 0.05))
CRYPTO_TAKER_FEE_RATE = float(_settings.get("crypto_taker_fee_rate", 0.004))
CRYPTO_MAKER_FEE_RATE = float(_settings.get("crypto_maker_fee_rate", 0.0016))
STRESS_TEST_MAKER_AS_TAKER = _settings.get("stress_test_maker_as_taker_fees", False)
BALANCE_USD = _settings.get("balance_usd", 1000)
MAX_BET_USD = _settings.get("max_bet_usd", 100)
MIN_EV_PERCENT = _settings.get("min_ev_percent", 1.0) / 100.0  # z.B. 0.025
KELLY_FRACTION = _settings.get("kelly_fraction", 0.25)
SLIPPAGE = _settings.get("slippage_tolerance", 0.01)

# Specific HFT & Risk Params
MAX_PORTFOLIO_PCT = _settings.get("max_portfolio_pct", 0.08)
MIN_DETECTION_EDGE = _settings.get("min_detection_edge_pct", 5.0) / 100.0
MIN_EXECUTION_EDGE = _settings.get("min_execution_edge_pct", 8.0) / 100.0
MIN_LIQUIDITY_USD = _settings.get("min_market_liquidity_usd", 50000)
MAX_MONITORED_MARKETS = _settings.get("max_monitored_markets", 20)
DAILY_LOSS_HALT_PCT = _settings.get("daily_loss_halt_pct", 20.0) / 100.0
ATH_DRAWDOWN_HALT_PCT = _settings.get("ath_drawdown_halt_pct", 40.0) / 100.0
CONSECUTIVE_LOSS_LIMIT = _settings.get("consecutive_loss_limit", 5)
CONSECUTIVE_LOSS_PAUSE_SEC = _settings.get("consecutive_loss_pause_min", 30) * 60
STALE_DATA_THRESHOLD = _settings.get("stale_data_threshold_sec", 10)
HEARTBEAT_INTERVAL = _settings.get("heartbeat_interval_sec", 3600)

# Whale Tracker Params
WHALE_INTERVAL = _settings.get("whale_monitor_interval_sec", 60)
WHALE_MIN_BET = _settings.get("whale_min_bet_usd", 1000)
WHALE_TOP_LIMIT = _settings.get("whale_top_limit", 10)
WHALE_CLASSIFIER_MIN_SCORE = _settings.get("whale_classifier_min_score", 65)
WHALE_MIN_NOTIONAL_USD = _settings.get("whale_min_notional_usd", 25000)
WHALE_MIN_DAYS_LEFT = _settings.get("whale_min_days_left", 7)
WHALE_MAX_BANKROLL_PCT = _settings.get("whale_max_bankroll_pct", 1.5)

# Solana / Jupiter
SOL_PRIVATE_KEY = os.getenv("SOL_PRIVATE_KEY")  # Base58-encoded Solana private key
SOL_RPC_URL = os.getenv("SOL_RPC_URL", "https://api.mainnet-beta.solana.com")

ALCHEMY_RPC_URL = os.getenv("ALCHEMY_RPC_URL")

# Azuro Protocol
AZURO_LP_CONTRACT = os.getenv("AZURO_LP_CONTRACT", "0xC065f57F1c4F9a01a9a7Ba33a5f1D98eF2C09aB1")
AZURO_CHAIN_ID    = int(os.getenv("AZURO_CHAIN_ID", "137"))
AZURO_USDC        = os.getenv("AZURO_USDC", "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
AZURO_SUBGRAPH    = os.getenv(
    "AZURO_SUBGRAPH",
    "https://api.thegraph.com/subgraphs/name/azuro-protocol/azuro-api-polygon-v3"
)
AZURO_FEE_RATE      = float(_settings.get("azuro_taker_fee_rate", 0.05))
AZURO_MIN_ODDS      = float(_settings.get("azuro_min_odds", 1.5))
AZURO_MAX_BET_USDC  = float(_settings.get("azuro_max_bet_usdc", 50))
AZURO_WHALE_MIN_USD = float(_settings.get("azuro_whale_min_usdc", 5000))
AZURO_INTERVAL      = int(_settings.get("azuro_monitor_interval_sec", 30))

# Smart Money Tracker
# Smart Money Tracker
SM_INTERVAL = _settings.get("smart_money_interval_sec", 120)
SM_MIN_WALLETS = _settings.get("smart_money_min_wallets", 3)
SM_MAX_EXIT_RATE = _settings.get("smart_money_max_exit_rate", 50)
SM_TRADE_MIN_WALLETS = _settings.get("smart_money_trade_min_wallets", 4)
SM_TRADE_MIN_VALUE_USD = _settings.get("smart_money_trade_min_value_usd", 3000)
SM_TRADE_MIN_SIGNALS = _settings.get("smart_money_trade_min_signals", 2)
SM_TRADE_COOLDOWN_SEC = _settings.get("smart_money_trade_cooldown_sec", 300)
SM_JUPITER_MAX_USD = _settings.get("smart_money_jupiter_max_usd", 5.0)
SM_JUPITER_MAX_POSITIONS = _settings.get("smart_money_jupiter_max_positions", 3)
SM_JUPITER_SLIPPAGE_BPS = _settings.get("smart_money_jupiter_slippage_bps", 150)
SM_JUPITER_MAX_HOLD_SEC = _settings.get("smart_money_jupiter_max_hold_sec", 1800)

HOURLY_SCAN_SEC = _settings.get("hourly_scan_interval_sec", 3600)
MARKET_MONITOR_SEC = _settings.get("market_monitor_interval_sec", 600)
CITIES = _settings.get("cities", [])

# Konstanten für Trade Execution
TRADE_TIMEOUT = 1.2
MAX_DAILY_LOSS = 0.05

def validate_config():
    missing = []
    if not PAPER_MODE:
        if not PRIVATE_KEY:
            missing.append("PRIVATE_KEY")
        if not CLOB_API_KEY:
            missing.append("CLOB_API_KEY")
        if not CLOB_SECRET:
            missing.append("CLOB_SECRET")
        if not CLOB_PASSPHRASE:
            missing.append("CLOB_PASSPHRASE")
        if not KRAKEN_API_KEY:
            missing.append("KRAKEN_API_KEY")
        if not KRAKEN_API_SECRET:
            missing.append("KRAKEN_API_SECRET")
        if PRIVATE_KEY and not str(PRIVATE_KEY).startswith("0x"):
            raise ValueError("PRIVATE_KEY muss mit 0x beginnen (MetaMask-Export).")
    if missing:
        raise ValueError(f"Fehlende Konfiguration in .env: {', '.join(missing)}")
