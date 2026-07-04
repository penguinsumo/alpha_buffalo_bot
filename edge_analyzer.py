import pandas as pd

class EdgeAnalyzer:
    def __init__(self, trades):
        self.df = pd.DataFrame(trades)

    def edge_by_pattern(self):
        return self.df.groupby("pattern")["r_multiple"].mean()

    def edge_by_vsa(self):
        return self.df.groupby("vsa_ok")["r_multiple"].mean()

    def edge_by_bos(self):
        return self.df.groupby("bos")["r_multiple"].mean()

    def edge_by_confidence_bucket(self):
        self.df["bucket"] = (self.df["confidence"] // 20) * 20
        return self.df.groupby("bucket")["r_multiple"].mean()

    def full_report(self):
        return {
            "pattern_edge": self.edge_by_pattern(),
            "vsa_edge": self.edge_by_vsa(),
            "bos_edge": self.edge_by_bos(),
            "confidence_edge": self.edge_by_confidence_bucket()
        }
