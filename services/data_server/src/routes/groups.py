"""Token groups and chain groups.

These are two orthogonal axes of "compounding": the user can pin a chart
to a single chain or a *group* of chains (e.g. EVM), and independently
to a single token or a *group* of tokens (e.g. Stables). At query time
the backend cross-products the resolved chains × tokens, intersects
with the streams catalogue (so missing pairs contribute 0), and emits a
single `(chain, kind, token) IN (...)` WHERE clause to ClickHouse.

Both registries are defined here so adding a new group is a code edit,
not a config change — which is what we want right now.
"""
from __future__ import annotations

from sanic import Blueprint, response

from routes.transfers_streams import get_streams_cached

bp = Blueprint("groups")

# ---- token groups -----------------------------------------------------------
# Each entry: name → {label, description, tokens: [...]}
# Tokens listed here are matched against the streams table at query time; a
# token that isn't ingested anywhere is silently skipped.
TOKEN_GROUPS: dict[str, dict] = {
    "USDC+USDT": {
        "label": "USDC + USDT",
        "description": "USDC + USDT (intersected with whatever chains are queried)",
        "tokens": ["USDC", "USDT"],
    },
    "Stables": {
        "label": "Stables",
        "description": "USDC + USDT + DAI + USDE (more added later)",
        "tokens": ["USDC", "USDT", "DAI", "USDE"],
    },
}


def is_token_group(name: str | None) -> bool:
    return bool(name) and name in TOKEN_GROUPS


def resolve_token_group(name: str) -> list[str]:
    spec = TOKEN_GROUPS.get(name)
    return list(spec["tokens"]) if spec else []


# ---- chain groups -----------------------------------------------------------
# `All` is computed dynamically from the streams catalogue so newly-ingested
# chains pick it up automatically. Other groups are explicit lists.
CHAIN_GROUPS_STATIC: dict[str, dict] = {
    "EVM": {
        "label": "EVM",
        "description": "Ethereum + L2s + BSC + Polygon (case-insensitive addresses)",
        "chains": ["ETH", "ARB", "BASE", "BSC", "POLYGON"],
    },
}
# Names handled specially because their member list is computed at query time.
_DYNAMIC_GROUPS = ("All",)


def is_chain_group(name: str | None) -> bool:
    return bool(name) and (name in CHAIN_GROUPS_STATIC or name in _DYNAMIC_GROUPS)


async def resolve_chain_group(name: str) -> list[str]:
    if name == "All":
        streams = await get_streams_cached()
        return sorted({s["chain"] for s in streams})
    spec = CHAIN_GROUPS_STATIC.get(name)
    return list(spec["chains"]) if spec else []


async def list_chain_groups() -> list[dict]:
    """All chain groups (static + dynamic), with their resolved chain lists at
    the moment of the call."""
    out: list[dict] = []
    streams = await get_streams_cached()
    all_chains = sorted({s["chain"] for s in streams})
    out.append({
        "name": "All",
        "label": "All",
        "description": "Every chain currently ingested",
        "chains": all_chains,
    })
    for name, spec in CHAIN_GROUPS_STATIC.items():
        out.append({
            "name": name,
            "label": spec["label"],
            "description": spec["description"],
            # Only show chains that actually have data, so the UI doesn't
            # advertise empty groups.
            "chains": [c for c in spec["chains"] if c in all_chains],
        })
    return out


def list_token_groups() -> list[dict]:
    return [
        {
            "name": name,
            "label": spec["label"],
            "description": spec["description"],
            "tokens": list(spec["tokens"]),
        }
        for name, spec in TOKEN_GROUPS.items()
    ]


async def resolve_pairs(
    *,
    chain: str | None,
    token: str | None,
    chain_group: str | None,
    token_group: str | None,
) -> tuple[list[tuple[str, str, str]], str | None]:
    """Cross-product chains × tokens against the streams catalogue.

    Returns `(pairs, addr_chain)` where `addr_chain` is:
      - the single chain string if every resolved pair shares one chain
        (so the wallet-filter predicates can use static address normalisation),
      - None when the query spans multiple chains (per-row conditional).
    """
    if chain_group:
        chains = await resolve_chain_group(chain_group)
    elif chain:
        chains = [chain]
    else:
        return [], None
    if token_group:
        tokens = resolve_token_group(token_group)
    elif token:
        tokens = [token]
    else:
        return [], None

    chain_set = set(chains)
    token_set = set(tokens)
    streams = await get_streams_cached()
    pairs = [
        (s["chain"], s["kind"], s["token"])
        for s in streams
        if s["chain"] in chain_set and s["token"] in token_set
    ]
    # Stable order (helps tests / logs but doesn't matter for CH).
    pairs.sort()
    unique_chains = {p[0] for p in pairs}
    addr_chain = next(iter(unique_chains)) if len(unique_chains) == 1 else None
    return pairs, addr_chain


# ---- endpoints --------------------------------------------------------------
@bp.get("/transfers/token-groups")
async def token_groups(_request):
    return response.json({"groups": list_token_groups()})


@bp.get("/transfers/chain-groups")
async def chain_groups(_request):
    return response.json({"groups": await list_chain_groups()})
