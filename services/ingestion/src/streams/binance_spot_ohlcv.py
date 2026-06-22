"""Stream worker: binance.spot_ohlcv."""
import asyncio
from groups.binance_spot_ohlcv import main

if __name__ == "__main__":
    asyncio.run(main(stream_name="binance.spot_ohlcv"))
