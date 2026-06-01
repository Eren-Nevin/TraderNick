"""HL stream worker: position_history. One subprocess per event — see streams.py."""
import asyncio
from streams._hl_common import run

if __name__ == "__main__":
    asyncio.run(run("hyperliquid.position_history", "position_history"))
