"""HL stream worker: trades. One subprocess per event — see streams.py."""
import asyncio
from streams._hl_common import run

if __name__ == "__main__":
    asyncio.run(run("hyperliquid.trades", "trades"))
