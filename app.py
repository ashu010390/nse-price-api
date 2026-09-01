from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import csv, io, time
from concurrent.futures import ThreadPoolExecutor, as_completed

app = Flask(__name__)
CORS(app)

# In-memory cache: symbol -> (price, prev_close, fetched_at)
_cache = {}
CACHE_TTL = 300  # 5 minutes

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}


# ── Single-symbol fetchers (used by /price and as fallback) ──────────────────

def fetch_stooq(symbol):
    """Stooq daily history. Returns (close, prev_close)."""
    url = f'https://stooq.com/q/d/l/?s={symbol.lower()}&i=d'
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    rows = []
    reader = csv.DictReader(io.StringIO(r.text))
    for row in reader:
        close = row.get('Close', '').strip()
        if close and close != 'N/D':
            rows.append(round(float(close), 2))
    if not rows:
        return None, None
    return rows[-1], (rows[-2] if len(rows) >= 2 else None)


def fetch_yfinance_single(symbol):
    """yfinance single symbol. Returns (close, prev_close)."""
    import yfinance as yf
    ticker = yf.Ticker(symbol)
    hist = ticker.history(period='5d')
    if len(hist) >= 2:
        return round(float(hist['Close'].iloc[-1]), 2), round(float(hist['Close'].iloc[-2]), 2)
    if len(hist) == 1:
        return round(float(hist['Close'].iloc[-1]), 2), None
    info = ticker.info
    p  = info.get('regularMarketPrice') or info.get('currentPrice')
    pc = info.get('previousClose') or info.get('regularMarketPreviousClose')
    return (round(p, 2), round(pc, 2) if pc else None) if p else (None, None)


def _fetch_one(symbol):
    """Try stooq → yfinance. Returns (symbol, price, prev_close, cached)."""
    symbol = symbol.upper()
    if symbol in _cache:
        p, pc, ts = _cache[symbol]
        if time.time() - ts < CACHE_TTL:
            return symbol, p, pc, True

    price, prev_close = None, None
    for fn in [fetch_stooq, fetch_yfinance_single]:
        try:
            price, prev_close = fn(symbol)
            if price is not None:
                break
        except Exception:
            continue

    if price is not None:
        _cache[symbol] = (price, prev_close, time.time())
    return symbol, price, prev_close, False


# ── Batch fetcher (used by /prices) ─────────────────────────────────────────

def fetch_batch_yfinance(symbols):
    """Single yfinance call for all symbols. Returns dict: symbol -> (close, prev_close) | None."""
    import yfinance as yf
    results = {s: None for s in symbols}
    try:
        raw = yf.download(
            symbols,
            period='5d',
            group_by='ticker',
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        single = len(symbols) == 1
        for sym in symbols:
            try:
                closes = (raw['Close'] if single else raw[sym]['Close']).dropna()
                if len(closes) >= 2:
                    results[sym] = (round(float(closes.iloc[-1]), 2),
                                    round(float(closes.iloc[-2]), 2))
                elif len(closes) == 1:
                    results[sym] = (round(float(closes.iloc[0]), 2), None)
            except Exception:
                pass
    except Exception:
        pass
    return results


# ── Routes ───────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return jsonify({'status': 'ok', 'endpoints': [
        '/price?symbol=TITAN.NS',
        '/prices?symbols=TITAN.NS,INFY.NS,RELIANCE.NS'
    ]})


@app.route('/price')
def price():
    symbol = request.args.get('symbol', '').strip()
    if not symbol:
        return jsonify({'error': 'symbol required'}), 400
    sym, p, pc, cached = _fetch_one(symbol)
    if p is None:
        return jsonify({'error': 'no price found'}), 404
    return jsonify({'symbol': sym, 'price': p, 'prev_close': pc, 'cached': cached})


@app.route('/prices')
def prices():
    raw = request.args.get('symbols', '').strip()
    symbols = [s.strip().upper() for s in raw.split(',') if s.strip()]
    if not symbols:
        return jsonify({'error': 'symbols required'}), 400

    result = {}

    # 1. Check cache — pull out any still-fresh entries
    now = time.time()
    missing = []
    for sym in symbols:
        if sym in _cache:
            p, pc, ts = _cache[sym]
            if now - ts < CACHE_TTL:
                result[sym] = {'price': p, 'prev_close': pc}
                continue
        missing.append(sym)

    if not missing:
        return jsonify({'prices': result})

    # 2. Batch fetch via yfinance for all missing symbols at once
    batch = fetch_batch_yfinance(missing)

    still_missing = []
    for sym in missing:
        val = batch.get(sym)
        if val is not None:
            p, pc = val
            _cache[sym] = (p, pc, time.time())
            result[sym] = {'price': p, 'prev_close': pc}
        else:
            still_missing.append(sym)

    # 3. Individual Stooq fallback for anything yfinance missed
    if still_missing:
        with ThreadPoolExecutor(max_workers=min(len(still_missing), 10)) as ex:
            futures = {ex.submit(_fetch_one, sym): sym for sym in still_missing}
            for f in as_completed(futures):
                sym, p, pc, _ = f.result()
                result[sym] = {'price': p, 'prev_close': pc}

    return jsonify({'prices': result})
