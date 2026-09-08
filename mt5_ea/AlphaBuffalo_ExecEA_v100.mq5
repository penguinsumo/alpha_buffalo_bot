//+------------------------------------------------------------------+
//| AlphaBuffalo_ExecEA_v100.mq5                                     |
//| Execution-only MT5 EA -- polls /execution/command, reports back  |
//| via /execution/fill and /execution/ack. The EA decides NOTHING:  |
//| direction, SL, TP1/TP_final and lot size all come from the       |
//| backend (execution_bridge.py). Replaces the older                |
//| AlphaBuffalo_CloudEA_ExecutionOnly_v302/v305 and                 |
//| AlphaBuffalo_RailwayPythonEA_v100 files, which targeted an       |
//| /execution/command endpoint that never existed on the deployed   |
//| backend -- a Railway HTTP-log audit showed zero hits on any of   |
//| those paths, ever. This version's endpoint contract is the one   |
//| actually implemented in alpha_buffalo_signal.py (7 ก.ย. 2026).   |
//+------------------------------------------------------------------+
#property strict
#property copyright "Alpha Buffalo"
#property version   "1.00"

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>

CTrade        trade;
CPositionInfo pos;

input string ApiUrl               = "https://alphabuffalobot-production.up.railway.app";
input string LicenseKey           = "DEMO123";
input int    PollSeconds          = 15;
input double FallbackLot          = 0.01;   // used ONLY if the backend ever omits "lot" -- should not normally happen
input int    Magic                = 20260907;
input int    Slippage             = 10;
input double MaxSpreadPoints      = 0.0;    // 0 = disabled
input bool   AllowMultiple        = false;
input bool   DebugLog             = true;
input bool   PollOnTickFallback   = true;
input double DailyProfitTargetPct = 8.0;    // 0 = disabled locally (server can still stop sending signals)

datetime LastPollTime = 0;
string   LastAckedCommandId = "";

//+------------------------------------------------------------------+
//| Bangkok trading-day key, for both the daily-profit lock AND the  |
//| day_start_equity this EA reports on every poll -- the backend    |
//| uses THAT value (not a live equity read) to size the next lot,   |
//| so it stays fixed for the whole day.                             |
//+------------------------------------------------------------------+
string BangkokDayKey()
{
   MqlDateTime bkk;
   TimeToStruct(TimeGMT() + 7 * 3600, bkk);
   return StringFormat("%04d%02d%02d", bkk.year, bkk.mon, bkk.day);
}

string DailyEquityVariable()
{
   return "AB.DSE." + (string)AccountInfoInteger(ACCOUNT_LOGIN) + "." + BangkokDayKey();
}

string DailyProfitLockVariable()
{
   return "AB.DPL." + (string)AccountInfoInteger(ACCOUNT_LOGIN) + "." + BangkokDayKey();
}

double DayStartEquity()
{
   string key = DailyEquityVariable();
   if(!GlobalVariableCheck(key))
      GlobalVariableSet(key, AccountInfoDouble(ACCOUNT_EQUITY));
   double value = GlobalVariableGet(key);
   if(value <= 0.0)
   {
      value = AccountInfoDouble(ACCOUNT_EQUITY);
      GlobalVariableSet(key, value);
   }
   return value;
}

double CurrentDailyProfitPct()
{
   double start = DayStartEquity();
   if(start <= 0.0) return 0.0;
   return 100.0 * (AccountInfoDouble(ACCOUNT_EQUITY) - start) / start;
}

bool DailyProfitLocked()
{
   if(DailyProfitTargetPct <= 0.0) return false;
   string key = DailyProfitLockVariable();
   if(GlobalVariableCheck(key) && GlobalVariableGet(key) > 0.5) return true;
   if(CurrentDailyProfitPct() >= DailyProfitTargetPct)
   {
      GlobalVariableSet(key, 1.0);
      return true;
   }
   return false;
}

//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(Magic);
   trade.SetDeviationInPoints(Slippage);
   int seconds = MathMax(5, PollSeconds);
   EventSetTimer(seconds);
   DayStartEquity();
   Print("AlphaBuffalo Execution EA v1.00 started | API=", ApiUrl,
         " | symbol=", _Symbol, " | PollSeconds=", seconds,
         " | day_start_equity=", DoubleToString(DayStartEquity(), 2));
   Print("Make sure this URL is allowed under Tools > Options > Expert Advisors > "
         "Allow WebRequest for listed URL: ", ApiUrl);
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
}

void OnTimer()
{
   LastPollTime = TimeCurrent();
   PollAndExecute();
}

void OnTick()
{
   if(!PollOnTickFallback) return;
   datetime now = TimeCurrent();
   if(LastPollTime == 0 || now - LastPollTime >= MathMax(5, PollSeconds))
   {
      LastPollTime = now;
      PollAndExecute();
   }
}

//+------------------------------------------------------------------+
//| Poll the durable command endpoint. This EA never decides         |
//| direction, SL/TP, or lot size -- all three come from the server. |
//+------------------------------------------------------------------+
void PollAndExecute()
{
   long   account_id = AccountInfoInteger(ACCOUNT_LOGIN);
   double balance    = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity     = AccountInfoDouble(ACCOUNT_EQUITY);
   double day_start  = DayStartEquity();
   string url = ApiBase() + "/execution/command?key=" + LicenseKey +
                "&symbol=" + _Symbol +
                "&account_id=" + (string)account_id +
                "&balance=" + DoubleToString(balance, 2) +
                "&equity=" + DoubleToString(equity, 2) +
                "&day_start_equity=" + DoubleToString(day_start, 2);
   string json = "";
   int status = HttpGet(url, json);

   if(status != 200)
   {
      Print("AlphaBuffalo command poll failed | HTTP=", status,
            " | response=", StringSubstr(json, 0, 300));
      return;
   }

   string command = ExtractObject(json, "command");
   if(command == "")
   {
      Print("AlphaBuffalo parse failed: command object not found | response=",
            StringSubstr(json, 0, 500));
      return;
   }

   string action     = ParseStr(command, "action");
   string reason     = ParseStr(command, "reason");
   string command_id = ParseStr(command, "command_id");

   if(DebugLog)
      Print("AlphaBuffalo command | action=", action,
            " reason=", reason, " command_id=", command_id);

   if(action == "" || action == "HOLD")
   {
      if(DebugLog) Print("AlphaBuffalo no order sent: action=HOLD | reason=", reason);
      return;
   }

   if(command_id == "")
   {
      Print("AlphaBuffalo command rejected: missing command_id");
      return;
   }

   if(command_id == LastAckedCommandId)
   {
      if(DebugLog) Print("AlphaBuffalo duplicate local command ignored: ", command_id);
      return;
   }

   string payload_symbol = ParseStr(command, "symbol");
   if(payload_symbol != "" &&
      CanonicalSymbol(payload_symbol) != CanonicalSymbol(_Symbol))
   {
      Print("AlphaBuffalo command rejected: symbol mismatch payload=",
            payload_symbol, " chart=", _Symbol);
      return;
   }

   if(action == "OPEN")
   {
      ExecuteOpen(command, command_id);
      return;
   }

   if(action == "PARTIAL_CLOSE_MOVE_BE")
   {
      ExecutePartialCloseMoveBE(command, command_id);
      return;
   }

   if(action == "CLOSE_ALL")
   {
      ExecuteCloseAll(command_id, reason);
      return;
   }

   if(action == "UPDATE_SL")
   {
      ExecuteUpdateSL(command, command_id);
      return;
   }

   Print("AlphaBuffalo command rejected: unsupported action=", action);
   PostAck(command_id, false, 100.0, 0.0);
}

void ExecuteOpen(string command, string command_id)
{
   string signal_id = ParseStr(command, "signal_id");
   string direction = ParseStr(command, "direction");
   double sl         = ParseDbl(command, "sl");
   double tp1        = ParseDbl(command, "tp1");
   double tp_final   = ParseDbl(command, "tp_final");
   double server_lot = ParseDbl(command, "lot");

   double daily_profit_pct = CurrentDailyProfitPct();
   if(DailyProfitLocked())
   {
      Print("AlphaBuffalo OPEN blocked: daily profit target reached | profit=",
            DoubleToString(daily_profit_pct, 2), "% target=",
            DoubleToString(DailyProfitTargetPct, 2), "%");
      return;
   }

   if(signal_id == "" || (direction != "BUY" && direction != "SELL"))
   {
      Print("AlphaBuffalo OPEN rejected: invalid signal/direction");
      PostAck(command_id, false, 100.0, 0.0);
      return;
   }

   if(sl <= 0.0 || tp1 <= 0.0 || tp_final <= 0.0)
   {
      Print("AlphaBuffalo OPEN rejected: invalid SL/TP levels");
      PostAck(command_id, false, 100.0, 0.0);
      return;
   }

   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double market_entry = (direction == "BUY") ? ask : bid;
   bool levels_ok = (direction == "BUY" && sl < market_entry && market_entry < tp_final) ||
                    (direction == "SELL" && tp_final < market_entry && market_entry < sl);
   if(!levels_ok)
   {
      Print("AlphaBuffalo OPEN rejected: broker price outside directional levels | entry=",
            market_entry, " sl=", sl, " tp=", tp_final);
      PostAck(command_id, false, 100.0, 0.0);
      return;
   }

   if(!SpreadOK())
   {
      Print("AlphaBuffalo OPEN delayed: spread too high");
      PostAck(command_id, false, 100.0, 0.0);
      return;
   }

   ulong existing_ticket = FindMagicPositionTicket();
   if(existing_ticket != 0 && !AllowMultiple)
   {
      double existing_entry = market_entry;
      if(pos.SelectByTicket(existing_ticket)) existing_entry = pos.PriceOpen();
      bool fill_ok = PostFill(signal_id, existing_ticket, existing_entry);
      bool ack_ok = fill_ok && PostAck(command_id, true, 100.0, 0.0);
      if(ack_ok)
      {
         LastAckedCommandId = command_id;
         Print("AlphaBuffalo recovered existing position and ACKed OPEN | ticket=",
               existing_ticket);
      }
      return;
   }

   // Lot size ALWAYS comes from the server (risk-% of the day's starting
   // equity, computed in execution_bridge.py). FallbackLot only guards
   // against a malformed/missing field -- it should never normally fire.
   double lot = NormalizeLot(server_lot > 0.0 ? server_lot : FallbackLot);
   if(server_lot <= 0.0)
      Print("AlphaBuffalo WARNING: server did not send a valid lot, using FallbackLot=",
            DoubleToString(FallbackLot, 2));

   // No broker-side TP is set (8 ก.ย. 2026, owner's explicit design): once
   // trailing stop is live, the position must keep running past the
   // original TP_final instead of being auto-closed there by the broker.
   // tp_final above is still used for the directional sanity check
   // (levels_ok) and is still reported to Telegram/Supabase as the
   // original target -- it just no longer arms MT5's own TP field.
   // TP1 partial-close is unaffected: it's driven by check_tp1_and_queue_be()
   // comparing live price to tp1 server-side, not by this broker TP.
   string comment = "AB|" + StringSubstr(signal_id, 0, 24);
   bool sent = false;
   if(direction == "BUY")
      sent = trade.Buy(lot, _Symbol, ask, NormalizeDouble(sl, _Digits),
                       0.0, comment);
   else
      sent = trade.Sell(lot, _Symbol, bid, NormalizeDouble(sl, _Digits),
                        0.0, comment);

   if(!sent)
   {
      Print("AlphaBuffalo OPEN failed | retcode=", trade.ResultRetcode(),
            " | ", trade.ResultRetcodeDescription());
      PostAck(command_id, false, 100.0, 0.0);
      return;
   }

   ulong ticket = FindMagicPositionTicket();
   if(ticket == 0)
   {
      Print("AlphaBuffalo OPEN sent but position ticket not found");
      PostAck(command_id, false, 100.0, 0.0);
      return;
   }

   double fill_price = market_entry;
   if(pos.SelectByTicket(ticket)) fill_price = pos.PriceOpen();

   if(!PostFill(signal_id, ticket, fill_price))
   {
      Print("AlphaBuffalo fill was not accepted; command retained for retry");
      PostAck(command_id, false, 100.0, 0.0);
      return;
   }

   if(!PostAck(command_id, true, 100.0, 0.0))
   {
      Print("AlphaBuffalo OPEN ACK failed; existing-position recovery will retry");
      return;
   }

   LastAckedCommandId = command_id;
   Print("AlphaBuffalo OPEN executed and ACKed | ", direction,
         " ticket=", ticket, " lot=", DoubleToString(lot, 2),
         " fill=", DoubleToString(fill_price, _Digits),
         " SL=", DoubleToString(sl, _Digits),
         " target(no broker TP, trailing-managed)=", DoubleToString(tp_final, _Digits));
}

void ExecutePartialCloseMoveBE(string command, string command_id)
{
   double close_pct = ParseDbl(command, "close_pct");
   double new_sl    = ParseDbl(command, "new_sl");
   if(close_pct <= 0.0 || close_pct >= 100.0) close_pct = 50.0;
   if(new_sl <= 0.0)
   {
      Print("AlphaBuffalo TP1/BE rejected: invalid new_sl");
      PostAck(command_id, false, 100.0, 0.0);
      return;
   }

   ulong ticket = FindMagicPositionTicket();
   if(ticket == 0 || !pos.SelectByTicket(ticket))
   {
      Print("AlphaBuffalo TP1/BE delayed: managed position not found");
      PostAck(command_id, false, 100.0, 0.0);
      return;
   }

   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double minv = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   if(step <= 0.0) step = 0.01;
   if(minv <= 0.0) minv = step;

   double volume_before   = pos.Volume();
   double current_tp      = pos.TakeProfit();
   double close_volume    = NormalizeVolumeDown(volume_before * close_pct / 100.0);
   double remainder       = volume_before - close_volume;
   bool   can_partial     = close_volume >= minv && remainder >= minv - (step * 0.5);
   bool   partial_done    = false;

   if(can_partial)
   {
      if(!trade.PositionClosePartial(_Symbol, close_volume))
      {
         Print("AlphaBuffalo TP1 partial close failed | retcode=",
               trade.ResultRetcode(), " | ", trade.ResultRetcodeDescription());
         PostAck(command_id, false, 100.0, 0.0);
         return;
      }
      partial_done = true;
      Print("AlphaBuffalo TP1 partial close executed | volume=",
            DoubleToString(close_volume, 2));
   }
   else
   {
      // A minimum-lot position cannot be split by this broker's minimum
      // volume. Keep the full runner, move it to BE, report 100% remaining.
      Print("AlphaBuffalo TP1 partial skipped: broker minimum volume | volume=",
            DoubleToString(volume_before, 2), " min=", DoubleToString(minv, 2));
   }

   if(!pos.SelectByTicket(ticket))
   {
      Print("AlphaBuffalo TP1/BE delayed: position disappeared after partial close");
      PostAck(command_id, false, 100.0, 0.0);
      return;
   }

   current_tp = pos.TakeProfit();
   double current_sl = pos.StopLoss();
   bool be_done = MathAbs(current_sl - new_sl) <= (_Point * 2.0);
   if(!be_done)
   {
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      ENUM_POSITION_TYPE side = pos.PositionType();
      bool valid_be = (side == POSITION_TYPE_BUY && new_sl < bid) ||
                      (side == POSITION_TYPE_SELL && new_sl > ask);
      if(!valid_be)
      {
         Print("AlphaBuffalo BE delayed: broker price has not cleared entry | new_sl=",
               DoubleToString(new_sl, _Digits));
         PostAck(command_id, false, 100.0, 0.0);
         return;
      }
      if(!trade.PositionModify(ticket, NormalizeDouble(new_sl, _Digits), current_tp))
      {
         Print("AlphaBuffalo BE move failed | retcode=", trade.ResultRetcode(),
               " | ", trade.ResultRetcodeDescription());
         PostAck(command_id, false, 100.0, 0.0);
         return;
      }
   }

   if(!pos.SelectByTicket(ticket))
   {
      PostAck(command_id, false, 100.0, 0.0);
      return;
   }
   double expected_before_partial = volume_before;
   double remaining_pct = expected_before_partial > 0.0
                          ? 100.0 * pos.Volume() / expected_before_partial
                          : 100.0;
   remaining_pct = MathMax(0.0, MathMin(100.0, remaining_pct));

   if(PostAck(command_id, true, remaining_pct, 0.0))
   {
      LastAckedCommandId = command_id;
      Print("AlphaBuffalo TP1/BE ACKed | remaining=",
            DoubleToString(remaining_pct, 2), "% SL=",
            DoubleToString(new_sl, _Digits));
   }
}

void ExecuteUpdateSL(string command, string command_id)
{
   // Trailing stop (8 ก.ย. 2026): the backend has already decided the new
   // SL (BB middle band, only ever sent when it's more favorable than what
   // it believes is live -- see execution_bridge.check_trailing_stop()).
   // This EA does not recompute or second-guess the level, only validates
   // it against live broker price before applying it, same pattern as the
   // BE move above. TP is (re)cleared to 0.0 on every trailing update too,
   // since ExecuteOpen() no longer arms a broker TP and nothing else here
   // should ever re-introduce one.
   double new_sl = ParseDbl(command, "new_sl");
   if(new_sl <= 0.0)
   {
      Print("AlphaBuffalo UPDATE_SL rejected: invalid new_sl");
      PostAck(command_id, false, 100.0, 0.0);
      return;
   }

   ulong ticket = FindMagicPositionTicket();
   if(ticket == 0 || !pos.SelectByTicket(ticket))
   {
      Print("AlphaBuffalo UPDATE_SL delayed: managed position not found");
      PostAck(command_id, false, 100.0, 0.0);
      return;
   }

   double current_sl = pos.StopLoss();
   if(MathAbs(current_sl - new_sl) <= (_Point * 2.0))
   {
      // Already at (or effectively at) this level -- likely a retry of an
      // already-applied update. ACK success as a no-op rather than fail.
      if(PostAck(command_id, true, 100.0, 0.0))
      {
         LastAckedCommandId = command_id;
         Print("AlphaBuffalo UPDATE_SL: already at target level | SL=",
               DoubleToString(new_sl, _Digits));
      }
      return;
   }

   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   ENUM_POSITION_TYPE side = pos.PositionType();
   bool valid = (side == POSITION_TYPE_BUY && new_sl < bid) ||
                (side == POSITION_TYPE_SELL && new_sl > ask);
   if(!valid)
   {
      Print("AlphaBuffalo UPDATE_SL delayed: candidate SL on wrong side of live price | new_sl=",
            DoubleToString(new_sl, _Digits));
      PostAck(command_id, false, 100.0, 0.0);
      return;
   }

   if(!trade.PositionModify(ticket, NormalizeDouble(new_sl, _Digits), 0.0))
   {
      Print("AlphaBuffalo UPDATE_SL failed | retcode=", trade.ResultRetcode(),
            " | ", trade.ResultRetcodeDescription());
      PostAck(command_id, false, 100.0, 0.0);
      return;
   }

   if(PostAck(command_id, true, 100.0, 0.0))
   {
      LastAckedCommandId = command_id;
      Print("AlphaBuffalo UPDATE_SL executed and ACKed | new_SL=",
            DoubleToString(new_sl, _Digits));
   }
}

void ExecuteCloseAll(string command_id, string reason)
{
   bool success = true;
   bool found = false;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(!pos.SelectByIndex(i)) continue;
      if(pos.Symbol() != _Symbol || pos.Magic() != Magic) continue;
      found = true;
      ulong ticket = pos.Ticket();
      if(!trade.PositionClose(ticket))
      {
         success = false;
         Print("AlphaBuffalo CLOSE failed | ticket=", ticket,
               " retcode=", trade.ResultRetcode(), " | ",
               trade.ResultRetcodeDescription());
      }
   }

   if(!success)
   {
      PostAck(command_id, false, 100.0, 0.0);
      return;
   }

   if(PostAck(command_id, true, 0.0, 0.0))
   {
      LastAckedCommandId = command_id;
      Print("AlphaBuffalo CLOSE_ALL ACKed | found_position=",
            (found ? "true" : "false"), " reason=", reason);
   }
}

bool PostFill(string signal_id, ulong ticket, double fill_price)
{
   string body = "{\"key\":\"" + LicenseKey + "\"," +
                 "\"symbol\":\"" + _Symbol + "\"," +
                 "\"signal_id\":\"" + signal_id + "\"," +
                 "\"ticket\":\"" + (string)ticket + "\"," +
                 "\"fill_price\":" + DoubleToString(fill_price, _Digits) + "}";
   string response = "";
   int status = HttpPost(ApiBase() + "/execution/fill", body, response);
   if(status != 200)
   {
      Print("AlphaBuffalo fill rejected | HTTP=", status,
            " response=", StringSubstr(response, 0, 400));
      return false;
   }
   return true;
}

bool PostAck(string command_id, bool success, double remaining_pct, double r_multiple)
{
   string body = "{\"key\":\"" + LicenseKey + "\"," +
                 "\"symbol\":\"" + _Symbol + "\"," +
                 "\"command_id\":\"" + command_id + "\"," +
                 "\"success\":" + (success ? "true" : "false") + "," +
                 "\"remaining_pct\":" + DoubleToString(remaining_pct, 2) + "," +
                 "\"r_multiple\":" + DoubleToString(r_multiple, 4) + "}";
   string response = "";
   int status = HttpPost(ApiBase() + "/execution/ack", body, response);
   if(status != 200)
   {
      Print("AlphaBuffalo ACK failed | HTTP=", status,
            " response=", StringSubstr(response, 0, 400));
      return false;
   }
   return true;
}

int HttpGet(string url, string &response)
{
   string headers = "Content-Type: application/json\r\n";
   char post[], result[];
   string result_headers;
   ResetLastError();
   int status = WebRequest("GET", url, headers, 5000, post, result, result_headers);
   int error = GetLastError();
   response = CharArrayToString(result);
   if(DebugLog)
      Print("AlphaBuffalo GET | HTTP=", status, " error=", error, " url=", url);
   return status;
}

int HttpPost(string url, string body, string &response)
{
   string headers = "Content-Type: application/json\r\n";
   char post[], result[];
   string result_headers;
   StringToCharArray(body, post, 0, StringLen(body));
   ResetLastError();
   int status = WebRequest("POST", url, headers, 5000, post, result, result_headers);
   int error = GetLastError();
   response = CharArrayToString(result);
   if(DebugLog)
      Print("AlphaBuffalo POST | HTTP=", status, " error=", error,
            " url=", url, " response=", StringSubstr(response, 0, 300));
   return status;
}

string ApiBase()
{
   string value = ApiUrl;
   while(StringLen(value) > 0 &&
         StringGetCharacter(value, StringLen(value) - 1) == '/')
      value = StringSubstr(value, 0, StringLen(value) - 1);
   return value;
}

string CanonicalSymbol(string value)
{
   StringToUpper(value);
   StringReplace(value, "/", "");
   int colon = StringFind(value, ":");
   if(colon >= 0) value = StringSubstr(value, colon + 1);
   if(StringFind(value, "XAUUSD") == 0) return "XAUUSD";
   return value;
}

bool SpreadOK()
{
   if(MaxSpreadPoints <= 0.0) return true;
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   return ((ask - bid) / _Point) <= MaxSpreadPoints;
}

ulong FindMagicPositionTicket()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(!pos.SelectByIndex(i)) continue;
      if(pos.Symbol() == _Symbol && pos.Magic() == Magic) return pos.Ticket();
   }
   return 0;
}

double NormalizeLot(double lot)
{
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double minv = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxv = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(step <= 0.0) step = 0.01;
   double normalized = MathRound(lot / step) * step;
   normalized = MathMax(minv, MathMin(maxv, normalized));
   return NormalizeDouble(normalized, 2);
}

double NormalizeVolumeDown(double volume)
{
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(step <= 0.0) step = 0.01;
   double normalized = MathFloor((volume + 1e-12) / step) * step;
   return NormalizeDouble(normalized, 8);
}

string ExtractObject(string json, string key)
{
   string marker = "\"" + key + "\"";
   int key_pos = StringFind(json, marker);
   if(key_pos < 0) return "";
   int colon = StringFind(json, ":", key_pos + StringLen(marker));
   if(colon < 0) return "";
   int start = StringFind(json, "{", colon);
   if(start < 0) return "";

   int depth = 0;
   bool in_string = false;
   ushort previous = 0;
   for(int i = start; i < StringLen(json); i++)
   {
      ushort ch = StringGetCharacter(json, i);
      if(ch == '"' && previous != '\\') in_string = !in_string;
      if(!in_string)
      {
         if(ch == '{') depth++;
         if(ch == '}')
         {
            depth--;
            if(depth == 0) return StringSubstr(json, start, i - start + 1);
         }
      }
      previous = ch;
   }
   return "";
}

int FindValueStart(string json, string key)
{
   string marker = "\"" + key + "\"";
   int key_pos = StringFind(json, marker);
   if(key_pos < 0) return -1;
   int colon = StringFind(json, ":", key_pos + StringLen(marker));
   if(colon < 0) return -1;
   int i = colon + 1;
   while(i < StringLen(json))
   {
      ushort ch = StringGetCharacter(json, i);
      if(ch != ' ' && ch != '\t' && ch != '\r' && ch != '\n') break;
      i++;
   }
   return i;
}

string ParseStr(string json, string key)
{
   int i = FindValueStart(json, key);
   if(i < 0 || StringGetCharacter(json, i) != '"') return "";
   i++;
   int end = i;
   while(end < StringLen(json) && StringGetCharacter(json, end) != '"') end++;
   if(end >= StringLen(json)) return "";
   return StringSubstr(json, i, end - i);
}

double ParseDbl(string json, string key)
{
   int i = FindValueStart(json, key);
   if(i < 0) return 0.0;
   int end = i;
   while(end < StringLen(json))
   {
      ushort ch = StringGetCharacter(json, end);
      if(ch == ',' || ch == '}' || ch == ']' || ch == '\r' || ch == '\n') break;
      end++;
   }
   return StringToDouble(StringSubstr(json, i, end - i));
}
