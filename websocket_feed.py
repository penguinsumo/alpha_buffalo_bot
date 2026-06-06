import json
import threading
import time
import websocket
import logging
logger = logging.getLogger(__name__)

class TickFeed:
    def __init__(self, api_key, symbol="XAU/USD", interval="1min"):
        self.api_key = api_key
        self.symbol = symbol
        self.interval = interval
        self.ws = None
        self.last_price = None
        self.callbacks = []
        self.running = False
        
    def on_message(self, ws, message):
        data = json.loads(message)
        if 'price' in data:
            self.last_price = float(data['price'])
            for cb in self.callbacks:
                cb(self.last_price)
        elif 'event' in data and data['event'] == 'price':
            self.last_price = float(data['price'])
            for cb in self.callbacks:
                cb(self.last_price)
    
    def on_error(self, ws, error):
        logger.error(f"WebSocket error: {error}")
    
    def on_close(self, ws, close_status_code, close_msg):
        logger.info("WebSocket closed")
        self.running = False
    
    def on_open(self, ws):
        logger.info("WebSocket opened")
        subscribe_msg = {"action": "subscribe", "symbols": self.symbol, "interval": self.interval}
        ws.send(json.dumps(subscribe_msg))
    
    def start(self):
        websocket_url = f"wss://ws.twelvedata.com/v1/quotes?apikey={self.api_key}"
        self.ws = websocket.WebSocketApp(websocket_url,
                                         on_open=self.on_open,
                                         on_message=self.on_message,
                                         on_error=self.on_error,
                                         on_close=self.on_close)
        self.running = True
        self.thread = threading.Thread(target=self.ws.run_forever)
        self.thread.daemon = True
        self.thread.start()
    
    def stop(self):
        if self.ws:
            self.ws.close()
        self.running = False
    
    def add_callback(self, callback):
        self.callbacks.append(callback)
    
    def get_last_price(self):
        return self.last_price
