import pandas as pd
import numpy as np

from scenario_scanner import ScenarioScanner
from decision_engine import engine


# =========================================================
# MOCK REALISTIC MARKET DATA (NO EXTERNAL DEPENDENCY)
# =========================================================
def make_df(n=100, trend=1):
    base = 2000
    data = []

    for i in range(n):
        drift = i * (0.5 * trend)
        noise = np.random.normal(0, 2)
        price = base + drift + noise

        data.append({
            "open": price - np.random.rand(),
            "high": price + np.random.rand() * 2,
            "low": price - np.random.rand() * 2,
            "close": price
        })

    return pd.DataFrame(data)


# =========================================================
# TEST PIPELINE
# =========================================================
if __name__ == "__main__":

    df_4h = make_df(200, trend=1)
    df_1h = make_df(200, trend=1)
    df_15m = make_df(200, trend=1)

    scanner = ScenarioScanner()

    bp = scanner.scan(df_4h, df_1h, df_15m)

    print("\n=== SCANNER OUTPUT ===")
    print("base_score:", bp.base_score)
    print("smc:", bp.smc_confirmed)
    print("vsa:", bp.vsa_confirmed)
    print("prz_top:", bp.prz_support_top)
    print("bias:", bp.decision_bias)

    decision = engine.evaluate(bp)

    print("\n=== DECISION ENGINE OUTPUT ===")
    print("action:", decision.action)
    print("score:", decision.score)
    print("confidence:", decision.confidence)
    print("reason:", decision.reason)

    print("\n=== FLOW STATUS ===")
    print("PIPELINE:", "OK")
