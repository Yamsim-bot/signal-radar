"""
Forex-optimized V2.1 sweep: find the best confluence config for day trading forex
Tests 80+ configs across 6 major pairs, targeting 15-20 trades with high WR
Key changes from gold A+:
  - Lower conf_min (3-5) since forex moves are smaller
  - More relaxed RSI (30-40 / 60-70) since forex rarely hits 20/75
  - Lower R:R (1.0-1.5) for tighter scalp targets
  - Wider SL (0.5-1.0 ATR) since forex needs room
  - TP at BB middle for faster exits
"""
import MetaTrader5 as mt5
from datetime import datetime, timedelta
import sys, json, pandas as pd, numpy as np, os, itertools

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
print("=== Forex-Optimized V2.1 Sweep ===")
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

def run_bt(m5, m15, conf_min, sl_m, rsi_b, rsi_s, min_rr, max_sl, tp_mode="bb"):
    """tp_mode: 'bb'=opposite BB, 'bbmid'=BB middle, 'atr'=1.5*ATR"""
    a_open=m5["open"].values;a_close=m5["close"].values;a_high=m5["high"].values;a_low=m5["low"].values
    a_atr=m5["atr"].values;a_rsi=m5["rsi"].values;a_bb_u=m5["bb_u"].values;a_bb_l=m5["bb_l"].values;a_bb_m=m5["bb_m"].values
    a_rej_bull=m5["rej_bull"].values;a_rej_bear=m5["rej_bear"].values
    m5_times=m5.index;m15_times=m15.index
    m5_to_m15=m15_times.get_indexer(m5_times,method="pad")

    sw_cache = {}
    for mi in range(len(m15)):
        slc_start=max(0,mi-50)
        slc_h=m15["high"].iloc[slc_start:mi+1]
        slc_l=m15["low"].iloc[slc_start:mi+1]
        if len(slc_h)<10: continue
        lb=2;sh_vals=[];sla_vals=[]
        for k in range(lb,len(slc_h)-lb):
            ok_h=True
            for j in range(1,lb+1):
                if slc_h.iloc[k]<=slc_h.iloc[k-j] or slc_h.iloc[k]<=slc_h.iloc[k+j]: ok_h=False;break
            if ok_h: sh_vals.append(slc_h.iloc[k])
            ok_l=True
            for j in range(1,lb+1):
                if slc_l.iloc[k]>=slc_l.iloc[k-j] or slc_l.iloc[k]>=slc_l.iloc[k+j]: ok_l=False;break
            if ok_l: sla_vals.append(slc_l.iloc[k])
        sw_cache[mi]=(sh_vals[-6:],sla_vals[-6:])

    trades=[];open_pos=[];bal=1000;td=0;day=None;dstart=1000
    paused=False;lclose=None;sl_today=0

    for i in range(len(a_close)):
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
        a15=m15["atr"].values[mi] if 0<=mi<len(m15) else a_atr[i]
        cb,cs=0,0
        sh_v=[];sla_v=[]
        if 0<=mi<len(m15):
            e20=m15["ema20"].values[mi];e50=m15["ema50"].values[mi];e200=m15["ema200"].values[mi]
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
# Format: (name, conf_min, sl_m, rsi_b, rsi_s, min_rr, max_sl, tp_mode)
configs = []

# Group 1: Relaxed confluence (3-4) with various RSI/R:R
for conf in [3, 4, 5]:
    for rsi_b, rsi_s in [(35,65), (30,70), (40,60)]:
        for rr in [1.0, 1.2, 1.5]:
            for sl in [0.5, 0.8]:
                for tp in ["bbmid", "bb", "atr"]:
                    configs.append((f"c{conf}_r{rsi_b}{rsi_s}_rr{rr}_sl{sl}_{tp}", conf, sl, rsi_b, rsi_s, rr, 99, tp))

# Group 2: Moderate confluence (5) with tighter controls
for rsi_b, rsi_s in [(30,70), (35,65)]:
    for rr in [1.0, 1.2, 1.5]:
        for sl in [0.5, 0.8, 1.0]:
            for max_sl in [5, 10]:
                for tp in ["bbmid", "atr"]:
                    configs.append((f"c5_r{rsi_b}{rsi_s}_rr{rr}_sl{sl}_ms{max_sl}_{tp}", 5, sl, rsi_b, rsi_s, rr, max_sl, tp))

# Group 3: Gold A+ for reference
configs.append(("gold_A+_strict", 6, 0.3, 20, 75, 2.0, 6, "bb"))
configs.append(("gold_A+_balanced", 5, 0.4, 20, 75, 1.5, 6, "bb"))

print(f"Testing {len(configs)} configs on {len(pairs)} pairs = {len(configs)*len(pairs)} backtests")

# Load pair data once
pair_data = {}
for pair in pairs:
    print(f"Loading {pair}...", end=" ")
    rates = mt5.copy_rates_range(pair, mt5.TIMEFRAME_M5, start, now)
    if rates is None or len(rates) < 500:
        print(f"SKIP ({len(rates) if rates is not None else 0} bars)")
        continue
    m5 = pd.DataFrame(rates)
    m5["time"] = pd.to_datetime(m5["time"], unit="s")
    m5.set_index("time", inplace=True)
    rates15 = mt5.copy_rates_range(pair, mt5.TIMEFRAME_M15, start, now)
    if rates15 is None or len(rates15) < 200:
        print("SKIP (no M15)")
        continue
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
    pair_data[pair]=(m5,m15)
    print(f"OK ({len(m5)} M5 bars)")

mt5.shutdown()
print(f"\nLoaded {len(pair_data)} pairs. Running sweep...\n")

# Run sweep
all_results = {}
for ci, cfg in enumerate(configs):
    name,cnf,sl,rb,rs,rr,ms,tp = cfg
    for pair, (m5,m15) in pair_data.items():
        r = run_bt(m5, m15, cnf, sl, rb, rs, rr, ms, tp)
        if r:
            key = f"{pair}|{name}"
            all_results[key] = {"pair":pair,"config":name,"conf":cnf,"sl":sl,"rsi_b":rb,"rsi_s":rs,"rr":rr,"max_sl":ms,"tp":tp,**r}
    if (ci+1) % 20 == 0:
        print(f"  {ci+1}/{len(configs)} configs done...")

# Score and rank: target 15-20 trades, high WR, positive return, low DD
print(f"\nTotal results: {len(all_results)}")

# Best per pair
print(f"\n{'='*130}")
print("=== BEST CONFIG PER PAIR (targeting 10-25 trades, WR>50%, Ret>0) ===")
print(f"{'='*130}")

for pair in pairs:
    pair_recs = [v for v in all_results.values() if v["pair"]==pair and v["trades"]>=8 and v["wr"]>=50 and v["ret"]>0]
    pair_recs.sort(key=lambda x: (-x["ret"], x["dd"]))
    print(f"\n--- {pair} (top 5 profitable configs with 8+ trades) ---")
    print(f"{'Config':<35} {'T':>3} {'WR%':>5} {'Ret%':>7} {'DD%':>5} {'PF':>5} {'Conf':>4} {'SL':>4} {'RSI':>7} {'RR':>4} {'TP':<5}")
    print("-"*105)
    for r in pair_recs[:5]:
        print(f"{r['config']:<35} {r['trades']:>3} {r['wr']:>5.1f} {r['ret']:>+6.2f} {r['dd']:>5.1f} {r['pf']:>5.2f} {r['conf']:>4} {r['sl']:>4.1f} {r['rsi_b']:>3}/{r['rsi_s']:<3} {r['rr']:>4.1f} {r['tp']:<5}")

# Overall top 20
print(f"\n{'='*130}")
print("=== TOP 20 OVERALL (weighted score: ret*0.4 + wr*0.3 - dd*0.3, min 8 trades) ===")
print(f"{'='*130}")
scored = [v for v in all_results.values() if v["trades"]>=8]
for v in scored:
    v["score"] = v["ret"]*0.4 + v["wr"]*0.3 - v["dd"]*0.3
scored.sort(key=lambda x: -x["score"])
print(f"{'#':>3} {'Pair':<8} {'Config':<35} {'T':>3} {'WR%':>5} {'Ret%':>7} {'DD%':>5} {'PF':>5} {'Score':>6}")
print("-"*95)
for i,r in enumerate(scored[:20]):
    print(f"{i+1:>3} {r['pair']:<8} {r['config']:<35} {r['trades']:>3} {r['wr']:>5.1f} {r['ret']:>+6.2f} {r['dd']:>5.1f} {r['pf']:>5.2f} {r['score']:>+6.1f}")

# Best pair+config combos
print(f"\n{'='*130}")
print("=== TOP 10 MOST TRADEABLE (highest trades with WR>55% AND Ret>5%) ===")
print(f"{'='*130}")
tradeable = [v for v in all_results.values() if v["trades"]>=10 and v["wr"]>=55 and v["ret"]>5]
tradeable.sort(key=lambda x: (-x["trades"], -x["ret"]))
print(f"{'#':>3} {'Pair':<8} {'Config':<35} {'T':>3} {'WR%':>5} {'Ret%':>7} {'DD%':>5} {'PF':>5}")
print("-"*85)
for i,r in enumerate(tradeable[:10]):
    print(f"{i+1:>3} {r['pair']:<8} {r['config']:<35} {r['trades']:>3} {r['wr']:>5.1f} {r['ret']:>+6.2f} {r['dd']:>5.1f} {r['pf']:>5.2f}")

outdir = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(outdir,"forex_sweep_results.json"),"w") as f:
    json.dump(list(all_results.values()), f, indent=2, default=str)

print(f"\nFull results saved to forex_sweep_results.json")
print("DONE")
