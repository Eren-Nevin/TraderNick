from sanic import Sanic, response

from routes.ohlcv import bp as ohlcv_bp
from routes.trade_volume import bp as trade_volume_bp

app = Sanic("tradernick_data_server")
app.config.RESPONSE_TIMEOUT = 60

app.blueprint(ohlcv_bp)
app.blueprint(trade_volume_bp)


@app.get("/health")
async def health(_request):
    return response.json({"ok": True})
