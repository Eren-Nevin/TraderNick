"""Stream worker: binance.ohlcv."""
import asyncio
from groups.binance_ohlcv import main

if __name__ == "__main__":
    asyncio.run(main(stream_name="binance.ohlcv"))
