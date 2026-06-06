import threading
import time
import logging
from collections import deque
logger = logging.getLogger(__name__)

class SpikeMonitor:
    def __init__(self, tick_feed, lookback_seconds=30, spike_threshold_pct=0.5):
        self.feed = tick_feed
        self.spike_threshold = spike_threshold_pct
        self.price_history = deque(maxlen=lookback_seconds)
        self.last_spike_time = None
        self.spike_type = None
        self.callbacks = []
        self.running = False
        
    def start(self):
        self.running = True
        self.feed.add_callback(self.on_tick)
        self.feed.start()
        self.monitor_thread = threading.Thread(target=self._monitor_loop)
        self.monitor_thread.daemon = True
        self.monitor_thread.start()
    
    def on_tick(self, price):
        now = time.time()
        self.price_history.append((now, price))
    
    def _monitor_loop(self):
        while self.running:
            if len(self.price_history) >= 10:
                self._detect_spike()
            time.sleep(1)
    
    def _detect_spike(self):
        if len(self.price_history) < 5:
            return
        now, current_price = self.price_history[-1]
        for i in range(-5, -2):
            ts, old_price = self.price_history[i]
            if now - ts <= 5:
                pct_change = abs(current_price - old_price) / old_price * 100
                if pct_change >= self.spike_threshold:
                    direction = 'bullish' if current_price > old_price else 'bearish'
                    if self.last_spike_time is None or (now - self.last_spike_time) > 60:
                        self.last_spike_time = now
                        self.spike_type = direction
                        logger.info(f"Spike detected: {direction} {pct_change:.2f}%")
                        for cb in self.callbacks:
                            cb(direction, current_price, old_price)
                    return
    
    def add_callback(self, callback):
        self.callbacks.append(callback)
    
    def get_spike_status(self):
        return self.spike_type if self.last_spike_time and (time.time() - self.last_spike_time) < 10 else None
