# AUTO PATCH (safety layer injection)

def safe_float(x, default=0.0):
    return default if x is None else float(x)

def safe_iter(x):
    return x if x is not None else []

def safe_score(obj):
    if obj is None:
        return 0.0
    if hasattr(obj, "total"):
        return obj.total
    if isinstance(obj, dict):
        return obj.get("total", 0.0)
    if isinstance(obj, (int, float)):
        return float(obj)
    return 0.0
