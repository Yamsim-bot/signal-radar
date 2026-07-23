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
