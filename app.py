from flask import Flask, jsonify, request, send_from_directory, Response
import yfinance as yf
import sqlite3, threading, time, os, logging
from datetime import datetime, timezone

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(APP_DIR, "ewz_history.sqlite3"))
APP_USERNAME = os.environ.get("APP_USERNAME", "")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
PORT = int(os.environ.get("PORT", "8765"))
AUTO_MINUTES = max(5, int(os.environ.get("AUTO_MINUTES", "15")))
app = Flask(__name__, static_folder=APP_DIR)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
db_lock = threading.Lock()
cache_lock = threading.Lock()
cache = {"rows": [], "expiration": None, "price": None, "fetched_at": None, "error": None}

def db():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            captured_at TEXT NOT NULL,
            expiration TEXT NOT NULL,
            underlying_price REAL,
            source TEXT NOT NULL DEFAULT 'Yahoo Finance'
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS option_rows (
            snapshot_id INTEGER NOT NULL,
            expiration TEXT NOT NULL,
            option_type TEXT NOT NULL,
            strike REAL NOT NULL,
            open_interest INTEGER,
            volume INTEGER,
            last_price REAL,
            bid REAL,
            ask REAL,
            implied_volatility REAL,
            contract_symbol TEXT,
            FOREIGN KEY(snapshot_id) REFERENCES snapshots(id)
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_options_snapshot ON option_rows(snapshot_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_options_lookup ON option_rows(expiration, option_type, strike)")

def to_records(df, exp, side):
    out = []
    if df is None:
        return out
    for _, r in df.iterrows():
        def val(k):
            v = r.get(k)
            try:
                if v is None or str(v) == "nan": return None
                return float(v)
            except Exception:
                return None
        oi = val("openInterest")
        out.append({
            "expiration": exp, "type": side, "strike": val("strike"),
            "oi": int(oi) if oi is not None else 0,
            "volume": int(val("volume") or 0),
            "lastPrice": val("lastPrice"), "bid": val("bid"), "ask": val("ask"),
            "iv": val("impliedVolatility"), "symbol": str(r.get("contractSymbol", ""))
        })
    return out

def fetch_chain(expiration=None):
    ticker = yf.Ticker("EWZ")
    expirations = list(ticker.options or [])
    if not expirations:
        raise RuntimeError("Yahoo Finance não retornou vencimentos para EWZ.")
    exp = expiration if expiration in expirations else expirations[0]
    chain = ticker.option_chain(exp)
    rows = to_records(chain.calls, exp, "call") + to_records(chain.puts, exp, "put")
    try:
        info = ticker.fast_info
        price = float(info.get("last_price")) if info.get("last_price") is not None else None
    except Exception:
        price = None
    if not rows:
        raise RuntimeError("A cadeia retornou vazia. Tente novamente mais tarde.")
    with cache_lock:
        cache.update({"rows": rows, "expiration": exp, "price": price,
                      "fetched_at": datetime.now(timezone.utc).isoformat(),
                      "error": None, "expirations": expirations})
    return {"rows": rows, "expiration": exp, "price": price, "expirations": expirations,
            "fetched_at": cache["fetched_at"]}

def save_snapshot(rows, exp, price):
    captured = datetime.now(timezone.utc).isoformat()
    with db_lock, db() as c:
        cur = c.execute("INSERT INTO snapshots(captured_at,expiration,underlying_price,source) VALUES(?,?,?,?)",
                        (captured, exp, price, "Yahoo Finance / yfinance"))
        sid = cur.lastrowid
        c.executemany("""INSERT INTO option_rows(snapshot_id,expiration,option_type,strike,open_interest,volume,
                         last_price,bid,ask,implied_volatility,contract_symbol)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                      [(sid, r["expiration"], r["type"], r["strike"], r["oi"], r["volume"],
                        r["lastPrice"], r["bid"], r["ask"], r["iv"], r["symbol"]) for r in rows])
    return {"id": sid, "captured_at": captured, "count": len(rows)}

@app.before_request
def require_login():
    # Protect every route with HTTP Basic Auth when credentials are configured.
    # Always configure both APP_USERNAME and APP_PASSWORD for an internet-facing deployment.
    if not APP_USERNAME or not APP_PASSWORD:
        return None
    if request.path == "/health":
        return jsonify({"status": "ok"})
    auth = request.authorization
    if auth and auth.username == APP_USERNAME and auth.password == APP_PASSWORD:
        return None
    return Response("Autenticacao necessaria", 401,
                    {"WWW-Authenticate": 'Basic realm="Farol Traders EWZ Monitor"'})

@app.get("/health")
def health():
    return jsonify({"status": "ok"})

@app.get("/")
def index():
    return send_from_directory(APP_DIR, "index.html")

@app.get("/api/status")
def status():
    with cache_lock:
        data = {k: cache.get(k) for k in ("expiration","price","fetched_at","error","expirations")}
    with db() as c:
        last = c.execute("SELECT id,captured_at,expiration FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
    data["last_snapshot"] = dict(last) if last else None
    data["auto_minutes"] = AUTO_MINUTES
    return jsonify(data)

@app.get("/api/expirations")
def get_expirations():
    try:
        ticker = yf.Ticker("EWZ")
        exps = list(ticker.options or [])
        if not exps: raise RuntimeError("Nenhum vencimento retornado.")
        return jsonify({"expirations": exps})
    except Exception as e:
        return jsonify({"error": str(e)}), 502

@app.post("/api/refresh")
def refresh():
    body = request.get_json(silent=True) or {}
    exp = body.get("expiration")
    try:
        data = fetch_chain(exp)
        if body.get("save", True):
            data["snapshot"] = save_snapshot(data["rows"], data["expiration"], data["price"])
        return jsonify(data)
    except Exception as e:
        logging.exception("Falha ao atualizar Yahoo Finance")
        with cache_lock:
            cache["error"] = str(e)
        return jsonify({"error": str(e)}), 502

@app.get("/api/history")
def history():
    exp = request.args.get("expiration")
    limit = min(100, max(1, int(request.args.get("limit", "20"))))
    with db() as c:
        if exp:
            snaps = c.execute("SELECT * FROM snapshots WHERE expiration=? ORDER BY id DESC LIMIT ?", (exp, limit)).fetchall()
        else:
            snaps = c.execute("SELECT * FROM snapshots ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        result = []
        for s in snaps:
            rs = c.execute("""SELECT expiration,option_type AS type,strike,open_interest AS oi,volume,last_price AS lastPrice,
                              bid,ask,implied_volatility AS iv,contract_symbol AS symbol
                              FROM option_rows WHERE snapshot_id=?""", (s["id"],)).fetchall()
            result.append({"id": s["id"], "captured_at": s["captured_at"], "expiration": s["expiration"],
                           "price": s["underlying_price"], "rows": [dict(r) for r in rs]})
    return jsonify({"snapshots": result})

@app.get("/api/latest")
def latest():
    exp = request.args.get("expiration")
    with db() as c:
        if exp:
            cur = c.execute("SELECT * FROM snapshots WHERE expiration=? ORDER BY id DESC LIMIT 2", (exp,)).fetchall()
        else:
            cur = c.execute("SELECT * FROM snapshots ORDER BY id DESC LIMIT 2").fetchall()
        if not cur:
            return jsonify({"rows": [], "changes": [], "message": "Ainda não há snapshots salvos."})
        def load(s):
            rr = c.execute("""SELECT expiration,option_type AS type,strike,open_interest AS oi,volume,last_price AS lastPrice,
                               bid,ask,implied_volatility AS iv,contract_symbol AS symbol
                               FROM option_rows WHERE snapshot_id=?""", (s["id"],)).fetchall()
            return dict(s), [dict(x) for x in rr]
        newest, rows = load(cur[0])
        prevrows = load(cur[1])[1] if len(cur)>1 else []
    pmap = {(r["expiration"],r["type"],float(r["strike"])): int(r["oi"] or 0) for r in prevrows}
    changes = []
    for r in rows:
        k=(r["expiration"],r["type"],float(r["strike"]))
        old=pmap.get(k)
        now=int(r["oi"] or 0)
        delta=None if old is None else now-old
        pct=None if old in (None,0) else delta/old*100
        changes.append({**r,"previous_oi":old,"delta":delta,"pct":pct,
                        "state":"Novo" if old is None else ("Aumento" if delta>0 else "Redução" if delta<0 else "Sem alteração")})
    changes.sort(key=lambda x: abs(x["delta"] or 0), reverse=True)
    return jsonify({"snapshot":{"id":newest["id"],"captured_at":newest["captured_at"],
                               "expiration":newest["expiration"],"price":newest["underlying_price"]},
                    "rows":rows,"changes":changes,"previous_snapshot_id":cur[1]["id"] if len(cur)>1 else None})

@app.get("/api/export")
def export():
    import csv, io
    output=io.StringIO()
    writer=csv.writer(output)
    writer.writerow(["snapshot_id","captured_at","expiration","type","strike","open_interest","volume","last_price","bid","ask","iv","contract_symbol"])
    with db() as c:
        records=c.execute("""SELECT s.id,s.captured_at,o.expiration,o.option_type,o.strike,o.open_interest,o.volume,
                            o.last_price,o.bid,o.ask,o.implied_volatility,o.contract_symbol
                            FROM snapshots s JOIN option_rows o ON o.snapshot_id=s.id ORDER BY s.id,o.option_type,o.strike""").fetchall()
        writer.writerows([tuple(r) for r in records])
    from flask import Response
    return Response("\\ufeff"+output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition":"attachment; filename=farol_ewz_historico.csv"})

if __name__ == "__main__":
    init_db()
    print("\\nFarol Traders — EWZ Options Monitor")
    print(f"Abra no navegador: http://127.0.0.1:{PORT}")
    print("Para encerrar, feche esta janela do terminal. Atualizações automáticas ocorrem enquanto o dashboard estiver aberto.")
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)
