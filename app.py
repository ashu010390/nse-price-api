from flask import Flask, jsonify, request
from flask_cors import CORS
import yfinance as yf

app = Flask(__name__)
CORS(app)  # allow requests from any origin (including your artifact)

@app.route('/')
def index():
    return jsonify({'status': 'ok', 'usage': '/price?symbol=TITAN.NS'})

@app.route('/price')
def price():
    symbol = request.args.get('symbol', '').strip()
    if not symbol:
        return jsonify({'error': 'symbol required'}), 400
    try:
        ticker = yf.Ticker(symbol)
        hist   = ticker.history(period='1d')
        if not hist.empty:
            p = float(hist['Close'].iloc[-1])
        else:
            info = ticker.info
            p = info.get('regularMarketPrice') or info.get('currentPrice')
        if p is None:
            return jsonify({'error': 'no price found'}), 404
        return jsonify({'symbol': symbol, 'price': round(p, 2)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
