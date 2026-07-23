"""
Forex-optimized V2.1 sweep: find the best confluence config for day trading forex
OPTIMIZED: swing cache precomputed once per pair, reused across all configs
"""
import MetaTrader5 as mt5
from datetime import datetime, timedelta
import sys, json, pandas as pd, numpy as np, os

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
print("=== Forex-Optimized V2.1 Sweep (fast) ===")
mt5.initialize()
now = datetime.now()
start = now - timedelta(days=210)

pairs = ['EURUSD','GBPUSD','USDJPY','AUDUSD','USDCAD','NZDUSD']

def ema(s, p): return s.ewm(span=p, adjust=False).mean()
def rsi_func(s, p=14):
    d=s.diff(); g=d.clip(lower=0); l=-d.clip(upper=0)
    ag=g.ewm(span=p, adjust=False).mean(); al=l.ewm(span=p, adjust=False).mean()
    return 100-100/(1+ag/(al+1e-10))
def atr(h,l,c,p=14):
    tr=pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    return tr.rolling(p).mean()
def bb(c,p=20,sd=2.0):
    m=c.ewm(span=p, adjust=False).mean(); std=c.rolling(p).std()
    return m+sd*std, m, m-sd*std

def is_rej_bull(r):
    if r["close"]<=r["open"]: return False
    b=r["close"]-r["open"];w=r["open"]-r["low"]
    return w>=0.1*r["atr"] and (b<=0 or w/b>=0.2) and b>=0.18*r["atr"]
def is_rej_bear(r):
    if r["close"]>=r["open"]: return False
    b=r["open"]-r["close"];w=r["high"]-r["open"]
    return w>=0.1*r["atr"] and (b<=0 or w/b>=0.2) and b>=0.18*r["atr"]

def precompute_swings(m15):
    """Precompute swing point cache for all M15 bars — done ONCE per pair"""
    sw_cache = {}
    h_arr = m15["high"].values
    l_arr = m15["low"].values
    n = len(m15)
    lb = 2
    for mi in range(n):
        slc_start = max(0, mi-50)
        sh_vals = []; sla_vals = []
        for k in range(slc_start+lb, mi+1-lb):
            ok_h = True
            for j in range(1, lb+1):
                if h_arr[k] <= h_arr[k-j] or h_arr[k] <= h_arr[k+j]:
                    ok_h = False; break
            if ok_h: sh_vals.append(h_arr[k])
            ok_l = True
            for j in range(1, lb+1):
                if l_arr[k] >= l_arr[k-j] or l_arr[k] >= l_arr[k+j]:
                    ok_l = False; break
            if ok_l: sla_vals.append(l_arr[k])
        sw_cache[mi] = (sh_vals[-6:], sla_vals[-6:])
    return sw_cache

def run_bt_fast(a_open, a_close, a_high, a_low, a_atr, a_rsi, a_bb_u, a_bb_l, a_bb_m,
                a_rej_bull, a_rej_bear, m5_times, m5_to_m15, m15_atr, m15_ema20, m15_ema50, m15_ema200,
                sw_cache, conf_min, sl_m, rsi_b, rsi_s, min_rr, max_sl, tp_mode):
    """Fast backtest using precomputed numpy arrays and swing cache"""
    n = len(a_close)
    trades=[];open_pos=[];bal=1000;td=0;day=None;dstart=1000
    paused=False;lclose=None;sl_today=0

    for i in range(n):
        if np.isnan(a_atr[i]) or np.isnan(a_bb_l[i]) or a_atr[i]<=0:
            keep=[]
            for t in open_pos:
                d=1 if t["side"]=="BUY" else -1;hs=ht=False;ep=0
                if d==1:
                    if a_low[i]<=t["sl"]:hs=True;ep=t["sl"]
                    elif a_high[i]>=t["tp"]:ht=True;ep=t["tp"]
                else:
                    if a_high[i]>=t["sl"]:hs=True;ep=t["sl"]
                    elif a_low[i]<=t["tp"]:ht=True;ep=t["tp"]
                if hs or ht:
                    pnl=d*(ep-t["entry_p"])*t["lot"]*10000
                    t["pnl"]=pnl;t["exit"]="TP" if ht else "SL";bal+=pnl
                    if t["exit"]=="SL": sl_today+=1
                    trades.append(dict(t))
                else: keep.append(t)
            open_pos=keep;continue
        dt=m5_times[i];dd=dt.date()
        if day!=dd: day=dd;td=0;dstart=bal;paused=False;sl_today=0
        if not paused and dstart>0 and (dstart-bal)/dstart*100>=7.0: paused=True
        if paused or (lclose and (dt-lclose).total_seconds()<15*60):
            keep=[]
            for t in open_pos:
                d=1 if t["side"]=="BUY" else -1;hs=ht=False;ep=0
                if d==1:
                    if a_low[i]<=t["sl"]:hs=True;ep=t["sl"]
                    elif a_high[i]>=t["tp"]:ht=True;ep=t["tp"]
                else:
                    if a_high[i]>=t["sl"]:hs=True;ep=t["sl"]
                    elif a_low[i]<=t["tp"]:ht=True;ep=t["tp"]
                if hs or ht:
                    pnl=d*(ep-t["entry_p"])*t["lot"]*10000
                    t["pnl"]=pnl;t["exit"]="TP" if ht else "SL";bal+=pnl
                    if t["exit"]=="SL": sl_today+=1
                    trades.append(dict(t))
                else: keep.append(t)
            open_pos=keep;continue
        if len(open_pos)>=2 or td>=20 or sl_today>=max_sl: continue

        keep=[]
        for t in open_pos:
            d=1 if t["side"]=="BUY" else -1;hs=ht=False;ep=0
            if d==1:
                if a_low[i]<=t["sl"]:hs=True;ep=t["sl"]
                elif a_high[i]>=t["tp"]:ht=True;ep=t["tp"]
            else:
                if a_high[i]>=t["sl"]:hs=True;ep=t["sl"]
                elif a_low[i]<=t["tp"]:ht=True;ep=t["tp"]
            if hs or ht:
                pnl=d*(ep-t["entry_p"])*t["lot"]*10000
                t["pnl"]=pnl;t["exit"]="TP" if ht else "SL";bal+=pnl
                if t["exit"]=="SL": sl_today+=1
                trades.append(dict(t))
            else:
                if t.get("pt") and a_atr[i]>0:
                    pd2=abs(t["tp"]-t["entry_p"]);pp=t["entry_p"]+d*pd2*0.75
                    if (d==1 and a_high[i]>=pp) or (d==-1 and a_low[i]<=pp):
                        cl=t["lot"]*0.5;ppn=d*(pp-t["entry_p"])*cl*10000
                        t["lot"]-=cl;t["pt"]=False;bal+=ppn
                        trades.append({"side":t["side"],"entry_p":t["entry_p"],"sl":t["sl"],"tp":t["tp"],"lot":cl,"pnl":ppn,"exit":"PARTIAL_TP","exit_price":pp,"cb":t["cb"],"cs":t["cs"],"time":dt})
                keep.append(t)
        if len(keep)<len(open_pos): lclose=dt
        open_pos=keep

        mi=m5_to_m15[i]
        if mi<0: continue
        a15=m15_atr[mi] if 0<=mi<len(m15_atr) else a_atr[i]
        cb,cs=0,0
        sh_v=[];sla_v=[]
        if 0<=mi<len(m15_ema20):
            e20=m15_ema20[mi];e50=m15_ema50[mi];e200=m15_ema200[mi]
            if e20>e50: cb+=2
            else: cs+=2
            if e50>e200: cb+=1
            elif e50<e200: cs+=1
        if mi in sw_cache:
            sh_v,sla_v=sw_cache[mi]
            if len(sh_v)>=2 and len(sla_v)>=2:
                if sh_v[-1]>sh_v[-2] and sla_v[-1]>sla_v[-2]: cb+=2
                if sh_v[-1]<sh_v[-2] and sla_v[-1]<sla_v[-2]: cs+=2
            for s in sh_v[-3:]:
                if a_close[i]>s+0.1*a15 and a_close[i]<=s+0.5*a15: cb+=2;break
            for s in sla_v[-3:]:
                if a_close[i]<s-0.1*a15 and a_close[i]>=s-0.5*a15: cs+=2;break
            for s in sla_v[-3:]:
                if abs(a_close[i]-s)<=0.3*a15: cb+=1;break
            for s in sh_v[-3:]:
                if abs(a_close[i]-s)<=0.3*a15: cs+=1;break
        if i>=2:
            if a_close[i-1]<a_open[i-1] and a_close[i]>a_open[i] and abs(a_close[i]-a_open[i])>abs(a_open[i-1]-a_close[i-1]) and a_open[i]<a_close[i-1] and a_close[i]>a_open[i-1]: cb+=2
            if a_close[i-1]>a_open[i-1] and a_close[i]<a_open[i] and abs(a_open[i]-a_close[i])>abs(a_close[i-1]-a_open[i-1]) and a_open[i]>a_close[i-1] and a_close[i]<a_open[i-1]: cs+=2

        if cb>=conf_min and cb>cs:
            if a_low[i]>a_bb_l[i]*1.01 or a_bb_l[i]<=0: continue
            if a_rsi[i]>rsi_b: continue
            found_rej=False
            for ii in range(max(0,i-2),i+1):
                if a_rej_bull[ii]: found_rej=True;break
            if not found_rej: continue
            sls=[s for s in sla_v if s<a_close[i]]
            swl=max(sls) if sls else 0
            raw=min(a_low[i],swl) if swl>0 else a_low[i]
            slp=raw-sl_m*a_atr[i];e=a_close[i]
            if e-slp>2.0*a_atr[i]: slp=e-2.0*a_atr[i]
            if e-slp<0.25*a_atr[i]: slp=e-0.25*a_atr[i]
            if slp>=e: continue
            if tp_mode=="bb": tp=a_bb_u[i] if a_bb_u[i]>0 else e+a_atr[i]*2
            elif tp_mode=="bbmid": tp=a_bb_m[i] if a_bb_m[i]>0 else e+a_atr[i]*1.0
            else: tp=e+a_atr[i]*1.5
            if tp<=e: continue
            if (tp-e)/(e-slp)<min_rr: continue
            lot=max(0.01,min(1.0,round(bal*0.5/100/((e-slp)*10000)/0.01)*0.01))
            if lot<=0: continue
            open_pos.append({"side":"BUY","entry_p":e,"sl":slp,"tp":tp,"lot":lot,"cb":cb,"cs":cs,"pt":True,"time":dt})
            td+=1
        elif cs>=conf_min and cs>cb:
            if a_high[i]<a_bb_u[i]*0.99 or a_bb_u[i]<=0: continue
            if a_rsi[i]<rsi_s: continue
            found_rej=False
            for ii in range(max(0,i-2),i+1):
                if a_rej_bear[ii]: found_rej=True;break
            if not found_rej: continue
            shs=[s for s in sh_v if s>a_close[i]]
            swh=min(shs) if shs else 99999
            raw=max(a_high[i],swh) if swh<99999 else a_high[i]
            slp=raw+sl_m*a_atr[i];e=a_close[i]
            if slp-e>2.0*a_atr[i]: slp=e+2.0*a_atr[i]
            if slp-e<0.25*a_atr[i]: slp=e+0.25*a_atr[i]
            if slp<=e: continue
            if tp_mode=="bb": tp=a_bb_l[i] if a_bb_l[i]>0 else e-a_atr[i]*2
            elif tp_mode=="bbmid": tp=a_bb_m[i] if a_bb_m[i]>0 else e-a_atr[i]*1.0
            else: tp=e-a_atr[i]*1.5
            if tp>=e: continue
            if (e-tp)/(slp-e)<min_rr: continue
            lot=max(0.01,min(1.0,round(bal*0.5/100/((slp-e)*10000)/0.01)*0.01))
            if lot<=0: continue
            open_pos.append({"side":"SELL","entry_p":e,"sl":slp,"tp":tp,"lot":lot,"cb":cb,"cs":cs,"pt":True,"time":dt})
            td+=1

    for t in open_pos:
        d=1 if t["side"]=="BUY" else -1;lc=a_close[-1]
        pnl=d*(lc-t["entry_p"])*t["lot"]*10000
        t["pnl"]=pnl;t["exit"]="MARKET";bal+=pnl;trades.append(dict(t))

    if not trades: return None
    df=pd.DataFrame(trades)
    w=df[df["pnl"]>0];l=df[df["pnl"]<0]
    if len(w)==0 or len(l)==0: return None
    wr=len(w)/len(df)*100
    ec=[1000.0]
    for t in trades: ec.append(ec[-1]+t["pnl"])
    ec=pd.Series(ec);dd=(ec-ec.expanding().max())/ec.expanding().max()*100
    pf=w["pnl"].sum()/(abs(l["pnl"].sum())+0.01)
    return {"trades":len(df),"wr":round(wr,1),"ret":round(float((bal-1000)/1000*100),2),
            "dd":round(float(abs(dd.min())),2),"pf":round(float(pf),2),
            "avg_win":round(float(w["pnl"].mean()),2),"avg_loss":round(float(l["pnl"].mean()),2),
            "buy":int(len(df[df["side"]=="BUY"])),"sell":int(len(df[df["side"]=="SELL"]))}

# ===== FOREX-FRIENDLY CONFIGS =====
configs = []
# Group 1: Low confluence + relaxed RSI/R:R
for conf in [3, 4]:
    for rsi_b, rsi_s in [(40,60), (35,65), (30,70)]:
        for rr in [1.0, 1.2]:
            for sl in [0.5, 0.8]:
                for tp in ["bbmid", "atr"]:
                    configs.append((f"c{conf}_r{rsi_b}{rsi_s}_rr{rr}_sl{sl}_{tp}", conf, sl, rsi_b, rsi_s, rr, 99, tp))
# Group 2: Mid confluence
for conf in [5]:
    for rsi_b, rsi_s in [(35,65), (30,70), (25,75)]:
        for rr in [1.0, 1.2, 1.5]:
            for sl in [0.5, 0.8]:
                for tp in ["bbmid", "atr", "bb"]:
                    configs.append((f"c{conf}_r{rsi_b}{rsi_s}_rr{rr}_sl{sl}_{tp}", conf, sl, rsi_b, rsi_s, rr, 99, tp))
# Group 3: With max_sl control
for conf in [4, 5]:
    for rsi_b, rsi_s in [(35,65), (30,70)]:
        for rr in [1.0, 1.2]:
            for sl in [0.5, 0.8]:
                for ms in [5, 10]:
                    configs.append((f"c{conf}_r{rsi_b}{rsi_s}_rr{rr}_sl{sl}_ms{ms}", conf, sl, rsi_b, rsi_s, rr, ms, "bbmid"))

print(f"Testing {len(configs)} configs on {len(pairs)} pairs")

# Load pair data + precompute swings ONCE
pair_data = {}
for pair in pairs:
    print(f"Loading {pair}...", end=" ", flush=True)
    rates = mt5.copy_rates_range(pair, mt5.TIMEFRAME_M5, start, now)
    if rates is None or len(rates) < 500:
        print(f"SKIP"); continue
    m5 = pd.DataFrame(rates)
    m5["time"] = pd.to_datetime(m5["time"], unit="s")
    m5.set_index("time", inplace=True)
    rates15 = mt5.copy_rates_range(pair, mt5.TIMEFRAME_M15, start, now)
    if rates15 is None or len(rates15) < 200:
        print("SKIP (no M15)"); continue
    m15 = pd.DataFrame(rates15)
    m15["time"] = pd.to_datetime(m15["time"], unit="s")
    m15.set_index("time", inplace=True)
    m5["atr"]=atr(m5["high"],m5["low"],m5["close"],14)
    m5["rsi"]=rsi_func(m5["close"],14)
    bu,bm,bl=bb(m5["close"],20,2.0)
    m5["bb_u"],m5["bb_m"],m5["bb_l"]=bu,bm,bl
    m15["atr"]=atr(m15["high"],m15["low"],m15["close"],14)
    m15["ema20"]=ema(m15["close"],20)
    m15["ema50"]=ema(m15["close"],50)
    m15["ema200"]=ema(m15["close"],200)
    m5["rej_bull"]=m5.apply(is_rej_bull,axis=1)
    m5["rej_bear"]=m5.apply(is_rej_bear,axis=1)

    print("computing swings...", end=" ", flush=True)
    sw = precompute_swings(m15)

    # Pre-extract numpy arrays
    data = {
        "a_open": m5["open"].values, "a_close": m5["close"].values,
        "a_high": m5["high"].values, "a_low": m5["low"].values,
        "a_atr": m5["atr"].values, "a_rsi": m5["rsi"].values,
        "a_bb_u": m5["bb_u"].values, "a_bb_l": m5["bb_l"].values,
        "a_bb_m": m5["bb_m"].values,
        "a_rej_bull": m5["rej_bull"].values, "a_rej_bear": m5["rej_bear"].values,
        "m5_times": m5.index,
        "m5_to_m15": m15.index.get_indexer(m5.index, method="pad"),
        "m15_atr": m15["atr"].values, "m15_ema20": m15["ema20"].values,
        "m15_ema50": m15["ema50"].values, "m15_ema200": m15["ema200"].values,
        "sw_cache": sw
    }
    pair_data[pair] = data
    print(f"OK ({len(m5)} bars)")

mt5.shutdown()
print(f"\nLoaded {len(pair_data)} pairs. Running {len(configs)*len(pair_data)} backtests...\n")

# Run sweep — swing cache reused across all configs
all_results = []
for ci, cfg in enumerate(configs):
    name,cnf,sl,rb,rs,rr,ms,tp = cfg
    for pair, d in pair_data.items():
        r = run_bt_fast(d["a_open"],d["a_close"],d["a_high"],d["a_low"],d["a_atr"],d["a_rsi"],
                       d["a_bb_u"],d["a_bb_l"],d["a_bb_m"],d["a_rej_bull"],d["a_rej_bear"],
                       d["m5_times"],d["m5_to_m15"],d["m15_atr"],d["m15_ema20"],d["m15_ema50"],
                       d["m15_ema200"],d["sw_cache"], cnf, sl, rb, rs, rr, ms, tp)
        if r:
            all_results.append({"pair":pair,"config":name,"conf":cnf,"sl":sl,"rsi_b":rb,"rsi_s":rs,"rr":rr,"max_sl":ms,"tp":tp,**r})
    if (ci+1) % 10 == 0:
        print(f"  {ci+1}/{len(configs)} configs done ({len(all_results)} results so far)")

# Score and rank
print(f"\nTotal results: {len(all_results)}")

print(f"\n{'='*130}")
print("=== BEST CONFIG PER PAIR (8+ trades, WR>50%, Ret>0%) ===")
print(f"{'='*130}")

for pair in pairs:
    pr = [v for v in all_results if v["pair"]==pair and v["trades"]>=8 and v["wr"]>=50 and v["ret"]>0]
    pr.sort(key=lambda x: (-x["ret"], x["dd"]))
    print(f"\n--- {pair} ---")
    print(f"{'Config':<35} {'T':>3} {'WR%':>5} {'Ret%':>7} {'DD%':>5} {'PF':>5} {'B':>3} {'S':>3}")
    print("-"*80)
    for r in pr[:5]:
        print(f"{r['config']:<35} {r['trades']:>3} {r['wr']:>5.1f} {r['ret']:>+6.2f} {r['dd']:>5.1f} {r['pf']:>5.2f} {r['buy']:>3} {r['sell']:>3}")

# Overall top 20 (weighted: ret*0.4 + wr*0.3 - dd*0.3)
print(f"\n{'='*130}")
print("=== TOP 20 OVERALL SCORE (min 8 trades) ===")
print(f"{'='*130}")
scored = [v for v in all_results if v["trades"]>=8]
for v in scored:
    v["score"] = v["ret"]*0.4 + v["wr"]*0.3 - v["dd"]*0.3
scored.sort(key=lambda x: -x["score"])
print(f"{'#':>3} {'Pair':<8} {'Config':<35} {'T':>3} {'WR%':>5} {'Ret%':>7} {'DD%':>5} {'PF':>5} {'Score':>7}")
print("-"*95)
for i,r in enumerate(scored[:20]):
    print(f"{i+1:>3} {r['pair']:<8} {r['config']:<35} {r['trades']:>3} {r['wr']:>5.1f} {r['ret']:>+6.2f} {r['dd']:>5.1f} {r['pf']:>5.2f} {r['score']:>+7.1f}")

# Most tradeable (10+ trades, WR>55%, Ret>5%)
print(f"\n{'='*130}")
print("=== TOP 10 MOST TRADEABLE (10+ trades, WR>55%, Ret>5%) ===")
print(f"{'='*130}")
tradeable = [v for v in all_results if v["trades"]>=10 and v["wr"]>=55 and v["ret"]>5]
tradeable.sort(key=lambda x: (-x["trades"], -x["ret"]))
print(f"{'#':>3} {'Pair':<8} {'Config':<35} {'T':>3} {'WR%':>5} {'Ret%':>7} {'DD%':>5} {'PF':>5}")
print("-"*85)
for i,r in enumerate(tradeable[:10]):
    print(f"{i+1:>3} {r['pair']:<8} {r['config']:<35} {r['trades']:>3} {r['wr']:>5.1f} {r['ret']:>+6.2f} {r['dd']:>5.1f} {r['pf']:>5.2f}")

outdir = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(outdir,"forex_sweep_results.json"),"w") as f:
    json.dump(all_results, f, indent=2, default=str)

print(f"\nResults saved to forex_sweep_results.json")
print("DONE")
