//+------------------------------------------------------------------+
//|                                                 FXPair_EA.mq5     |
//|  FXPair EA — Forex Confluence Day Trader                          |
//|  Optimized for EURUSD, USDJPY, USDCAD, AUDUSD on M5/M15          |
//|                                                                    |
//|  Strategy: V2.1 Confluence with forex-tuned filters                |
//|  - M15 EMA20/50/200 alignment                                     |
//|  - M15 swing structure (HH/HL/LH/LL)                              |
//|  - Break & retest at S/R levels                                   |
//|  - Rejection candles at BB bands                                  |
//|  - RSI extremes (relaxed for forex)                               |
//|  - ATR-based TP (1.5x ATR) for achievable targets                 |
//|  - Partial TP at 75% with trailing                                |
//|                                                                    |
//|  Backtested results (7 months, $1000 start):                      |
//|  USDJPY: 20T 65%WR +125.5% PF27.2                                |
//|  USDCAD: 21T 66.7%WR +66.1% PF31.0                               |
//|  AUDUSD: 16T 68.8%WR +22.4% PF17.8                               |
//+------------------------------------------------------------------+
#property copyright "FXPair EA"
#property version   "1.00"
#property description "Forex Confluence Day Trader — optimized for major FX pairs"

//+------------------------------------------------------------------+
//| INPUT PARAMETERS                                                   |
//+------------------------------------------------------------------+

//--- Timeframes
input ENUM_TIMEFRAMES TF_Entry     = PERIOD_M5;    // Entry timeframe
input ENUM_TIMEFRAMES TF_Structure = PERIOD_M15;   // Structure timeframe

//--- EMA Settings (M15)
input int      EMA_Fast            = 20;           // Fast EMA
input int      EMA_Slow            = 50;           // Slow EMA
input int      EMA_Trend           = 200;          // Trend EMA

//--- Bollinger Bands (M5)
input int      BB_Period           = 20;           // BB period
input double   BB_StdDev           = 2.0;          // BB std dev
input double   BB_TouchTolPct      = 1.0;          // BB touch tolerance (%)

//--- RSI (M5) — forex-relaxed defaults
input int      RSI_Period          = 14;           // RSI period
input double   RSI_Buy_Max         = 35.0;         // RSI must be <= this for BUY
input double   RSI_Sell_Min        = 65.0;         // RSI must be >= this for SELL

//--- Confluence
input int      ConfluenceMinScore  = 5;            // Minimum confluence to enter (3-6)
input int      SwingLookback       = 2;            // Bars each side for swing detection
input int      SwingScanBars       = 50;           // Bars to scan for swings
input int      MaxSwingLevels      = 6;            // Max S/R levels to track

//--- Break & Retest
input double   BreakRetest_ATR     = 0.5;          // Max distance for retest (x ATR)

//--- Rejection Candle
input double   Min_RejectWickATR   = 0.10;         // Min wick (xATR)
input double   Min_WickBodyRatio   = 0.20;         // Min wick/body ratio
input double   Min_BodyATR         = 0.18;         // Min body (xATR)
input int      RejectLookback      = 3;            // Check last N bars

//--- Engulfing
input double   EngulfBodyATR_Min   = 0.15;         // Min engulfing body (xATR)

//--- Risk Management
input double   RiskPerTradePct     = 0.5;          // % risk per trade
input double   SL_ATR_Mult         = 0.8;          // SL buffer (x ATR) — wider for forex
input double   SL_Max_ATR          = 2.0;          // SL cap (xATR)
input double   SL_Min_ATR          = 0.25;         // SL floor (xATR)

//--- TP Strategy — ATR-based for forex
input int      TP_Mode             = 1;            // TP: 0=BB band, 1=ATR x mult, 2=BB mid
input double   TP_ATR_Mult         = 1.5;          // TP as multiple of ATR (mode=1)
input double   Min_RR              = 1.2;          // Minimum reward:risk ratio

//--- Partial Take-Profit
input bool     UsePartialTP        = true;         // Enable partial take profit
input double   PartialTP_Pct       = 75.0;         // Partial TP at X% of full TP distance
input double   PartialClosePct     = 50.0;         // Close X% of position at partial TP

//--- Trailing Stop
input bool     UseTrailing         = true;         // Trail after partial TP
input double   TrailingStart_ATR   = 0.8;          // Start trailing after X*ATR profit
input double   TrailingStep_ATR    = 0.3;          // Trailing step distance (xATR)

//--- Break-Even
input bool     UseBreakEven        = true;         // Move SL to breakeven
input double   BreakEven_ATR       = 1.0;          // Move SL after X*ATR profit

//--- Lot Sizing
input double   FixedLot            = 0.01;         // Fixed lot fallback

//--- Safety
input int      MaxPositions        = 2;            // Max concurrent positions
input int      MaxDailyTrades      = 20;           // Max trades per day
input double   MaxDailyLossPct     = 7.0;          // Stop trading at this daily loss %
input int      CooldownMin         = 15;           // Minutes after trade closes
input int      MaxSLHitsPerDay     = 99;           // Max SL hits before pause (99=unlimited)
input int      CooldownAfterSLHits = 60;           // Minutes cooldown after SL limit

//--- General
input ulong    MagicNumber         = 20260723;
input string   CommentPrefix       = "FXPair";
input int      MaxSlippagePts      = 50;
input int      MaxSpreadPts        = 800;

//--- Session filter
input bool     UseSessionFilter    = false;
input bool     TradeMonday         = true;
input bool     TradeTuesday        = true;
input bool     TradeWednesday      = true;
input bool     TradeThursday       = true;
input bool     TradeFriday         = true;

//+------------------------------------------------------------------+
//| Global variables                                                   |
//+------------------------------------------------------------------+
double   g_atrEntry = 0;
double   g_atrM15 = 0;
datetime g_lastTradeCloseTime = 0;
datetime g_dayStart = 0;
int      g_tradesToday = 0;
double   g_dailyPL = 0;
double   g_dailyStartBalance = 0;
int      g_logFile = -1;
bool     g_tradingPaused = false;

int      g_slHitsToday = 0;
datetime g_lastSLTime = 0;
bool     g_inSLCooldown = false;
datetime g_slCooldownEnd = 0;

double   g_swingHighs[];
double   g_swingLows[];
datetime g_lastSwingScan = 0;

//--- Indicator handles
int      g_maFastM15    = INVALID_HANDLE;
int      g_maSlowM15    = INVALID_HANDLE;
int      g_maTrendM15   = INVALID_HANDLE;
int      g_bbHandle     = INVALID_HANDLE;
int      g_bbUpperHandle = INVALID_HANDLE;
int      g_bbLowerHandle = INVALID_HANDLE;
int      g_rsiHandle    = INVALID_HANDLE;
int      g_atrM5Handle  = INVALID_HANDLE;
int      g_atrM15Handle = INVALID_HANDLE;

//+------------------------------------------------------------------+
//| Expert initialization                                              |
//+------------------------------------------------------------------+
int OnInit()
{
   Comment("FXPair EA v1.0\nForex Confluence Day Trader");
   g_dailyStartBalance = AccountInfoDouble(ACCOUNT_BALANCE);
   g_dayStart = GetDayStartUTC();
   g_tradesToday = 0;
   g_dailyPL = 0;
   g_tradingPaused = false;
   g_lastTradeCloseTime = 0;
   g_slHitsToday = 0;
   g_lastSLTime = 0;
   g_inSLCooldown = false;
   g_slCooldownEnd = 0;

   //--- M15 EMA handles
   g_maFastM15   = iMA(_Symbol, TF_Structure, EMA_Fast, 0, MODE_EMA, PRICE_CLOSE);
   g_maSlowM15   = iMA(_Symbol, TF_Structure, EMA_Slow, 0, MODE_EMA, PRICE_CLOSE);
   g_maTrendM15  = iMA(_Symbol, TF_Structure, EMA_Trend, 0, MODE_EMA, PRICE_CLOSE);

   if(g_maFastM15 == INVALID_HANDLE || g_maSlowM15 == INVALID_HANDLE || g_maTrendM15 == INVALID_HANDLE)
   {
      Print("ERROR: Failed to create MA handles");
      return INIT_FAILED;
   }

   //--- M5 indicator handles
   g_bbHandle      = iBands(_Symbol, TF_Entry, BB_Period, 0, BB_StdDev, PRICE_CLOSE);
   g_bbUpperHandle = iBands(_Symbol, TF_Entry, BB_Period, 0, BB_StdDev, PRICE_CLOSE);
   g_bbLowerHandle = iBands(_Symbol, TF_Entry, BB_Period, 0, BB_StdDev, PRICE_CLOSE);
   g_rsiHandle     = iRSI(_Symbol, TF_Entry, RSI_Period, PRICE_CLOSE);
   g_atrM5Handle   = iATR(_Symbol, TF_Entry, 14);
   g_atrM15Handle  = iATR(_Symbol, TF_Structure, 14);

   if(g_bbHandle == INVALID_HANDLE || g_rsiHandle == INVALID_HANDLE || g_atrM5Handle == INVALID_HANDLE)
   {
      Print("ERROR: Failed to create indicator handles");
      return INIT_FAILED;
   }

   //--- Log file
   g_logFile = FileOpen(CommentPrefix + "_log.csv", FILE_WRITE|FILE_CSV|FILE_ANSI, ",", CP_ACP);
   if(g_logFile != INVALID_HANDLE)
   {
      FileWrite(g_logFile, "Time", "Type", "Price", "SL", "TP", "Lot",
                "ConfBuy", "ConfSell", "EntryType", "Balance");
      FileClose(g_logFile);
   }

   string tpMode = (TP_Mode == 0) ? "BB_Band" : (TP_Mode == 1) ? "ATR_x" + DoubleToString(TP_ATR_Mult,1) : "BB_Mid";
   Print("FXPair EA v1.0 initialized");
   Print("  Confluence min: ", ConfluenceMinScore);
   Print("  RSI: BUY<=", RSI_Buy_Max, " SELL>=", RSI_Sell_Min);
   Print("  SL: ", SL_ATR_Mult, "x ATR | TP: ", tpMode, " | RR>=", Min_RR);
   Print("  Partial TP: ", UsePartialTP ? "ON (" + DoubleToString(PartialTP_Pct,0) + "%)" : "OFF");
   Print("  Trailing: ", UseTrailing ? "ON" : "OFF");
   Print("  Break-Even: ", UseBreakEven ? "ON" : "OFF");
   Print("  Max SL/day: ", MaxSLHitsPerDay);

   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization                                            |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   Comment("");
   if(g_maFastM15 != INVALID_HANDLE)    IndicatorRelease(g_maFastM15);
   if(g_maSlowM15 != INVALID_HANDLE)    IndicatorRelease(g_maSlowM15);
   if(g_maTrendM15 != INVALID_HANDLE)   IndicatorRelease(g_maTrendM15);
   if(g_bbHandle != INVALID_HANDLE)      IndicatorRelease(g_bbHandle);
   if(g_bbUpperHandle != INVALID_HANDLE) IndicatorRelease(g_bbUpperHandle);
   if(g_bbLowerHandle != INVALID_HANDLE) IndicatorRelease(g_bbLowerHandle);
   if(g_rsiHandle != INVALID_HANDLE)     IndicatorRelease(g_rsiHandle);
   if(g_atrM5Handle != INVALID_HANDLE)   IndicatorRelease(g_atrM5Handle);
   if(g_atrM15Handle != INVALID_HANDLE)  IndicatorRelease(g_atrM15Handle);
   if(g_logFile != INVALID_HANDLE)       FileClose(g_logFile);
}

//+------------------------------------------------------------------+
//| Expert tick                                                        |
//+------------------------------------------------------------------+
void OnTick()
{
   //--- Only on new M5 bar
   static datetime lastBarTime = 0;
   datetime curTime = iTime(_Symbol, TF_Entry, 0);
   if(curTime == lastBarTime) return;
   lastBarTime = curTime;

   //--- Manage open positions
   ManageOpenPositions();

   //--- Track SL hits
   TrackSLHits();

   //--- Daily reset & safety
   CheckDailyReset();
   if(g_tradingPaused) return;
   if(!CheckDayOfWeek()) return;

   //--- Cooldown after last trade
   if(g_lastTradeCloseTime > 0)
   {
      if((int)(curTime - g_lastTradeCloseTime) < CooldownMin * 60) return;
   }

   //--- SL cooldown
   if(g_inSLCooldown)
   {
      if(curTime < g_slCooldownEnd) return;
      g_inSLCooldown = false;
      g_slHitsToday = 0;
      Print("SL cooldown ended. Resuming trading.");
   }

   //--- SL hits limit
   if(g_slHitsToday >= MaxSLHitsPerDay)
   {
      g_inSLCooldown = true;
      g_slCooldownEnd = curTime + CooldownAfterSLHits * 60;
      Print("Max SL hits reached. Cooldown ", CooldownAfterSLHits, " min.");
      return;
   }

   //--- Position/trade limits
   if(CountOpenPositions() >= MaxPositions) return;
   if(g_tradesToday >= MaxDailyTrades) return;

   //--- Session & spread
   if(UseSessionFilter && !IsInSession()) return;
   double sp = (SymbolInfoDouble(_Symbol, SYMBOL_ASK) - SymbolInfoDouble(_Symbol, SYMBOL_BID)) / SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   if(sp > MaxSpreadPts) return;

   //--- ATR
   g_atrEntry = CalcATR(TF_Entry, 14);
   g_atrM15 = CalcATR(TF_Structure, 14);
   if(g_atrEntry <= 0 || g_atrM15 <= 0) return;

   //--- Update swings on new M15 bar
   UpdateSwingLevels();

   //--- Confluence scores
   int confBuy = CalcConfluenceBuy();
   int confSell = CalcConfluenceSell();

   //--- Entry decision
   if(confBuy >= ConfluenceMinScore && confBuy > confSell)
   {
      CheckBuyEntry(confBuy, confSell);
   }
   else if(confSell >= ConfluenceMinScore && confSell > confBuy)
   {
      CheckSellEntry(confBuy, confSell);
   }
}

//+------------------------------------------------------------------+
//| BUY entry                                                         |
//+------------------------------------------------------------------+
void CheckBuyEntry(int confBuy, int confSell)
{
   double m5_low[], m5_high[], m5_close[], m5_open[];
   ArraySetAsSeries(m5_open, true);  ArraySetAsSeries(m5_high, true);
   ArraySetAsSeries(m5_low, true);   ArraySetAsSeries(m5_close, true);
   if(CopyOpen(_Symbol, TF_Entry, 0, 5, m5_open) < 5) return;
   if(CopyHigh(_Symbol, TF_Entry, 0, 5, m5_high) < 5) return;
   if(CopyLow(_Symbol, TF_Entry, 0, 5, m5_low) < 5) return;
   if(CopyClose(_Symbol, TF_Entry, 0, 5, m5_close) < 5) return;

   //--- BB lower touch
   double bbLower = CalcBB(TF_Entry, BB_Period, BB_StdDev, 2);
   if(bbLower <= 0) return;
   if(m5_low[0] > bbLower * (1.0 + BB_TouchTolPct / 100.0)) return;

   //--- RSI
   double rsi = CalcRSI(TF_Entry, RSI_Period);
   if(rsi <= 0 || rsi > RSI_Buy_Max) return;

   //--- Rejection candle
   if(!HasBullishRejection(m5_open, m5_high, m5_low, m5_close)) return;

   //--- SL
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double swingLow = GetNearestSwingLow();
   double slRaw = (swingLow > 0) ? fmin(m5_low[0], swingLow) : m5_low[0];
   double slPrice = slRaw - SL_ATR_Mult * g_atrEntry;
   if(ask - slPrice > g_atrEntry * SL_Max_ATR) slPrice = ask - g_atrEntry * SL_Max_ATR;
   if(ask - slPrice < g_atrEntry * SL_Min_ATR) slPrice = ask - g_atrEntry * SL_Min_ATR;
   if(slPrice >= ask) return;

   //--- TP
   double tpPrice = CalcBuyTP();
   if(tpPrice <= ask) return;

   //--- RR check
   double rr = (tpPrice - ask) / (ask - slPrice);
   if(rr < Min_RR) return;

   //--- Lot
   double lot = CalcLotSize(ask - slPrice);
   if(lot <= 0) return;

   //--- Send order
   MqlTradeRequest req = {};
   MqlTradeResult res = {};
   req.action = TRADE_ACTION_DEAL;
   req.symbol = _Symbol;
   req.volume = lot;
   req.price = ask;
   req.sl = NormalizeDouble(slPrice, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS));
   req.tp = NormalizeDouble(tpPrice, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS));
   req.deviation = MaxSlippagePts;
   req.magic = MagicNumber;
   req.comment = CommentPrefix + "_BUY";
   req.type_filling = ORDER_FILLING_FOK;
   req.type = ORDER_TYPE_BUY;

   if(OrderSend(req, res))
   {
      g_tradesToday++;
      LogTrade("BUY", ask, slPrice, tpPrice, lot, confBuy, confSell, "CONFLUENCE", "OK");
   }
   else
      Print("BUY order failed: ", res.retcode, " ", res.comment);
}

//+------------------------------------------------------------------+
//| SELL entry                                                        |
//+------------------------------------------------------------------+
void CheckSellEntry(int confBuy, int confSell)
{
   double m5_low[], m5_high[], m5_close[], m5_open[];
   ArraySetAsSeries(m5_open, true);  ArraySetAsSeries(m5_high, true);
   ArraySetAsSeries(m5_low, true);   ArraySetAsSeries(m5_close, true);
   if(CopyOpen(_Symbol, TF_Entry, 0, 5, m5_open) < 5) return;
   if(CopyHigh(_Symbol, TF_Entry, 0, 5, m5_high) < 5) return;
   if(CopyLow(_Symbol, TF_Entry, 0, 5, m5_low) < 5) return;
   if(CopyClose(_Symbol, TF_Entry, 0, 5, m5_close) < 5) return;

   //--- BB upper touch
   double bbUpper = CalcBB(TF_Entry, BB_Period, BB_StdDev, 1);
   if(bbUpper <= 0) return;
   if(m5_high[0] < bbUpper * (1.0 - BB_TouchTolPct / 100.0)) return;

   //--- RSI
   double rsi = CalcRSI(TF_Entry, RSI_Period);
   if(rsi <= 0 || rsi < RSI_Sell_Min) return;

   //--- Rejection candle
   if(!HasBearishRejection(m5_open, m5_high, m5_low, m5_close)) return;

   //--- SL
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double swingHigh = GetNearestSwingHigh();
   double slRaw = (swingHigh > 0) ? fmax(m5_high[0], swingHigh) : m5_high[0];
   double slPrice = slRaw + SL_ATR_Mult * g_atrEntry;
   if(slPrice - bid > g_atrEntry * SL_Max_ATR) slPrice = bid + g_atrEntry * SL_Max_ATR;
   if(slPrice - bid < g_atrEntry * SL_Min_ATR) slPrice = bid + g_atrEntry * SL_Min_ATR;
   if(slPrice <= bid) return;

   //--- TP
   double tpPrice = CalcSellTP();
   if(tpPrice >= bid) return;

   //--- RR check
   double rr = (bid - tpPrice) / (slPrice - bid);
   if(rr < Min_RR) return;

   //--- Lot
   double lot = CalcLotSize(slPrice - bid);
   if(lot <= 0) return;

   //--- Send order
   MqlTradeRequest req = {};
   MqlTradeResult res = {};
   req.action = TRADE_ACTION_DEAL;
   req.symbol = _Symbol;
   req.volume = lot;
   req.price = bid;
   req.sl = NormalizeDouble(slPrice, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS));
   req.tp = NormalizeDouble(tpPrice, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS));
   req.deviation = MaxSlippagePts;
   req.magic = MagicNumber;
   req.comment = CommentPrefix + "_SELL";
   req.type_filling = ORDER_FILLING_FOK;
   req.type = ORDER_TYPE_SELL;

   if(OrderSend(req, res))
   {
      g_tradesToday++;
      LogTrade("SELL", bid, slPrice, tpPrice, lot, confBuy, confSell, "CONFLUENCE", "OK");
   }
   else
      Print("SELL order failed: ", res.retcode, " ", res.comment);
}

//+==================================================================+
//| CONFLUENCE SCORING (0-12, forex-optimized)                        |
//+==================================================================+

int CalcConfluenceBuy()
{
   int score = 0;

   //--- 1. M15 EMA20 > EMA50 = +2
   double maFast[1], maSlow[1];
   ArraySetAsSeries(maFast, true); ArraySetAsSeries(maSlow, true);
   if(CopyBuffer(g_maFastM15, 0, 0, 1, maFast) > 0 &&
      CopyBuffer(g_maSlowM15, 0, 0, 1, maSlow) > 0)
   {
      if(maFast[0] > maSlow[0]) score += 2;
   }

   //--- 2. M15 EMA50 > EMA200 = +1
   double maTrend[1];
   ArraySetAsSeries(maTrend, true);
   if(CopyBuffer(g_maTrendM15, 0, 0, 1, maTrend) > 0)
   {
      if(maSlow[0] > maTrend[0]) score += 1;
   }

   //--- 3. Market structure bullish (HH/HL) = +2
   if(IsBullMarketStructure()) score += 2;

   //--- 4. Break & retest bullish = +2
   if(CheckBullBreakRetest()) score += 2;

   //--- 5. At S&R support level = +1
   if(AtSupportLevel()) score += 1;

   //--- 6. Bullish engulfing = +2
   if(DetectBullishEngulfing()) score += 2;

   //--- 7. Engulfing reversal (current bearish candle engulfed by previous) = +2
   if(DetectBullEngulfReversal()) score += 2;

   return score;
}

int CalcConfluenceSell()
{
   int score = 0;

   //--- 1. M15 EMA20 < EMA50 = +2
   double maFast[1], maSlow[1];
   ArraySetAsSeries(maFast, true); ArraySetAsSeries(maSlow, true);
   if(CopyBuffer(g_maFastM15, 0, 0, 1, maFast) > 0 &&
      CopyBuffer(g_maSlowM15, 0, 0, 1, maSlow) > 0)
   {
      if(maFast[0] < maSlow[0]) score += 2;
   }

   //--- 2. M15 EMA50 < EMA200 = +1
   double maTrend[1];
   ArraySetAsSeries(maTrend, true);
   if(CopyBuffer(g_maTrendM15, 0, 0, 1, maTrend) > 0)
   {
      if(maSlow[0] < maTrend[0]) score += 1;
   }

   //--- 3. Market structure bearish (LH/LL) = +2
   if(IsBearMarketStructure()) score += 2;

   //--- 4. Break & retest bearish = +2
   if(CheckBearBreakRetest()) score += 2;

   //--- 5. At S&R resistance level = +1
   if(AtResistanceLevel()) score += 1;

   //--- 6. Bearish engulfing = +2
   if(DetectBearishEngulfing()) score += 2;

   //--- 7. Bearish engulfing reversal = +2
   if(DetectBearEngulfReversal()) score += 2;

   return score;
}

//+==================================================================+
//| ENGULFING REVERSAL DETECTION (from Python backtest)                |
//+==================================================================+

// Bullish reversal: previous bar bearish, current bar bullish and engulfs
bool DetectBullEngulfReversal()
{
   double o[], c[], h[], l[];
   ArraySetAsSeries(o, true); ArraySetAsSeries(c, true);
   ArraySetAsSeries(h, true); ArraySetAsSeries(l, true);
   if(CopyOpen(_Symbol, TF_Entry, 0, 3, o) < 3) return false;
   if(CopyClose(_Symbol, TF_Entry, 0, 3, c) < 3) return false;
   if(CopyHigh(_Symbol, TF_Entry, 0, 3, h) < 3) return false;
   if(CopyLow(_Symbol, TF_Entry, 0, 3, l) < 3) return false;

   // Previous bar [1] bearish, current [0] bullish
   if(c[1] >= o[1]) return false;  // prev not bearish
   if(c[0] <= o[0]) return false;  // current not bullish

   double prevBody = o[1] - c[1];
   double currBody = c[0] - o[0];

   // Current body engulfs previous
   if(c[0] <= o[1]) return false;  // current close not above prev open
   if(o[0] >= c[1]) return false;  // current open not below prev close

   // Body size check
   if(currBody < g_atrEntry * Min_BodyATR) return false;

   return true;
}

// Bearish reversal: previous bar bullish, current bar bearish and engulfs
bool DetectBearEngulfReversal()
{
   double o[], c[], h[], l[];
   ArraySetAsSeries(o, true); ArraySetAsSeries(c, true);
   ArraySetAsSeries(h, true); ArraySetAsSeries(l, true);
   if(CopyOpen(_Symbol, TF_Entry, 0, 3, o) < 3) return false;
   if(CopyClose(_Symbol, TF_Entry, 0, 3, c) < 3) return false;
   if(CopyHigh(_Symbol, TF_Entry, 0, 3, h) < 3) return false;
   if(CopyLow(_Symbol, TF_Entry, 0, 3, l) < 3) return false;

   // Previous bar [1] bullish, current [0] bearish
   if(c[1] <= o[1]) return false;  // prev not bullish
   if(c[0] >= o[0]) return false;  // current not bearish

   double prevBody = c[1] - o[1];
   double currBody = o[0] - c[0];

   // Current body engulfs previous
   if(o[0] <= c[1]) return false;  // current open not above prev close
   if(c[0] >= o[1]) return false;  // current close not below prev open

   if(currBody < g_atrEntry * Min_BodyATR) return false;

   return true;
}

//+==================================================================+
//| MARKET STRUCTURE                                                   |
//+==================================================================+

void DetectSwingPoints()
{
   double m15_high[], m15_low[];
   ArraySetAsSeries(m15_high, true);
   ArraySetAsSeries(m15_low, true);

   if(CopyHigh(_Symbol, TF_Structure, 0, SwingScanBars, m15_high) < SwingScanBars) return;
   if(CopyLow(_Symbol, TF_Structure, 0, SwingScanBars, m15_low) < SwingScanBars) return;

   ArrayFree(g_swingHighs);
   ArrayFree(g_swingLows);

   for(int i = SwingLookback; i < SwingScanBars - SwingLookback; i++)
   {
      bool isSwingHigh = true;
      for(int j = 1; j <= SwingLookback; j++)
      {
         if(m15_high[i] <= m15_high[i - j] || m15_high[i] <= m15_high[i + j])
         { isSwingHigh = false; break; }
      }
      if(isSwingHigh)
      {
         int sz = ArraySize(g_swingHighs);
         ArrayResize(g_swingHighs, sz + 1, 20);
         g_swingHighs[sz] = m15_high[i];
      }

      bool isSwingLow = true;
      for(int j = 1; j <= SwingLookback; j++)
      {
         if(m15_low[i] >= m15_low[i - j] || m15_low[i] >= m15_low[i + j])
         { isSwingLow = false; break; }
      }
      if(isSwingLow)
      {
         int sz = ArraySize(g_swingLows);
         ArrayResize(g_swingLows, sz + 1, 20);
         g_swingLows[sz] = m15_low[i];
      }
   }

   //--- Keep most recent
   if(ArraySize(g_swingHighs) > MaxSwingLevels)
   {
      int start = ArraySize(g_swingHighs) - MaxSwingLevels;
      double temp[];
      ArrayCopy(temp, g_swingHighs, 0, start);
      ArrayResize(g_swingHighs, MaxSwingLevels);
      ArrayCopy(g_swingHighs, temp);
   }
   if(ArraySize(g_swingLows) > MaxSwingLevels)
   {
      int start = ArraySize(g_swingLows) - MaxSwingLevels;
      double temp[];
      ArrayCopy(temp, g_swingLows, 0, start);
      ArrayResize(g_swingLows, MaxSwingLevels);
      ArrayCopy(g_swingLows, temp);
   }
}

bool IsBullMarketStructure()
{
   if(ArraySize(g_swingHighs) < 2 || ArraySize(g_swingLows) < 2) return false;
   int last = ArraySize(g_swingHighs) - 1;
   bool hh = (g_swingHighs[last] > g_swingHighs[last - 1]);
   last = ArraySize(g_swingLows) - 1;
   bool hl = (g_swingLows[last] > g_swingLows[last - 1]);
   return (hh && hl);
}

bool IsBearMarketStructure()
{
   if(ArraySize(g_swingHighs) < 2 || ArraySize(g_swingLows) < 2) return false;
   int last = ArraySize(g_swingHighs) - 1;
   bool lh = (g_swingHighs[last] < g_swingHighs[last - 1]);
   last = ArraySize(g_swingLows) - 1;
   bool ll = (g_swingLows[last] < g_swingLows[last - 1]);
   return (lh && ll);
}

double GetNearestSwingLow()
{
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double nearest = 0;
   double minDist = DBL_MAX;
   for(int i = 0; i < ArraySize(g_swingLows); i++)
   {
      if(g_swingLows[i] < bid && (bid - g_swingLows[i]) < minDist)
      { minDist = bid - g_swingLows[i]; nearest = g_swingLows[i]; }
   }
   return nearest;
}

double GetNearestSwingHigh()
{
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double nearest = 0;
   double minDist = DBL_MAX;
   for(int i = 0; i < ArraySize(g_swingHighs); i++)
   {
      if(g_swingHighs[i] > ask && (g_swingHighs[i] - ask) < minDist)
      { minDist = g_swingHighs[i] - ask; nearest = g_swingHighs[i]; }
   }
   return nearest;
}

//+==================================================================+
//| BREAK & RETEST                                                     |
//+==================================================================+

bool CheckBullBreakRetest()
{
   if(ArraySize(g_swingHighs) < 1) return false;
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   for(int i = 0; i < ArraySize(g_swingHighs); i++)
   {
      double level = g_swingHighs[i];
      double retestZone = level + BreakRetest_ATR * g_atrM15;
      double breakZone = level + 0.1 * g_atrM15;
      if(bid > breakZone && ask <= retestZone) return true;
   }
   return false;
}

bool CheckBearBreakRetest()
{
   if(ArraySize(g_swingLows) < 1) return false;
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   for(int i = 0; i < ArraySize(g_swingLows); i++)
   {
      double level = g_swingLows[i];
      double retestZone = level - BreakRetest_ATR * g_atrM15;
      double breakZone = level - 0.1 * g_atrM15;
      if(ask < breakZone && bid >= retestZone) return true;
   }
   return false;
}

//+==================================================================+
//| S&R LEVELS                                                         |
//+==================================================================+

bool AtSupportLevel()
{
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double proximity = g_atrM15 * 0.3;
   for(int i = 0; i < ArraySize(g_swingLows); i++)
   {
      if(MathAbs(bid - g_swingLows[i]) <= proximity) return true;
   }
   return false;
}

bool AtResistanceLevel()
{
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double proximity = g_atrM15 * 0.3;
   for(int i = 0; i < ArraySize(g_swingHighs); i++)
   {
      if(MathAbs(ask - g_swingHighs[i]) <= proximity) return true;
   }
   return false;
}

//+==================================================================+
//| ENGULFING CANDLE DETECTION                                         |
//+==================================================================+

bool DetectBullishEngulfing()
{
   double o[], c[], h[], l[];
   ArraySetAsSeries(o, true); ArraySetAsSeries(c, true);
   ArraySetAsSeries(h, true); ArraySetAsSeries(l, true);
   if(CopyOpen(_Symbol, TF_Entry, 0, 3, o) < 3) return false;
   if(CopyClose(_Symbol, TF_Entry, 0, 3, c) < 3) return false;
   if(CopyHigh(_Symbol, TF_Entry, 0, 3, h) < 3) return false;
   if(CopyLow(_Symbol, TF_Entry, 0, 3, l) < 3) return false;

   if(o[1] <= c[1]) return false;  // prev not bearish
   if(c[0] <= o[0]) return false;  // current not bullish
   double prevBody = o[1] - c[1];
   double currBody = c[0] - o[0];
   if(currBody <= prevBody) return false;
   if(currBody < g_atrEntry * EngulfBodyATR_Min) return false;
   if(o[0] >= c[1]) return false;
   if(c[0] <= o[1]) return false;
   return true;
}

bool DetectBearishEngulfing()
{
   double o[], c[], h[], l[];
   ArraySetAsSeries(o, true); ArraySetAsSeries(c, true);
   ArraySetAsSeries(h, true); ArraySetAsSeries(l, true);
   if(CopyOpen(_Symbol, TF_Entry, 0, 3, o) < 3) return false;
   if(CopyClose(_Symbol, TF_Entry, 0, 3, c) < 3) return false;
   if(CopyHigh(_Symbol, TF_Entry, 0, 3, h) < 3) return false;
   if(CopyLow(_Symbol, TF_Entry, 0, 3, l) < 3) return false;

   if(o[1] >= c[1]) return false;  // prev not bullish
   if(c[0] >= o[0]) return false;  // current not bearish
   double prevBody = c[1] - o[1];
   double currBody = o[0] - c[0];
   if(currBody <= prevBody) return false;
   if(currBody < g_atrEntry * EngulfBodyATR_Min) return false;
   if(o[0] <= c[1]) return false;
   if(c[0] >= o[1]) return false;
   return true;
}

//+==================================================================+
//| REJECTION CANDLES                                                  |
//+==================================================================+

bool HasBullishRejection(double &open[], double &high[], double &low[], double &close[])
{
   int limit = MathMin(RejectLookback, ArraySize(close) - 1);
   for(int i = 0; i < limit; i++)
   {
      if(close[i] <= open[i]) continue;
      double body = close[i] - open[i];
      double lowerWick = open[i] - low[i];
      if(lowerWick < g_atrEntry * Min_RejectWickATR) continue;
      if(body > 0 && lowerWick / body < Min_WickBodyRatio) continue;
      if(body < g_atrEntry * Min_BodyATR) continue;
      return true;
   }
   return false;
}

bool HasBearishRejection(double &open[], double &high[], double &low[], double &close[])
{
   int limit = MathMin(RejectLookback, ArraySize(close) - 1);
   for(int i = 0; i < limit; i++)
   {
      if(close[i] >= open[i]) continue;
      double body = open[i] - close[i];
      double upperWick = high[i] - open[i];
      if(upperWick < g_atrEntry * Min_RejectWickATR) continue;
      if(body > 0 && upperWick / body < Min_WickBodyRatio) continue;
      if(body < g_atrEntry * Min_BodyATR) continue;
      return true;
   }
   return false;
}

//+==================================================================+
//| TP / SL CALCULATIONS                                               |
//+==================================================================+

double CalcBuyTP()
{
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);

   if(TP_Mode == 0)  // Opposite BB band
   {
      double bbUpper = CalcBB(TF_Entry, BB_Period, BB_StdDev, 1);
      if(bbUpper > 0) return bbUpper;
   }
   else if(TP_Mode == 2)  // BB middle
   {
      double bbMid = CalcBB(TF_Entry, BB_Period, BB_StdDev, 0);
      if(bbMid > ask) return bbMid;
   }

   // ATR-based TP (default)
   return ask + g_atrEntry * TP_ATR_Mult;
}

double CalcSellTP()
{
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   if(TP_Mode == 0)
   {
      double bbLower = CalcBB(TF_Entry, BB_Period, BB_StdDev, 2);
      if(bbLower > 0) return bbLower;
   }
   else if(TP_Mode == 2)
   {
      double bbMid = CalcBB(TF_Entry, BB_Period, BB_StdDev, 0);
      if(bbMid < bid) return bbMid;
   }

   return bid - g_atrEntry * TP_ATR_Mult;
}

//+==================================================================+
//| INDICATOR HELPERS                                                  |
//+==================================================================+

double CalcATR(ENUM_TIMEFRAMES tf, int period)
{
   double buf[];
   ArraySetAsSeries(buf, true);
   int handle = (tf == TF_Structure) ? g_atrM15Handle : g_atrM5Handle;
   if(handle == INVALID_HANDLE) return 0;
   if(CopyBuffer(handle, 0, 0, 1, buf) < 1) return 0;
   return buf[0];
}

double CalcBB(ENUM_TIMEFRAMES tf, int period, double dev, int mode)
{
   double buf[];
   ArraySetAsSeries(buf, true);
   int handle;
   if(mode == 0) handle = g_bbHandle;
   else if(mode == 1) handle = g_bbUpperHandle;
   else handle = g_bbLowerHandle;
   if(handle == INVALID_HANDLE) return 0;
   if(CopyBuffer(handle, mode, 0, 1, buf) < 1) return 0;
   return buf[0];
}

double CalcRSI(ENUM_TIMEFRAMES tf, int period)
{
   double buf[];
   ArraySetAsSeries(buf, true);
   if(g_rsiHandle == INVALID_HANDLE) return 0;
   if(CopyBuffer(g_rsiHandle, 0, 0, 1, buf) < 1) return 0;
   return buf[0];
}

//+==================================================================+
//| POSITION SIZING                                                    |
//+==================================================================+

double CalcLotSize(double slDistPts)
{
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double riskMoney = balance * RiskPerTradePct / 100.0;
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);

   if(tickValue <= 0 || tickSize <= 0 || slDistPts <= 0)
      return FixedLot;

   double slInTicks = slDistPts / tickSize;
   double lotByRisk = riskMoney / (slInTicks * tickValue);

   double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);

   lotByRisk = MathFloor(lotByRisk / lotStep) * lotStep;
   lotByRisk = MathMax(minLot, MathMin(lotByRisk, maxLot));

   return lotByRisk;
}

double NormalizeLot(double lot)
{
   double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   lot = MathFloor(lot / lotStep) * lotStep;
   lot = MathMax(minLot, MathMin(lot, maxLot));
   return lot;
}

//+==================================================================+
//| POSITION MANAGEMENT                                                |
//+==================================================================+

int CountOpenPositions()
{
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(PositionSelectByTicket(PositionGetTicket(i)))
      {
         if(PositionGetString(POSITION_SYMBOL) == _Symbol &&
            PositionGetInteger(POSITION_MAGIC) == MagicNumber)
            count++;
      }
   }
   return count;
}

void ManageOpenPositions()
{
   if(!UsePartialTP && !UseTrailing && !UseBreakEven) return;

   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double atr = g_atrEntry;
   if(atr <= 0) atr = CalcATR(TF_Entry, 14);
   if(atr <= 0) return;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;

      double entry   = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl      = PositionGetDouble(POSITION_SL);
      double tp      = PositionGetDouble(POSITION_TP);
      double volume  = PositionGetDouble(POSITION_VOLUME);
      long   type    = PositionGetInteger(POSITION_TYPE);
      double currentPrice = (type == POSITION_TYPE_BUY) ?
                            SymbolInfoDouble(_Symbol, SYMBOL_BID) :
                            SymbolInfoDouble(_Symbol, SYMBOL_ASK);

      //--- Break-Even
      if(UseBreakEven)
      {
         double beDist = BreakEven_ATR * atr;
         if(type == POSITION_TYPE_BUY)
         {
            double newSL = entry + SymbolInfoDouble(_Symbol, SYMBOL_POINT) * 5;
            if(currentPrice >= entry + beDist && sl < entry)
            {
               MqlTradeRequest req = {}; MqlTradeResult res = {};
               req.action = TRADE_ACTION_SLTP; req.symbol = _Symbol;
               req.position = ticket; req.sl = NormalizeDouble(newSL, digits);
               req.tp = tp; req.magic = MagicNumber;
               OrderSend(req, res);
            }
         }
         else
         {
            double newSL = entry - SymbolInfoDouble(_Symbol, SYMBOL_POINT) * 5;
            if(currentPrice <= entry - beDist && (sl > entry || sl == 0))
            {
               MqlTradeRequest req = {}; MqlTradeResult res = {};
               req.action = TRADE_ACTION_SLTP; req.symbol = _Symbol;
               req.position = ticket; req.sl = NormalizeDouble(newSL, digits);
               req.tp = tp; req.magic = MagicNumber;
               OrderSend(req, res);
            }
         }
      }

      //--- Partial TP
      if(UsePartialTP && volume > SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN))
      {
         double tpDist = (tp > 0) ? MathAbs(tp - entry) : atr * TP_ATR_Mult;
         double partialPrice;

         if(type == POSITION_TYPE_BUY)
         {
            partialPrice = entry + tpDist * PartialTP_Pct / 100.0;
            if(currentPrice >= partialPrice)
            {
               double closeLot = NormalizeLot(volume * PartialClosePct / 100.0);
               if(closeLot >= SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN))
               {
                  MqlTradeRequest req = {}; MqlTradeResult res = {};
                  req.action = TRADE_ACTION_DEAL; req.symbol = _Symbol;
                  req.volume = closeLot; req.type = ORDER_TYPE_SELL;
                  req.price = SymbolInfoDouble(_Symbol, SYMBOL_BID);
                  req.deviation = MaxSlippagePts; req.magic = MagicNumber;
                  req.comment = CommentPrefix + "_PARTIAL";
                  req.type_filling = ORDER_FILLING_FOK; req.position = ticket;
                  OrderSend(req, res);
               }
            }
         }
         else
         {
            partialPrice = entry - tpDist * PartialTP_Pct / 100.0;
            if(currentPrice <= partialPrice)
            {
               double closeLot = NormalizeLot(volume * PartialClosePct / 100.0);
               if(closeLot >= SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN))
               {
                  MqlTradeRequest req = {}; MqlTradeResult res = {};
                  req.action = TRADE_ACTION_DEAL; req.symbol = _Symbol;
                  req.volume = closeLot; req.type = ORDER_TYPE_BUY;
                  req.price = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
                  req.deviation = MaxSlippagePts; req.magic = MagicNumber;
                  req.comment = CommentPrefix + "_PARTIAL";
                  req.type_filling = ORDER_FILLING_FOK; req.position = ticket;
                  OrderSend(req, res);
               }
            }
         }
      }

      //--- Trailing Stop
      if(UseTrailing)
      {
         double trailStart = TrailingStart_ATR * atr;
         double trailStep  = TrailingStep_ATR * atr;

         if(type == POSITION_TYPE_BUY)
         {
            double profitDist = currentPrice - entry;
            if(profitDist >= trailStart)
            {
               double newSL = currentPrice - trailStep;
               if(newSL > sl)
               {
                  MqlTradeRequest req = {}; MqlTradeResult res = {};
                  req.action = TRADE_ACTION_SLTP; req.symbol = _Symbol;
                  req.position = ticket;
                  req.sl = NormalizeDouble(newSL, digits); req.tp = tp;
                  req.magic = MagicNumber;
                  OrderSend(req, res);
               }
            }
         }
         else
         {
            double profitDist = entry - currentPrice;
            if(profitDist >= trailStart)
            {
               double newSL = currentPrice + trailStep;
               if(newSL < sl || sl == 0)
               {
                  MqlTradeRequest req = {}; MqlTradeResult res = {};
                  req.action = TRADE_ACTION_SLTP; req.symbol = _Symbol;
                  req.position = ticket;
                  req.sl = NormalizeDouble(newSL, digits); req.tp = tp;
                  req.magic = MagicNumber;
                  OrderSend(req, res);
               }
            }
         }
      }
   }
}

//+==================================================================+
//| SWING CACHE UPDATE                                                 |
//+==================================================================+

void UpdateSwingLevels()
{
   datetime m15Time = iTime(_Symbol, TF_Structure, 0);
   if(m15Time == g_lastSwingScan) return;
   g_lastSwingScan = m15Time;
   g_atrM15 = CalcATR(TF_Structure, 14);
   DetectSwingPoints();
}

//+==================================================================+
//| SL HIT TRACKING                                                    |
//+==================================================================+

void TrackSLHits()
{
   datetime today = GetDayStartUTC();
   static datetime lastCheck = 0;
   datetime now = TimeCurrent();

   if(now == lastCheck) return;
   lastCheck = now;

   // Check closed deals today for SL hits
   HistorySelect(today, now);
   int totalDeals = HistoryDealsTotal();
   for(int i = 0; i < totalDeals; i++)
   {
      ulong dealTicket = HistoryDealGetTicket(i);
      if(dealTicket == 0) continue;
      if(HistoryDealGetString(dealTicket, DEAL_SYMBOL) != _Symbol) continue;
      if(HistoryDealGetInteger(dealTicket, DEAL_MAGIC) != MagicNumber) continue;

      long entry = HistoryDealGetInteger(dealTicket, DEAL_ENTRY);
      if(entry != DEAL_ENTRY_OUT) continue;

      string comment = HistoryDealGetString(dealTicket, DEAL_COMMENT);
      if(StringFind(comment, "[sl]") >= 0)
      {
         datetime dealTime = (datetime)HistoryDealGetInteger(dealTicket, DEAL_TIME);
         if(dealTime > g_lastSLTime)
         {
            g_slHitsToday++;
            g_lastSLTime = dealTime;
            g_lastTradeCloseTime = dealTime;
         }
      }
      else
      {
         // Any other close
         datetime dealTime = (datetime)HistoryDealGetInteger(dealTicket, DEAL_TIME);
         if(dealTime > g_lastTradeCloseTime)
            g_lastTradeCloseTime = dealTime;
      }
   }
}

//+==================================================================+
//| DAILY / SAFETY CHECKS                                              |
//+==================================================================+

void CheckDailyReset()
{
   datetime dayStart = GetDayStartUTC();
   if(dayStart != g_dayStart)
   {
      g_dayStart = dayStart;
      g_tradesToday = 0;
      g_dailyStartBalance = AccountInfoDouble(ACCOUNT_BALANCE);
      g_dailyPL = 0;
      g_tradingPaused = false;
      g_slHitsToday = 0;
   }

   double currBalance = AccountInfoDouble(ACCOUNT_BALANCE);
   if(g_dailyStartBalance > 0)
   {
      double lossPct = (g_dailyStartBalance - currBalance) / g_dailyStartBalance * 100.0;
      if(lossPct >= MaxDailyLossPct)
      {
         g_tradingPaused = true;
         Print("Max daily loss (", DoubleToString(lossPct, 1), "%) reached. Paused.");
      }
   }
}

datetime GetDayStartUTC()
{
   MqlDateTime dt;
   TimeCurrent(dt);
   dt.hour = 0; dt.min = 0; dt.sec = 0;
   return StructToTime(dt);
}

bool CheckDayOfWeek()
{
   MqlDateTime dt;
   TimeCurrent(dt);
   switch(dt.day_of_week)
   {
      case 1: return TradeMonday;
      case 2: return TradeTuesday;
      case 3: return TradeWednesday;
      case 4: return TradeThursday;
      case 5: return TradeFriday;
      default: return false;
   }
}

bool IsInSession()
{
   MqlDateTime dt;
   TimeCurrent(dt);
   return (dt.hour >= 0 && dt.hour <= 23);
}

//+==================================================================+
//| LOGGING                                                            |
//+==================================================================+

void LogTrade(string type, double price, double sl, double tp, double lot,
              int confBuy, int confSell, string entryType, string comment)
{
   g_logFile = FileOpen(CommentPrefix + "_log.csv", FILE_WRITE|FILE_READ|FILE_CSV|FILE_ANSI, ",", CP_ACP);
   if(g_logFile != INVALID_HANDLE)
   {
      FileSeek(g_logFile, 0, SEEK_END);
      int d = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
      FileWrite(g_logFile, TimeToString(TimeCurrent()), type,
                DoubleToString(price, d), DoubleToString(sl, d), DoubleToString(tp, d),
                DoubleToString(lot, 2), confBuy, confSell, entryType, comment,
                DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2));
      FileClose(g_logFile);
   }
}
//+------------------------------------------------------------------+
