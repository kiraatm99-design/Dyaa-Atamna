"""
╔══════════════════════════════════════════════════╗
║         DASI BET — Football Prediction Bot       ║
║                    v3.0                          ║
║  Single-file · GitHub · Replit · Render · Railway║
╚══════════════════════════════════════════════════╝
المفاتيح تُقرأ من .env فقط — لا تُكتب في الكود أبداً
"""

# ═══════════════════════════════════════════════════════════════
#  IMPORTS
# ═══════════════════════════════════════════════════════════════
import logging, os, json, hashlib, threading, time, uuid, re, asyncio
import requests
from difflib import SequenceMatcher
from datetime import datetime, timedelta, time as dtime
from typing import Optional
from flask import Flask
from threading import Thread

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from groq import Groq
except ImportError:
    Groq = None

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (ApplicationBuilder, CommandHandler,
                           MessageHandler, CallbackQueryHandler,
                           filters, ContextTypes)

# ═══════════════════════════════════════════════════════════════
#  CONFIG — من .env فقط
# ═══════════════════════════════════════════════════════════════
def _env(key, default=""):
    return os.environ.get(key, default).strip()

TELEGRAM_TOKEN   = _env("TELEGRAM_TOKEN")
GROQ_API_KEY     = _env("GROQ_API_KEY")
FOOTBALL_API_KEY = _env("FOOTBALL_API_KEY")
ODDS_API_KEY     = _env("ODDS_API_KEY")
APIFOOTBALL_KEY  = _env("APIFOOTBALL_KEY")   # api-football (ركنيات/بطاقات/لايف)
TAVILY_API_KEY   = _env("TAVILY_API_KEY")

CHANNEL          = _env("CHANNEL",       "@dasi_bet")
CHANNEL_URL      = _env("CHANNEL_URL",   "https://t.me/dasi_bet")
ADMIN_ID         = int(_env("ADMIN_ID",  "7046072164"))
ADMIN_USERNAME   = _env("ADMIN_USERNAME","@dasi_supportt")
BOT_USERNAME     = _env("BOT_USERNAME",  "dasiibet_bot")
BET_LINK         = _env("BET_LINK",      "https://reffpa.com/L?tag=d_5553701m_1599c_&site=5553701&ad=1599")

FREE_LIMIT       = 3
REFERRAL_GOAL    = 5
VIP_DAYS         = 30
POINTS_PER_VIP   = 100
POINTS_PER_REF   = 10
POINTS_BUY_PRED  = 50
PORT             = int(_env("PORT", "8080"))

DB_FILE          = "data/users.json"
CACHE_FILE       = "data/cache.json"
WELCOME_ID_FILE  = "data/welcome_file_id.txt"

TTL_MATCHES  = 60
TTL_ODDS     = 30
TTL_LIVE     = 2
TTL_ANALYSIS = 360
TTL_SAFE_BET = 120
TTL_TOP3     = 120

def _current_season():
    n = datetime.now()
    return str(n.year - 1) if n.month < 8 else str(n.year)
SEASON = _current_season()

LEAGUES = {
    "PL":  {"name":"🏴󠁧󠁢󠁥󠁮󠁧󠁿 الإنجليزي",  "id":2021,"odds_key":"soccer_epl",                  "apif_id":39},
    "PD":  {"name":"🇪🇸 الإسباني",    "id":2014,"odds_key":"soccer_spain_la_liga",         "apif_id":140},
    "BL1": {"name":"🇩🇪 الألماني",    "id":2002,"odds_key":"soccer_germany_bundesliga",     "apif_id":78},
    "SA":  {"name":"🇮🇹 الإيطالي",    "id":2019,"odds_key":"soccer_italy_serie_a",          "apif_id":135},
    "FL1": {"name":"🇫🇷 الفرنسي",     "id":2015,"odds_key":"soccer_france_ligue_one",       "apif_id":61},
    "CL":  {"name":"🌍 أبطال أوروبا","id":2001,"odds_key":"soccer_uefa_champs_league",      "apif_id":2},
}

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

try:
    groq_client = Groq(api_key=GROQ_API_KEY) if (Groq and GROQ_API_KEY) else None
except Exception as _e:
    logger.warning(f"Groq: {_e}"); groq_client = None

_db_lock    = threading.Lock()
_cache_lock = threading.Lock()

# ═══════════════════════════════════════════════════════════════
#  CACHE
# ═══════════════════════════════════════════════════════════════
def _ensure_dirs():
    os.makedirs("data", exist_ok=True)

def _load_cache():
    _ensure_dirs()
    try:
        with open(CACHE_FILE,"r",encoding="utf-8") as f: return json.load(f)
    except: return {}

def _save_cache(c):
    _ensure_dirs()
    with _cache_lock:
        tmp = CACHE_FILE+".tmp"
        with open(tmp,"w",encoding="utf-8") as f: json.dump(c,f,ensure_ascii=False,indent=2)
        os.replace(tmp, CACHE_FILE)

def cache_key(*parts):
    return hashlib.md5("|".join(str(p).lower().strip() for p in parts).encode()).hexdigest()[:16]

def cache_get(key, ttl_minutes):
    c = _load_cache()
    if key not in c: return None
    try:
        t = datetime.strptime(c[key]["time"],"%Y-%m-%d %H:%M")
        if datetime.now()-t > timedelta(minutes=ttl_minutes): return None
        return c[key]["data"]
    except: return None

def cache_set(key, data):
    c = _load_cache()
    c[key] = {"data":data,"time":datetime.now().strftime("%Y-%m-%d %H:%M")}
    if len(c)>600:
        for k,_ in sorted(c.items(),key=lambda x:x[1]["time"])[:100]: del c[k]
    _save_cache(c)

def cache_clear(): _save_cache({})

# ═══════════════════════════════════════════════════════════════
#  DATABASE
# ═══════════════════════════════════════════════════════════════
def db_load():
    _ensure_dirs()
    try:
        with open(DB_FILE,"r",encoding="utf-8") as f: return json.load(f)
    except: return {"users":{},"total_requests":0}

def db_save(db):
    _ensure_dirs()
    with _db_lock:
        tmp = DB_FILE+".tmp"
        with open(tmp,"w",encoding="utf-8") as f: json.dump(db,f,ensure_ascii=False,indent=2)
        os.replace(tmp, DB_FILE)

def db_user(db, uid, update=None):
    k = str(uid)
    if k not in db["users"]:
        db["users"][k] = {
            "name":              getattr(getattr(update,"effective_user",None),"full_name",""),
            "username":          getattr(getattr(update,"effective_user",None),"username",""),
            "joined":            datetime.now().strftime("%Y-%m-%d %H:%M"),
            "requests_today":    0,"bonus_requests":0,"last_request_date":"",
            "total_requests":    0,"vip":False,"vip_expiry":"","blocked":False,
            "points":            0,"referrals":[],"referred_by":"",
            "history":           [],"results":[],"first_visit":True,
        }
        db_save(db)
    return db["users"][k]

def is_vip(db, uid):
    if uid == ADMIN_ID: return True
    u = db_user(db,uid)
    if not u["vip"]: return False
    if u["vip_expiry"] and datetime.now().strftime("%Y-%m-%d") > u["vip_expiry"]:
        u["vip"]=False; db_save(db); return False
    return True

def get_limit(db, uid):
    return 9999 if is_vip(db,uid) else FREE_LIMIT+db_user(db,uid).get("bonus_requests",0)

def has_quota(db, uid):
    if is_vip(db,uid): return True
    u=db_user(db,uid); today=datetime.now().strftime("%Y-%m-%d")
    if u["last_request_date"]!=today: u["requests_today"]=0; u["last_request_date"]=today; db_save(db)
    return u["requests_today"]<get_limit(db,uid)

def remaining(db, uid):
    if is_vip(db,uid): return "♾️"
    u=db_user(db,uid); today=datetime.now().strftime("%Y-%m-%d")
    used=u["requests_today"] if u["last_request_date"]==today else 0
    return max(0, get_limit(db,uid)-used)

def consume(db, uid, match):
    u=db_user(db,uid); today=datetime.now().strftime("%Y-%m-%d")
    if u["last_request_date"]!=today: u["requests_today"]=0; u["last_request_date"]=today
    u["requests_today"]+=1; u["total_requests"]+=1
    db["total_requests"]=db.get("total_requests",0)+1
    u["history"].append({"match":match,"date":datetime.now().strftime("%Y-%m-%d %H:%M")})
    u["history"]=u["history"][-20:]
    _add_points(db,uid,5)

def _add_points(db, uid, pts):
    u=db_user(db,uid); u["points"]=u.get("points",0)+pts
    if u["points"]>=POINTS_PER_VIP:
        u["points"]-=POINTS_PER_VIP; u["vip"]=True
        u["vip_expiry"]=(datetime.now()+timedelta(days=1)).strftime("%Y-%m-%d")
        db_save(db); return True
    db_save(db); return False

def buy_prediction(db, uid):
    u=db_user(db,uid)
    if u.get("points",0)<POINTS_BUY_PRED: return False
    u["points"]-=POINTS_BUY_PRED; u["bonus_requests"]=u.get("bonus_requests",0)+1
    db_save(db); return True

def activate_vip(db, uid):
    u=db_user(db,uid); u["vip"]=True
    expiry=(datetime.now()+timedelta(days=VIP_DAYS)).strftime("%Y-%m-%d")
    u["vip_expiry"]=expiry; db_save(db); return expiry

def handle_referral(db, new_uid, ref_id):
    if str(new_uid)==ref_id or ref_id not in db.get("users",{}): return
    ref=db_user(db,int(ref_id))
    if str(new_uid) in ref.get("referrals",[]): return
    ref.setdefault("referrals",[]).append(str(new_uid))
    db_user(db,new_uid)["referred_by"]=ref_id
    if len(ref["referrals"])%REFERRAL_GOAL==0:
        ref["bonus_requests"]=ref.get("bonus_requests",0)+1
    _add_points(db,int(ref_id),POINTS_PER_REF); db_save(db)

def save_result(db, uid, match, pred_winner, correct):
    u=db_user(db,uid)
    u.setdefault("results",[]).append({
        "match":match,"pred":pred_winner,"correct":correct,
        "date":datetime.now().strftime("%Y-%m-%d")})
    u["results"]=u["results"][-50:]; db_save(db)

def get_results_summary(db, uid):
    u=db_user(db,uid); results=u.get("results",[])
    if not results: return "📊 لا يوجد سجل نتائج بعد."
    total=len(results); correct=sum(1 for r in results if r.get("correct"))
    pct=round(correct/total*100) if total else 0
    lines=[]
    for r in reversed(results[-5:]):
        icon="✅" if r.get("correct") else "❌"
        lines.append(f"{icon} {r['match']} — {r['pred']}")
    return (f"📊 *سجل النتائج*\n━━━━━━━━━━━━━━━━━━\n"
            f"🎯 الدقة: *{pct}%* ({correct}/{total})\n\n"
            f"*آخر 5 توقعات:*\n"+"\n".join(lines))

# ═══════════════════════════════════════════════════════════════
#  FOOTBALL-DATA API
# ═══════════════════════════════════════════════════════════════
_FAPI_BASE    = "https://api.football-data.org/v4"
_FAPI_HEADERS = {"X-Auth-Token": FOOTBALL_API_KEY}

def _fapi(endpoint, params=None, retries=2):
    for attempt in range(retries+1):
        try:
            r=requests.get(f"{_FAPI_BASE}/{endpoint}",headers=_FAPI_HEADERS,params=params,timeout=12)
            if r.status_code==200: return r.json()
            logger.warning(f"FootballData {r.status_code}: {endpoint}")
        except requests.Timeout: logger.warning(f"FootballData timeout ({attempt+1})")
        except Exception as e: logger.error(f"FootballData: {e}")
        if attempt<retries: time.sleep(1.5)
    return None

def get_matches(league_code, date):
    ck=cache_key("matches",league_code,date)
    cached=cache_get(ck,TTL_MATCHES)
    if cached: return cached
    lid=LEAGUES[league_code]["id"]
    data=_fapi(f"competitions/{lid}/matches",{"dateFrom":date,"dateTo":date,"season":SEASON})
    if not data or not data.get("matches"):
        data=_fapi(f"competitions/{lid}/matches",{"dateFrom":date,"dateTo":date})
    if not data: return []
    result=[{
        "home":m["homeTeam"]["name"],"away":m["awayTeam"]["name"],
        "time":m["utcDate"][11:16],"league":LEAGUES[league_code]["name"],
        "code":league_code,"home_id":m["homeTeam"].get("id",0),"away_id":m["awayTeam"].get("id",0)
    } for m in data.get("matches",[])]
    if result: cache_set(ck,result)
    return result

def get_all_matches(date):
    ck=cache_key("all_matches",date); cached=cache_get(ck,TTL_MATCHES)
    if cached: return cached
    all_m=[]
    for code in LEAGUES: all_m.extend(get_matches(code,date))
    if all_m: cache_set(ck,all_m)
    return all_m

def get_team_form(team_id):
    if not team_id: return {}
    ck=cache_key("form",team_id); cached=cache_get(ck,TTL_MATCHES)
    if cached: return cached
    data=_fapi(f"teams/{team_id}/matches",{"status":"FINISHED","limit":5})
    if not data: return {}
    wins=draws=losses=gf_total=ga_total=0; results_str=[]
    for m in data.get("matches",[]):
        ht=m["score"]["fullTime"].get("home",0) or 0
        at=m["score"]["fullTime"].get("away",0) or 0
        is_home=m["homeTeam"].get("id")==team_id
        gf=ht if is_home else at; ga=at if is_home else ht
        gf_total+=gf; ga_total+=ga
        if gf>ga: wins+=1; results_str.append("✅")
        elif gf==ga: draws+=1; results_str.append("🟡")
        else: losses+=1; results_str.append("❌")
    played=len(data.get("matches",[]))
    form={"wins":wins,"draws":draws,"losses":losses,"goals_for":gf_total,
          "goals_against":ga_total,"played":played,
          "results":" ".join(results_str[-5:]),
          "form_score":round((wins*3+draws)/max(played*3,1)*100,1)}
    cache_set(ck,form); return form

def get_standings(league_id):
    ck=cache_key("standings",league_id); cached=cache_get(ck,TTL_MATCHES)
    if cached: return cached
    data=_fapi(f"competitions/{league_id}/standings",{"season":SEASON})
    if not data: return {}
    standings={}
    for table in data.get("standings",[]):
        if table.get("type")=="TOTAL":
            for row in table.get("table",[]):
                tid=row["team"].get("id")
                if tid: standings[tid]={"position":row["position"],"points":row["points"],
                    "played":row["playedGames"],"won":row["won"],"draw":row["draw"],
                    "lost":row["lost"],"gf":row["goalsFor"],"ga":row["goalsAgainst"]}
    cache_set(ck,standings); return standings

# ═══════════════════════════════════════════════════════════════
#  API-FOOTBALL (لايف + ركنيات + بطاقات)
# ═══════════════════════════════════════════════════════════════
_APIF_BASE    = "https://v3.football.api-sports.io"
_APIF_HEADERS = {"x-apisports-key": APIFOOTBALL_KEY}

def _apif(endpoint, params=None):
    if not APIFOOTBALL_KEY: return None
    try:
        r=requests.get(f"{_APIF_BASE}/{endpoint}",headers=_APIF_HEADERS,params=params,timeout=12)
        if r.status_code==200: return r.json()
        logger.warning(f"APIFootball {r.status_code}: {endpoint}")
    except Exception as e: logger.error(f"APIFootball: {e}")
    return None

def get_live_matches():
    ck=cache_key("live"); cached=cache_get(ck,TTL_LIVE)
    if cached: return cached
    data=_apif("fixtures",{"live":"all"})
    if not data: return []
    result=[]
    for m in data.get("response",[])[:15]:
        fix=m.get("fixture",{}); teams=m.get("teams",{})
        goals=m.get("goals",{}); stats=m.get("statistics",[])
        home=teams.get("home",{}).get("name",""); away=teams.get("away",{}).get("name","")
        gh=goals.get("home",0) or 0; ga=goals.get("away",0) or 0
        minute=fix.get("status",{}).get("elapsed",0) or 0
        corners_h=corners_a=cards_h=cards_a=0
        for st in stats:
            tn=st.get("team",{}).get("name","")
            for s in st.get("statistics",[]):
                t=s.get("type",""); v=s.get("value") or 0
                if t=="Corner Kicks":
                    if tn==home: corners_h=v
                    else: corners_a=v
                if t in ("Yellow Cards","Red Cards"):
                    if tn==home: cards_h+=v
                    else: cards_a+=v
        result.append({"home":home,"away":away,"score":f"{gh}-{ga}","minute":minute,
                        "corners_h":corners_h,"corners_a":corners_a,
                        "cards_h":cards_h,"cards_a":cards_a,
                        "league":m.get("league",{}).get("name","")})
    if result: cache_set(ck,result)
    return result

def get_team_stats_apif(team_id, apif_league_id):
    if not team_id or not apif_league_id or not APIFOOTBALL_KEY: return {}
    ck=cache_key("apif_stats",team_id,apif_league_id); cached=cache_get(ck,TTL_MATCHES*6)
    if cached: return cached
    season=datetime.now().year if datetime.now().month>=8 else datetime.now().year-1
    data=_apif("teams/statistics",{"team":team_id,"league":apif_league_id,"season":season})
    if not data or not data.get("response"): return {}
    r=data["response"]
    played=max(r.get("fixtures",{}).get("played",{}).get("total",1) or 1,1)
    stats={
        "avg_corners": round((r.get("statistics",{}).get("corners",{}).get("total",{}).get("total",0) or 0)/played,2),
        "avg_cards":   round(((r.get("cards",{}).get("yellow",{}).get("total",0) or 0)+
                               (r.get("cards",{}).get("red",{}).get("total",0) or 0))/played,2),
        "avg_goals_for":     float(r.get("goals",{}).get("for",{}).get("average",{}).get("total",0) or 0),
        "avg_goals_against": float(r.get("goals",{}).get("against",{}).get("average",{}).get("total",0) or 0),
    }
    cache_set(ck,stats); return stats

def format_live(matches):
    if not matches: return "😔 لا توجد مباريات لايف الآن."
    lines=["🔴 *المباريات اللايف الآن*\n━━━━━━━━━━━━━━━━━━"]
    for m in matches[:8]:
        lines.append(
            f"\n⚽ *{m['home']} {m['score']} {m['away']}* ({m['minute']}')\n"
            f"  🎯 ركنيات: {m['corners_h']+m['corners_a']} | 🟨 بطاقات: {m['cards_h']+m['cards_a']}\n"
            f"  🏟️ {m['league']}")
    return "\n".join(lines)

# ═══════════════════════════════════════════════════════════════
#  ODDS API
# ═══════════════════════════════════════════════════════════════
_ODDS_BASE = "https://api.the-odds-api.com/v4"

_NAME_MAP = {
    "manchester united fc":["manchester utd","man united","manchester united"],
    "manchester city fc":["manchester city","man city"],
    "nottingham forest fc":["nottingham forest"],
    "tottenham hotspur fc":["tottenham","spurs","tottenham hotspur"],
    "newcastle united fc":["newcastle","newcastle united"],
    "wolverhampton wanderers fc":["wolverhampton","wolves"],
    "brighton & hove albion fc":["brighton","brighton & hove albion"],
    "west ham united fc":["west ham","west ham united"],
    "aston villa fc":["aston villa"],"chelsea fc":["chelsea"],
    "arsenal fc":["arsenal"],"liverpool fc":["liverpool"],
    "everton fc":["everton"],"leicester city fc":["leicester"],
    "crystal palace fc":["crystal palace"],"brentford fc":["brentford"],
    "fulham fc":["fulham"],"bournemouth":["afc bournemouth"],
    "ipswich town fc":["ipswich"],"southampton fc":["southampton"],
    "atletico madrid":["atletico de madrid"],"real betis":["betis"],
    "athletic bilbao":["athletic club"],"inter milan":["inter","internazionale"],
    "ac milan":["milan"],"paris saint-germain fc":["psg","paris saint-germain"],
    "bayer 04 leverkusen":["leverkusen","bayer leverkusen"],
    "borussia dortmund":["dortmund"],"rb leipzig":["leipzig"],
    "fc bayern münchen":["bayern munich","bayern münchen","bayern"],
}

def _norm(name):
    return re.sub(r"[^a-z0-9 ]","",name.lower()).strip()

def _match(n1,n2):
    a,b=n1.lower(),n2.lower()
    if a==b: return True
    ac,bc=_norm(n1),_norm(n2)
    if ac==bc or ac in bc or bc in ac: return True
    for key,aliases in _NAME_MAP.items():
        ns={key}|set(aliases)
        if a in ns and b in ns: return True
        if any(x in a for x in aliases) and any(x in b for x in aliases): return True
    return SequenceMatcher(None,ac,bc).ratio()>=0.72

def get_league_odds(sport_key):
    if not ODDS_API_KEY or not sport_key: return []
    ck=cache_key("lodds",sport_key); cached=cache_get(ck,TTL_ODDS)
    if cached: return cached
    try:
        r=requests.get(f"{_ODDS_BASE}/sports/{sport_key}/odds",
            params={"apiKey":ODDS_API_KEY,"regions":"eu","markets":"h2h,totals,btts","oddsFormat":"decimal"},
            timeout=12)
        if r.status_code==200:
            data=r.json()
            if data: cache_set(ck,data)
            return data or []
        logger.warning(f"OddsAPI {r.status_code} {sport_key}")
    except Exception as e: logger.error(f"OddsAPI: {e}")
    return []

def _parse_odds(events, home, away):
    for ev in events:
        if not _match(home,ev.get("home_team","")): continue
        if not _match(away,ev.get("away_team","")): continue
        odds={k:None for k in ["home_win","draw","away_win","over_2_5","under_2_5","btts_yes","btts_no"]}
        for bk in ev.get("bookmakers",[])[:4]:
            for mkt in bk.get("markets",[]):
                if mkt["key"]=="h2h":
                    for o in mkt.get("outcomes",[]):
                        n=o["name"].lower(); p=round(o["price"],2)
                        if "draw" in n: odds["draw"]=odds["draw"] or p
                        elif _match(o["name"],home): odds["home_win"]=odds["home_win"] or p
                        else: odds["away_win"]=odds["away_win"] or p
                elif mkt["key"]=="totals":
                    for o in mkt.get("outcomes",[]):
                        p=round(o["price"],2); pt=o.get("point",0)
                        if abs(pt-2.5)<0.1:
                            if "over" in o["name"].lower(): odds["over_2_5"]=odds["over_2_5"] or p
                            else: odds["under_2_5"]=odds["under_2_5"] or p
                elif mkt["key"]=="btts":
                    for o in mkt.get("outcomes",[]):
                        p=round(o["price"],2)
                        if "yes" in o["name"].lower(): odds["btts_yes"]=odds["btts_yes"] or p
                        elif "no" in o["name"].lower(): odds["btts_no"]=odds["btts_no"] or p
        return odds
    return {}

def get_real_odds(home, away, sport_key):
    if not ODDS_API_KEY or not sport_key: return {}
    ck=cache_key("odds",home,away); cached=cache_get(ck,TTL_ODDS)
    if cached: return cached
    events=get_league_odds(sport_key)
    result=_parse_odds(events,home,away)
    if result: cache_set(ck,result)
    return result or {}

# ═══════════════════════════════════════════════════════════════
#  PREDICTION ENGINE
# ═══════════════════════════════════════════════════════════════
def calc_confidence(odd):
    if not odd or odd<=1.0: return 60
    return max(50,min(95,round((1/odd)*100)))

def _strength(form, standing, is_home):
    s=50.0+form.get("form_score",50)*0.3
    s+=max(0,(20-standing.get("position",10)))*1.2
    played=max(form.get("played",1),1)
    s+=(form.get("goals_for",0)/played)*3-(form.get("goals_against",0)/played)*2
    if is_home: s+=6
    return round(min(100,max(0,s)),1)

def predict_match(home, away, home_id=0, away_id=0, league_id=0, odds=None):
    hf=get_team_form(home_id) if home_id else {}
    af=get_team_form(away_id) if away_id else {}
    st=get_standings(league_id) if league_id else {}
    hs=_strength(hf,st.get(home_id,{}),True)
    as_=_strength(af,st.get(away_id,{}),False)
    diff=hs-as_; odds=odds or {}
    if diff>8:   winner,rk,wo="home_win","home_win",odds.get("home_win"); winner=home
    elif diff<-8: winner,rk,wo=away,"away_win",odds.get("away_win")
    else:         winner,rk,wo="تعادل","draw",odds.get("draw")
    ph=max(hf.get("played",1),1); pa=max(af.get("played",1),1)
    hgf=round(hf.get("goals_for",0)/ph,1); agf=round(af.get("goals_for",0)/pa,1)
    hg=max(0,round(hgf*0.85+(0.3 if diff>0 else 0)))
    ag=max(0,round(agf*0.85+(0.3 if diff<0 else 0)))
    btts=hf.get("goals_for",0)>0 and af.get("goals_for",0)>0
    ol=2.5 if hgf+agf>=2.5 else 1.5
    return {
        "home":home,"away":away,"winner":winner,"result_key":rk,
        "score":f"{hg}-{ag}","best_bet":winner,"best_odd":wo,
        "confidence":calc_confidence(wo),"home_strength":hs,"away_strength":as_,
        "over_line":ol,"over_pred":"أوفر" if hgf+agf>=ol else "أندر",
        "over_odd":odds.get("over_2_5"),"btts":"نعم" if btts else "لا",
        "btts_odd":odds.get("btts_yes" if btts else "btts_no"),
        "home_results":hf.get("results","—"),"away_results":af.get("results","—"),
        "home_position":st.get(home_id,{}).get("position","—"),
        "away_position":st.get(away_id,{}).get("position","—"),
        "home_form_score":hf.get("form_score",0),"away_form_score":af.get("form_score",0),
        "home_gf_avg":hgf,"away_gf_avg":agf,
        "home_win_odd":odds.get("home_win"),"draw_odd":odds.get("draw"),"away_win_odd":odds.get("away_win"),
    }

# ═══════════════════════════════════════════════════════════════
#  AI — Groq للشرح فقط
# ═══════════════════════════════════════════════════════════════
_SYS = """أنت محلل كرة قدم. مهمتك شرح التوقع الجاهز فقط.
القواعد: 1) العربية فقط. 2) لا تغيّر التوقع أو الأود. 3) الأسباب تدعم الفائز فقط. 4) لا تخترع أخبار."""

def _groq(system, user, tokens=600):
    if not groq_client: return "❌ Groq غير مكوّن."
    for i in range(3):
        try:
            r=groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",max_tokens=tokens,temperature=0.3,
                messages=[{"role":"system","content":system},{"role":"user","content":user}])
            return r.choices[0].message.content.strip()
        except Exception as e: logger.warning(f"Groq {i+1}: {e}"); time.sleep(2)
    return "❌ تعذّر الاتصال."

def generate_reasons(pred):
    ck=cache_key("reasons",pred["home"],pred["away"]); cached=cache_get(ck,TTL_ANALYSIS)
    if cached: return cached
    msg=(f"التوقع: {pred['home']} vs {pred['away']} — الفائز: {pred['winner']} ({pred['confidence']}%)\n"
         f"شكل {pred['home']}: {pred['home_results']} | قوة {pred['home_strength']}/100 | مركز {pred['home_position']}\n"
         f"شكل {pred['away']}: {pred['away_results']} | قوة {pred['away_strength']}/100 | مركز {pred['away_position']}\n"
         f"اكتب تحليلاً (5-7 أسطر بالعربية) يدعم فوز {pred['winner']} فقط.")
    result=_groq(_SYS,msg,600); cache_set(ck,result); return result

# ═══════════════════════════════════════════════════════════════
#  MESSAGE BUILDERS
# ═══════════════════════════════════════════════════════════════
def fo(o): return f"{o:.2f}" if o else "—"

def build_analysis(pred):
    return (
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⚽ *{pred['home']} vs {pred['away']}*\n━━━━━━━━━━━━━━━━━━\n"
        f"🏆 *التوقع:* {pred['winner']}"+(f" | 💰 {fo(pred['best_odd'])}" if pred['best_odd'] else "")+"\n"
        f"📊 *النتيجة:* {pred['score']} | 📈 *الثقة:* {pred['confidence']}%\n\n"
        f"💰 *الأود الكامل:*\n"
        f"  • فوز {pred['home']}: {fo(pred['home_win_odd'])}\n"
        f"  • تعادل: {fo(pred['draw_odd'])}\n"
        f"  • فوز {pred['away']}: {fo(pred['away_win_odd'])}\n\n"
        f"⚽ *أهداف:* {pred['over_pred']} {pred['over_line']}"+(f" | 💰 {fo(pred['over_odd'])}" if pred['over_odd'] else "")+"\n"
        f"👥 *كلاهما يسجل:* {pred['btts']}"+(f" | 💰 {fo(pred['btts_odd'])}" if pred['btts_odd'] else "")+"\n\n"
        f"💡 *أفضل رهان:* {pred['best_bet']}"+(f" | 💰 {fo(pred['best_odd'])}" if pred['best_odd'] else "")+"\n"
        f"━━━━━━━━━━━━━━━━━━\n⚠️ _للترفيه فقط_"
    )

def build_corners_msg(pred, hs, as_):
    hc=hs.get("avg_corners",0); ac=as_.get("avg_corners",0); tc=round(hc+ac,1)
    hcd=hs.get("avg_cards",0); acd=as_.get("avg_cards",0); tcd=round(hcd+acd,1)
    lc=9.5 if tc>=9.5 else 8.5 if tc>=8.5 else 7.5
    lcd=3.5 if tcd>=3.5 else 2.5
    return (f"🎯 *ركنيات وبطاقات*\n━━━━━━━━━━━━━━━━━━\n"
            f"⚽ *{pred['home']} vs {pred['away']}*\n\n"
            f"🎯 *الركنيات:*\n"
            f"  • {pred['home']}: {hc} | {pred['away']}: {ac} | مجموع: {tc}\n"
            f"  ✅ *{'أوفر' if tc>=lc else 'أندر'} {lc} ركنيات*\n\n"
            f"🟨 *البطاقات:*\n"
            f"  • {pred['home']}: {hcd} | {pred['away']}: {acd} | مجموع: {tcd}\n"
            f"  ✅ *{'أوفر' if tcd>=lcd else 'أندر'} {lcd} بطاقات*\n"
            f"━━━━━━━━━━━━━━━━━━\n⚠️ _للترفيه فقط_")

# ═══════════════════════════════════════════════════════════════
#  أفضل 3 رهانات + أضمن رهان
# ═══════════════════════════════════════════════════════════════
def _get_preds(matches, min_conf=62):
    le={}; preds=[]
    for m in matches[:20]:
        try:
            code=m.get("code","PL"); ok=LEAGUES[code]["odds_key"]; lid=LEAGUES[code]["id"]
            if ok not in le: le[ok]=get_league_odds(ok)
            odds=_parse_odds(le[ok],m["home"],m["away"])
            p=predict_match(m["home"],m["away"],m.get("home_id",0),m.get("away_id",0),lid,odds)
            if p["best_odd"] and p["confidence"]>=min_conf: preds.append(p)
        except: pass
    return sorted(preds,key=lambda x:x["confidence"],reverse=True)

def get_top3(matches):
    ck=cache_key("top3",datetime.now().strftime("%Y-%m-%d")); cached=cache_get(ck,TTL_TOP3)
    if cached: return cached
    top=_get_preds(matches)[:3]
    if not top: result="😔 لا توجد رهانات آمنة كافية اليوم."; cache_set(ck,result); return result
    lines=["🔥 *أفضل 3 رهانات اليوم*\n━━━━━━━━━━━━━━━━━━"]
    for i,p in enumerate(top,1):
        lines.append(f"\n{i}. *{p['home']} vs {p['away']}*\n   ✅ {p['best_bet']} | 💰 {fo(p['best_odd'])} | 📈 {p['confidence']}%")
    lines.append("\n━━━━━━━━━━━━━━━━━━\n⚠️ _للترفيه فقط_")
    result="\n".join(lines); cache_set(ck,result); return result

def get_safe_bet(matches):
    ck=cache_key("safe",datetime.now().strftime("%Y-%m-%d")); cached=cache_get(ck,TTL_SAFE_BET)
    if cached: return cached
    preds=_get_preds(matches,60)
    if not preds: result="😔 لا توجد مباريات كافية."; cache_set(ck,result); return result
    p=preds[0]
    result=(f"🔒 *أضمن رهان اليوم*\n━━━━━━━━━━━━━━━━━━\n"
            f"⚽ *{p['home']} vs {p['away']}*\n"
            f"✅ *{p['best_bet']}* | 💰 {fo(p['best_odd'])} | 📈 {p['confidence']}%\n"
            f"📊 {p['home_results']} / {p['away_results']}\n"
            f"━━━━━━━━━━━━━━━━━━\n⚠️ _للترفيه فقط_")
    cache_set(ck,result); return result

# ═══════════════════════════════════════════════════════════════
#  القسيمة الذهبية
# ═══════════════════════════════════════════════════════════════
def _safe_pick(p):
    hw,aw,dw=p.get("home_win_odd"),p.get("away_win_odd"),p.get("draw_odd")
    cands=[]
    if hw and hw<=1.85: cands.append((f"فوز {p['home']}",hw,calc_confidence(hw)))
    if aw and aw<=1.85: cands.append((f"فوز {p['away']}",aw,calc_confidence(aw)))
    if hw and dw:
        dc=round(1/(1/hw+1/dw),2)
        if dc<=1.50: cands.append((f"{p['home']} أو تعادل (1X)",dc,calc_confidence(dc)))
    if aw and dw:
        dc=round(1/(1/aw+1/dw),2)
        if dc<=1.50: cands.append((f"{p['away']} أو تعادل (X2)",dc,calc_confidence(dc)))
    if not cands: return None,None,0
    cands.sort(key=lambda x:x[2],reverse=True); return cands[0]

def ai_coupon(target, m_today, m_tmrw=None):
    ck=cache_key("coupon",str(target),datetime.now().strftime("%Y-%m-%d"))
    cached=cache_get(ck,TTL_SAFE_BET)
    if cached: return cached
    all_m=list(m_today or [])
    if m_tmrw: all_m.extend(m_tmrw)
    le={}; cands=[]
    for m in all_m[:30]:
        try:
            code=m.get("code","PL"); ok=LEAGUES[code]["odds_key"]; lid=LEAGUES[code]["id"]
            if ok not in le: le[ok]=get_league_odds(ok)
            odds=_parse_odds(le[ok],m["home"],m["away"])
            p=predict_match(m["home"],m["away"],m.get("home_id",0),m.get("away_id",0),lid,odds)
            bl,bo,bc=_safe_pick(p)
            day="📅 اليوم" if m in m_today else "📆 الغد"
            if bl and bo and bc>=60:
                cands.append({"home":m["home"],"away":m["away"],"bet":bl,"odd":bo,"conf":bc,"day":day})
        except Exception as e: logger.warning(f"coupon: {e}")
    if not cands:
        result="😔 لا توجد مباريات آمنة كافية."; cache_set(ck,result); return result
    cands.sort(key=lambda x:x["conf"],reverse=True)
    sel=[]; cur=1.0
    for c in cands:
        if len(sel)>=10: break
        nw=round(cur*c["odd"],3)
        if nw<=target*1.30: sel.append(c); cur=nw
        if cur>=target*0.85: break
    if not sel: sel=cands[:4]; cur=1.0
    for c in sel: cur=round(cur*c["odd"],3)
    actual=round(cur,2)
    lines=[]
    for i,c in enumerate(sel,1):
        lines.append(str(i)+". "+c["day"]+" | *"+c["home"]+" vs "+c["away"]+"*\n"
                     "   \u2705 "+c["bet"]+" | \U0001f4b0 "+str(round(c["odd"],2))+" | \U0001f4c8 "+str(c["conf"])+"%")
    result=("\U0001f3ab *\u0627\u0644\u0642\u0633\u064a\u0645\u0629 \u0627\u0644\u0630\u0647\u0628\u064a\u0629*\n"
            +"\U0001f3af \u0627\u0644\u0623\u0648\u062f \u0627\u0644\u0645\u0637\u0644\u0648\u0628: *"+str(target)+"*\n"
            +"\u2501"*18+"\n"+"\n".join(lines)+"\n"+"\u2501"*18+"\n"
            +"\U0001f4b0 \u0627\u0644\u0623\u0648\u062f \u0627\u0644\u0641\u0639\u0644\u064a: *"+str(actual)+"x*\n"
            +"\U0001f4ca \u0627\u062d\u062a\u0645\u0627\u0644 \u0627\u0644\u0646\u062c\u0627\u062d: *"+str(min(95,round(100/actual)))+"%*\n"
            +"\u26a0\ufe0f _\u0644\u0644\u062a\u0631\u0641\u064a\u0647 \u0641\u0642\u0637_")
    cache_set(ck,result); return result

# ═══════════════════════════════════════════════════════════════
#  MATCH STORE
# ═══════════════════════════════════════════════════════════════
def store_match(ctx, m):
    mid=uuid.uuid4().hex[:8]; ctx.user_data.setdefault("matches",{})[mid]=m; return mid
def retrieve_match(ctx, mid): return ctx.user_data.get("matches",{}).get(mid)

# ═══════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════
def ref_link(uid): return f"https://t.me/{BOT_USERNAME}?start=ref_{uid}"
def day_date(day): return ((datetime.now()+timedelta(days=1)).strftime("%Y-%m-%d") if day=="tomorrow" else datetime.now().strftime("%Y-%m-%d"))
def day_lbl(day): return "الغد 📆" if day=="tomorrow" else "اليوم 📅"
def _li(code): lg=LEAGUES.get(code,{}); return lg.get("id",0),lg.get("odds_key",""),lg.get("apif_id",0)

async def ssend(msg, text, **kw):
    try: await msg.reply_text(text[:4096],parse_mode="Markdown",**kw)
    except:
        try: await msg.reply_text(text[:4096],**kw)
        except Exception as e: logger.error(f"ssend: {e}")

async def sedit(q, text, **kw):
    try: await q.edit_message_text(text[:4096],parse_mode="Markdown",**kw)
    except:
        try: await q.edit_message_text(text[:4096],**kw)
        except Exception as e: logger.error(f"sedit: {e}")

async def _run_pred(home, away, home_id, away_id, code):
    loop=asyncio.get_event_loop()
    def _b():
        lid,ok,_=_li(code)
        ev=get_league_odds(ok) if ok else []
        odds=_parse_odds(ev,home,away) if ev else {}
        pred=predict_match(home,away,home_id,away_id,lid,odds)
        return pred,build_analysis(pred)
    return await loop.run_in_executor(None,_b)

# ═══════════════════════════════════════════════════════════════
#  KEYBOARDS
# ═══════════════════════════════════════════════════════════════
def kb_main(vip):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 مباريات اليوم",    callback_data="leagues_today"),
         InlineKeyboardButton("📆 مباريات الغد",     callback_data="leagues_tomorrow")],
        [InlineKeyboardButton("🔒 أضمن رهان",        callback_data="safe_bet"),
         InlineKeyboardButton("🔥 أفضل 3 رهانات",   callback_data="top3")],
        [InlineKeyboardButton("🔴 مباريات لايف",     callback_data="live"),
         InlineKeyboardButton("🎫 القسيمة الذهبية", callback_data="coupon")],
        [InlineKeyboardButton("⚽ توقع مباراة",      callback_data="predict"),
         InlineKeyboardButton("📊 سجل النتائج",      callback_data="results")],
        [InlineKeyboardButton("👥 أحل صديقاً",       callback_data="referral"),
         InlineKeyboardButton("📊 إحصائياتي",        callback_data="my_stats")],
        [InlineKeyboardButton("🛒 اشترِ توقعاً (50 نقطة)", callback_data="buy_pred")],
        [InlineKeyboardButton("💎 VIP ✅ نشط" if vip else "💎 اشترك VIP — $5/شهر",
                              callback_data="my_stats" if vip else "vip_info")],
    ])

def kb_leagues(day):
    rows=[]; items=list(LEAGUES.items())
    for i in range(0,len(items),2):
        row=[InlineKeyboardButton(items[i][1]["name"],callback_data=f"league_{items[i][0]}_{day}")]
        if i+1<len(items): row.append(InlineKeyboardButton(items[i+1][1]["name"],callback_data=f"league_{items[i+1][0]}_{day}"))
        rows.append(row)
    rows.append([InlineKeyboardButton("🔙 رجوع",callback_data="back_main")]); return InlineKeyboardMarkup(rows)

def kb_matches(mlist, ctx, code, day):
    rows=[]
    for m in mlist[:10]:
        mid=store_match(ctx,m)
        rows.append([InlineKeyboardButton(f"⚽ {m['home']} vs {m['away']}  🕐{m['time']}",callback_data=f"match_{mid}")])
    rows.append([InlineKeyboardButton("🔙 رجوع",callback_data=f"leagues_{day}")]); return InlineKeyboardMarkup(rows)

def kb_after(mid, corners=False):
    rows=[[InlineKeyboardButton("🔍 سبب التوقع",callback_data=f"reason_{mid}")],
          [InlineKeyboardButton("💰 راهن الآن 1xBet",url=BET_LINK)]]
    if corners: rows.insert(1,[InlineKeyboardButton("🎯 ركنيات وبطاقات",callback_data=f"corners_{mid}")])
    rows.append([InlineKeyboardButton("✅ صحيح",callback_data=f"res_win_{mid}"),
                 InlineKeyboardButton("❌ خاطئ",callback_data=f"res_lose_{mid}")])
    rows.append([InlineKeyboardButton("🔙 الرئيسية",callback_data="back_main")]); return InlineKeyboardMarkup(rows)

def kb_vip():
    return InlineKeyboardMarkup([[InlineKeyboardButton("💳 اشترك $5/شهر",callback_data="pay_vip")],
                                  [InlineKeyboardButton("🔙 رجوع",callback_data="back_main")]])
def kb_back():
    return InlineKeyboardMarkup([[InlineKeyboardButton("💰 راهن الآن 1xBet",url=BET_LINK),
                                   InlineKeyboardButton("🔙 رجوع",callback_data="back_main")]])
def kb_bet():
    return InlineKeyboardMarkup([[InlineKeyboardButton("💰 راهن الآن 1xBet",url=BET_LINK)],
                                  [InlineKeyboardButton("🔙 رجوع",callback_data="back_main")]])

# ═══════════════════════════════════════════════════════════════
#  WELCOME + HOME
# ═══════════════════════════════════════════════════════════════
async def _welcome(msg):
    cap=(f"👑 *أهلاً بك في DASI BET!*\n\n"
         f"🏆 بوت التوقعات الرياضية الاحترافي\n"
         f"تحليلات حقيقية • أود واقعي • توقعات دقيقة 🚀\n\n"
         f"📢 اشترك في قناتنا:\n{CHANNEL_URL}")
    kb=InlineKeyboardMarkup([[InlineKeyboardButton("📢 اشترك في القناة",url=CHANNEL_URL)]])
    try:
        if os.path.exists(WELCOME_ID_FILE):
            fid=open(WELCOME_ID_FILE).read().strip()
            if fid: await msg.reply_photo(photo=fid,caption=cap,parse_mode="Markdown",reply_markup=kb); return
        if os.path.exists("welcome.png"):
            with open("welcome.png","rb") as img:
                sent=await msg.reply_photo(photo=img,caption=cap,parse_mode="Markdown",reply_markup=kb)
            _ensure_dirs(); open(WELCOME_ID_FILE,"w").write(sent.photo[-1].file_id)
        else: await ssend(msg,cap,reply_markup=kb)
    except Exception as e: logger.warning(f"welcome: {e}"); await ssend(msg,cap,reply_markup=kb)

async def _home(msg, uid, db):
    u=db_user(db,uid); badge="💎 VIP" if is_vip(db,uid) else "🆓 مجاني"
    name=getattr(getattr(msg,"chat",None),"first_name","")
    await ssend(msg,f"👑 *DASI BET — {name}*\n\n"
                f"🏷️ {badge} | 🎯 متبقي: *{remaining(db,uid)}* | ⭐ {u.get('points',0)}/100\n\n"
                f"اختر من القائمة 👇",reply_markup=kb_main(is_vip(db,uid)))

# ═══════════════════════════════════════════════════════════════
#  HANDLERS — USER
# ═══════════════════════════════════════════════════════════════
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db=db_load(); uid=update.effective_user.id; u=db_user(db,uid,update)
    if context.args and context.args[0].startswith("ref_"): handle_referral(db,uid,context.args[0][4:])
    if u.get("first_visit",True): u["first_visit"]=False; db_save(db); await _welcome(update.message)
    await _home(update.message,uid,db)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db=db_load(); uid=update.effective_user.id; text=update.message.text.strip()
    u=db_user(db,uid,update)
    if u.get("blocked"): return
    mode=context.user_data.pop("mode",None) or "predict"

    if mode=="review":
        try: await context.bot.send_message(ADMIN_ID,f"⭐ *تقييم*\n👤 {u.get('name','?')} | `{uid}`\n\n💬 {text}",parse_mode="Markdown")
        except: pass
        await ssend(update.message,"✅ شكراً! تم إرسال تقييمك 🙏"); return

    if mode=="coupon":
        try:
            target=float(text.replace(",",".")); assert 1.5<=target<=100
        except:
            await ssend(update.message,"❌ أرسل رقماً بين 1.5 و 100\nمثال: `5.00`")
            context.user_data["mode"]="coupon"; return
        wait=await update.message.reply_text("🎫 جاري بناء القسيمة من مباريات اليوم والغد...")
        try:
            today=datetime.now().strftime("%Y-%m-%d")
            tmrw=(datetime.now()+timedelta(days=1)).strftime("%Y-%m-%d")
            mt=get_all_matches(today); mm=get_all_matches(tmrw)
            if not mt and not mm: await wait.edit_text("😔 لا توجد مباريات كافية."); return
            loop=asyncio.get_event_loop()
            result=await loop.run_in_executor(None,ai_coupon,target,mt,mm)
            await wait.delete()
            await ssend(update.message,result,reply_markup=kb_bet())
        except Exception as e:
            logger.error(f"coupon: {e}")
            try: await wait.edit_text("❌ حدث خطأ، حاول مرة أخرى.")
            except: pass
        return

    if not has_quota(db,uid):
        link=ref_link(uid)
        await ssend(update.message,
            f"⛔ *انتهت توقعاتك اليوم!*\n\n"
            f"🆓 كل {REFERRAL_GOAL} إحالات = توقع مجاني\n`{link}`\n\n"
            f"🛒 50 نقطة = توقع إضافي\n💎 VIP = $5/شهر",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💎 VIP",callback_data="vip_info")],
                [InlineKeyboardButton("🛒 اشترِ توقعاً",callback_data="buy_pred")],
                [InlineKeyboardButton("👥 الإحالة",callback_data="referral")],
            ])); return

    has_vs=" vs " in text.lower(); has_dad=" ضد " in text
    if len(text)<3 or (not has_vs and not has_dad):
        await ssend(update.message,"⚽ أرسل المباراة بصيغة:\n`ريال مدريد vs برشلونة`\nأو: `ريال مدريد ضد برشلونة`"); return

    wait=await update.message.reply_text("🔍 جاري التحليل...")
    try:
        if has_vs: idx=text.lower().index(" vs "); home=text[:idx].strip(); away=text[idx+4:].strip()
        else: parts=text.split(" ضد ",1); home=parts[0].strip(); away=parts[1].strip() if len(parts)>1 else text
        pred,analysis=await _run_pred(home,away,0,0,"PL")
        consume(db,uid,text); db_save(db)
        mid=store_match(context,{"home":home,"away":away,"home_id":0,"away_id":0,"code":"PL","pred":pred})
        context.user_data[f"pred_{mid}"]=pred
        await wait.delete()
        await ssend(update.message,analysis)
        await update.message.reply_text(f"🎯 متبقي: *{remaining(db,uid)}*",
            parse_mode="Markdown",reply_markup=kb_after(mid,corners=bool(APIFOOTBALL_KEY)))
    except Exception as e:
        logger.error(f"predict: {e}")
        try: await wait.edit_text("❌ حدث خطأ، حاول مرة أخرى.")
        except: pass

# ═══════════════════════════════════════════════════════════════
#  HANDLERS — CALLBACKS
# ═══════════════════════════════════════════════════════════════
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    db=db_load(); uid=q.from_user.id; d=q.data

    if d.startswith("reason_"):
        mid=d[7:]; pred=context.user_data.get(f"pred_{mid}")
        if not pred:
            m=retrieve_match(context,mid); pred=m.get("pred") if m else None
        if not pred: await q.edit_message_text("❌ انتهت الجلسة."); return
        await q.edit_message_text("🔍 جاري إعداد التحليل المفصل...")
        try:
            result=await asyncio.get_event_loop().run_in_executor(None,generate_reasons,pred)
            await sedit(q,result,reply_markup=kb_back())
        except Exception as e: logger.error(e); await q.edit_message_text("❌ حدث خطأ.")

    elif d.startswith("corners_"):
        mid=d[8:]; pred=context.user_data.get(f"pred_{mid}")
        m=retrieve_match(context,mid)
        if not pred and m: pred=m.get("pred")
        if not pred: await q.edit_message_text("❌ انتهت الجلسة."); return
        if not APIFOOTBALL_KEY:
            await sedit(q,"🔒 *هذه الميزة تتطلب API-Football*\nأضف `APIFOOTBALL_KEY` في .env",reply_markup=kb_back()); return
        await q.edit_message_text("🎯 جاري جلب الإحصائيات...")
        try:
            code=m.get("code","PL") if m else "PL"; _,_,al=_li(code)
            loop=asyncio.get_event_loop()
            hs=await loop.run_in_executor(None,get_team_stats_apif,m.get("home_id",0),al)
            as_=await loop.run_in_executor(None,get_team_stats_apif,m.get("away_id",0),al)
            await sedit(q,build_corners_msg(pred,hs,as_),reply_markup=kb_back())
        except Exception as e: logger.error(e); await q.edit_message_text("❌ حدث خطأ.")

    elif d.startswith("res_"):
        parts=d.split("_"); correct=parts[1]=="win"; mid=parts[2]
        pred=context.user_data.get(f"pred_{mid}"); m=retrieve_match(context,mid)
        if pred and m:
            save_result(db,uid,f"{m['home']} vs {m['away']}",pred.get("winner","?"),correct)
            try: await q.answer("✅ تم حفظ النتيجة!" if correct else "❌ تم حفظ النتيجة!")
            except: pass

    elif d=="live":
        await q.edit_message_text("🔴 جاري جلب المباريات اللايف...")
        try:
            matches=await asyncio.get_event_loop().run_in_executor(None,get_live_matches)
            await sedit(q,format_live(matches),reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 تحديث",callback_data="live")],
                [InlineKeyboardButton("💰 راهن الآن 1xBet",url=BET_LINK)],
                [InlineKeyboardButton("🔙 رجوع",callback_data="back_main")]]))
        except Exception as e: logger.error(e); await q.edit_message_text("❌ حدث خطأ.")

    elif d=="top3":
        await q.edit_message_text("🔥 جاري تحليل أفضل 3 رهانات اليوم...")
        try:
            matches=get_all_matches(datetime.now().strftime("%Y-%m-%d"))
            if not matches: await sedit(q,"😔 لا توجد مباريات.",reply_markup=kb_back()); return
            result=await asyncio.get_event_loop().run_in_executor(None,get_top3,matches)
            await sedit(q,result,reply_markup=kb_bet())
        except Exception as e: logger.error(e); await q.edit_message_text("❌ حدث خطأ.")

    elif d=="safe_bet":
        await q.edit_message_text("🔒 جاري البحث عن أضمن رهان...")
        try:
            matches=get_all_matches(datetime.now().strftime("%Y-%m-%d"))
            if not matches: await sedit(q,"😔 لا توجد مباريات.",reply_markup=kb_back()); return
            result=await asyncio.get_event_loop().run_in_executor(None,get_safe_bet,matches)
            await sedit(q,result,reply_markup=kb_bet())
        except Exception as e: logger.error(e); await q.edit_message_text("❌ حدث خطأ.")

    elif d=="results":
        await sedit(q,get_results_summary(db,uid),reply_markup=kb_back())

    elif d in ("leagues_today","leagues_tomorrow"):
        day="today" if d=="leagues_today" else "tomorrow"
        await sedit(q,f"🏆 *اختر الدوري — {day_lbl(day)}:*",reply_markup=kb_leagues(day))

    elif d.startswith("league_"):
        parts=d.split("_"); code=parts[1]; day=parts[2] if len(parts)>2 else "today"
        if code not in LEAGUES: await q.edit_message_text("❌ دوري غير معروف."); return
        await q.edit_message_text(f"⏳ جاري جلب مباريات {LEAGUES[code]['name']}...")
        matches=get_matches(code,day_date(day))
        if not matches: await sedit(q,f"😔 لا توجد مباريات {day_lbl(day)}.",reply_markup=kb_back())
        else: await sedit(q,f"📅 *{LEAGUES[code]['name']} — {day_lbl(day)}*\n\nاضغط مباراة للتحليل 👇",reply_markup=kb_matches(matches,context,code,day))

    elif d.startswith("match_"):
        mid=d[6:]; m=retrieve_match(context,mid)
        if not m: await q.edit_message_text("❌ انتهت الجلسة، اختر الدوري من جديد."); return
        if not has_quota(db,uid): await sedit(q,"⛔ *انتهت توقعاتك!*",reply_markup=kb_vip()); return
        home,away,code=m["home"],m["away"],m.get("code","PL")
        await q.edit_message_text(f"🔍 جاري تحليل {home} vs {away}...")
        try:
            pred,analysis=await _run_pred(home,away,m.get("home_id",0),m.get("away_id",0),code)
            consume(db,uid,f"{home} vs {away}"); db_save(db)
            context.user_data[f"pred_{mid}"]=pred; m["pred"]=pred
            await sedit(q,analysis)
            await context.bot.send_message(q.message.chat_id,f"🎯 متبقي: *{remaining(db,uid)}*",
                parse_mode="Markdown",reply_markup=kb_after(mid,corners=bool(APIFOOTBALL_KEY)))
        except Exception as e: logger.error(f"match: {e}"); await q.edit_message_text("❌ حدث خطأ.")

    elif d=="predict":
        context.user_data["mode"]="predict"
        await sedit(q,"⚽ *أرسل اسم المباراة:*\n\nمثال: `ريال مدريد vs برشلونة`\nأو: `Manchester City vs Arsenal`")

    elif d=="coupon":
        if not is_vip(db,uid): await sedit(q,"🔒 *القسيمة للـ VIP فقط!*",reply_markup=kb_vip()); return
        context.user_data["mode"]="coupon"
        await sedit(q,"🎫 *القسيمة الذهبية*\n\nأرسل الأود الإجمالي:\nمثال: `5.00` أو `10.00` أو `20.00`\n\nسأختار مباريات اليوم والغد 🎯")

    elif d=="buy_pred":
        u=db_user(db,uid); pts=u.get("points",0)
        if pts<POINTS_BUY_PRED:
            await sedit(q,f"🛒 *شراء توقع إضافي*\n\nنقاطك: *{pts}* | تحتاج: *{POINTS_BUY_PRED}*\n\nاحصل على نقاط عبر الإحالة 👥",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👥 الإحالة",callback_data="referral")],[InlineKeyboardButton("🔙 رجوع",callback_data="back_main")]])); return
        if buy_prediction(db,uid): await sedit(q,f"✅ *تم شراء توقع إضافي!*\n\nنقاطك المتبقية: *{db_user(db,uid)['points']}*",reply_markup=kb_back())
        else: await sedit(q,"❌ فشل الشراء.",reply_markup=kb_back())

    elif d=="referral":
        u=db_user(db,uid); refs=len(u.get("referrals",[])); link=ref_link(uid)
        await sedit(q,
            f"👥 *نظام الإحالة*\n\n🔗 رابطك:\n`{link}`\n\n"
            f"📊 إحالاتك: *{refs}*\n"
            f"⭐ كل إحالة = {POINTS_PER_REF} نقاط\n"
            f"🛒 {POINTS_BUY_PRED} نقطة = توقع إضافي\n"
            f"💎 {POINTS_PER_VIP} نقطة = يوم VIP مجاني\n"
            f"🎁 كل {REFERRAL_GOAL} إحالات = توقع مجاني",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📤 شارك الرابط",url=f"https://t.me/share/url?url={link}&text=🏆 أفضل بوت توقعات!")],
                [InlineKeyboardButton("🔙 رجوع",callback_data="back_main")]]))

    elif d=="my_stats":
        u=db_user(db,uid); badge="💎 VIP" if is_vip(db,uid) else "🆓 مجاني"
        xp=f"\n📅 ينتهي: {u['vip_expiry']}" if u.get("vip_expiry") else ""
        total=len(u.get("results",[])); correct=sum(1 for r in u.get("results",[]) if r.get("correct"))
        acc=f"{round(correct/total*100)}%" if total else "—"
        await sedit(q,
            f"📊 *إحصائياتك*\n\n🏷️ {badge}{xp}\n"
            f"🎯 متبقي: {remaining(db,uid)} | 📈 إجمالي: {u['total_requests']}\n"
            f"✅ الدقة: {acc} | 👥 الإحالات: {len(u.get('referrals',[]))}\n"
            f"⭐ النقاط: {u.get('points',0)}/{POINTS_PER_VIP}\n"
            f"🎁 توقعات مكسوبة: {u.get('bonus_requests',0)}\n"
            f"📅 انضمت: {u['joined']}",reply_markup=kb_back())

    elif d=="vip_info":
        await sedit(q,
            f"💎 *VIP — $5/شهر*\n\n"
            f"✅ توقعات غير محدودة\n✅ القسيمة الذهبية\n"
            f"✅ مباريات لايف\n✅ ركنيات وبطاقات\n"
            f"✅ أفضل 3 رهانات يومياً\n✅ أضمن رهان\n\n"
            f"للاشتراك: {ADMIN_USERNAME}",reply_markup=kb_vip())

    elif d=="pay_vip":
        await sedit(q,f"💳 *للاشتراك VIP:*\n\n👤 {ADMIN_USERNAME}\n💰 $5/شهر | ⚡ تفعيل فوري\n\nطرق الدفع: USDT · PayPal · تحويل بنكي")

    elif d=="back_main":
        u=db_user(db,uid); badge="💎 VIP" if is_vip(db,uid) else "🆓 مجاني"
        await sedit(q,
            f"👑 *DASI BET — {q.from_user.first_name}*\n\n"
            f"🏷️ {badge} | 🎯 متبقي: *{remaining(db,uid)}* | ⭐ {u.get('points',0)}/100\n\n"
            f"اختر من القائمة 👇",reply_markup=kb_main(is_vip(db,uid)))

# ═══════════════════════════════════════════════════════════════
#  ADMIN
# ═══════════════════════════════════════════════════════════════
def _adm(fn):
    async def w(update,context):
        if update.effective_user.id!=ADMIN_ID: return
        await fn(update,context)
    return w

@_adm
async def cmd_admin(update,context):
    db=db_load(); today=datetime.now().strftime("%Y-%m-%d")
    total=len(db["users"]); vip_c=sum(1 for u in db["users"].values() if u.get("vip"))
    active=sum(1 for u in db["users"].values() if u.get("last_request_date")==today)
    await update.message.reply_text(
        f"👑 *لوحة التحكم*\n\n👥 {total} | 💎 {vip_c} VIP | 🟢 {active} نشط\n\n"
        f"`/vip [ID]` | `/unvip [ID]` | `/ban [ID]` | `/unban [ID]`\n"
        f"`/broadcast [رسالة]` | `/users` | `/stats` | `/clearcache`",
        parse_mode="Markdown")

@_adm
async def cmd_vip(update,context):
    if not context.args: return
    db=db_load(); uid=context.args[0]
    if uid not in db["users"]: await update.message.reply_text("❌ غير موجود"); return
    exp=activate_vip(db,int(uid))
    await update.message.reply_text(f"✅ VIP لـ `{uid}` حتى {exp}",parse_mode="Markdown")
    try: await context.bot.send_message(int(uid),"🎉 *تم تفعيل VIP!*\n\nاضغط /start 🚀",parse_mode="Markdown")
    except: pass

@_adm
async def cmd_unvip(update,context):
    if not context.args: return
    db=db_load(); uid=context.args[0]
    if uid in db["users"]: db["users"][uid]["vip"]=False; db_save(db)
    await update.message.reply_text(f"✅ إلغاء VIP لـ `{uid}`",parse_mode="Markdown")

@_adm
async def cmd_ban(update,context):
    if not context.args: return
    db=db_load(); uid=context.args[0]
    if uid in db["users"]: db["users"][uid]["blocked"]=True; db_save(db)
    await update.message.reply_text(f"⛔ حظر `{uid}`",parse_mode="Markdown")

@_adm
async def cmd_unban(update,context):
    if not context.args: return
    db=db_load(); uid=context.args[0]
    if uid in db["users"]: db["users"][uid]["blocked"]=False; db_save(db)
    await update.message.reply_text(f"✅ فك حظر `{uid}`",parse_mode="Markdown")

@_adm
async def cmd_broadcast(update,context):
    if not context.args: return
    db=db_load(); msg=" ".join(context.args); sent=failed=0
    for uid_str in db["users"]:
        try: await context.bot.send_message(int(uid_str),f"📢 *من الإدارة:*\n\n{msg}",parse_mode="Markdown"); sent+=1
        except: failed+=1
    await update.message.reply_text(f"✅ {sent} | ❌ {failed}")

@_adm
async def cmd_users(update,context):
    db=db_load(); lines=[]
    for uid,u in list(db["users"].items())[-20:]:
        b="💎" if u.get("vip") else "🆓"; x="⛔" if u.get("blocked") else ""
        lines.append(f"{b}{x} `{uid}` {u.get('name','?')} | {u.get('total_requests',0)}")
    await update.message.reply_text("👥 *آخر 20:*\n\n"+"\n".join(lines),parse_mode="Markdown")

@_adm
async def cmd_stats(update,context):
    db=db_load(); today=datetime.now().strftime("%Y-%m-%d")
    active=sum(1 for u in db["users"].values() if u.get("last_request_date")==today)
    vip_c=sum(1 for u in db["users"].values() if u.get("vip"))
    refs=sum(len(u.get("referrals",[])) for u in db["users"].values())
    await update.message.reply_text(
        f"📊 *إحصائيات:*\n\n👥 {len(db['users'])} | 💎 {vip_c} VIP | 🟢 {active} اليوم\n"
        f"📈 {db.get('total_requests',0)} طلب | 👥 {refs} إحالة",parse_mode="Markdown")

@_adm
async def cmd_clearcache(update,context):
    cache_clear(); await update.message.reply_text("✅ تم مسح الكاش!")

async def daily_report(context):
    db=db_load(); today=datetime.now().strftime("%Y-%m-%d")
    active=sum(1 for u in db["users"].values() if u.get("last_request_date")==today)
    try:
        await context.bot.send_message(ADMIN_ID,
            f"📊 *تقرير يومي — {today}*\n\n👥 {len(db['users'])} | 🟢 {active} نشط | 📈 {db.get('total_requests',0)} طلب",
            parse_mode="Markdown")
    except Exception as e: logger.error(f"daily: {e}")

# ═══════════════════════════════════════════════════════════════
#  .env GENERATOR
# ═══════════════════════════════════════════════════════════════
def _ensure_env():
    if not os.path.exists(".env"):
        with open(".env","w") as f:
            f.write("""# DASI BET — Environment Variables
# أضف مفاتيحك هنا — لا ترفع هذا الملف على GitHub

TELEGRAM_TOKEN=
GROQ_API_KEY=
FOOTBALL_API_KEY=
ODDS_API_KEY=
APIFOOTBALL_KEY=
TAVILY_API_KEY=

ADMIN_ID=7046072164
BOT_USERNAME=dasiibet_bot
CHANNEL=@dasi_bet
CHANNEL_URL=https://t.me/dasi_bet
ADMIN_USERNAME=@dasi_supportt
BET_LINK=https://reffpa.com/L?tag=d_5553701m_1599c_&site=5553701&ad=1599
PORT=8080
""")
        logger.info("✅ تم إنشاء .env — أضف مفاتيحك فيه")

def _ensure_gitignore():
    gi=".gitignore"
    lines=[".env","data/","*.tmp","__pycache__/","*.pyc","welcome_file_id.txt"]
    existing=open(gi).read() if os.path.exists(gi) else ""
    with open(gi,"a") as f:
        for l in lines:
            if l not in existing: f.write(l+"\n")

# ═══════════════════════════════════════════════════════════════
#  FLASK
# ═══════════════════════════════════════════════════════════════
_flask=Flask(__name__)
@_flask.route("/")
def health(): return "✅ DASI BET v3.0",200

# ═══════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════
def main():
    _ensure_dirs(); _ensure_env(); _ensure_gitignore(); cache_clear()
    if not TELEGRAM_TOKEN:
        logger.error("❌ TELEGRAM_TOKEN غير موجود! أضفه في .env"); return
    Thread(target=lambda:_flask.run(host="0.0.0.0",port=PORT,use_reloader=False),daemon=True).start()
    logger.info(f"✅ Flask port {PORT}")
    app=ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("admin",     cmd_admin))
    app.add_handler(CommandHandler("vip",       cmd_vip))
    app.add_handler(CommandHandler("unvip",     cmd_unvip))
    app.add_handler(CommandHandler("ban",       cmd_ban))
    app.add_handler(CommandHandler("unban",     cmd_unban))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("users",     cmd_users))
    app.add_handler(CommandHandler("stats",     cmd_stats))
    app.add_handler(CommandHandler("clearcache",cmd_clearcache))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.job_queue.run_daily(daily_report, time=dtime(8,0))
    logger.info("🚀 DASI BET v3.0 started!")
    app.run_polling(drop_pending_updates=True)

if __name__=="__main__":
    main()
