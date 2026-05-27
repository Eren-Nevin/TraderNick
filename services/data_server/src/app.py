from sanic import Sanic, response

from routes.aave import bp as aave_bp
from routes.derivatives import bp as derivatives_bp
from routes.groups import bp as groups_bp
from routes.ohlcv import bp as ohlcv_bp
from routes.trade_volume import bp as trade_volume_bp
from routes.transfers import bp as transfers_bp
from routes.transfers_streams import bp as transfers_streams_bp
from routes.uniswap import bp as uniswap_bp
from routes.lido import bp as lido_bp
from routes.aave_v2 import bp as aave_v2_bp
from routes.uniswap_v2 import bp as uniswap_v2_bp

app = Sanic("tradernick_data_server")
app.config.RESPONSE_TIMEOUT = 60

app.blueprint(ohlcv_bp)
app.blueprint(trade_volume_bp)
app.blueprint(derivatives_bp)
app.blueprint(transfers_bp)
app.blueprint(transfers_streams_bp)
app.blueprint(groups_bp)
app.blueprint(aave_bp)
app.blueprint(uniswap_bp)
app.blueprint(lido_bp)
app.blueprint(aave_v2_bp)
app.blueprint(uniswap_v2_bp)


@app.get("/health")
async def health(_request):
    return response.json({"ok": True})
