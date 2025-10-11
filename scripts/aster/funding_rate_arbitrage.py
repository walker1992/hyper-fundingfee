import os
import time
import hmac
import hashlib
import urllib.parse
import decimal
import requests
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
import threading

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('funding_arbitrage.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
try:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        CONFIG = json.load(f)
except Exception as e:
    logger.error(f"Failed to load config: {e}")
    CONFIG = {}

API_KEY = CONFIG.get("api_key")
SECRET_KEY = CONFIG.get("api_secret")

if not API_KEY or not SECRET_KEY:
    logger.error("API credentials missing: set in config.json.")
    exit(1)

# API endpoints
SPOT_BASE_URL = CONFIG.get("spot_base_url", "https://sapi.asterdex.com")
FUTURES_BASE_URL = CONFIG.get("base_url", "https://fapi.asterdex.com")
SPOT_API_PREFIX = "/api/v1"
FUTURES_API_PREFIX = "/fapi/v1"

# Trading parameters
SYMBOL = CONFIG.get("symbol", "ASTERUSDT")
SPOT_SYMBOL = CONFIG.get("spot_symbol", "ASTERUSDT")
POSITION_SIZE = decimal.Decimal(str(CONFIG.get("position_size", "1000")))
MIN_FUNDING_RATE = decimal.Decimal(str(CONFIG.get("min_funding_rate", "0.0002")))
STOP_LOSS_FUNDING_RATE = decimal.Decimal(str(CONFIG.get("stop_loss_funding_rate", "-0.0005")))
CHECK_INTERVAL = int(CONFIG.get("check_interval", "300"))
MAX_UNREALIZED_LOSS = decimal.Decimal(str(CONFIG.get("max_unrealized_loss", "100")))
TRADING_FEE_RATE = decimal.Decimal(str(CONFIG.get("trading_fee_rate", "0.0004")))

# Risk management
MAX_LEVERAGE = int(CONFIG.get("max_leverage", "1"))
MIN_MARGIN_RATIO = decimal.Decimal(str(CONFIG.get("min_margin_ratio", "0.2")))
RISK_CHECK_INTERVAL = int(CONFIG.get("risk_check_interval", "60"))

# Precision settings
FUTURES_TICK_SIZE = decimal.Decimal(str(CONFIG.get("futures_tick_size", "0.001")))
FUTURES_STEP_SIZE = decimal.Decimal(str(CONFIG.get("futures_step_size", "1")))
SPOT_TICK_SIZE = decimal.Decimal(str(CONFIG.get("spot_tick_size", "0.01")))
SPOT_STEP_SIZE = decimal.Decimal(str(CONFIG.get("spot_step_size", "0.001")))

# Time sync
TIME_SYNC_INTERVAL_MS = 60000
_SERVER_TIME_OFFSET_MS = 0
_LAST_TIME_SYNC_MS = 0

def quantize_price(price: decimal.Decimal, tick_size: decimal.Decimal) -> decimal.Decimal:
    """Quantize price to tick size"""
    if tick_size == 0:
        return price
    return (price / tick_size).quantize(decimal.Decimal('1'), rounding=decimal.ROUND_DOWN) * tick_size

def quantize_quantity(quantity: decimal.Decimal, step_size: decimal.Decimal) -> decimal.Decimal:
    """Quantize quantity to step size"""
    if step_size == 0:
        return quantity
    return (quantity / step_size).quantize(decimal.Decimal('1'), rounding=decimal.ROUND_DOWN) * step_size

class FundingRateArbitrage:
    def __init__(self):
        self.spot_position = None
        self.futures_position = None
        self.is_running = False
        self.last_funding_time = None
        self.total_profit = decimal.Decimal("0")
        self.entry_prices = {"spot": None, "futures": None}
        
        # Initialize precision settings
        self.spot_tick_size = SPOT_TICK_SIZE
        self.spot_step_size = SPOT_STEP_SIZE
        self.futures_tick_size = FUTURES_TICK_SIZE
        self.futures_step_size = FUTURES_STEP_SIZE
        
        # Try to get actual precision from exchange
        self._update_precision_from_exchange()
        
    def _update_precision_from_exchange(self):
        """Update precision settings from exchange info"""
        try:
            # Get spot precision
            spot_endpoint = f"{SPOT_API_PREFIX}/exchangeInfo"
            resp = requests.get(f"{SPOT_BASE_URL}{spot_endpoint}")
            if resp.ok:
                data = resp.json()
                for symbol_info in data.get("symbols", []):
                    if symbol_info.get("symbol") == SPOT_SYMBOL:
                        for filter_info in symbol_info.get("filters", []):
                            if filter_info.get("filterType") == "PRICE_FILTER":
                                self.spot_tick_size = decimal.Decimal(str(filter_info.get("tickSize", "0.01")))
                            elif filter_info.get("filterType") == "LOT_SIZE":
                                self.spot_step_size = decimal.Decimal(str(filter_info.get("stepSize", "0.001")))
                        break
            
            # Get futures precision
            futures_endpoint = f"{FUTURES_API_PREFIX}/exchangeInfo"
            resp = requests.get(f"{FUTURES_BASE_URL}{futures_endpoint}")
            if resp.ok:
                data = resp.json()
                for symbol_info in data.get("symbols", []):
                    if symbol_info.get("symbol") == SYMBOL:
                        for filter_info in symbol_info.get("filters", []):
                            if filter_info.get("filterType") == "PRICE_FILTER":
                                self.futures_tick_size = decimal.Decimal(str(filter_info.get("tickSize", "0.001")))
                            elif filter_info.get("filterType") == "LOT_SIZE":
                                self.futures_step_size = decimal.Decimal(str(filter_info.get("stepSize", "1")))
                        break
            
            logger.info(f"Updated precision - Spot: tick={self.spot_tick_size}, step={self.spot_step_size}")
            logger.info(f"Updated precision - Futures: tick={self.futures_tick_size}, step={self.futures_step_size}")
            
        except Exception as e:
            logger.warning(f"Failed to update precision from exchange: {e}")
        
    def _sync_server_time(self):
        global _SERVER_TIME_OFFSET_MS, _LAST_TIME_SYNC_MS
        try:
            resp = requests.get(f"{FUTURES_BASE_URL}{FUTURES_API_PREFIX}/time")
            resp.raise_for_status()
            server_ms = int(resp.json().get("serverTime", 0))
            local_ms = int(time.time() * 1000)
            _SERVER_TIME_OFFSET_MS = server_ms - local_ms
            _LAST_TIME_SYNC_MS = local_ms
        except Exception as e:
            logger.warning(f"Time sync failed: {e}")

    def _now_ms(self) -> int:
        return int(time.time() * 1000) + _SERVER_TIME_OFFSET_MS

    def _generate_signature(self, params_str: str) -> str:
        return hmac.new(SECRET_KEY.encode("utf-8"), params_str.encode("utf-8"), hashlib.sha256).hexdigest()

    def _make_signed_request(self, method: str, endpoint: str, params: dict = None, base_url: str = FUTURES_BASE_URL, api_prefix: str = FUTURES_API_PREFIX):
        if params is None:
            params = {}

        if _LAST_TIME_SYNC_MS == 0 or (int(time.time() * 1000) - _LAST_TIME_SYNC_MS) > TIME_SYNC_INTERVAL_MS:
            self._sync_server_time()
            
        params_for_signing = dict(params)
        params_for_signing["timestamp"] = self._now_ms()
        params_for_signing["recvWindow"] = 5000

        query_string_to_sign = urllib.parse.urlencode(sorted(params_for_signing.items()))
        signature = self._generate_signature(query_string_to_sign)
        final_query_string = f"{query_string_to_sign}&signature={signature}"
        full_url = f"{base_url}{endpoint}?{final_query_string}"

        headers = {"X-MBX-APIKEY": API_KEY}
        if method.upper() in ["POST", "DELETE"]:
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        try:
            if method.upper() == "GET":
                resp = requests.get(full_url, headers=headers)
            elif method.upper() == "POST":
                post_url = f"{base_url}{endpoint}"
                resp = requests.post(post_url, headers=headers, data=final_query_string)
            elif method.upper() == "DELETE":
                resp = requests.delete(full_url, headers=headers)
            else:
                raise ValueError(f"Unsupported method: {method}")

            if not resp.ok:
                try:
                    err_json = resp.json()
                    code = err_json.get("code")
                    msg = err_json.get("msg")
                    raise requests.HTTPError(f"HTTP {resp.status_code}: code={code} msg={msg}", response=resp)
                except ValueError:
                    raise requests.HTTPError(f"HTTP {resp.status_code}: {resp.text}", response=resp)

            return resp.json()
        except requests.HTTPError as e:
            if "-1021" in str(e):
                self._sync_server_time()
                return self._make_signed_request(method, endpoint, params, base_url, api_prefix)
            raise

    def get_funding_rate(self) -> Optional[decimal.Decimal]:
        """Get current funding rate for the symbol"""
        try:
            endpoint = f"{FUTURES_API_PREFIX}/premiumIndex"
            params = {"symbol": SYMBOL}
            resp = requests.get(f"{FUTURES_BASE_URL}{endpoint}", params=params)
            resp.raise_for_status()
            data = resp.json()
            return decimal.Decimal(str(data.get("lastFundingRate", "0")))
        except Exception as e:
            logger.error(f"Failed to get funding rate: {e}")
            return None

    def get_next_funding_time(self) -> Optional[datetime]:
        """Get next funding time"""
        try:
            endpoint = f"{FUTURES_API_PREFIX}/premiumIndex"
            params = {"symbol": SYMBOL}
            resp = requests.get(f"{FUTURES_BASE_URL}{endpoint}", params=params)
            resp.raise_for_status()
            data = resp.json()
            next_funding_time = data.get("nextFundingTime")
            if next_funding_time:
                return datetime.fromtimestamp(next_funding_time / 1000)
            return None
        except Exception as e:
            logger.error(f"Failed to get next funding time: {e}")
            return None

    def get_spot_balance(self) -> Dict[str, decimal.Decimal]:
        """Get spot account balances"""
        try:
            endpoint = f"{SPOT_API_PREFIX}/account"
            info = self._make_signed_request("GET", endpoint, {}, SPOT_BASE_URL, SPOT_API_PREFIX)
            balances = {}
            for balance in info.get("balances", []):
                asset = balance.get("asset")
                free = decimal.Decimal(str(balance.get("free", "0")))
                locked = decimal.Decimal(str(balance.get("locked", "0")))
                balances[asset] = {"free": free, "locked": locked}
            return balances
        except Exception as e:
            logger.error(f"Failed to get spot balances: {e}")
            return {}

    def get_futures_balance(self) -> Dict[str, decimal.Decimal]:
        """Get futures account balances"""
        try:
            endpoint = f"{FUTURES_API_PREFIX}/account"
            info = self._make_signed_request("GET", endpoint)
            balances = {}
            for asset in info.get("assets", []):
                asset_name = asset.get("asset")
                wallet_balance = decimal.Decimal(str(asset.get("walletBalance", "0")))
                cross_wallet_balance = decimal.Decimal(str(asset.get("crossWalletBalance", "0")))
                balances[asset_name] = {
                    "wallet": wallet_balance,
                    "cross_wallet": cross_wallet_balance
                }
            return balances
        except Exception as e:
            logger.error(f"Failed to get futures balances: {e}")
            return {}

    def get_spot_ticker(self) -> Optional[Dict[str, decimal.Decimal]]:
        """Get spot ticker data"""
        try:
            endpoint = f"{SPOT_API_PREFIX}/ticker/bookTicker"
            params = {"symbol": SPOT_SYMBOL}
            resp = requests.get(f"{SPOT_BASE_URL}{endpoint}", params=params)
            resp.raise_for_status()
            data = resp.json()
            return {
                "bid": decimal.Decimal(str(data["bidPrice"])),
                "ask": decimal.Decimal(str(data["askPrice"]))
            }
        except Exception as e:
            logger.error(f"Failed to get spot ticker: {e}")
            return None

    def get_futures_ticker(self) -> Optional[Dict[str, decimal.Decimal]]:
        """Get futures ticker data"""
        try:
            endpoint = f"{FUTURES_API_PREFIX}/ticker/bookTicker"
            params = {"symbol": SYMBOL}
            resp = requests.get(f"{FUTURES_BASE_URL}{endpoint}", params=params)
            resp.raise_for_status()
            data = resp.json()
            return {
                "bid": decimal.Decimal(str(data["bidPrice"])),
                "ask": decimal.Decimal(str(data["askPrice"]))
            }
        except Exception as e:
            logger.error(f"Failed to get futures ticker: {e}")
            return None

    def place_spot_buy_order(self, quantity: decimal.Decimal, price: decimal.Decimal) -> Optional[Dict]:
        """Place spot buy order"""
        try:
            # Quantize price and quantity to proper precision
            quantized_price = quantize_price(price, self.spot_tick_size)
            quantized_quantity = quantize_quantity(quantity, self.spot_step_size)
            
            logger.info(f"Placing spot buy order: quantity={quantized_quantity}, price={quantized_price}")
            
            endpoint = f"{SPOT_API_PREFIX}/order"
            params = {
                "symbol": SPOT_SYMBOL,
                "side": "BUY",
                "type": "LIMIT",
                "timeInForce": "GTC",
                "quantity": str(quantized_quantity),
                "price": str(quantized_price)
            }
            return self._make_signed_request("POST", endpoint, params, SPOT_BASE_URL, SPOT_API_PREFIX)
        except Exception as e:
            logger.error(f"Failed to place spot buy order: {e}")
            return None

    def place_futures_sell_order(self, quantity: decimal.Decimal, price: decimal.Decimal) -> Optional[Dict]:
        """Place futures sell order (short position)"""
        try:
            # Quantize price and quantity to proper precision
            quantized_price = quantize_price(price, self.futures_tick_size)
            quantized_quantity = quantize_quantity(quantity, self.futures_step_size)
            
            logger.info(f"Placing futures sell order: quantity={quantized_quantity}, price={quantized_price}")
            
            endpoint = f"{FUTURES_API_PREFIX}/order"
            params = {
                "symbol": SYMBOL,
                "side": "SELL",
                "type": "LIMIT",
                "timeInForce": "GTC",
                "quantity": str(quantized_quantity),
                "price": str(quantized_price)
            }
            return self._make_signed_request("POST", endpoint, params)
        except Exception as e:
            logger.error(f"Failed to place futures sell order: {e}")
            return None

    def close_spot_position(self) -> bool:
        """Close spot position by selling all holdings"""
        try:
            balances = self.get_spot_balance()
            base_asset = SYMBOL.replace("USDT", "")
            if base_asset not in balances or balances[base_asset]["free"] <= 0:
                logger.info("No spot position to close")
                return True

            quantity = balances[base_asset]["free"]
            logger.info(f"Closing spot position of {quantity} {base_asset}")
            
            ticker = self.get_spot_ticker()
            if not ticker:
                logger.error("Cannot get spot ticker for closing position")
                return False

            price = ticker["bid"]  # Use bid price for selling
            
            # Quantize price and quantity to proper precision
            quantized_price = quantize_price(price, self.spot_tick_size)
            quantized_quantity = quantize_quantity(quantity, self.spot_step_size)
            
            logger.info(f"Placing spot sell order to close position: quantity={quantized_quantity}, price={quantized_price}")
            
            endpoint = f"{SPOT_API_PREFIX}/order"
            params = {
                "symbol": SPOT_SYMBOL,
                "side": "SELL",
                "type": "LIMIT",
                "timeInForce": "GTC",
                "quantity": str(quantized_quantity),
                "price": str(quantized_price)
            }
            result = self._make_signed_request("POST", endpoint, params, SPOT_BASE_URL, SPOT_API_PREFIX)
            if result:
                logger.info(f"Spot position closed: {result}")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to close spot position: {e}")
            return False

    def close_futures_position(self) -> bool:
        """Close futures position by buying back"""
        try:
            # Get current position
            endpoint = f"{FUTURES_API_PREFIX}/positionRisk"
            positions = self._make_signed_request("GET", endpoint)
            
            position_size = decimal.Decimal("0")
            for pos in positions:
                if pos.get("symbol") == SYMBOL:
                    position_size = decimal.Decimal(str(pos.get("positionAmt", "0")))
                    logger.info(f"Current futures position size: {position_size}")
                    break

            if position_size >= 0:  # No short position
                logger.info("No futures short position to close")
                return True

            # We have a short position, need to buy back
            logger.info(f"Closing futures short position of {abs(position_size)} {SYMBOL}")
            
            ticker = self.get_futures_ticker()
            if not ticker:
                logger.error("Cannot get futures ticker for closing position")
                return False

            quantity = abs(position_size)
            price = ticker["ask"]  # Use ask price for buying back
            
            # Quantize price and quantity to proper precision
            quantized_price = quantize_price(price, self.futures_tick_size)
            quantized_quantity = quantize_quantity(quantity, self.futures_step_size)
            
            logger.info(f"Placing futures buy order to close short: quantity={quantized_quantity}, price={quantized_price}")
            
            endpoint = f"{FUTURES_API_PREFIX}/order"
            params = {
                "symbol": SYMBOL,
                "side": "BUY",
                "type": "LIMIT",
                "timeInForce": "GTC",
                "quantity": str(quantized_quantity),
                "price": str(quantized_price)
            }
            result = self._make_signed_request("POST", endpoint, params)
            if result:
                logger.info(f"Futures position closed: {result}")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to close futures position: {e}")
            return False

    def verify_positions_closed(self) -> bool:
        """Verify that all positions are properly closed"""
        try:
            # Check spot position
            balances = self.get_spot_balance()
            base_asset = SYMBOL.replace("USDT", "")
            spot_balance = balances.get(base_asset, {}).get("free", decimal.Decimal("0"))
            
            # Check futures position
            endpoint = f"{FUTURES_API_PREFIX}/positionRisk"
            positions = self._make_signed_request("GET", endpoint)
            
            futures_position = decimal.Decimal("0")
            for pos in positions:
                if pos.get("symbol") == SYMBOL:
                    futures_position = decimal.Decimal(str(pos.get("positionAmt", "0")))
                    break
            
            logger.info(f"Position verification - Spot {base_asset}: {spot_balance}, Futures {SYMBOL}: {futures_position}")
            
            # Consider positions closed if spot balance is very small and futures position is zero
            if spot_balance < decimal.Decimal("0.01") and abs(futures_position) < decimal.Decimal("0.01"):
                logger.info("All positions successfully closed")
                return True
            else:
                logger.warning(f"Positions may not be fully closed - Spot: {spot_balance}, Futures: {futures_position}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to verify positions: {e}")
            return False

    def calculate_profit_loss(self) -> decimal.Decimal:
        """Calculate current P&L from the arbitrage position"""
        try:
            if not self.entry_prices["spot"] or not self.entry_prices["futures"]:
                return decimal.Decimal("0")

            spot_ticker = self.get_spot_ticker()
            futures_ticker = self.get_futures_ticker()
            
            if not spot_ticker or not futures_ticker:
                return decimal.Decimal("0")

            # Calculate spot P&L (current value - entry value)
            spot_pnl = (spot_ticker["bid"] - self.entry_prices["spot"]) * (POSITION_SIZE / self.entry_prices["spot"])
            
            # Calculate futures P&L (entry value - current value, since we're short)
            futures_pnl = (self.entry_prices["futures"] - futures_ticker["ask"]) * (POSITION_SIZE / self.entry_prices["futures"])
            
            # Total P&L minus trading fees
            total_pnl = spot_pnl + futures_pnl - (POSITION_SIZE * TRADING_FEE_RATE * 2)
            
            return total_pnl
        except Exception as e:
            logger.error(f"Failed to calculate P&L: {e}")
            return decimal.Decimal("0")

    def check_risk_limits(self) -> bool:
        """Check if we're within risk limits"""
        try:
            pnl = self.calculate_profit_loss()
            
            # Check maximum loss limit
            if pnl < -MAX_UNREALIZED_LOSS:
                logger.warning(f"Maximum loss exceeded: {pnl}")
                return False
            
            # Check margin ratio for futures
            balances = self.get_futures_balance()
            usdt_balance = balances.get("USDT", {}).get("wallet", decimal.Decimal("0"))
            if usdt_balance < POSITION_SIZE * MIN_MARGIN_RATIO:
                logger.warning(f"Insufficient margin: {usdt_balance}")
                return False
                
            return True
        except Exception as e:
            logger.error(f"Risk check failed: {e}")
            return False

    def open_arbitrage_position(self) -> bool:
        """Open arbitrage position: buy spot, sell futures"""
        try:
            spot_ticker = self.get_spot_ticker()
            futures_ticker = self.get_futures_ticker()
            
            if not spot_ticker or not futures_ticker:
                logger.error("Cannot get ticker data")
                return False

            # Calculate quantities
            spot_price = spot_ticker["ask"]  # Buy at ask
            futures_price = futures_ticker["bid"]  # Sell at bid
            
            spot_quantity = POSITION_SIZE / spot_price
            futures_quantity = POSITION_SIZE / futures_price

            # Place orders
            spot_order = self.place_spot_buy_order(spot_quantity, spot_price)
            futures_order = self.place_futures_sell_order(futures_quantity, futures_price)

            if spot_order and futures_order:
                self.entry_prices["spot"] = spot_price
                self.entry_prices["futures"] = futures_price
                logger.info(f"Arbitrage position opened - Spot: {spot_price}, Futures: {futures_price}")
                return True
            else:
                logger.error("Failed to place one or both orders")
                return False
                
        except Exception as e:
            logger.error(f"Failed to open arbitrage position: {e}")
            return False

    def close_arbitrage_position(self) -> bool:
        """Close arbitrage position"""
        try:
            logger.info("Starting to close arbitrage position...")
            
            spot_closed = self.close_spot_position()
            futures_closed = self.close_futures_position()
            
            if spot_closed and futures_closed:
                logger.info("Initial close orders placed successfully")
                
                # Wait a moment for orders to potentially execute
                time.sleep(2)
                
                # Verify positions are actually closed
                if self.verify_positions_closed():
                    logger.info("Arbitrage position closed successfully")
                    self.entry_prices = {"spot": None, "futures": None}
                    return True
                else:
                    logger.warning("Close orders placed but positions may not be fully closed")
                    return True  # Still return True as orders were placed
            else:
                logger.error("Failed to close arbitrage position completely")
                return False
        except Exception as e:
            logger.error(f"Failed to close arbitrage position: {e}")
            return False

    def run_arbitrage_strategy(self):
        """Main arbitrage strategy loop"""
        logger.info("Starting funding rate arbitrage strategy")
        self.is_running = True
        
        while self.is_running:
            try:
                # Check if we should stop before each iteration
                if not self.is_running:
                    break
                    
                # Get current funding rate
                funding_rate = self.get_funding_rate()
                if funding_rate is None:
                    logger.warning("Cannot get funding rate, skipping this cycle")
                    # Use shorter sleep and check is_running
                    for _ in range(60):
                        if not self.is_running:
                            break
                        time.sleep(1)
                    continue

                logger.info(f"Current funding rate: {funding_rate:.6f}")

                # Check if we should open a position
                if funding_rate >= MIN_FUNDING_RATE and not self.entry_prices["spot"]:
                    logger.info(f"Funding rate {funding_rate} >= {MIN_FUNDING_RATE}, opening position")
                    if self.open_arbitrage_position():
                        logger.info("Position opened successfully")
                    else:
                        logger.error("Failed to open position")

                # Check if we should close position
                elif funding_rate <= STOP_LOSS_FUNDING_RATE and self.entry_prices["spot"]:
                    logger.info(f"Funding rate {funding_rate} <= {STOP_LOSS_FUNDING_RATE}, closing position")
                    if self.close_arbitrage_position():
                        logger.info("Position closed successfully")
                    else:
                        logger.error("Failed to close position")

                # Risk management check
                if self.entry_prices["spot"] and not self.check_risk_limits():
                    logger.warning("Risk limits exceeded, closing position")
                    self.close_arbitrage_position()

                # Log current status
                if self.entry_prices["spot"]:
                    pnl = self.calculate_profit_loss()
                    logger.info(f"Current P&L: {pnl:.4f} USDT")

                # Use shorter sleep intervals and check is_running
                for _ in range(CHECK_INTERVAL):
                    if not self.is_running:
                        break
                    time.sleep(1)
                
            except KeyboardInterrupt:
                logger.info("Received interrupt signal, closing positions...")
                self.is_running = False
                self.close_arbitrage_position()
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                # Use shorter sleep and check is_running
                for _ in range(60):
                    if not self.is_running:
                        break
                    time.sleep(1)

        self.is_running = False
        logger.info("Arbitrage strategy stopped")

    def stop(self):
        """Stop the arbitrage strategy"""
        self.is_running = False

def main():
    """Main function"""
    logger.info("Initializing funding rate arbitrage bot")
    
    # Verify credentials
    try:
        arbitrage = FundingRateArbitrage()
        # Test API access
        spot_balances = arbitrage.get_spot_balance()
        futures_balances = arbitrage.get_futures_balance()
        
        if not spot_balances and not futures_balances:
            logger.error("Failed to access API, check credentials")
            return
            
        logger.info("API credentials verified")
        logger.info(f"Spot balances: {spot_balances}")
        logger.info(f"Futures balances: {futures_balances}")
        
    except Exception as e:
        logger.error(f"Initialization failed: {e}")
        return

    # Start arbitrage strategy
    try:
        arbitrage.run_arbitrage_strategy()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot crashed: {e}")

if __name__ == "__main__":
    main()
