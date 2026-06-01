"""Stream worker: binance.raw_trades."""
import asyncio
from groups.binance_raw_trades import main

if __name__ == "__main__":
    asyncio.run(main(stream_name="binance.raw_trades"))
