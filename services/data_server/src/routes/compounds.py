"""Compound-token definitions.

A *compound* is a synthetic token that aggregates transfer volume across
several real (chain, kind, token) tuples. The dashboard surfaces compounds
alongside real tokens in the token dropdown; when one is picked, the
aggregate query swaps its single chain/kind/token WHERE clause for an
`(chain, kind, token) IN (...)` predicate and sums everything in one
ClickHouse pass.

Compounds are deliberately *server-defined* — the dashboard receives names
+ labels via /transfers/compounds but the pair list lives here. Adding a
new compound or a new chain to an existing one is a code edit, not a
config change, which is what we want for now.

If a pair listed below isn't actually ingested yet (e.g. USDE on ETH, or
the native ETH/BNB/POL streams), it contributes 0 to the sum until the
ingestion side catches up — no schema or compound-definition change is
needed at that point.
"""
from __future__ import annotations

from sanic import Blueprint, response

bp = Blueprint("compounds")

# Each compound: name → {label, description, pairs: [(chain, kind, token), ...]}
# `name` is the dashboard-facing identifier (also the value of
# `?compound=<name>` on the aggregate endpoint).
COMPOUNDS: dict[str, dict] = {
    "USDC+USDT": {
        "label": "USDC + USDT",
        "description": "USDC + USDT across every chain we ingest",
        "pairs": [
            ("ARB", "erc20", "USDC"),
            ("BASE", "erc20", "USDC"),
            ("BSC", "erc20", "USDC"),
            ("ETH", "erc20", "USDC"),
            ("POLYGON", "erc20", "USDC"),
            ("ARB", "erc20", "USDT"),
            ("BASE", "erc20", "USDT"),
            ("BSC", "erc20", "USDT"),
            ("ETH", "erc20", "USDT"),
            ("POLYGON", "erc20", "USDT"),
            ("TRON", "trc20", "USDT"),
        ],
    },
    "Stables": {
        "label": "Stables",
        "description": "USDC + USDT + DAI + USDE (more added later)",
        "pairs": [
            # USDC across all chains
            ("ARB", "erc20", "USDC"),
            ("BASE", "erc20", "USDC"),
            ("BSC", "erc20", "USDC"),
            ("ETH", "erc20", "USDC"),
            ("POLYGON", "erc20", "USDC"),
            # USDT across all chains
            ("ARB", "erc20", "USDT"),
            ("BASE", "erc20", "USDT"),
            ("BSC", "erc20", "USDT"),
            ("ETH", "erc20", "USDT"),
            ("POLYGON", "erc20", "USDT"),
            ("TRON", "trc20", "USDT"),
            # DAI (only ingested on ARB + ETH today)
            ("ARB", "erc20", "DAI"),
            ("ETH", "erc20", "DAI"),
            # USDE — not ingested yet; included so the compound auto-picks
            # it up the moment the stream lands.
            ("ETH", "erc20", "USDE"),
        ],
    },
    "Native": {
        "label": "Native",
        "description": "Native L1/L2 assets: ETH (Ethereum / Arbitrum / Base), BNB (BSC), POL (Polygon)",
        "pairs": [
            ("ETH", "native", "ETH"),
            ("ARB", "native", "ETH"),
            ("BASE", "native", "ETH"),
            ("BSC", "native", "BNB"),
            ("POLYGON", "native", "POL"),
        ],
    },
}


def get_compound(name: str | None) -> dict | None:
    if not name:
        return None
    return COMPOUNDS.get(name)


def compound_pairs(name: str) -> list[tuple[str, str, str]]:
    spec = COMPOUNDS.get(name)
    return list(spec["pairs"]) if spec else []


def list_compounds() -> list[dict]:
    """Public catalogue surfaced via /transfers/compounds — names + metadata
    only, no SQL details."""
    out = []
    for name, spec in COMPOUNDS.items():
        out.append({
            "name": name,
            "label": spec["label"],
            "description": spec["description"],
            "chains": sorted({p[0] for p in spec["pairs"]}),
        })
    return out


@bp.get("/transfers/compounds")
async def compounds(_request):
    return response.json({"compounds": list_compounds()})
