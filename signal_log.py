"""
signal_log.py — Alpha Buffalo v5 (7 ก.ย. 2026)
================================================
Persist every signal actually sent to Telegram into Supabase's
`signal_log` table, so historical stats survive past Railway's ~7-day
deploy-log retention (discovered the hard way: pulling "signals sent
this week" required grepping 30 old deployments' logs one by one, and
anything older than ~7 days was already gone -- there was no database
record anywhere, only license_manager.py's `licenses` table existed).

Table (already created by the bot owner via Supabase SQL editor):

    create table signal_log (
        id            bigserial primary key,
        sent_at       timestamptz not null default now(),
        symbol        text not null,          -- XAUUSD / BTCUSD / US100 / JPN225 / GBPJPY / EURUSD
        direction     text not null,          -- BUY / SELL
        category      text not null,          -- V4_SESSION / V5_SNIPER / SWEEP_REENTRY
        score         int,                    -- null for SWEEP_REENTRY (no score concept there)
        entry         numeric not null,
        sl            numeric,
        tp            numeric,
        pattern       text,
        session       text,
        ea_executes   boolean not null default false,
        trade1_entry  numeric,                -- only set for SWEEP_REENTRY rows
        source        text not null default 'main_loop'  -- main_loop / extra_symbol / reentry
    );
    create index idx_signal_log_symbol  on signal_log (symbol);
    create index idx_signal_log_sent_at on signal_log (sent_at);

Design, same pattern as license_manager.py's `_sb()`: lazy import,
reads SUPABASE_URL/SUPABASE_KEY, and is ENTIRELY best-effort -- this
module must never be able to block a signal from firing or being sent
to Telegram. Every failure mode (missing credentials, network error,
table not created yet, schema mismatch) is swallowed and logged to
stdout only; callers get a bool back and are expected to ignore it
(the actual call sites wrap this in their own try/except too, since
"the DB write half-worked" should never surface as a real exception).
"""
import os


def _sb():
    try:
        from supabase import create_client
        url = os.getenv("SUPABASE_URL", "")
        k   = os.getenv("SUPABASE_KEY", "")
        if url and k:
            return create_client(url, k)
    except Exception:
        pass
    return None


def log_signal(
    symbol:       str,
    direction:    str,
    category:     str,
    entry:        float,
    sl:           float = None,
    tp:           float = None,
    score:        int   = None,
    pattern:      str   = "",
    session:      str   = "",
    ea_executes:  bool  = False,
    trade1_entry: float = None,
    source:       str   = "main_loop",
) -> bool:
    """
    Best-effort insert into Supabase's signal_log table.

    Returns True on a successful insert, False on ANY failure --
    never raises. category is a free-form label ("V4_SESSION",
    "V5_SNIPER", "SWEEP_REENTRY", ...); score/pattern/session/
    trade1_entry are None/empty where they don't apply (e.g. Round-2
    re-entry rows have no score).
    """
    sb = _sb()
    if not sb:
        return False
    try:
        sb.table("signal_log").insert({
            "symbol":       symbol,
            "direction":    direction,
            "category":     category,
            "score":        score,
            "entry":        entry,
            "sl":           sl,
            "tp":           tp,
            "pattern":      pattern or None,
            "session":      session or None,
            "ea_executes":  bool(ea_executes),
            "trade1_entry": trade1_entry,
            "source":       source,
        }).execute()
        return True
    except Exception as e:
        print(f"⚠️ signal_log insert error: {e}")
        return False
