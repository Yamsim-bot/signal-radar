"""
Multi-pair forex backtest: V2.1 confluence scalper on all major + minor pairs
Tests 2 configs: baseline (V2.1) and A+ optimized
"""
import MetaTrader5 as mt5
from datetime import datetime, timedelta
import sys, json, pandas as pd, numpy as np, os

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
print("=== Forex Multi-Pair Confluence Backtest ===")
mt5.initialize()
now = datetime.now()
start = now - timedelta(days=210)

# Discover available forex pairs on this broker
all_symbols = mt5.symbols_get()
avail_names = [s.name for s in all_symbols]
print(f"Total symbols: {len(avail_names)}")

# Target pairs (try plain, +, .crp variants)
targets = ['EURUSD','GBPUSD','USDJPY','USDCHF','AUDUSD','USDCAD','NZDUSD',
           'EURGBP','EURJPY','GBPJPY','AUDJPY','CADJPY','CHFJPY','NZDJPY',
           'EURAUD','EURNZD','GBPAUD','GBPNZD','EURCHF','GBPCHF',
           'AUDCAD','AUDNZD','CADCHF','NZDCHF']

pairs = []
for p in targets:
    for suffix in ['', '+', '.crp']:
        name = p + suffix
        if name in avail_names:
            pairs.append(name)
            break

print(f"Found {len(pairs)} forex pairs: {pairs}")

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

def run_backtest(m5, m15, conf_min, sl_m, rsi_b, rsi_s, min_rr, max_sl=99):
    a_open=m5["open"].values;a_close=m5["close"].values;a_high=m5["high"].values;a_low=m5["low"].values
    a_atr=m5["atr"].values;a_rsi=m5["rsi"].values;a_bb_u=m5["bb_u"].values;a_bb_l=m5["bb_l"].values
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
            tp=a_bb_u[i] if a_bb_u[i]>0 else e+a_atr[i]*2
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
            tp=a_bb_l[i] if a_bb_l[i]>0 else e-a_atr[i]*2
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

    if not trades: return None, None
    df=pd.DataFrame(trades)
    w=df[df["pnl"]>0];l=df[df["pnl"]<0]
    wr=len(w)/len(df)*100
    ec=[1000.0]
    for t in trades: ec.append(ec[-1]+t["pnl"])
    ec=pd.Series(ec);dd=(ec-ec.expanding().max())/ec.expanding().max()*100
    pf=w["pnl"].sum()/(abs(l["pnl"].sum())+0.01)
    result={"trades":len(df),"wr":round(wr,1),"ret":round(float((bal-1000)/1000*100),2),
            "dd":round(float(abs(dd.min())),2),"pf":round(float(pf),2),
            "avg_win":round(float(w["pnl"].mean()),2),"avg_loss":round(float(l["pnl"].mean()),2),
            "buy":int(len(df[df["side"]=="BUY"])),"sell":int(len(df[df["side"]=="SELL"])),
            "tp":int(len(df[df["exit"]=="TP"])),"sl":int(len(df[df["exit"]=="SL"])),
            "partial":int(len(df[df["exit"]=="PARTIAL_TP"]))}
    return result, df

# ===== CONFIGS =====
configs = {
    "V2.1_Baseline": (5, 0.4, 25, 75, 1.5, 99),
    "A+_Strict":     (6, 0.3, 20, 75, 2.0, 6),
    "A+_Balanced":   (5, 0.4, 20, 75, 1.5, 6),
    "A+_Tight":      (7, 0.3, 20, 80, 2.0, 3),
}

outdir = os.path.dirname(os.path.abspath(__file__))
all_results = {}
all_trades = {}

for pair in pairs:
    print(f"\n--- {pair} ---")
    try:
        rates = mt5.copy_rates_range(pair, mt5.TIMEFRAME_M5, start, now)
        if rates is None or len(rates) < 500:
            print(f"  No data or too few bars ({len(rates) if rates is not None else 0})")
            continue
        m5 = pd.DataFrame(rates)
        m5["time"] = pd.to_datetime(m5["time"], unit="s")
        m5.set_index("time", inplace=True)

        rates15 = mt5.copy_rates_range(pair, mt5.TIMEFRAME_M15, start, now)
        if rates15 is None or len(rates15) < 200:
            print(f"  No M15 data")
            continue
        m15 = pd.DataFrame(rates15)
        m15["time"] = pd.to_datetime(m15["time"], unit="s")
        m15.set_index("time", inplace=True)

        # Indicators
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

        pair_results = {}
        for cname, cfg in configs.items():
            r, tdf = run_backtest(m5, m15, *cfg)
            if r:
                pair_results[cname] = r
                print(f"  {cname}: {r['trades']}T {r['wr']}%WR {r['ret']:+.2f}% PF{r['pf']} DD{r['dd']}%")
                if tdf is not None:
                    all_trades[f"{pair}_{cname}"] = tdf
            else:
                print(f"  {cname}: 0 trades")
        all_results[pair] = pair_results

    except Exception as e:
        print(f"  Error: {e}")

# ===== RANKING =====
print(f"\n{'='*120}")
print("=== FOREX PAIR RANKING BY CONFIG ===")
print(f"{'='*120}")

for cname in configs:
    print(f"\n--- {cname} ---")
    print(f"{'Pair':<12} {'T':>3} {'WR%':>5} {'Ret%':>7} {'DD%':>5} {'PF':>5} {'AvgW':>7} {'AvgL':>7} {'B':>3} {'S':>3}")
    print("-"*75)
    ranked = []
    for pair, pres in all_results.items():
        if cname in pres:
            r = pres[cname]
            r["pair"] = pair
            ranked.append(r)
    ranked.sort(key=lambda x: (-x["ret"], x["dd"]))
    for i,r in enumerate(ranked):
        print(f"{r['pair']:<12} {r['trades']:>3} {r['wr']:>5.1f} {r['ret']:>+6.2f} {r['dd']:>5.1f} {r['pf']:>5.2f} {r['avg_win']:>+6.1f} {r['avg_loss']:>+6.1f} {r['buy']:>3} {r['sell']:>3}")

# Best overall
print(f"\n{'='*120}")
print("=== TOP 10 BEST PAIR+CONFIG COMBOS ===")
print(f"{'='*120}")
all_combos = []
for pair, pres in all_results.items():
    for cname, r in pres.items():
        r2 = dict(r); r2["pair"]=pair; r2["config"]=cname
        all_combos.append(r2)
all_combos.sort(key=lambda x: (-x["ret"]))
print(f"{'#':>3} {'Pair':<12} {'Config':<15} {'T':>3} {'WR%':>5} {'Ret%':>7} {'DD%':>5} {'PF':>5}")
print("-"*70)
for i,r in enumerate(all_combos[:10]):
    print(f"{i+1:>3} {r['pair']:<12} {r['config']:<15} {r['trades']:>3} {r['wr']:>5.1f} {r['ret']:>+6.2f} {r['dd']:>5.1f} {r['pf']:>5.2f}")

# Save trade logs for best combos
for key, tdf in all_trades.items():
    tdf.to_csv(os.path.join(outdir, f"forex_trades_{key}.csv"), index=False)

with open(os.path.join(outdir, "forex_results.json"),"w") as f:
    json.dump(all_results, f, indent=2, default=str)

mt5.shutdown()
print(f"\nResults and trade logs saved to {outdir}")
print("DONE")
