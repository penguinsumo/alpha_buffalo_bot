"""
harmonic_pattern_log.py — Alpha Buffalo v5 (11 ก.ย. 2026)
================================================
Owner's request: give the Harmonic Pattern Forecast feature
(signal_engine.build_harmonic_forecast()) a "memory", so its rule-based
confidence heuristic can eventually be calibrated against REAL outcomes
instead of staying a fixed-weight guess forever. This is Phase 2 of that
strategy:
    Phase 1 (done, signal_engine.py)   -- tf_conflict flag: warn when
                                           H1's and H4's best pattern
                                           disagree on direction.
    Phase 2 (this file)                -- log every pattern that goes
                                           "active" and later resolve
                                           whether the reversal it bet on
                                           actually happened.
    Phase 3 (not built yet)            -- once enough resolved rows exist,
                                           blend their real win-rate back
                                           into score_harmonic_confidence()
                                           instead of the fixed heuristic
                                           weights.
    Phase 4 (not built yet)            -- the "AI learning" step the owner
                                           separately flagged as a future
                                           roadmap item, trained on this
                                           table's history once it's big
                                           enough to be meaningful.

What gets logged: every time a NEW harmonic pattern becomes the "active"
one in a TrendResult.harmonic_forecast (price entered a trend-aligned PRZ
that scan_harmonic() found) -- one row per distinct pattern instance.
Dedup uses the same identity idea trend_monitor.py's own alert-state
comparator uses for its harmonic_active_key (name+tf+direction+prz_mid),
so a pattern staying "active" across many poll cycles logs exactly once,
not once per cycle.

What does NOT get logged: "next_target" entries (price hasn't reached
them yet -- there's no prediction to grade), or any ranked-but-not-active
/ non-trend-aligned pattern (it was never a live prediction to begin
with).

Table (owner must create via Supabase SQL editor, same as signal_log's
table -- see CREATE_TABLE_SQL below):

    create table harmonic_pattern_log (
        id                   bigserial primary key,
        detected_at          timestamptz not null default now(),
        symbol               text not null,
        pattern_name         text not null,
        tf                   text not null,           -- H1 / H4
        direction            text not null,           -- BUY / SELL
        priority             int,
        prz_low              numeric,
        prz_high             numeric,
        prz_mid              numeric,
        confidence           numeric,                 -- score_harmonic_confidence() at detection time
        cascade_direction    text,
        auto_fibo_confluence boolean not null default false,
        status               text not null default 'PENDING',  -- PENDING / WIN / LOSS / EXPIRED
        resolved_at          timestamptz,
        resolved_price       numeric,
        bars_to_resolve      int
    );
    create index idx_harmonic_pattern_log_status on harmonic_pattern_log (status);
    create index idx_harmonic_pattern_log_symbol on harmonic_pattern_log (symbol);

Same design contract as signal_log.py: lazy Supabase import, entirely
best-effort (missing credentials/table/network error never raises, never
blocks the caller -- a broken DB write must never be able to interrupt
trend_loop()), and resolution checks are throttled (default every 10
min, same convention as outcome_tracker.py's OUTCOME_CHECK_INTERVAL_SEC)
so this doesn't add an extra DB round-trip to every single poll cycle.

WIN/LOSS definition (evaluate_harmonic_outcome()): a harmonic PRZ is a
reversal bet -- "price should turn around here and move back the other
way." So, scanning bars after detection in order:
  - WIN    : price moves away from the PRZ, in the predicted direction,
             by at least one PRZ-width beyond the near edge
             (BUY: high >= prz_high + width; SELL: low <= prz_low - width)
             -- a real, measurable reversal, not a single wick.
  - LOSS   : price instead breaks THROUGH the zone in the failure
             direction by at least half a PRZ-width past the far edge
             (BUY: low <= prz_low - 0.5*width; SELL: high >= prz_high +
             0.5*width) -- the reversal never happened, pattern invalidated.
  - Neither happens within `max_bars` (default 200 bars on whatever TF
    the pattern was detected on): EXPIRED -- inconclusive, meant to be
    excluded from any future win-rate calculation, never counted as a loss.
  - A single bar that would satisfy both WIN and LOSS conditions at once
    (a huge spike bar) resolves conservatively as LOSS, same tie-break
    convention as outcome_tracker.evaluate_outcome().
"""
import os
import time
from datetime import datetime, timezone

CREATE_TABLE_SQL = """
create table harmonic_pattern_log (
    id                   bigserial primary key,
    detected_at          timestamptz not null default now(),
    symbol               text not null,
    pattern_name         text not null,
    tf                   text not null,
    direction            text not null,
    priority             int,
    prz_low              numeric,
    prz_high             numeric,
    prz_mid              numeric,
    confidence           numeric,
    cascade_direction    text,
    auto_fibo_confluence boolean not null default false,
    status               text not null default 'PENDING',
    resolved_at          timestamptz,
    resolved_price       numeric,
    bars_to_resolve      int
);
create index idx_harmonic_pattern_log_status on harmonic_pattern_log (status);
create index idx_harmonic_pattern_log_symbol on harmonic_pattern_log (symbol);
"""

OUTCOME_CHECK_INTERVAL_SEC = int(os.getenv("ALPHA_HARMONIC_OUTCOME_CHECK_INTERVAL_SEC", "600"))
MAX_BARS_TO_RESOLVE = int(os.getenv("ALPHA_HARMONIC_MAX_BARS_TO_RESOLVE", "200"))

_last_check_ts = 0.0
_last_logged_active_key = None  # per-process; dedupes the SAME active pattern across repeated poll cycles


def outcome_check_allowed() -> bool:
    """Throttle gate, same shape as outcome_tracker.outcome_check_allowed()
    -- pattern outcomes don't need checking every single trend_loop() cycle."""
    global _last_check_ts
    now = time.time()
    if now - _last_check_ts >= OUTCOME_CHECK_INTERVAL_SEC:
        _last_check_ts = now
        return True
    return False


def _sb():
    try:
        from supabase import create_client
        url = os.getenv("SUPABASE_URL", "")
        k = os.getenv("SUPABASE_KEY", "")
        if url and k:
            return create_client(url, k)
    except Exception:
        pass
    return None


def _active_key(active: dict) -> str:
    mid = active.get("prz_mid")
    return f"{active.get('name')}|{active.get('tf')}|{active.get('direction')}|{round(mid, 1) if mid is not None else None}"


def reset_dedup_state():
    """Test/process-restart helper -- clears the in-memory 'last logged'
    key so the next active pattern (even if identical) logs again."""
    global _last_logged_active_key
    _last_logged_active_key = None


def log_new_active_pattern(symbol: str, harmonic_forecast: dict) -> bool:
    """
    Best-effort: if harmonic_forecast['active'] is a NEW pattern instance
    (a different identity than the last one this process logged), insert
    a PENDING row into harmonic_pattern_log. Returns True on a fresh
    insert, False otherwise (no active pattern right now, same pattern as
    last call, missing Supabase credentials, or any DB error) -- never
    raises, same contract as signal_log.log_signal().

    cascade_direction is taken straight from active['direction'] rather
    than a caller-supplied value: signal_engine.build_harmonic_forecast()
    only ever sets 'active' when a candidate's own direction already
    equals the cascade direction at that moment (see its 'active_raw'
    selection loop), so the two are guaranteed identical here -- no risk
    of the caller passing a stale/different cascade reading by mistake.
    """
    global _last_logged_active_key
    active = (harmonic_forecast or {}).get("active")
    if not active:
        return False

    key = _active_key(active)
    if key == _last_logged_active_key:
        return False
    _last_logged_active_key = key

    sb = _sb()
    if not sb:
        return False
    try:
        sb.table("harmonic_pattern_log").insert({
            "symbol": symbol,
            "pattern_name": active.get("name"),
            "tf": active.get("tf"),
            "direction": active.get("direction"),
            "priority": active.get("priority"),
            "prz_low": active.get("prz_low"),
            "prz_high": active.get("prz_high"),
            "prz_mid": active.get("prz_mid"),
            "confidence": active.get("confidence"),
            "cascade_direction": active.get("direction"),
            "auto_fibo_confluence": bool(active.get("auto_fibo_confluence", False)),
            "status": "PENDING",
        }).execute()
        return True
    except Exception as e:
        print(f"⚠️ harmonic_pattern_log insert error: {e}")
        return False


def evaluate_harmonic_outcome(direction: str, prz_low: float, prz_high: float, bars_after):
    """
    Walk `bars_after` (OHLC rows strictly after detection, ascending by
    time -- a DataFrame or list of dict-likes with high/low) and decide
    WIN / LOSS / None (still pending -- not enough bars yet or neither
    condition touched). Never raises: missing prz_low/prz_high returns
    (None, None) immediately, same "never guess" contract as
    outcome_tracker.evaluate_outcome().
    """
    if prz_low is None or prz_high is None or bars_after is None:
        return None, None
    width = prz_high - prz_low
    if width <= 0:
        return None, None

    try:
        n = len(bars_after)
    except TypeError:
        return None, None
    if n == 0:
        return None, None

    for i in range(n):
        row = bars_after.iloc[i] if hasattr(bars_after, "iloc") else bars_after[i]
        hi = row["high"] if "high" in row else row.get("high")
        lo = row["low"] if "low" in row else row.get("low")
        if hi is None or lo is None:
            continue

        if direction == "BUY":
            win_hit = hi >= prz_high + width
            loss_hit = lo <= prz_low - 0.5 * width
        else:  # SELL
            win_hit = lo <= prz_low - width
            loss_hit = hi >= prz_high + 0.5 * width

        if win_hit and loss_hit:
            return "LOSS", i + 1  # conservative tie-break, same as outcome_tracker
        if loss_hit:
            return "LOSS", i + 1
        if win_hit:
            return "WIN", i + 1

    return None, None  # still pending -- caller decides EXPIRED once bars exceed MAX_BARS_TO_RESOLVE


def check_pending_harmonic_outcomes(get_ohlcv_fn, bars: int = 1000) -> int:
    """
    Fetch every PENDING row per (symbol, tf) pair, pull that many bars of
    that TF, and resolve WIN/LOSS/EXPIRED via evaluate_harmonic_outcome().
    Returns the number of rows resolved this call. Entirely best-effort --
    any failure (missing credentials, network error, table not created
    yet) is caught and logged, never raised; kept independently testable
    like outcome_tracker.check_pending_outcomes().
    """
    sb = _sb()
    if not sb:
        return 0

    try:
        res = (sb.table("harmonic_pattern_log").select("*")
               .eq("status", "PENDING").execute())
        pending = res.data or []
    except Exception as e:
        print(f"⚠️ harmonic_pattern_log fetch error: {e}")
        return 0

    resolved = 0
    tf_interval = {"H1": "1h", "H4": "4h"}
    for row in pending:
        try:
            interval = tf_interval.get(row.get("tf"), "1h")
            df = get_ohlcv_fn(interval, bars, symbol=row.get("symbol"))
            if df is None or len(df) == 0:
                continue

            detected_at = row.get("detected_at")
            if detected_at and "time" in df.columns:
                bars_after = df[df["time"] > detected_at]
            else:
                bars_after = df  # best-effort fallback when time isn't comparable

            status, bars_to_resolve = evaluate_harmonic_outcome(
                row.get("direction"), row.get("prz_low"), row.get("prz_high"), bars_after,
            )
            if status is None:
                if bars_after is not None and len(bars_after) >= MAX_BARS_TO_RESOLVE:
                    status, bars_to_resolve = "EXPIRED", len(bars_after)
                else:
                    continue  # still pending, not enough bars yet

            resolved_price = None
            try:
                if bars_to_resolve and bars_after is not None and bars_to_resolve <= len(bars_after):
                    last_row = bars_after.iloc[bars_to_resolve - 1]
                    resolved_price = float(last_row["close"]) if "close" in last_row else None
            except Exception:
                resolved_price = None

            sb.table("harmonic_pattern_log").update({
                "status": status,
                "resolved_at": datetime.now(timezone.utc).isoformat(),
                "resolved_price": resolved_price,
                "bars_to_resolve": bars_to_resolve,
            }).eq("id", row["id"]).execute()
            resolved += 1
        except Exception as e:
            print(f"⚠️ harmonic_pattern_log evaluate error (id={row.get('id')}): {e}")

    return resolved
