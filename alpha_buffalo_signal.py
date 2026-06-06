#!/usr/bin/env python3
"""
alpha_buffalo_signal.py — Alpha Buffalo v5.2 Signal Generator
Production-ready signal generator ที่รวมทุก component

Supported assets:
    - Forex: EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, NZDUSD
    - Indices: JPN225 (Nikkei 225), US100 (NASDAQ 100)
"""

import argparse
import json
import sys
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import numpy as np

# Import components
from signal_composer import compose_signal, format_composed, ComposedSignal
from alphatrend_gate import check_at_zone, get_at_confluence
from vsa_gate import check_vsa_signal, check_vsa_mtf

# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class AlphaBuffaloSignalGenerator:
    """Main signal generator orchestrator"""
    
    def __init__(
        self,
        data_dir: str = "./data",
        min_confidence: float = 60.0,
        risk_per_trade: float = 0.01,
    ):
        self.data_dir = Path(data_dir)
        self.min_confidence = min_confidence
        self.risk_per_trade = risk_per_trade
        self.composer = SignalComposer(
            use_vsa=True,
            use_alphatrend=True,
            min_confidence=min_confidence,
            risk_per_trade=risk_per_trade,
        )
    
    def load_data(
        self,
        pair: str,
        timeframe: str = "H1",
        bars: int = 200
    ) -> Optional[pd.DataFrame]:
        """Load OHLCV data for a pair/asset"""
        
        # รองรับชื่อ asset แบบต่างๆ
        asset_variations = self._get_asset_variations(pair)
        
        for variation in asset_variations:
            # Try multiple file name patterns
            patterns = [
                self.data_dir / f"{variation}_{timeframe}.csv",
                self.data_dir / f"{variation}.csv",
                self.data_dir / f"{variation.lower()}_{timeframe.lower()}.csv",
                self.data_dir / f"{variation.upper()}_{timeframe}.csv",
                self.data_dir / f"{timeframe}_{variation}.csv",
            ]
            
            for filepath in patterns:
                if filepath.exists():
                    logger.info(f"Loading data from {filepath}")
                    df = pd.read_csv(filepath)
                    
                    # Standardize columns
                    df.columns = [c.lower().strip() for c in df.columns]
                    
                    # Convert time column if exists
                    if 'time' in df.columns:
                        df['time'] = pd.to_datetime(df['time'])
                        df.set_index('time', inplace=True)
                    elif 'date' in df.columns:
                        df['date'] = pd.to_datetime(df['date'])
                        df.set_index('date', inplace=True)
                    elif 'timestamp' in df.columns:
                        df['timestamp'] = pd.to_datetime(df['timestamp'])
                        df.set_index('timestamp', inplace=True)
                    
                    # Ensure required columns
                    required = ['open', 'high', 'low', 'close']
                    missing = [c for c in required if c not in df.columns]
                    if missing:
                        logger.error(f"Missing columns: {missing}")
                        continue
                    
                    # Add volume if not exists
                    if 'volume' not in df.columns:
                        # สำหรับ indices บางครั้งไม่มี volume data
                        df['volume'] = 1000000
                    
                    return df.tail(bars)
        
        logger.error(f"No data file found for {pair} {timeframe}")
        logger.info(f"Tried variations: {asset_variations}")
        return None
    
    def _get_asset_variations(self, asset: str) -> List[str]:
        """Get possible variations of asset name for file lookup"""
        
        asset_upper = asset.upper()
        variations = [asset_upper]
        
        # Forex pairs
        if asset_upper in ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "NZDUSD"]:
            variations.extend([
                asset_upper.replace("/", ""),
                f"{asset_upper[:3]}/{asset_upper[3:]}",
                asset_upper.lower(),
            ])
        
        # JPN225 (Nikkei 225)
        elif asset_upper == "JPN225":
            variations.extend([
                "NIKKEI", "NIKKEI225", "JP225", 
                "NK225", "NKD", "NI225",
                "JPN225_IDX", "JPX",
            ])
        
        # US100 (NASDAQ 100)
        elif asset_upper == "US100":
            variations.extend([
                "NAS100", "NASDAQ100", "NDX", 
                "USTEC", "US100_IDX", "NAS",
                "QQQ", "NDX100",
            ])
        
        # Gold/Silver
        elif asset_upper == "XAUUSD":
            variations.extend(["GOLD", "XAU", "GOLDUSD"])
        
        elif asset_upper == "XAGUSD":
            variations.extend(["SILVER", "XAG"])
        
        # Oil
        elif asset_upper == "WTI":
            variations.extend(["USOIL", "CL", "WTIUSD"])
        
        elif asset_upper == "BRENT":
            variations.extend(["UKOIL", "BRN", "BZ"])
        
        return variations
    
    def get_atr_multiplier(self, asset: str) -> float:
        """Get ATR multiplier based on asset volatility"""
        
        asset_upper = asset.upper()
        
        # Indices (more volatile)
        if asset_upper in ["JPN225", "US100", "NAS100", "NDX"]:
            return 1.5  # Wider stop for indices
        
        # Forex majors (less volatile)
        elif asset_upper in ["EURUSD", "GBPUSD", "USDJPY"]:
            return 1.0
        
        # Forex minors (medium volatility)
        elif asset_upper in ["AUDUSD", "USDCAD", "NZDUSD"]:
            return 1.2
        
        # Commodities
        elif asset_upper in ["XAUUSD", "XAGUSD", "WTI", "BRENT"]:
            return 1.3
        
        return 1.0
    
    def generate_signal(
        self,
        pair: str,
        account_balance: float = 10000.0,
    ) -> Dict:
        """Generate complete signal for a pair/asset"""
        
        result = {
            "timestamp": datetime.now().isoformat(),
            "asset": pair,
            "signal": None,
            "error": None,
        }
        
        try:
            # Load timeframes
            df_h1 = self.load_data(pair, "H1", 200)
            df_h4 = self.load_data(pair, "H4", 100)
            
            if df_h1 is None or df_h4 is None:
                result["error"] = f"Failed to load data for {pair}"
                return result
            
            # Adjust composer parameters based on asset
            atr_mult = self.get_atr_multiplier(pair)
            
            # Generate signal with adjusted parameters
            signal = self.composer.compose(
                df_h1=df_h1,
                df_h4=df_h4,
                account_balance=account_balance,
            )
            
            # Adjust stop loss based on asset volatility
            if signal.direction != "HOLD" and atr_mult != 1.0:
                original_sl = signal.stop_loss
                if signal.direction == "BUY":
                    signal.stop_loss = signal.entry_price - (signal.entry_price - original_sl) * atr_mult
                else:
                    signal.stop_loss = signal.entry_price + (original_sl - signal.entry_price) * atr_mult
            
            # Convert to dict
            result["signal"] = {
                "direction": signal.direction,
                "strength": signal.strength.value,
                "entry_price": round(signal.entry_price, 5),
                "stop_loss": round(signal.stop_loss, 5),
                "take_profit": [round(tp, 5) for tp in signal.take_profit],
                "volume": round(signal.volume, 2),
                "confidence": round(signal.confidence, 1),
                "session": signal.session,
                "reasons": signal.reasons,
                "warnings": signal.warnings,
                "bonus": signal.bonus,
                "asset_type": self._classify_asset(pair),
            }
            
            # Add VSA and AT details if requested
            result["details"] = {
                "vsa": check_vsa_signal(df_h1, signal.direction if signal.direction != "HOLD" else "BUY"),
                "alphatrend": get_at_confluence(df_h1, df_h4, "BUY"),
            }
            
            logger.info(f"Signal for {pair}: {signal.direction} (conf: {signal.confidence})")
            
        except Exception as e:
            logger.error(f"Error generating signal for {pair}: {e}")
            result["error"] = str(e)
        
        return result
    
    def _classify_asset(self, asset: str) -> str:
        """Classify asset type"""
        asset_upper = asset.upper()
        
        if asset_upper in ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "NZDUSD"]:
            return "forex_major"
        elif asset_upper in ["JPN225", "US100", "NAS100", "NDX", "NI225"]:
            return "index"
        elif asset_upper in ["XAUUSD", "XAGUSD"]:
            return "metal"
        elif asset_upper in ["WTI", "BRENT", "CL", "USOIL", "UKOIL"]:
            return "oil"
        else:
            return "unknown"
    
    def scan_all_pairs(
        self,
        assets: List[str],
        min_confidence: float = 70.0,
    ) -> List[Dict]:
        """Scan multiple assets and return only strong signals"""
        
        signals = []
        
        for asset in assets:
            logger.info(f"Scanning {asset}...")
            result = self.generate_signal(asset)
            
            if result["signal"]:
                signal = result["signal"]
                if signal["direction"] != "HOLD" and signal["confidence"] >= min_confidence:
                    signals.append(result)
        
        # Sort by confidence
        signals.sort(key=lambda x: x["signal"]["confidence"], reverse=True)
        
        return signals

# ─────────────────────────────────────────────
# CLI Interface
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Alpha Buffalo v5.2 Signal Generator"
    )
    
    parser.add_argument(
        "--asset",
        type=str,
        help="Asset to analyze (EURUSD, JPN225, US100, etc.)"
    )
    
    parser.add_argument(
        "--all-assets",
        action="store_true",
        help="Scan all configured assets"
    )
    
    parser.add_argument(
        "--assets",
        type=str,
        help="Comma-separated list of assets"
    )
    
    parser.add_argument(
        "--data-dir",
        type=str,
        default="./data",
        help="Data directory"
    )
    
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=60.0,
        help="Minimum confidence for signal (0-100)"
    )
    
    parser.add_argument(
        "--output",
        type=str,
        choices=["console", "json", "csv"],
        default="console",
        help="Output format"
    )
    
    parser.add_argument(
        "--balance",
        type=float,
        default=10000.0,
        help="Account balance for position sizing"
    )
    
    args = parser.parse_args()
    
    # Initialize generator
    generator = AlphaBuffaloSignalGenerator(
        data_dir=args.data_dir,
        min_confidence=args.min_confidence,
    )
    
    # Determine which assets to scan
    default_assets = ["EURUSD", "JPN225", "US100"]
    
    if args.all_assets:
        assets = default_assets
    elif args.assets:
        assets = [a.strip().upper() for a in args.assets.split(",")]
    elif args.asset:
        assets = [args.asset.upper()]
    else:
        assets = default_assets
    
    # Generate signals
    if len(assets) == 1:
        result = generator.generate_signal(assets[0], args.balance)
        signals = [result] if result["signal"] else []
    else:
        signals = generator.scan_all_pairs(assets, args.min_confidence)
    
    # Output results
    if args.output == "json":
        print(json.dumps(signals, indent=2))
    
    elif args.output == "csv" and signals:
        df = pd.DataFrame([s["signal"] for s in signals])
        df["asset"] = [s["asset"] for s in signals]
        print(df.to_csv(index=False))
    
    else:  # console
        print("\n" + "="*80)
        print(f"🐂 ALPHA BUFFALO v5.2 SIGNALS")
        print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"📊 Assets: {', '.join(assets)}")
        print("="*80)
        
        if not signals:
            print("\n⚠️  No strong signals found.")
            print("\n💡 Tips:")
            print("   - Ensure data files exist in ./data/ directory")
            print(f"   - Expected files: {', '.join([f'{a}_H1.csv' for a in assets])}")
            print("   - Data format: time,open,high,low,close,volume")
        else:
            for result in signals:
                sig = result["signal"]
                
                # Asset icon
                if sig["asset_type"] == "index":
                    icon = "📈"
                elif sig["asset_type"] == "metal":
                    icon = "🥇"
                elif sig["asset_type"] == "oil":
                    icon = "🛢️"
                else:
                    icon = "💱"
                
                print(f"\n{icon} {result['asset']}")
                
                # Direction with arrow
                if sig['direction'] == "BUY":
                    dir_display = "🟢 BUY  ▲"
                elif sig['direction'] == "SELL":
                    dir_display = "🔴 SELL ▼"
                else:
                    dir_display = "⚪ HOLD"
                
                print(f"   {dir_display}")
                
                # Strength badge
                strength_badge = {
                    "sniper": "🎯 SNIPER",
                    "strong": "💪 STRONG",
                    "normal": "📊 NORMAL",
                    "weak": "⚠️ WEAK",
                }.get(sig['strength'], sig['strength'].upper())
                
                print(f"   Strength: {strength_badge}")
                print(f"   Confidence: {sig['confidence']}%")
                print(f"   Session: {sig['session']}")
                print(f"   Bonus: +{sig['bonus']}")
                print(f"\n   📍 Entry: {sig['entry_price']}")
                print(f"   🛑 Stop Loss: {sig['stop_loss']}")
                print(f"   🎯 Take Profit:")
                for i, tp in enumerate(sig['take_profit'], 1):
                    print(f"      TP{i}: {tp}")
                print(f"\n   💰 Position: {sig['volume']:.2f} units")
                
                print(f"\n   ✅ Confirmation Reasons:")
                for reason in sig['reasons']:
                    print(f"      ✓ {reason}")
                
                if sig['warnings']:
                    print(f"\n   ⚠️ Warnings:")
                    for warning in sig['warnings']:
                        print(f"      ⚠ {warning}")
                
                print("-"*40)
        
        print("\n" + "="*80)
        print("💡 LEGEND:")
        print("   🎯 SNIPER = Highest confidence (80%+) after spike")
        print("   💪 STRONG = Strong confirmation (75%+)")
        print("   📊 NORMAL = Basic confirmation (60%+)")
        print("   ⚠️ WEAK = Low confidence (60%-)")
        print("\n   📍 Session Impact:")
        print("      ASIA  → 0.5x position size")
        print("      NY    → 1.2x position size")
        print("="*80)

if __name__ == "__main__":
beta-sniper
    print("🐃 ALPHA BUFFALO v5 (Sniper Ambush) started\n")
    threading.Thread(target=command_loop, daemon=True).start()
    threading.Thread(target=signal_loop,  daemon=True).start()
    port = int(os.getenv("PORT",8080))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")

def compute_dynamic_lot(signal, df_1h, account_balance=10000, risk_percent=0.01):
    tr = pd.concat([df_1h['high']-df_1h['low'],
                    (df_1h['high']-df_1h['close'].shift(1)).abs(),
                    (df_1h['low']-df_1h['close'].shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().iloc[-1]
    if signal.direction == "BUY":
        sl_distance = abs(signal.entry_price - signal.sl_price)
    else:
        sl_distance = abs(signal.sl_price - signal.entry_price)
    risk_amount = account_balance * risk_percent
    if sl_distance > 0:
        base_lot = risk_amount / sl_distance
    else:
        base_lot = 0
    atr_normal = atr / signal.entry_price
    if atr_normal > 0.02:
        base_lot *= 0.7
    elif atr_normal < 0.005:
        base_lot *= 1.3
    if hasattr(signal, 'position_multiplier'):
        base_lot *= signal.position_multiplier
    max_lot = account_balance * 0.1 / signal.entry_price
    return min(base_lot, max_lot)

    main()
 main
