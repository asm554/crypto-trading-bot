"""
Jupiter Aggregator Client — Solana DEX Swaps via Jupiter V6 API.

Flow:
1. Get quote (best route across all Solana DEXes)
2. Get swap transaction (serialized, unsigned)
3. Sign with local keypair
4. Send to Solana RPC

Requires: solders, base58
"""

import aiohttp
import base64
import base58
import logging
import time
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

logger = logging.getLogger(__name__)

JUPITER_QUOTE_URL = "https://quote-api.jup.ag/v6/quote"
JUPITER_SWAP_URL = "https://quote-api.jup.ag/v6/swap"
SOLANA_RPC = "https://api.mainnet-beta.solana.com"

# Well-known Solana token mints
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
SOL_MINT = "So11111111111111111111111111111111111111112"


class JupiterClient:
    """Thin async client for Jupiter V6 swap API."""

    def __init__(self, private_key_b58: str, rpc_url: str = None):
        """
        Args:
            private_key_b58: Base58-encoded Solana private key (64 bytes)
            rpc_url: Solana RPC endpoint (default: mainnet)
        """
        key_bytes = base58.b58decode(private_key_b58)
        self.keypair = Keypair.from_bytes(key_bytes)
        self.pubkey = str(self.keypair.pubkey())
        self.rpc_url = rpc_url or SOLANA_RPC
        logger.info(f"Jupiter Client initialisiert: {self.pubkey[:8]}...")

    async def get_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount_lamports: int,
        slippage_bps: int = 100,
    ) -> dict | None:
        """
        Get best swap quote from Jupiter.

        Args:
            input_mint: Input token mint address
            output_mint: Output token mint address
            amount_lamports: Amount in smallest unit (lamports for SOL, raw for SPL)
            slippage_bps: Slippage tolerance in basis points (100 = 1%)

        Returns:
            Quote dict or None on error
        """
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount_lamports),
            "slippageBps": str(slippage_bps),
            "onlyDirectRoutes": "false",
            "asLegacyTransaction": "false",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    JUPITER_QUOTE_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error(f"Jupiter Quote Fehler {resp.status}: {body}")
                        return None
                    return await resp.json()
        except Exception as e:
            logger.error(f"Jupiter Quote Request fehlgeschlagen: {e}")
            return None

    async def execute_swap(self, quote: dict) -> dict:
        """
        Execute a swap: get transaction from Jupiter, sign it, send to RPC.

        Args:
            quote: Quote dict from get_quote()

        Returns:
            {"status": "ok", "txid": "..."} or {"status": "error", "reason": "..."}
        """
        # 1. Get serialized swap transaction
        swap_payload = {
            "quoteResponse": quote,
            "userPublicKey": self.pubkey,
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": "auto",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    JUPITER_SWAP_URL,
                    json=swap_payload,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error(f"Jupiter Swap Fehler {resp.status}: {body}")
                        return {"status": "error", "reason": f"swap_api_{resp.status}"}
                    swap_data = await resp.json()
        except Exception as e:
            logger.error(f"Jupiter Swap Request fehlgeschlagen: {e}")
            return {"status": "error", "reason": str(e)}

        swap_tx_b64 = swap_data.get("swapTransaction")
        if not swap_tx_b64:
            logger.error(f"Keine swapTransaction in Response: {swap_data}")
            return {"status": "error", "reason": "no_swap_transaction"}

        # 2. Deserialize, sign, serialize
        try:
            raw_tx = base64.b64decode(swap_tx_b64)
            tx = VersionedTransaction.from_bytes(raw_tx)
            signed_tx = VersionedTransaction(tx.message, [self.keypair])
            signed_bytes = bytes(signed_tx)
        except Exception as e:
            logger.error(f"Transaction Signing fehlgeschlagen: {e}")
            return {"status": "error", "reason": f"sign_error: {e}"}

        # 3. Send to Solana RPC
        tx_b64 = base64.b64encode(signed_bytes).decode()
        rpc_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendTransaction",
            "params": [
                tx_b64,
                {"encoding": "base64", "skipPreflight": False, "maxRetries": 3},
            ],
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.rpc_url,
                    json=rpc_payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    result = await resp.json()
        except Exception as e:
            logger.error(f"Solana RPC Fehler: {e}")
            return {"status": "error", "reason": f"rpc_error: {e}"}

        if "error" in result:
            logger.error(f"Solana TX Fehler: {result['error']}")
            return {"status": "error", "reason": str(result["error"])}

        txid = result.get("result", "")
        logger.info(f"✅ Jupiter Swap TX: {txid}")
        return {"status": "ok", "txid": txid}

    async def swap_usdc_for_token(
        self, token_mint: str, amount_usdc: float, slippage_bps: int = 150
    ) -> dict:
        """
        Convenience: Swap USDC → Token.

        Args:
            token_mint: Target token contract address
            amount_usdc: Amount in USDC (e.g. 5.0 = $5)
            slippage_bps: Slippage in bps (150 = 1.5%)

        Returns:
            Swap result dict
        """
        # USDC has 6 decimals
        amount_raw = int(amount_usdc * 1_000_000)

        quote = await self.get_quote(USDC_MINT, token_mint, amount_raw, slippage_bps)
        if not quote:
            return {"status": "error", "reason": "no_quote"}

        out_amount = int(quote.get("outAmount", 0))
        price_impact = float(quote.get("priceImpactPct", 0))

        logger.info(
            f"Jupiter Quote: {amount_usdc} USDC → {out_amount} raw tokens | "
            f"Price Impact: {price_impact:.2%}"
        )

        # Safety: block if price impact too high
        if abs(price_impact) > 5.0:
            logger.warning(f"Price Impact zu hoch: {price_impact:.2%} — Trade abgebrochen")
            return {"status": "error", "reason": f"price_impact_too_high: {price_impact:.2%}"}

        return await self.execute_swap(quote)

    async def swap_token_for_usdc(
        self, token_mint: str, amount_raw: int, slippage_bps: int = 150
    ) -> dict:
        """
        Convenience: Swap Token → USDC (sell/exit).

        Args:
            token_mint: Token contract address to sell
            amount_raw: Amount in raw token units
            slippage_bps: Slippage in bps
        """
        quote = await self.get_quote(token_mint, USDC_MINT, amount_raw, slippage_bps)
        if not quote:
            return {"status": "error", "reason": "no_quote"}

        price_impact = float(quote.get("priceImpactPct", 0))
        if abs(price_impact) > 5.0:
            logger.warning(f"Sell Price Impact zu hoch: {price_impact:.2%}")
            return {"status": "error", "reason": f"price_impact_too_high: {price_impact:.2%}"}

        return await self.execute_swap(quote)

    async def get_token_balance(self, token_mint: str) -> int:
        """Get SPL token balance for our wallet (raw units)."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenAccountsByOwner",
            "params": [
                self.pubkey,
                {"mint": token_mint},
                {"encoding": "jsonParsed"},
            ],
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    result = await resp.json()

            accounts = result.get("result", {}).get("value", [])
            if not accounts:
                return 0

            info = accounts[0].get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
            return int(info.get("tokenAmount", {}).get("amount", 0))
        except Exception as e:
            logger.error(f"Balance-Abfrage fehlgeschlagen: {e}")
            return 0
