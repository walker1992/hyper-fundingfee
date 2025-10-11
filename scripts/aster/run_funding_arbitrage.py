#!/usr/bin/env python3
"""
Funding Rate Arbitrage Bot Runner
This script runs the funding rate arbitrage strategy with proper error handling and monitoring.
"""

import os
import sys
import time
import signal
import logging
from datetime import datetime
import json

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from funding_rate_arbitrage import FundingRateArbitrage

# Setup logging
log_dir = "logs"
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

log_filename = os.path.join(log_dir, f"funding_arbitrage_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_filename, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

class ArbitrageBotRunner:
    def __init__(self):
        self.arbitrage = None
        self.is_running = False
        self.shutdown_count = 0
        
    def signal_handler(self, signum, frame):
        """Handle interrupt signals"""
        self.shutdown_count += 1
        logger.info(f"Received signal {signum}, shutting down gracefully... (attempt {self.shutdown_count})")
        
        if self.shutdown_count == 1:
            self.stop()
        elif self.shutdown_count >= 2:
            logger.warning("Force exit after multiple signals")
            import os
            os._exit(1)
        
    def start(self):
        """Start the arbitrage bot"""
        try:
            logger.info("Starting Funding Rate Arbitrage Bot")
            logger.info(f"Log file: {log_filename}")
            
            # Load and display configuration
            config_path = os.path.join(os.path.dirname(__file__), "config.json")
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
                logger.info("Configuration loaded:")
                logger.info(f"  Symbol: {config.get('symbol', 'N/A')}")
                logger.info(f"  Position Size: {config.get('position_size', 'N/A')} USDT")
                logger.info(f"  Min Funding Rate: {config.get('min_funding_rate', 'N/A')}")
                logger.info(f"  Stop Loss Rate: {config.get('stop_loss_funding_rate', 'N/A')}")
                logger.info(f"  Check Interval: {config.get('check_interval', 'N/A')} seconds")
            
            # Initialize arbitrage bot
            self.arbitrage = FundingRateArbitrage()
            self.is_running = True
            
            # Setup signal handlers
            signal.signal(signal.SIGINT, self.signal_handler)
            signal.signal(signal.SIGTERM, self.signal_handler)
            
            # Start the strategy
            self.arbitrage.run_arbitrage_strategy()
            
        except Exception as e:
            logger.error(f"Failed to start bot: {e}")
            self.stop()
            
    def stop(self):
        """Stop the arbitrage bot"""
        if self.arbitrage and self.is_running:
            logger.info("Stopping arbitrage bot...")
            self.is_running = False
            self.arbitrage.stop()
            
            # # Close any open positions
            # try:
            #     if self.arbitrage.entry_prices["spot"]:
            #         logger.info("Closing open positions...")
            #         self.arbitrage.close_arbitrage_position()
            # except Exception as e:
            #     logger.error(f"Error closing positions: {e}")
                
            logger.info("Bot stopped successfully")
        else:
            logger.info("Bot already stopped or not running")

def main():
    """Main entry point"""
    runner = ArbitrageBotRunner()
    
    try:
        runner.start()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
    finally:
        runner.stop()
        logger.info("Program exiting...")

if __name__ == "__main__":
    main()
