"""Pure body-construction tests — no HTTP.

Every builder just mutates ``self._body`` (and transfer/event builders resolve
a path). We instantiate them, chain methods, and assert the payload + resolved
path. This is the cheapest, broadest coverage layer and pins the client↔server
contract (paths, param names) without a server.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tradernick_data_provider import DataProviderClient
from tradernick_data_provider._query import _to_timestamp

BASE = "http://data-provider.test"


@pytest.fixture
def client():
    # No await needed — we never make a request in this module.
    return DataProviderClient(BASE)


# ---------------------------------------------------------------------------
# _to_timestamp — every accepted input normalizes to 'YYYY-MM-DDTHH:MM:SSZ'
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("value,expected", [
    (1_783_641_600_000, "2026-07-10T00:00:00Z"),                 # int epoch-ms
    ("2026-07-10", "2026-07-10T00:00:00Z"),                       # YYYY-MM-DD
    ("2026-07-10T06:30:00Z", "2026-07-10T06:30:00Z"),             # ISO w/ Z
    ("2026-07-10 06:30:00", "2026-07-10T06:30:00Z"),             # space form
    (datetime(2026, 7, 10, 6, 30, tzinfo=timezone.utc), "2026-07-10T06:30:00Z"),
    (datetime(2026, 7, 10, 6, 30), "2026-07-10T06:30:00Z"),      # naive → UTC
])
def test_to_timestamp_normalization(value, expected):
    assert _to_timestamp(value) == expected


def test_to_timestamp_rejects_bad_type():
    with pytest.raises(ValueError):
        _to_timestamp(1.5)  # float is not a supported DateType


# ---------------------------------------------------------------------------
# Binance perp
# ---------------------------------------------------------------------------
def test_binance_ohlcv_body(client):
    q = client.binance.ohlcv("BTC", "1h").time_range("2026-07-10", "2026-07-11")
    assert q._body == {
        "token": "BTC", "window": "1h",
        "since": "2026-07-10T00:00:00Z", "until": "2026-07-11T00:00:00Z",
    }


def test_binance_raw_trades_flags(client):
    q = client.binance.raw_trades("ETH").add_symbol().with_id()
    assert q._body == {"token": "ETH", "add_symbol": True, "with_id": True}


# ---------------------------------------------------------------------------
# Binance SPOT (new) — .spot sub-namespace, /binance/spot/* paths
# ---------------------------------------------------------------------------
def test_binance_spot_namespace_exists(client):
    assert hasattr(client.binance, "spot")


@pytest.mark.asyncio
async def test_binance_spot_ohlcv_path(client, monkeypatch):
    q = client.binance.spot.ohlcv("BTC", "1h")
    assert q._body == {"token": "BTC", "window": "1h"}
    captured = {}

    async def fake_fetch(session, url, body):
        captured["url"] = url
        return None
    monkeypatch.setattr("tradernick_data_provider.binance.fetch_table", fake_fetch)
    await q._fetch_table()
    assert captured["url"] == BASE + "/binance/spot/ohlcv/read"


@pytest.mark.asyncio
async def test_binance_spot_raw_trades_path_and_flags(client, monkeypatch):
    q = client.binance.spot.raw_trades("BTC").with_id().add_symbol()
    assert q._body == {"token": "BTC", "with_id": True, "add_symbol": True}
    captured = {}

    async def fake_fetch(session, url, body):
        captured["url"] = url
        return None
    monkeypatch.setattr("tradernick_data_provider.binance.fetch_table", fake_fetch)
    await q._fetch_table()
    assert captured["url"] == BASE + "/binance/spot/raw_trades/read"


# ---------------------------------------------------------------------------
# Hyperliquid — chainables + paths
# ---------------------------------------------------------------------------
def test_hyperliquid_chainables(client):
    q = (client.hyperliquid.fills()
         .tokens("BTC", "ETH").wallets("0xabc").window("1h")
         .per_token().market_type("perp").limit(50))
    assert q._body["tokens"] == ["BTC", "ETH"]
    assert q._body["wallets"] == ["0xabc"]
    assert q._body["window"] == "1h"
    assert q._body["per_token"] is True
    assert q._body["market_type"] == "perp"
    assert q._body["limit"] == 50


@pytest.mark.parametrize("factory,path", [
    (lambda hl: hl.fills(), "/hyperliquid/fills/read"),
    (lambda hl: hl.trades(), "/hyperliquid/trades/read"),
    (lambda hl: hl.ohlcv(), "/hyperliquid/ohlcv/read"),
    (lambda hl: hl.funding(), "/hyperliquid/funding/read"),
    (lambda hl: hl.transfers(), "/hyperliquid/transfers/read"),
    (lambda hl: hl.vaults(), "/hyperliquid/vaults/read"),
    (lambda hl: hl.sends(), "/hyperliquid/sends/read"),
    (lambda hl: hl.spot_transfers(), "/hyperliquid/spot_transfers/read"),
    (lambda hl: hl.trade_history(), "/hyperliquid/trade_history/read"),
    (lambda hl: hl.position_history(), "/hyperliquid/position_history/read"),
])
@pytest.mark.asyncio
async def test_hyperliquid_paths(client, monkeypatch, factory, path):
    q = factory(client.hyperliquid)
    captured = {}

    async def fake_fetch(session, url, body):
        captured["url"] = url
        return None
    monkeypatch.setattr("tradernick_data_provider.binance.fetch_table", fake_fetch)
    await q._fetch_table()
    assert captured["url"] == BASE + path


# ---------------------------------------------------------------------------
# Transfers — network, min_amount, aggregate path resolution
# ---------------------------------------------------------------------------
def test_erc20_rejects_bare_string(client):
    with pytest.raises(TypeError):
        client.evm.erc20.transfers("USDC")  # must be a list


def test_erc20_default_path(client):
    q = client.evm.erc20.transfers(["USDC"]).network("ethereum")
    assert q._resolve_path() == "/evm/erc20_transfers/read"
    assert q._body["tokens"] == ["USDC"]
    assert q._body["network"] == "ethereum"


def test_erc20_min_amount_routes_to_read_min(client):
    # The erc20 /read/min bug fix: .min_amount() must resolve to /read/min and
    # inject the value into the body.
    q = client.evm.erc20.transfers(["USDC"]).min_amount(1_000_000)
    assert q._resolve_path() == "/evm/erc20_transfers/read/min"
    assert q._body["min_amount"] == 1_000_000


def test_erc20_aggregate_path(client):
    q = client.evm.erc20.transfers(["USDC"]).aggregate()
    assert q._resolve_path() == "/evm/erc20_transfers/aggregate"
    assert q._body["aggregate"] is True


def test_network_list_sets_networks_and_auto_with_network(client):
    q = client.evm.erc20.transfers(["USDC"]).network(["ethereum", "base"])
    assert q._body["networks"] == ["ethereum", "base"]
    assert "with_network" not in q._body
    q._auto_with_network()
    assert q._body["with_network"] is True


def test_with_network_false_survives_auto(client):
    q = client.evm.erc20.transfers(["USDC"]).network(["ethereum", "base"]).with_network(False)
    q._auto_with_network()
    assert q._body["with_network"] is False


# ---------------------------------------------------------------------------
# local_* filters — accumulation + validation
# ---------------------------------------------------------------------------
def test_local_filters_accumulate_in_order(client):
    q = (client.evm.erc20.transfers(["USDC"])
         .local_involving_labels(["Binance"])
         .local_exclude_sender_categories(["Hot-Wallet"]))
    assert q._body["local_filters"] == [
        {"op": "involving_labels", "values": ["Binance"]},
        {"op": "exclude_sender_categories", "values": ["Hot-Wallet"]},
    ]


def test_local_filter_rejects_non_list(client):
    with pytest.raises(TypeError):
        client.evm.erc20.transfers(["USDC"]).local_involving("0xabc")  # not a list


def test_local_filter_empty_list_is_noop(client):
    q = client.evm.erc20.transfers(["USDC"]).local_involving([])
    assert "local_filters" not in q._body


# ---------------------------------------------------------------------------
# EventQuery family (aave/uniswap/lido/spark/morpho/aerodrome)
# ---------------------------------------------------------------------------
def test_aave_event_and_market_type(client):
    q = client.evm.aave.borrows().network("ethereum").eth_market_type("core")
    assert q._body["event"] == "borrow"
    assert q._body["eth_market_type"] == "core"
    assert q._resolve_path() == "/evm/aave/read"


def test_aave_aggregate_rewrites_path(client):
    q = client.evm.aave.deposits().aggregate(group_by="token", period="1d")
    assert q._resolve_path() == "/evm/aave/aggregate"
    assert q._body["group_by"] == "token" and q._body["period"] == "1d"


def test_uniswap_body(client):
    q = client.evm.uniswap.swaps("WETH", "USDC", 3000)
    assert q._body == {"event": "swap", "symbol0": "WETH", "symbol1": "USDC", "fee": 3000}


def test_morpho_market_id(client):
    q = client.evm.morpho.borrows().market_id("0xdead")
    assert q._body["event"] == "borrow"
    assert q._body["market_id"] == "0xdead"


def test_aerodrome_namespaces(client):
    conc = client.evm.aerodrome.concentrated.swaps("WETH", "USDC", tick_spacing=100)
    basic = client.evm.aerodrome.basic.swaps("WETH", "USDC", stable=True)
    assert conc._body["tick_spacing"] == 100
    assert basic._body["stable"] is True


# ---------------------------------------------------------------------------
# Wallet groups — direct (list-valued) + local filters
# ---------------------------------------------------------------------------
def test_involving_groups_direct(client):
    q = client.evm.erc20.transfers(["USDC"]).involving_groups(["Whales", "CEX"])
    assert q._body["involving_groups"] == ["Whales", "CEX"]


def test_sender_receiver_groups_direct(client):
    q = (client.evm.native_transfers()
         .sender_groups(["A"]).receiver_groups(["B"])
         .exclude_sender_groups(["C"]).exclude_receiver_groups(["D"])
         .exclude_involving_groups(["E"]))
    assert q._body["sender_groups"] == ["A"]
    assert q._body["receiver_groups"] == ["B"]
    assert q._body["exclude_sender_groups"] == ["C"]
    assert q._body["exclude_receiver_groups"] == ["D"]
    assert q._body["exclude_involving_groups"] == ["E"]


def test_group_filters_on_every_transfer_type(client):
    builders = [
        client.evm.erc20.transfers(["USDC"]),
        client.evm.native_transfers(),
        client.tron.trc20.transfers(["USDT"]),
        client.tron.native_transfers(),
        client.btc.native_transfers(),
    ]
    for q in builders:
        assert q.sender_groups(["G"])._body["sender_groups"] == ["G"]
        assert q.involving_groups(["H"])._body["involving_groups"] == ["H"]


def test_local_group_ops(client):
    q = (client.scan_parquet("snap")
         .local_receiver_groups(["Whales"])
         .local_exclude_involving_groups(["CEX"]))
    assert q._body["local_filters"] == [
        {"op": "receiver_groups", "values": ["Whales"]},
        {"op": "exclude_involving_groups", "values": ["CEX"]},
    ]


def test_local_groups_reject_non_list(client):
    with pytest.raises(TypeError):
        client.scan_parquet("snap").local_sender_groups("Whales")  # must be a list
