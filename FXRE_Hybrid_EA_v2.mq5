//+------------------------------------------------------------------+
//|                                      FXRE_Hybrid_EA_v2.mq5       |
//|               FXRE Hybrid v2.0 — Asian Range Liquidity Sweep     |
//|               XAU/USD | M5 execution | H4/D1 trend alignment     |
//+------------------------------------------------------------------+
//| Strategy based on GOLD.txt institutional liquidity sweep model:  |
//| 1. Mark Asian session range (00:00-07:00 GMT)                   |
//| 2. Detect liquidity sweep at London/NY open                      |
//| 3. Enter after rejection back inside range                        |
//| 4. Secondary: HTF trend pullback into S&D zones                  |
//+------------------------------------------------------------------+
#property copyright "FXRE Hybrid v2.0"
#property version   "2.00"
#property description "Asian Range Liquidity Sweep + HTF Trend Pullback for XAU/USD"

//--- Include module headers

// ================================================================
// ================================================================
// === Session Filter Input Parameters ===
// ================================================================
input bool     UseSessionFilter    = true;       // Enable session/time filter
input bool     TradeMonday         = true;       // Allow trading on Monday
input bool     TradeTuesday        = true;       // Allow trading on Tuesday
input bool     TradeWednesday      = true;       // Allow trading on Wednesday
input bool     TradeThursday       = true;       // Allow trading on Thursday
input bool     TradeFriday         = true;       // Allow trading on Friday

// ================================================================
// === FXRE_SessionFilter.mqh (inlined) ===
// ================================================================
// ================================================================

//+------------------------------------------------------------------+
//|                                      FXRE_SessionFilter_v2.mqh   |
//|               FXRE Hybrid v2.0 — GMT Session Windows             |
//|               Asian Range / London Sweep / NY Overlap             |
//+------------------------------------------------------------------+
//| v2.0: Added GMT conversion, Asian range window, London sweep,    |
//|       NY overlap session logic for liquidity sweep strategy       |
//+------------------------------------------------------------------+

//+------------------------------------------------------------------+
//| Get current hour/minute in GMT                                   |
//+------------------------------------------------------------------+
int GMTHour()
{
   MqlDateTime dt;
   TimeTradeServer(dt);
   // Server time may be broker time; offset to GMT
   // Most brokers use GMT+2 or GMT+3; adjust as needed
   int gmtHour = dt.hour - 2;  // Adjust for GMT+2 broker (VantageMarkets)
   if(gmtHour >= 24) gmtHour -= 24;
   if(gmtHour < 0)   gmtHour += 24;
   return gmtHour;
}

int GMTMin()
{
   MqlDateTime dt;
   TimeTradeServer(dt);
   return dt.min;
}

int GMTDayOfWeek()
{
   MqlDateTime dt;
   TimeTradeServer(dt);
   int gmtHour = dt.hour - 2;
   int gmtDow  = dt.day_of_week;
   if(gmtHour >= 24) { gmtDow++; if(gmtDow > 6) gmtDow = 0; }
   if(gmtHour < 0)   { gmtDow--; if(gmtDow < 0) gmtDow = 6; }
   return gmtDow;
}

//+------------------------------------------------------------------+
//| Session window checks (all in GMT)                               |
//+------------------------------------------------------------------+

// Asian session: 00:00 - 07:00 GMT (range building, NO trading)
bool IsAsianSession()
{
   int h = GMTHour();
   return (h >= 0 && h < 7);
}

// London open sweep window: 07:00 - 10:00 GMT (trade sweeps)
bool IsLondonSweepWindow()
{
   int h = GMTHour();
   return (h >= 7 && h < 10);
}

// London/NY overlap: 12:00 - 16:00 GMT (trade sweeps + HTF pullbacks)
bool IsOverlapWindow()
{
   int h = GMTHour();
   return (h >= 12 && h < 16);
}

// Any active trading window
bool IsTradingWindow()
{
   return IsLondonSweepWindow() || IsOverlapWindow();
}

//+------------------------------------------------------------------+
//| Legacy session check (kept for backward compatibility)           |
//+------------------------------------------------------------------+
bool IsInSession()
{
   if(!UseSessionFilter) return true;
   return IsTradingWindow();
}

//+------------------------------------------------------------------+
//| Check if current day is valid for trading                        |
//+------------------------------------------------------------------+
bool IsTradingDay()
{
   if(!UseSessionFilter) return true;
   int dow = GMTDayOfWeek();
   switch(dow)
   {
      case 1: return TradeMonday;
      case 2: return TradeTuesday;
      case 3: return TradeWednesday;
      case 4: return TradeThursday;
      case 5: return TradeFriday;
      default: return false;
   }
}

//+------------------------------------------------------------------+
//| Combined check                                                   |
//+------------------------------------------------------------------+
bool ShouldTradeNow()
{
   if(!UseSessionFilter) return true;
   return IsTradingDay() && IsTradingWindow();
}

//+------------------------------------------------------------------+
//| Status string                                                    |
//+------------------------------------------------------------------+
string GetSessionStatus()
{
   if(!UseSessionFilter) return "No filter";

   int h = GMTHour();
   int m = GMTMin();
   int dow = GMTDayOfWeek();
   string dayNames[] = {"Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"};
   string status = dayNames[dow] + " " + IntegerToString(h) + ":" + StringFormat("%02d", m) + " GMT | ";

   if(!IsTradingDay())
      status += "NOT A TRADING DAY";
   else if(IsAsianSession())
      status += "ASIAN SESSION (range building)";
   else if(IsLondonSweepWindow())
      status += "LONDON SWEEP WINDOW (active)";
   else if(IsOverlapWindow())
      status += "NY OVERLAP (active)";
   else
      status += "OUTSIDE SESSION";

   return status;
}
//+------------------------------------------------------------------+


// ================================================================
// === FXRE_SwingSD.mqh (inlined) ===
// ================================================================

//+------------------------------------------------------------------+
//|                                      FXRE_SwingSD_v2.mqh         |
//|               FXRE Hybrid v2.0 — Swing S&D + FVG Zones           |
//+------------------------------------------------------------------+
//| v2.0: Added Fair Value Gap (FVG) detection for entry zones       |
//| Detects swing highs/lows on M15, clusters into S&D zones,       |
//| and detects M5 FVGs for precision entries                        |
//+------------------------------------------------------------------+

//--- Swing-point S&D structure
struct SwingSDZone
{
   datetime   formationTime;
   double     priceHigh;
   double     priceLow;
   double     priceMid;
   bool       isDemand;      // true=demand(buy), false=supply(sell)
   double     strength;      // 1.0 to 5.0 (higher = stronger)
   int        ageCandles;    // candles since last swing in cluster
   int        swingCount;    // number of swings clustered
   double     zoneWidth;     // priceHigh - priceLow
};

//--- Fair Value Gap structure
struct FVGZone
{
   datetime   time;          // Time of the middle candle
   double     priceHigh;     // Upper boundary of gap
   double     priceLow;      // Lower boundary of gap
   double     priceMid;      // Midpoint
   bool       isBullish;     // true=bullish FVG (buy), false=bearish FVG (sell)
   double     sizeATR;       // Gap size as multiple of ATR
   int        ageCandles;    // Bars since formation
};

//--- Module state
SwingSDZone g_swingBullish[];   // Demand zones
SwingSDZone g_swingBearish[];   // Supply zones
int   g_swingBullishTotal = 0;
int   g_swingBearishTotal = 0;

FVGZone g_fvgBullish[];         // Bullish FVGs
FVGZone g_fvgBearish[];         // Bearish FVGs
int   g_fvgBullishTotal = 0;
int   g_fvgBearishTotal = 0;

//+------------------------------------------------------------------+
//| Detect swing points & build zones                                |
//| Returns total zone count                                         |
//+------------------------------------------------------------------+
int DetectSwingZones(ENUM_TIMEFRAMES tf, int lookbackBars, int swingLookback,
                     double clusterPoints, int maxAge, double minStrength)
{
   ArrayFree(g_swingBullish);
   ArrayFree(g_swingBearish);
   g_swingBullishTotal = 0;
   g_swingBearishTotal = 0;

   if(lookbackBars < 20) return 0;

   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   int copied = CopyRates(_Symbol, tf, 0, lookbackBars + swingLookback * 2 + 5, rates);
   if(copied < lookbackBars) return 0;

   int look = swingLookback;
   double clusterThresh = clusterPoints;

   //--- Collect raw swing points
   struct RawSwing { double price; int idx; int strength; bool isDemand; };
   RawSwing rawSwings[];
   int rawCount = 0;
   ArrayResize(rawSwings, 5000);

   for(int i = look; i < lookbackBars - look; i++)
   {
      //--- Swing high (supply potential)
      bool isHigh = true;
      for(int k = 1; k <= look; k++)
      {
         if(rates[i].high < rates[i - k].high ||
            rates[i].high < rates[i + k].high ||
            rates[i].high <= rates[i - 1].high)
         { isHigh = false; break; }
      }
      if(isHigh)
      {
         int str = 1;
         for(int k = 1; k <= look; k++)
            if(rates[i].close > rates[i + k].close) str++;
         rawSwings[rawCount].price   = rates[i].high;
         rawSwings[rawCount].idx     = i;
         rawSwings[rawCount].strength = MathMin(str, 5);
         rawSwings[rawCount].isDemand = false;
         rawCount++;
      }

      //--- Swing low (demand potential)
      bool isLow = true;
      for(int k = 1; k <= look; k++)
      {
         if(rates[i].low > rates[i - k].low ||
            rates[i].low > rates[i + k].low ||
            rates[i].low >= rates[i - 1].low)
         { isLow = false; break; }
      }
      if(isLow)
      {
         int str = 1;
         for(int k = 1; k <= look; k++)
            if(rates[i].close < rates[i + k].close) str++;
         rawSwings[rawCount].price   = rates[i].low;
         rawSwings[rawCount].idx     = i;
         rawSwings[rawCount].strength = MathMin(str, 5);
         rawSwings[rawCount].isDemand = true;
         rawCount++;
      }
   }

   if(rawCount == 0) return 0;

   //--- Sort raw swings by price
   bool swapped = true;
   while(swapped)
   {
      swapped = false;
      for(int i = 0; i < rawCount - 1; i++)
      {
         if(rawSwings[i].price > rawSwings[i + 1].price)
         {
            RawSwing t = rawSwings[i]; rawSwings[i] = rawSwings[i + 1]; rawSwings[i + 1] = t;
            swapped = true;
         }
      }
   }

   //--- Cluster nearby swings into zones (separate demand/supply)
   SwingSDZone tempDZ[], tempSZ[];
   int dzCount = 0, szCount = 0;
   ArrayResize(tempDZ, rawCount);
   ArrayResize(tempSZ, rawCount);

   // Cluster demand
   for(int i = 0; i < rawCount; i++)
   {
      if(!rawSwings[i].isDemand) continue;
      if(dzCount == 0 || rawSwings[i].price - tempDZ[dzCount - 1].priceHigh > clusterThresh)
      {
         tempDZ[dzCount].formationTime = rates[rawSwings[i].idx].time;
         tempDZ[dzCount].priceHigh = rawSwings[i].price;
         tempDZ[dzCount].priceLow  = rawSwings[i].price;
         tempDZ[dzCount].priceMid  = rawSwings[i].price;
         tempDZ[dzCount].isDemand  = true;
         tempDZ[dzCount].strength  = (double)rawSwings[i].strength;
         tempDZ[dzCount].ageCandles = rawSwings[i].idx;
         tempDZ[dzCount].swingCount = 1;
         tempDZ[dzCount].zoneWidth  = 0;
         dzCount++;
      }
      else
      {
         int ci = dzCount - 1;
         if(rawSwings[i].price > tempDZ[ci].priceHigh) tempDZ[ci].priceHigh = rawSwings[i].price;
         if(rawSwings[i].price < tempDZ[ci].priceLow)  tempDZ[ci].priceLow  = rawSwings[i].price;
         tempDZ[ci].priceMid = (tempDZ[ci].priceHigh + tempDZ[ci].priceLow) / 2.0;
         tempDZ[ci].strength = (tempDZ[ci].strength * tempDZ[ci].swingCount + rawSwings[i].strength)
                              / (tempDZ[ci].swingCount + 1);
         tempDZ[ci].swingCount++;
         if(rawSwings[i].idx > tempDZ[ci].ageCandles)
            tempDZ[ci].ageCandles = rawSwings[i].idx;
         tempDZ[ci].zoneWidth = tempDZ[ci].priceHigh - tempDZ[ci].priceLow;
      }
   }

   // Cluster supply
   for(int i = 0; i < rawCount; i++)
   {
      if(rawSwings[i].isDemand) continue;
      if(szCount == 0 || rawSwings[i].price - tempSZ[szCount - 1].priceHigh > clusterThresh)
      {
         tempSZ[szCount].formationTime = rates[rawSwings[i].idx].time;
         tempSZ[szCount].priceHigh = rawSwings[i].price;
         tempSZ[szCount].priceLow  = rawSwings[i].price;
         tempSZ[szCount].priceMid  = rawSwings[i].price;
         tempSZ[szCount].isDemand  = false;
         tempSZ[szCount].strength  = (double)rawSwings[i].strength;
         tempSZ[szCount].ageCandles = rawSwings[i].idx;
         tempSZ[szCount].swingCount = 1;
         tempSZ[szCount].zoneWidth  = 0;
         szCount++;
      }
      else
      {
         int ci = szCount - 1;
         if(rawSwings[i].price > tempSZ[ci].priceHigh) tempSZ[ci].priceHigh = rawSwings[i].price;
         if(rawSwings[i].price < tempSZ[ci].priceLow)  tempSZ[ci].priceLow  = rawSwings[i].price;
         tempSZ[ci].priceMid = (tempSZ[ci].priceHigh + tempSZ[ci].priceLow) / 2.0;
         tempSZ[ci].strength = (tempSZ[ci].strength * tempSZ[ci].swingCount + rawSwings[i].strength)
                              / (tempSZ[ci].swingCount + 1);
         tempSZ[ci].swingCount++;
         if(rawSwings[i].idx > tempSZ[ci].ageCandles)
            tempSZ[ci].ageCandles = rawSwings[i].idx;
         tempSZ[ci].zoneWidth = tempSZ[ci].priceHigh - tempSZ[ci].priceLow;
      }
   }

   //--- Filter by age, strength — copy to global arrays
   for(int i = 0; i < dzCount; i++)
   {
      if(tempDZ[i].ageCandles <= maxAge && tempDZ[i].strength >= minStrength)
      {
         ArrayResize(g_swingBullish, g_swingBullishTotal + 1, 20);
         g_swingBullish[g_swingBullishTotal] = tempDZ[i];
         g_swingBullishTotal++;
      }
   }
   for(int i = 0; i < szCount; i++)
   {
      if(tempSZ[i].ageCandles <= maxAge && tempSZ[i].strength >= minStrength)
      {
         ArrayResize(g_swingBearish, g_swingBearishTotal + 1, 20);
         g_swingBearish[g_swingBearishTotal] = tempSZ[i];
         g_swingBearishTotal++;
      }
   }

   SortSwingZones(g_swingBullish, g_swingBullishTotal, true);
   SortSwingZones(g_swingBearish, g_swingBearishTotal, true);

   return g_swingBullishTotal + g_swingBearishTotal;
}

//+------------------------------------------------------------------+
//| Detect Fair Value Gaps on M5                                     |
//| FVG = 3-candle pattern where candle[2].low > candle[0].high (bull)|
//|       or candle[0].low > candle[2].high (bear)                   |
//| Returns total FVG count                                          |
//+------------------------------------------------------------------+
int DetectFVGs(ENUM_TIMEFRAMES tf, int lookbackBars, double minSizeATR, double atrValue)
{
   ArrayFree(g_fvgBullish);
   ArrayFree(g_fvgBearish);
   g_fvgBullishTotal = 0;
   g_fvgBearishTotal = 0;

   if(atrValue <= 0 || lookbackBars < 5) return 0;

   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   int copied = CopyRates(_Symbol, tf, 0, lookbackBars + 5, rates);
   if(copied < lookbackBars) return 0;

   double minGap = atrValue * minSizeATR;

   for(int i = 1; i < lookbackBars - 1; i++)
   {
      // Bullish FVG: gap between candle[i+1].low and candle[i-1].high
      double bullGap = rates[i + 1].low - rates[i - 1].high;
      if(bullGap >= minGap)
      {
         int sz = g_fvgBullishTotal;
         ArrayResize(g_fvgBullish, sz + 1, 50);
         g_fvgBullish[sz].time       = rates[i].time;
         g_fvgBullish[sz].priceHigh  = rates[i + 1].low;   // Upper boundary
         g_fvgBullish[sz].priceLow   = rates[i - 1].high;  // Lower boundary
         g_fvgBullish[sz].priceMid   = (g_fvgBullish[sz].priceHigh + g_fvgBullish[sz].priceLow) / 2.0;
         g_fvgBullish[sz].isBullish  = true;
         g_fvgBullish[sz].sizeATR    = bullGap / atrValue;
         g_fvgBullish[sz].ageCandles = i;
         g_fvgBullishTotal++;
      }

      // Bearish FVG: gap between candle[i-1].low and candle[i+1].high
      double bearGap = rates[i - 1].low - rates[i + 1].high;
      if(bearGap >= minGap)
      {
         int sz = g_fvgBearishTotal;
         ArrayResize(g_fvgBearish, sz + 1, 50);
         g_fvgBearish[sz].time       = rates[i].time;
         g_fvgBearish[sz].priceHigh  = rates[i - 1].low;   // Upper boundary
         g_fvgBearish[sz].priceLow   = rates[i + 1].high;  // Lower boundary
         g_fvgBearish[sz].priceMid   = (g_fvgBearish[sz].priceHigh + g_fvgBearish[sz].priceLow) / 2.0;
         g_fvgBearish[sz].isBullish  = false;
         g_fvgBearish[sz].sizeATR    = bearGap / atrValue;
         g_fvgBearish[sz].ageCandles = i;
         g_fvgBearishTotal++;
      }
   }

   return g_fvgBullishTotal + g_fvgBearishTotal;
}

//+------------------------------------------------------------------+
//| Find nearest demand zone below/at price                          |
//+------------------------------------------------------------------+
bool GetNearestDemandZone(double price, double proximityATR, double atrValue,
                          SwingSDZone &zone)
{
   double nearestDist = DBL_MAX;
   int nearestIdx = -1;
   double thresh = atrValue * proximityATR;

   for(int i = 0; i < g_swingBullishTotal; i++)
   {
      if(price < g_swingBullish[i].priceLow - thresh) continue;
      double dist = price - g_swingBullish[i].priceMid;
      if(dist >= -thresh && dist < nearestDist)
      {
         nearestDist = dist;
         nearestIdx = i;
      }
   }

   if(nearestIdx >= 0) { zone = g_swingBullish[nearestIdx]; return true; }
   return false;
}

//+------------------------------------------------------------------+
//| Find nearest supply zone above/at price                          |
//+------------------------------------------------------------------+
bool GetNearestSupplyZone(double price, double proximityATR, double atrValue,
                          SwingSDZone &zone)
{
   double nearestDist = DBL_MAX;
   int nearestIdx = -1;
   double thresh = atrValue * proximityATR;

   for(int i = 0; i < g_swingBearishTotal; i++)
   {
      if(price > g_swingBearish[i].priceHigh + thresh) continue;
      double dist = g_swingBearish[i].priceMid - price;
      if(dist >= -thresh && dist < nearestDist)
      {
         nearestDist = dist;
         nearestIdx = i;
      }
   }

   if(nearestIdx >= 0) { zone = g_swingBearish[nearestIdx]; return true; }
   return false;
}

//+------------------------------------------------------------------+
//| Find nearest bullish FVG below/at price                          |
//+------------------------------------------------------------------+
bool GetNearestBullFVG(double price, double proximityATR, double atrValue, FVGZone &fvg)
{
   double nearestDist = DBL_MAX;
   int nearestIdx = -1;
   double thresh = atrValue * proximityATR;

   for(int i = 0; i < g_fvgBullishTotal; i++)
   {
      if(price < g_fvgBullish[i].priceLow - thresh) continue;
      double dist = price - g_fvgBullish[i].priceMid;
      if(dist >= -thresh && dist < nearestDist)
      {
         nearestDist = dist;
         nearestIdx = i;
      }
   }

   if(nearestIdx >= 0) { fvg = g_fvgBullish[nearestIdx]; return true; }
   return false;
}

//+------------------------------------------------------------------+
//| Find nearest bearish FVG above/at price                          |
//+------------------------------------------------------------------+
bool GetNearestBearFVG(double price, double proximityATR, double atrValue, FVGZone &fvg)
{
   double nearestDist = DBL_MAX;
   int nearestIdx = -1;
   double thresh = atrValue * proximityATR;

   for(int i = 0; i < g_fvgBearishTotal; i++)
   {
      if(price > g_fvgBearish[i].priceHigh + thresh) continue;
      double dist = g_fvgBearish[i].priceMid - price;
      if(dist >= -thresh && dist < nearestDist)
      {
         nearestDist = dist;
         nearestIdx = i;
      }
   }

   if(nearestIdx >= 0) { fvg = g_fvgBearish[nearestIdx]; return true; }
   return false;
}

//+------------------------------------------------------------------+
//| Sort zones by strength descending                                |
//+------------------------------------------------------------------+
void SortSwingZones(SwingSDZone &zones[], int count, bool descending)
{
   for(int i = 0; i < count - 1; i++)
      for(int j = i + 1; j < count; j++)
         if(descending ? (zones[j].strength > zones[i].strength)
                       : (zones[j].strength < zones[i].strength))
         { SwingSDZone t = zones[i]; zones[i] = zones[j]; zones[j] = t; }
}

//+------------------------------------------------------------------+
//| Print active zones                                               |
//+------------------------------------------------------------------+
void PrintSwingZones()
{
   Print("=== Demand Zones: ", g_swingBullishTotal, " ===");
   for(int i = 0; i < MathMin(g_swingBullishTotal, 5); i++)
      PrintFormat("  DZ[%d] [%.2f-%.2f] Str=%.1f Age=%d Sw=%d",
         i, g_swingBullish[i].priceLow, g_swingBullish[i].priceHigh,
         g_swingBullish[i].strength, g_swingBullish[i].ageCandles,
         g_swingBullish[i].swingCount);

   Print("=== Supply Zones: ", g_swingBearishTotal, " ===");
   for(int i = 0; i < MathMin(g_swingBearishTotal, 5); i++)
      PrintFormat("  SZ[%d] [%.2f-%.2f] Str=%.1f Age=%d Sw=%d",
         i, g_swingBearish[i].priceLow, g_swingBearish[i].priceHigh,
         g_swingBearish[i].strength, g_swingBearish[i].ageCandles,
         g_swingBearish[i].swingCount);

   if(g_fvgBullishTotal + g_fvgBearishTotal > 0)
   {
      Print("=== FVGs: ", g_fvgBullishTotal, " Bull / ", g_fvgBearishTotal, " Bear ===");
      for(int i = 0; i < MathMin(g_fvgBullishTotal, 3); i++)
         PrintFormat("  BullFVG[%d] [%.2f-%.2f] Size=%.1fATR Age=%d",
            i, g_fvgBullish[i].priceLow, g_fvgBullish[i].priceHigh,
            g_fvgBullish[i].sizeATR, g_fvgBullish[i].ageCandles);
      for(int i = 0; i < MathMin(g_fvgBearishTotal, 3); i++)
         PrintFormat("  BearFVG[%d] [%.2f-%.2f] Size=%.1fATR Age=%d",
            i, g_fvgBearish[i].priceLow, g_fvgBearish[i].priceHigh,
            g_fvgBearish[i].sizeATR, g_fvgBearish[i].ageCandles);
   }
}
//+------------------------------------------------------------------+


// ================================================================
// === EA Body ===
// ================================================================


//+------------------------------------------------------------------+
//| Input Parameters                                                 |
//+------------------------------------------------------------------+

//--- Mode Selection
input bool     UseAsianSweep       = true;      // Enable Asian Range sweep mode
input bool     UseHTFPullback      = true;      // Enable HTF trend pullback mode

//--- Asian Range Parameters
input int      AsianStartGMT       = 0;         // Asian session start (GMT hour)
input int      AsianEndGMT         = 7;         // Asian session end (GMT hour)
input double   SweepMinWickATR     = 0.3;       // Min wick beyond Asian level (xATR)
input double   SweepMaxWickATR     = 3.0;       // Max wick (too big = genuine breakout)
input bool     RequireCloseInside  = true;       // Candle must close back inside range

//--- HTF Trend Pullback Parameters
input int      HTF_Period          = 0;         // HTF for trend (0=H4, use PERIOD_H4)
input int      HTF_EMA_Period      = 200;       // EMA period on HTF
input double   FVG_MinSizeATR      = 0.3;       // Min FVG size (xATR M5)
input double   ZoneProximityATR    = 0.4;       // Max distance from S&D zone (xATR)

//--- Swing S&D Parameters
input int      Swing_LookbackCandles = 1000;    // Scan depth for swings
input int      Swing_LookbackBars  = 3;         // Bars each side for swing detection
input double   Swing_ClusterPts    = 0.8;       // Cluster threshold (price pts)
input int      Swing_MaxAge        = 80;        // Max zone age in M15 candles
input double   Swing_MinStrength   = 2.0;       // Minimum zone strength (1-5)

//--- Entry Confirmation
input bool     RequireZoneReject   = true;       // Require M5 rejection candle
input double   MinRejectWickATR    = 0.12;      // Min rejection wick (xATR)
input bool     RequireBothTrendAndReject = true; // A+ mode: need BOTH trend AND rejection
input int      MinZoneStrength     = 3;         // Min zone strength for entry
input int      CooldownMinutes     = 15;        // Pause after trade close (minutes)

//--- Risk Management (GOLD-optimized)
input double   RiskPerTradePct     = 1.0;       // % risk per trade
input double   SL_BufferATR        = 1.2;       // SL buffer (xATR) — wider for gold
input double   TP_MinRR            = 2.0;       // Minimum R:R ratio
input double   TP_SwingMult        = 1.5;       // TP = swing distance * mult (for pullback)
input double   FixedLotPer2k       = 0.01;      // Fallback lot per $2k

//--- Safety Limits
input int      MaxPositions        = 3;
input int      MaxDailyTrades      = 15;        // Max trades per day
input double   MaxDailyLossPct     = 5.0;       // Stop trading at this loss %
input int      MaxSpreadPts        = 500;       // Max spread in points

//--- News Filter
input bool     SkipHighImpactNews  = true;       // Skip during high-impact news
input int      NewsPreMinutes      = 30;         // Minutes before news to stop
input int      NewsPostMinutes     = 30;         // Minutes after news to resume

//--- General
input ulong    MagicNumber         = 20241201;
input string   CommentPrefix       = "FXRE_HYBRID";
input int      MaxSlippagePts      = 50;
input bool     DebugMode           = true;

//+------------------------------------------------------------------+
//| Global Variables                                                 |
//+------------------------------------------------------------------+
double   g_atrM15 = 0;
double   g_atrM5  = 0;
int      g_signalBarTime = 0;
datetime g_lastTradeCloseTime = 0;
int      g_logFile = -1;

// Asian Range
double   g_asianHigh = 0;
double   g_asianLow  = 0;
double   g_asianMid  = 0;
double   g_asianWidth = 0;
bool     g_asianValid = false;
datetime g_asianDate  = 0;
bool     g_sweepDetected_Buy  = false;  // Swept below Asian Low → look for BUY
bool     g_sweepDetected_Sell = false;  // Swept above Asian High → look for SELL
bool     g_sweepTraded_Buy  = false;
bool     g_sweepTraded_Sell = false;

// H4 Trend
bool     g_htfBull = false;
bool     g_htfBear = false;

// Daily tracking
struct DailyStats { datetime date; int tradeCount; double startBal; bool stopped; };
DailyStats g_daily;
datetime g_dailyResetDay = 0;

// Zone dedup
double   g_tradedZoneMids[];
datetime g_tradedZoneDay = 0;

//+------------------------------------------------------------------+
//| Logging                                                          |
//+------------------------------------------------------------------+
void LogMsg(string msg)
{
   Print(msg);
   if(DebugMode)
   {
      if(g_logFile == -1)
      {
         g_logFile = FileOpen("FXRE_Hybrid_v2.log", FILE_TXT|FILE_WRITE|FILE_SHARE_READ, ',');
         if(g_logFile != -1)
            FileWrite(g_logFile, "=== FXRE Hybrid v2.0 Log Started ===");
      }
      if(g_logFile != -1)
      {
         FileWrite(g_logFile, TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS) + " | " + msg);
         FileFlush(g_logFile);
      }
   }
}

//+------------------------------------------------------------------+
//| ATR Calculation                                                  |
//+------------------------------------------------------------------+
double CalcATR(int period, ENUM_TIMEFRAMES tf)
{
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   int copied = CopyRates(_Symbol, tf, 0, period + 2, rates);
   if(copied < period + 1) return 0;
   double sum = 0;
   for(int i = 0; i < period; i++)
   {
      double tr = MathMax(rates[i].high - rates[i].low,
         MathMax(MathAbs(rates[i].high - rates[i+1].close),
                 MathAbs(rates[i].low - rates[i+1].close)));
      sum += tr;
   }
   return sum / period;
}

//+------------------------------------------------------------------+
//| Lot sizing from risk %                                           |
//+------------------------------------------------------------------+
double CalcRiskLot(double slDistPts)
{
   if(slDistPts <= 0)
   {
      double bal = AccountInfoDouble(ACCOUNT_BALANCE);
      double lot = (bal / 2000.0) * FixedLotPer2k;
      lot = MathMax(lot, SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN));
      double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
      if(step > 0) lot = MathFloor(lot / step) * step;
      return lot;
   }

   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double riskAmt = balance * (RiskPerTradePct / 100.0);
   double tickVal = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSz  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tickVal <= 0 || tickSz <= 0) return 0.01;

   double lot = riskAmt / (slDistPts * tickVal);
   lot = MathMax(lot, SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN));
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(step > 0) lot = MathFloor(lot / step) * step;
   return lot;
}

//+------------------------------------------------------------------+
//| Send market order                                                |
//+------------------------------------------------------------------+
bool OpenOrder(int type, double volume, double price,
               double sl, double tp, string comment)
{
   MqlTradeRequest req = {};
   MqlTradeResult  res = {};
   req.action    = TRADE_ACTION_DEAL;
   req.symbol    = _Symbol;
   req.volume    = volume;
   req.type      = (ENUM_ORDER_TYPE)type;
   req.price     = price;
   req.deviation = MaxSlippagePts;
   req.sl        = sl;
   req.tp        = tp;
   req.comment   = comment;
   req.magic     = MagicNumber;
   req.type_filling = ORDER_FILLING_IOC;

   if(OrderSend(req, res))
   {
      if(res.retcode == TRADE_RETCODE_DONE)
      {
         LogMsg("ORDER: " + (type == ORDER_TYPE_BUY ? "BUY" : "SELL") +
               " Vol=" + DoubleToString(volume, 2) + " @ " + DoubleToString(price, _Digits) +
               " SL=" + DoubleToString(sl, _Digits) + " TP=" + DoubleToString(tp, _Digits) +
               " [" + comment + "]");
         return true;
      }
      else
         LogMsg("ORDER FAILED: " + IntegerToString(res.retcode) + " " + res.comment);
   }
   return false;
}

//+------------------------------------------------------------------+
//| Daily safety                                                     |
//+------------------------------------------------------------------+
void ResetDaily()
{
   MqlDateTime dt;
   TimeCurrent(dt);
   datetime today = StructToTime(dt);
   today = today - (today % 86400);
   if(today != g_dailyResetDay)
   {
      ZeroMemory(g_daily);
      g_daily.date = today;
      g_daily.startBal = AccountInfoDouble(ACCOUNT_BALANCE);
      g_dailyResetDay = today;
   }
}

bool CanTrade()
{
   ResetDaily();
   if(g_daily.stopped) return false;
   if(g_daily.tradeCount >= MaxDailyTrades) return false;

   double dd = (g_daily.startBal - AccountInfoDouble(ACCOUNT_EQUITY))
               / MathMax(g_daily.startBal, 1.0) * 100.0;
   if(dd >= MaxDailyLossPct)
   {
      g_daily.stopped = true;
      LogMsg("STOPPED: Daily DD limit " + DoubleToString(dd, 1) + "% >= " + DoubleToString(MaxDailyLossPct, 1) + "%");
      CloseAllPositions();
      return false;
   }

   double spread = (double)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   if(spread > MaxSpreadPts) return false;

   if(g_lastTradeCloseTime > 0)
   {
      int mins = (int)((TimeCurrent() - g_lastTradeCloseTime) / 60);
      if(mins < CooldownMinutes) return false;
   }

   return true;
}

//+------------------------------------------------------------------+
//| Count open positions                                             |
//+------------------------------------------------------------------+
int CountPositions()
{
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != (long)MagicNumber) continue;
      count++;
   }
   return count;
}

//+------------------------------------------------------------------+
//| Close all positions (emergency)                                  |
//+------------------------------------------------------------------+
void CloseAllPositions()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

      MqlTradeRequest req = {};
      MqlTradeResult  res = {};
      int type = (int)PositionGetInteger(POSITION_TYPE);
      req.action = TRADE_ACTION_DEAL;
      req.position = ticket;
      req.symbol = _Symbol;
      req.volume = PositionGetDouble(POSITION_VOLUME);
      req.deviation = MaxSlippagePts;
      req.type_filling = ORDER_FILLING_IOC;
      if(type == POSITION_TYPE_BUY)
      { req.price = SymbolInfoDouble(_Symbol, SYMBOL_BID); req.type = ORDER_TYPE_SELL; }
      else
      { req.price = SymbolInfoDouble(_Symbol, SYMBOL_ASK); req.type = ORDER_TYPE_BUY; }
      OrderSend(req, res);
   }
}

//+------------------------------------------------------------------+
//| Zone dedup                                                       |
//+------------------------------------------------------------------+
void ResetZoneTracker()
{
   MqlDateTime dt;
   TimeCurrent(dt);
   datetime today = StructToTime(dt);
   today = today - (today % 86400);
   if(today != g_tradedZoneDay)
   {
      ArrayFree(g_tradedZoneMids);
      g_tradedZoneDay = today;
   }
}

bool AlreadyTradedZone(double mid)
{
   for(int i = 0; i < ArraySize(g_tradedZoneMids); i++)
      if(MathAbs(g_tradedZoneMids[i] - mid) < 1.0)  // within $1 for gold
         return true;
   return false;
}

void TrackZone(double mid)
{
   int sz = ArraySize(g_tradedZoneMids);
   ArrayResize(g_tradedZoneMids, sz + 1);
   g_tradedZoneMids[sz] = mid;
}

//+------------------------------------------------------------------+
//| Detect Asian Range (00:00-07:00 GMT)                             |
//+------------------------------------------------------------------+
void DetectAsianRange()
{
   MqlDateTime dt;
   TimeCurrent(dt);
   datetime today = StructToTime(dt);
   today = today - (today % 86400);

   // Already computed for today?
   if(today == g_asianDate && g_asianValid) return;

   // Get H1 bars for the Asian session
   MqlRates rates[];
   ArraySetAsSeries(rates, true);

   // We need bars from today's 00:00 to 07:00 GMT
   // Server time is GMT+2, so we need bars from 22:00 prev day to 05:00 today (server time)
   // Or more simply, scan today's H1 bars
   int copied = CopyRates(_Symbol, PERIOD_H1, today, 24, rates);
   if(copied < 7) return;

   double hi = -DBL_MAX;
   double lo = DBL_MAX;

   // Find bars within Asian window (first 7 H1 bars = 00:00-07:00 server time ≈ GMT)
   for(int i = copied - 1; i >= 0; i--)
   {
      MqlDateTime barDt;
      TimeToStruct(rates[i].time, barDt);
      int serverHour = barDt.hour;

      // Server hour 0-6 = Asian session (GMT+2 offset: server 0 = GMT 22 prev day)
      // For simplicity, use first 7 H1 bars of the trading day
      if(serverHour >= 0 && serverHour < 7)
      {
         if(rates[i].high > hi) hi = rates[i].high;
         if(rates[i].low < lo)  lo = rates[i].low;
      }
   }

   if(hi > lo && hi != -DBL_MAX)
   {
      g_asianHigh = hi;
      g_asianLow  = lo;
      g_asianMid  = (hi + lo) / 2.0;
      g_asianWidth = hi - lo;
      g_asianValid = true;
      g_asianDate  = today;
      g_sweepDetected_Buy  = false;
      g_sweepDetected_Sell = false;
      g_sweepTraded_Buy  = false;
      g_sweepTraded_Sell = false;
      LogMsg("ASIAN RANGE: High=" + DoubleToString(hi, _Digits) +
             " Low=" + DoubleToString(lo, _Digits) +
             " Width=" + DoubleToString(g_asianWidth, _Digits));
   }
}

//+------------------------------------------------------------------+
//| Get H4 trend bias via EMA200                                    |
//+------------------------------------------------------------------+
void UpdateHTFBias()
{
   ENUM_TIMEFRAMES htf = (ENUM_TIMEFRAMES)HTF_Period;
   if(htf == 0) htf = PERIOD_H4;

   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   int copied = CopyRates(_Symbol, htf, 0, HTF_EMA_Period + 10, rates);
   if(copied < HTF_EMA_Period) return;

   // Calculate EMA200
   double ema = 0;
   double mult = 2.0 / (HTF_EMA_Period + 1);
   // Seed with SMA
   double sum = 0;
   for(int i = HTF_EMA_Period; i >= 1; i--)
      sum += rates[i].close;
   ema = sum / HTF_EMA_Period;

   // EMA smoothing
   for(int i = HTF_EMA_Period - 1; i >= 0; i--)
      ema = (rates[i].close - ema) * mult + ema;

   g_htfBull = (rates[0].close > ema);
   g_htfBear = (rates[0].close < ema);
}

//+------------------------------------------------------------------+
//| Detect Liquidity Sweep                                           |
//| Check if M5 candle wicked beyond Asian level then closed inside  |
//+------------------------------------------------------------------+
void DetectSweep()
{
   if(!g_asianValid || !UseAsianSweep) return;

   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   if(CopyRates(_Symbol, PERIOD_M5, 0, 5, rates) < 3) return;

   double atr = g_atrM5;
   if(atr <= 0) return;

   // Check the most recently closed candle (rates[1])
   double wickBelow = g_asianLow - rates[1].low;
   double wickAbove = rates[1].high - g_asianHigh;

   // Bullish sweep: price swept below Asian Low
   if(wickBelow >= SweepMinWickATR * atr && wickBelow <= SweepMaxWickATR * atr)
   {
      // Must close back inside (above Asian Low)
      if(!RequireCloseInside || rates[1].close > g_asianLow)
      {
         g_sweepDetected_Buy = true;
         LogMsg("SWEEP DETECTED (BUY): Wick below Asian Low by " +
                DoubleToString(wickBelow, _Digits) + " (" +
                DoubleToString(wickBelow / atr, 1) + "xATR). Close=" +
                DoubleToString(rates[1].close, _Digits));
      }
      else if(DebugMode && TimeCurrent() % 120 < 2)
         LogMsg("SWEEP BUY NEAR-MISS: wick below OK (" + DoubleToString(wickBelow/atr, 1) +
                "xATR) but close=" + DoubleToString(rates[1].close, _Digits) +
                " NOT inside (asianLo=" + DoubleToString(g_asianLow, _Digits) + ")");
   }
   else if(DebugMode && wickBelow > 0 && TimeCurrent() % 300 < 2)
      LogMsg("SWEEP SCAN: wickBelow=" + DoubleToString(wickBelow, _Digits) +
             " (" + DoubleToString(wickBelow/atr, 1) + "xATR)" +
             " min=" + DoubleToString(SweepMinWickATR, 1) + " max=" + DoubleToString(SweepMaxWickATR, 1));

   // Bearish sweep: price swept above Asian High
   if(wickAbove >= SweepMinWickATR * atr && wickAbove <= SweepMaxWickATR * atr)
   {
      // Must close back inside (below Asian High)
      if(!RequireCloseInside || rates[1].close < g_asianHigh)
      {
         g_sweepDetected_Sell = true;
         LogMsg("SWEEP DETECTED (SELL): Wick above Asian High by " +
                DoubleToString(wickAbove, _Digits) + " (" +
                DoubleToString(wickAbove / atr, 1) + "xATR). Close=" +
                DoubleToString(rates[1].close, _Digits));
      }
      else if(DebugMode && TimeCurrent() % 120 < 2)
         LogMsg("SWEEP SELL NEAR-MISS: wick above OK (" + DoubleToString(wickAbove/atr, 1) +
                "xATR) but close=" + DoubleToString(rates[1].close, _Digits) +
                " NOT inside (asianHi=" + DoubleToString(g_asianHigh, _Digits) + ")");
   }
   else if(DebugMode && wickAbove > 0 && TimeCurrent() % 300 < 2)
      LogMsg("SWEEP SCAN: wickAbove=" + DoubleToString(wickAbove, _Digits) +
             " (" + DoubleToString(wickAbove/atr, 1) + "xATR)" +
             " min=" + DoubleToString(SweepMinWickATR, 1) + " max=" + DoubleToString(SweepMaxWickATR, 1));
}

//+------------------------------------------------------------------+
//| M5 rejection candle check                                        |
//+------------------------------------------------------------------+
bool CheckRejectionBull()
{
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   if(CopyRates(_Symbol, PERIOD_M5, 0, 5, rates) < 3) return false;

   double minWick = MinRejectWickATR * g_atrM5;

   for(int c = 1; c <= 3; c++)
   {
      double lowerWick = MathMin(rates[c].close, rates[c].open) - rates[c].low;
      double body = MathAbs(rates[c].close - rates[c].open);

      if(lowerWick >= minWick && lowerWick >= body * 0.2 &&
         rates[c].close > rates[c].open)
         return true;
   }
   return false;
}

bool CheckRejectionBear()
{
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   if(CopyRates(_Symbol, PERIOD_M5, 0, 5, rates) < 3) return false;

   double minWick = MinRejectWickATR * g_atrM5;

   for(int c = 1; c <= 3; c++)
   {
      double upperWick = rates[c].high - MathMax(rates[c].close, rates[c].open);
      double body = MathAbs(rates[c].close - rates[c].open);

      if(upperWick >= minWick && upperWick >= body * 0.2 &&
         rates[c].close < rates[c].open)
         return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| MODE 1: Asian Range Liquidity Sweep Entry                        |
//+------------------------------------------------------------------+
void CheckAsianSweepEntry()
{
   if(!UseAsianSweep || !g_asianValid) return;
   if(!g_sweepDetected_Buy && !g_sweepDetected_Sell) return;

   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);

   //--- BUY: Swept below Asian Low → rejection → BUY
   if(g_sweepDetected_Buy && !g_sweepTraded_Buy)
   {
      bool rejection = !RequireZoneReject || CheckRejectionBull();
      bool trendOk = !RequireBothTrendAndReject || g_htfBull;

      if(rejection && trendOk)
      {
         // SL: below the sweep low + buffer
         MqlRates rates[];
         ArraySetAsSeries(rates, true);
         CopyRates(_Symbol, PERIOD_M5, 0, 5, rates);
         double sweepLow = rates[1].low;
         double sl = sweepLow - SL_BufferATR * g_atrM15;

         // TP: opposite side of Asian range (or min R:R)
         double asianTP = g_asianHigh;
         double slDist = ask - sl;
         double minTP = ask + slDist * TP_MinRR;
         double tp = MathMax(asianTP, minTP);

         double slPts = slDist / _Point;
         double lot = CalcRiskLot(slPts);

         if(DebugMode)
            LogMsg("SWEEP BUY SIGNAL: bid=" + DoubleToString(bid, _Digits) +
                   " ask=" + DoubleToString(ask, _Digits) +
                   " sweepLow=" + DoubleToString(sweepLow, _Digits) +
                   " SL=" + DoubleToString(sl, _Digits) +
                   " TP=" + DoubleToString(tp, _Digits) +
                   " SLdist=" + DoubleToString(slDist, _Digits) +
                   " R:R=" + DoubleToString(slDist > 0 ? (tp - ask) / slDist : 0, 2) +
                   " lot=" + DoubleToString(lot, 2) +
                   " asianHi=" + DoubleToString(g_asianHigh, _Digits) +
                   " asianLo=" + DoubleToString(g_asianLow, _Digits));

         if(lot >= SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN))
         {
            if(OpenOrder(ORDER_TYPE_BUY, lot, ask, sl, tp,
               CommentPrefix + "_SWEEP_BUY"))
            {
               g_sweepTraded_Buy = true;
               g_sweepDetected_Buy = false;
               g_daily.tradeCount++;
               TrackZone(g_asianLow);
               LogMsg("SWEEP BUY FILLED: Lot=" + DoubleToString(lot, 2) +
                      " SL=" + DoubleToString(sl, _Digits) +
                      " TP=" + DoubleToString(tp, _Digits));
            }
         }
         else
            LogMsg("SWEEP BUY REJECTED: lot=" + DoubleToString(lot, 2) +
                   " < min=" + DoubleToString(SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN), 2));
      }
      else
      {
         if(DebugMode && TimeCurrent() % 60 < 2)
            LogMsg("SWEEP BUY WAITING: rejection=" + (string)rejection +
                   " trendOk=" + (string)trendOk +
                   " htfBull=" + (string)g_htfBull +
                   " htfBear=" + (string)g_htfBear);
      }
   }

   //--- SELL: Swept above Asian High → rejection → SELL
   if(g_sweepDetected_Sell && !g_sweepTraded_Sell)
   {
      bool rejection = !RequireZoneReject || CheckRejectionBear();
      bool trendOk = !RequireBothTrendAndReject || g_htfBear;

      if(rejection && trendOk)
      {
         MqlRates rates[];
         ArraySetAsSeries(rates, true);
         CopyRates(_Symbol, PERIOD_M5, 0, 5, rates);
         double sweepHigh = rates[1].high;
         double sl = sweepHigh + SL_BufferATR * g_atrM15;

         double asianTP = g_asianLow;
         double slDist = sl - bid;
         double minTP = bid - slDist * TP_MinRR;
         double tp = MathMin(asianTP, minTP);

         double slPts = slDist / _Point;
         double lot = CalcRiskLot(slPts);

         if(DebugMode)
            LogMsg("SWEEP SELL SIGNAL: bid=" + DoubleToString(bid, _Digits) +
                   " ask=" + DoubleToString(ask, _Digits) +
                   " sweepHigh=" + DoubleToString(sweepHigh, _Digits) +
                   " SL=" + DoubleToString(sl, _Digits) +
                   " TP=" + DoubleToString(tp, _Digits) +
                   " SLdist=" + DoubleToString(slDist, _Digits) +
                   " R:R=" + DoubleToString(slDist > 0 ? (sl - bid) / slDist : 0, 2) +
                   " lot=" + DoubleToString(lot, 2) +
                   " asianHi=" + DoubleToString(g_asianHigh, _Digits) +
                   " asianLo=" + DoubleToString(g_asianLow, _Digits));

         if(lot >= SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN))
         {
            if(OpenOrder(ORDER_TYPE_SELL, lot, bid, sl, tp,
               CommentPrefix + "_SWEEP_SELL"))
            {
               g_sweepTraded_Sell = true;
               g_sweepDetected_Sell = false;
               g_daily.tradeCount++;
               TrackZone(g_asianHigh);
               LogMsg("SWEEP SELL FILLED: Lot=" + DoubleToString(lot, 2) +
                      " SL=" + DoubleToString(sl, _Digits) +
                      " TP=" + DoubleToString(tp, _Digits));
            }
         }
         else
            LogMsg("SWEEP SELL REJECTED: lot=" + DoubleToString(lot, 2) +
                   " < min=" + DoubleToString(SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN), 2));
      }
      else
      {
         if(DebugMode && TimeCurrent() % 60 < 2)
            LogMsg("SWEEP SELL WAITING: rejection=" + (string)rejection +
                   " trendOk=" + (string)trendOk +
                   " htfBull=" + (string)g_htfBull +
                   " htfBear=" + (string)g_htfBear);
      }
   }
}

//+------------------------------------------------------------------+
//| MODE 2: HTF Trend Pullback Entry                                |
//+------------------------------------------------------------------+
void CheckHTFPullbackEntry()
{
   if(!UseHTFPullback) return;
   if(!g_htfBull && !g_htfBear) return;

   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);

   // Detect S&D zones + FVGs on M15/M5
   DetectSwingZones(PERIOD_M15, Swing_LookbackCandles, Swing_LookbackBars,
                    Swing_ClusterPts, Swing_MaxAge, Swing_MinStrength);
   DetectFVGs(PERIOD_M5, 200, FVG_MinSizeATR, g_atrM5);

   // Debug: log zone summary
   if(DebugMode && TimeCurrent() % 300 < 2)
      LogMsg("HTF PULLBACK SCAN: bid=" + DoubleToString(bid, _Digits) +
             " Demand=" + IntegerToString(g_swingBullishTotal) +
             " Supply=" + IntegerToString(g_swingBearishTotal) +
             " BullFVG=" + IntegerToString(g_fvgBullishTotal) +
             " BearFVG=" + IntegerToString(g_fvgBearishTotal) +
             " H4=" + (g_htfBull ? "BULL" : g_htfBear ? "BEAR" : "?"));

   //--- BUY: HTF bullish + pullback into demand zone or bullish FVG
   if(g_htfBull)
   {
      SwingSDZone demandZone;
      FVGZone bullFVG;
      bool inDemand = GetNearestDemandZone(bid, ZoneProximityATR, g_atrM15, demandZone);
      bool inFVG = GetNearestBullFVG(bid, ZoneProximityATR, g_atrM5, bullFVG);

      if(DebugMode && (inDemand || inFVG) && TimeCurrent() % 60 < 2)
         LogMsg("HTF BUY ZONE HIT: inDemand=" + (string)inDemand +
                (inDemand ? " [str=" + DoubleToString(demandZone.strength, 1) +
                 " hi=" + DoubleToString(demandZone.priceHigh, _Digits) +
                 " lo=" + DoubleToString(demandZone.priceLow, _Digits) + "]" : "") +
                " inFVG=" + (string)inFVG +
                (inFVG ? " [size=" + DoubleToString(bullFVG.sizeATR, 1) + "ATR" +
                 " hi=" + DoubleToString(bullFVG.priceHigh, _Digits) +
                 " lo=" + DoubleToString(bullFVG.priceLow, _Digits) + "]" : ""));

      if((inDemand || inFVG) && !RequireZoneReject || CheckRejectionBull())
      {
         double zoneLow, zoneHigh;
         if(inDemand)
         {
            zoneLow = demandZone.priceLow;
            zoneHigh = demandZone.priceHigh;
            if(demandZone.strength < MinZoneStrength)
            {
               if(DebugMode) LogMsg("HTF BUY REJECTED: zone strength " +
                  DoubleToString(demandZone.strength, 1) + " < min " + DoubleToString(MinZoneStrength, 1));
               return;
            }
         }
         else
         {
            zoneLow = bullFVG.priceLow;
            zoneHigh = bullFVG.priceHigh;
         }

         // Don't re-enter same zone
         double zoneMid = (zoneLow + zoneHigh) / 2.0;
         if(AlreadyTradedZone(zoneMid))
         {
            if(DebugMode) LogMsg("HTF BUY REJECTED: zone already traded [mid=" + DoubleToString(zoneMid, _Digits) + "]");
            return;
         }

         double sl = zoneLow - SL_BufferATR * g_atrM15;
         double slDist = ask - sl;
         double tp = MathMax(ask + slDist * TP_MinRR,
                           ask + slDist * TP_SwingMult);

         double slPts = slDist / _Point;
         double lot = CalcRiskLot(slPts);

         if(DebugMode)
            LogMsg("HTF BUY SIGNAL: ask=" + DoubleToString(ask, _Digits) +
                   " zone=[" + DoubleToString(zoneLow, _Digits) + "-" + DoubleToString(zoneHigh, _Digits) + "]" +
                   " SL=" + DoubleToString(sl, _Digits) +
                   " TP=" + DoubleToString(tp, _Digits) +
                   " R:R=" + DoubleToString(slDist > 0 ? (tp - ask) / slDist : 0, 2) +
                   " lot=" + DoubleToString(lot, 2));

         if(lot >= SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN))
         {
            if(OpenOrder(ORDER_TYPE_BUY, lot, ask, sl, tp,
               CommentPrefix + "_HTF_BUY"))
            {
               g_daily.tradeCount++;
               TrackZone(zoneMid);
               LogMsg("HTF BUY FILLED: Lot=" + DoubleToString(lot, 2) +
                      " SL=" + DoubleToString(sl, _Digits) +
                      " TP=" + DoubleToString(tp, _Digits));
            }
         }
         else
            LogMsg("HTF BUY REJECTED: lot=" + DoubleToString(lot, 2) +
                   " < min=" + DoubleToString(SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN), 2));
      }
   }

   //--- SELL: HTF bearish + pullback into supply zone or bearish FVG
   if(g_htfBear)
   {
      SwingSDZone supplyZone;
      FVGZone bearFVG;
      bool inSupply = GetNearestSupplyZone(bid, ZoneProximityATR, g_atrM15, supplyZone);
      bool inFVG = GetNearestBearFVG(bid, ZoneProximityATR, g_atrM5, bearFVG);

      if(DebugMode && (inSupply || inFVG) && TimeCurrent() % 60 < 2)
         LogMsg("HTF SELL ZONE HIT: inSupply=" + (string)inSupply +
                (inSupply ? " [str=" + DoubleToString(supplyZone.strength, 1) +
                 " hi=" + DoubleToString(supplyZone.priceHigh, _Digits) +
                 " lo=" + DoubleToString(supplyZone.priceLow, _Digits) + "]" : "") +
                " inFVG=" + (string)inFVG +
                (inFVG ? " [size=" + DoubleToString(bearFVG.sizeATR, 1) + "ATR" +
                 " hi=" + DoubleToString(bearFVG.priceHigh, _Digits) +
                 " lo=" + DoubleToString(bearFVG.priceLow, _Digits) + "]" : ""));

      if((inSupply || inFVG) && !RequireZoneReject || CheckRejectionBear())
      {
         double zoneLow, zoneHigh;
         if(inSupply)
         {
            zoneLow = supplyZone.priceLow;
            zoneHigh = supplyZone.priceHigh;
            if(supplyZone.strength < MinZoneStrength)
            {
               if(DebugMode) LogMsg("HTF SELL REJECTED: zone strength " +
                  DoubleToString(supplyZone.strength, 1) + " < min " + DoubleToString(MinZoneStrength, 1));
               return;
            }
         }
         else
         {
            zoneLow = bearFVG.priceLow;
            zoneHigh = bearFVG.priceHigh;
         }

         double zoneMid = (zoneLow + zoneHigh) / 2.0;
         if(AlreadyTradedZone(zoneMid))
         {
            if(DebugMode) LogMsg("HTF SELL REJECTED: zone already traded [mid=" + DoubleToString(zoneMid, _Digits) + "]");
            return;
         }

         double sl = zoneHigh + SL_BufferATR * g_atrM15;
         double slDist = sl - bid;
         double tp = MathMin(bid - slDist * TP_MinRR,
                           bid - slDist * TP_SwingMult);

         double slPts = slDist / _Point;
         double lot = CalcRiskLot(slPts);

         if(DebugMode)
            LogMsg("HTF SELL SIGNAL: bid=" + DoubleToString(bid, _Digits) +
                   " zone=[" + DoubleToString(zoneLow, _Digits) + "-" + DoubleToString(zoneHigh, _Digits) + "]" +
                   " SL=" + DoubleToString(sl, _Digits) +
                   " TP=" + DoubleToString(tp, _Digits) +
                   " R:R=" + DoubleToString(slDist > 0 ? (sl - bid) / slDist : 0, 2) +
                   " lot=" + DoubleToString(lot, 2));

         if(lot >= SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN))
         {
            if(OpenOrder(ORDER_TYPE_SELL, lot, bid, sl, tp,
               CommentPrefix + "_HTF_SELL"))
            {
               g_daily.tradeCount++;
               TrackZone(zoneMid);
               LogMsg("HTF SELL FILLED: Lot=" + DoubleToString(lot, 2) +
                      " SL=" + DoubleToString(sl, _Digits) +
                      " TP=" + DoubleToString(tp, _Digits));
            }
         }
         else
            LogMsg("HTF SELL REJECTED: lot=" + DoubleToString(lot, 2) +
                   " < min=" + DoubleToString(SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN), 2));
      }
   }
}

//+------------------------------------------------------------------+
//| Track trade closes for cooldown                                  |
//+------------------------------------------------------------------+
int g_prevPosCount = 0;
void TrackCloses()
{
   int cur = CountPositions();
   if(cur < g_prevPosCount)
   {
      g_lastTradeCloseTime = TimeCurrent();
      LogMsg("Trade closed. Cooldown " + IntegerToString(CooldownMinutes) + "m started.");
   }
   g_prevPosCount = cur;
}

//+------------------------------------------------------------------+
//| Chart Comment                                                    |
//+------------------------------------------------------------------+
string RepStr(string s, int n) { string r = ""; for(int i = 0; i < n; i++) r += s; return r; }

void UpdateComment()
{
   string sep = "\n" + RepStr("-", 30) + "\n";
   string info = "=== FXRE Hybrid v2.0 ===" + sep;
   info += "Balance: $" + DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2);
   info += " | Equity: $" + DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2) + "\n";

   if(g_daily.startBal > 0)
   {
      double dd = (g_daily.startBal - AccountInfoDouble(ACCOUNT_EQUITY))
                  / g_daily.startBal * 100.0;
      info += "Today: " + IntegerToString(g_daily.tradeCount) + "/" + IntegerToString(MaxDailyTrades);
      info += " | DD: " + DoubleToString(dd, 1) + "%\n";
   }

   info += "Open: " + IntegerToString(CountPositions()) + "/" + IntegerToString(MaxPositions) + sep;

   // Asian Range
   if(g_asianValid)
      info += "Asian: " + DoubleToString(g_asianLow, 2) + " - " + DoubleToString(g_asianHigh, 2) +
              " (W=" + DoubleToString(g_asianWidth, 2) + ")\n";

   // Session
   info += "Session: " + GetSessionStatus() + "\n";

   // Trend
   info += "H4 Trend: " + (g_htfBull ? "BULL" : g_htfBear ? "BEAR" : "NEUTRAL") + "\n";

   // ATR
   info += "ATR: M15=" + DoubleToString(g_atrM15, 1) + " M5=" + DoubleToString(g_atrM5, 1) + "\n";

   // Sweep status
   if(UseAsianSweep)
   {
      info += "Sweep: " + (g_sweepDetected_Buy ? "BUY READY" :
                           g_sweepDetected_Sell ? "SELL READY" : "waiting") + "\n";
   }

   // S&D zones
   info += "Zones: " + IntegerToString(g_swingBullishTotal) + "D/" +
           IntegerToString(g_swingBearishTotal) + "S";
   if(g_fvgBullishTotal + g_fvgBearishTotal > 0)
      info += " | FVGs: " + IntegerToString(g_fvgBullishTotal) + "B/" +
              IntegerToString(g_fvgBearishTotal) + "Bear";
   info += "\n";

   if(g_daily.stopped)
      info += "!!! TRADING STOPPED !!!\n";

   Comment(info);
}

//+------------------------------------------------------------------+
//| Expert initialization                                            |
//+------------------------------------------------------------------+
int OnInit()
{
   LogMsg("=== FXRE Hybrid v2.0 Initializing ===");
   LogMsg("Symbol=" + _Symbol + " TF=" + EnumToString(Period()));
   LogMsg("Modes: AsianSweep=" + (string)UseAsianSweep + " HTFPullback=" + (string)UseHTFPullback);
   LogMsg("Risk: " + DoubleToString(RiskPerTradePct, 1) + "% SL_Buffer=" + DoubleToString(SL_BufferATR, 1) + "xATR TP_MinRR=1:" + DoubleToString(TP_MinRR, 1));
   LogMsg("Asian: " + IntegerToString(AsianStartGMT) + "-" + IntegerToString(AsianEndGMT) + " GMT");
   LogMsg("Sweep: MinWick=" + DoubleToString(SweepMinWickATR, 1) + "xATR MaxWick=" + DoubleToString(SweepMaxWickATR, 1) + "xATR CloseInside=" + (string)RequireCloseInside);

   ResetDaily();
   ResetZoneTracker();
   DetectAsianRange();
   UpdateHTFBias();

   g_atrM15 = CalcATR(14, PERIOD_M15);
   g_atrM5  = CalcATR(14, PERIOD_M5);

   LogMsg("Init OK. ATR M15=" + DoubleToString(g_atrM15, 1) + " M5=" + DoubleToString(g_atrM5, 1));
   LogMsg("Asian Range: " + DoubleToString(g_asianLow, _Digits) + " - " + DoubleToString(g_asianHigh, _Digits));
   LogMsg("H4 Trend: " + (g_htfBull ? "BULL" : g_htfBear ? "BEAR" : "NEUTRAL"));
   LogMsg("Balance: " + DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2));

   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   LogMsg("Hybrid v2.0 deinit. Reason: " + (string)reason);
   if(g_logFile != -1) { FileClose(g_logFile); g_logFile = -1; }
}

//+------------------------------------------------------------------+
//| OnTick                                                           |
//+------------------------------------------------------------------+
//--- Debug heartbeat timer (log every 5 min)
datetime g_lastHeartbeat = 0;
int      g_lastTrendState = 0; // 0=neutral, 1=bull, -1=bear

void OnTick()
{
   ResetDaily();
   ResetZoneTracker();
   TrackCloses();

   // === HEARTBEAT: Log full state every 5 minutes ===
   if(DebugMode && TimeCurrent() - g_lastHeartbeat >= 300)
   {
      g_lastHeartbeat = TimeCurrent();
      double bal = AccountInfoDouble(ACCOUNT_BALANCE);
      double eq  = AccountInfoDouble(ACCOUNT_EQUITY);
      double dd  = (g_daily.startBal > 0) ? (g_daily.startBal - eq) / g_daily.startBal * 100.0 : 0;
      double spread = (double)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
      string hb = "HEARTBEAT | Bal=$" + DoubleToString(bal, 2) +
                  " Eq=$" + DoubleToString(eq, 2) +
                  " DD=" + DoubleToString(dd, 1) + "%" +
                  " | Trades today: " + IntegerToString(g_daily.tradeCount) +
                  " | Open: " + IntegerToString(CountPositions()) +
                  " | Spread: " + DoubleToString(spread, 0) + "pts" +
                  " | ATR M15=" + DoubleToString(g_atrM15, 1) +
                  " M5=" + DoubleToString(g_atrM5, 1);
      if(g_asianValid)
         hb += " | Asian: " + DoubleToString(g_asianLow, _Digits) + "-" + DoubleToString(g_asianHigh, _Digits) +
               " (W=" + DoubleToString(g_asianWidth, _Digits) + ")";
      hb += " | H4: " + (g_htfBull ? "BULL" : g_htfBear ? "BEAR" : "NEUTRAL");
      hb += " | " + GetSessionStatus();
      hb += " | Zones: " + IntegerToString(g_swingBullishTotal) + "D/" + IntegerToString(g_swingBearishTotal) + "S";
      if(g_fvgBullishTotal + g_fvgBearishTotal > 0)
         hb += " FVGs: " + IntegerToString(g_fvgBullishTotal) + "B/" + IntegerToString(g_fvgBearishTotal) + "Bear";
      LogMsg(hb);
   }

   // Emergency stop
   if(g_daily.stopped) { CloseAllPositions(); UpdateComment(); return; }

   // Position limit
   if(CountPositions() >= MaxPositions) { UpdateComment(); return; }

   // Can we trade?
   if(!CanTrade())
   {
      if(DebugMode && TimeCurrent() % 120 < 2) // log reason every ~2 min
      {
         string reason = "";
         double dd = (g_daily.startBal > 0) ?
                     (g_daily.startBal - AccountInfoDouble(ACCOUNT_EQUITY)) / g_daily.startBal * 100.0 : 0;
         if(g_daily.stopped) reason = "DD_STOPPED";
         else if(g_daily.tradeCount >= MaxDailyTrades) reason = "MAX_TRADES(" + IntegerToString(g_daily.tradeCount) + ")";
         else if(dd >= MaxDailyLossPct) reason = "DD_LIMIT(" + DoubleToString(dd,1) + "%)";
         double spread = (double)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
         if(spread > MaxSpreadPts) reason = "SPREAD(" + DoubleToString(spread,0) + "pts)";
         if(g_lastTradeCloseTime > 0)
         {
            int mins = (int)((TimeCurrent() - g_lastTradeCloseTime) / 60);
            if(mins < CooldownMinutes) reason = "COOLDOWN(" + IntegerToString(mins) + "/" + IntegerToString(CooldownMinutes) + "m)";
         }
         if(reason == "") reason = "UNKNOWN";
         LogMsg("CANTRADE BLOCKED: " + reason);
      }
      UpdateComment();
      return;
   }

   // Update ATR
   g_atrM15 = CalcATR(14, PERIOD_M15);
   g_atrM5  = CalcATR(14, PERIOD_M5);
   if(g_atrM15 <= 0 || g_atrM5 <= 0)
   {
      if(DebugMode) LogMsg("ATR INVALID: M15=" + DoubleToString(g_atrM15, 2) + " M5=" + DoubleToString(g_atrM5, 2));
      UpdateComment();
      return;
   }

   // Detect/re-detect Asian range
   DetectAsianRange();

   // Update H4 trend (every 5 minutes)
   static datetime lastTrendUpdate = 0;
   if(TimeCurrent() - lastTrendUpdate >= 300)
   {
      bool prevBull = g_htfBull, prevBear = g_htfBear;
      UpdateHTFBias();
      if(DebugMode && (g_htfBull != prevBull || g_htfBear != prevBear))
         LogMsg("H4 TREND CHANGED: " + (prevBull ? "BULL" : prevBear ? "BEAR" : "NEUTRAL") +
                " → " + (g_htfBull ? "BULL" : g_htfBear ? "BEAR" : "NEUTRAL"));
      lastTrendUpdate = TimeCurrent();
   }

   // Detect sweep
   DetectSweep();

   // Debug: log session state periodically
   if(DebugMode && TimeCurrent() % 300 < 2)
   {
      int h = GMTHour();
      LogMsg("SESSION CHECK: GMT " + IntegerToString(h) + ":" + StringFormat("%02d", GMTMin()) +
             " | TradingWindow=" + (IsTradingWindow() ? "YES" : "NO") +
             " | TradingDay=" + (IsTradingDay() ? "YES" : "NO") +
             " | LondonSweep=" + (IsLondonSweepWindow() ? "YES" : "NO") +
             " | Overlap=" + (IsOverlapWindow() ? "YES" : "NO") +
             " | SweepBuy=" + (g_sweepDetected_Buy ? "READY" : "no") +
             " | SweepSell=" + (g_sweepDetected_Sell ? "READY" : "no"));
   }

   // Check session and trade
   if(IsTradingWindow() && IsTradingDay())
   {
      // MODE 1: Asian Range Sweep (London open priority)
      if(IsLondonSweepWindow() && UseAsianSweep)
         CheckAsianSweepEntry();

      // MODE 2: HTF Pullback (overlap window)
      if(IsOverlapWindow() && UseHTFPullback)
         CheckHTFPullbackEntry();

      // Also allow sweep entries during overlap if detected earlier
      if(IsOverlapWindow() && UseAsianSweep)
         CheckAsianSweepEntry();
   }
   else if(DebugMode && TimeCurrent() % 600 < 2)
   {
      LogMsg("OUTSIDE SESSION: No entry checks. " + GetSessionStatus());
   }

   UpdateComment();
}
//+------------------------------------------------------------------+
