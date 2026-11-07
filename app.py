# -*- coding: utf-8 -*-
"""Bada Bing MotoGP : les Grands Prix sans spoil (même principe que le site NBA).

Données : API officielle MotoGP (api.motogp.pulselive.com, sans clé), actus (flux RSS de Motorsport.com France et
Paddock GP), vidéos (chaîne YouTube officielle MotoGP). Lancer : python -m streamlit run app.py
"""
import html
import json
import logging
import os
import re
import threading
import time
import unicodedata
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, quote
from zoneinfo import ZoneInfo

import requests
import streamlit as st

try:
    import streamlit.components.v1 as components
except Exception:  # pragma: no cover
    components = None

APP_VERSION = "2026-11-m001"
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, ".cache")
log = logging.getLogger("motogp")

API = "https://api.motogp.pulselive.com/motogp/v1"
MOTOGP_CHANNEL = "UC8pYaQzbBBXg9GIOHRvTmDQ"  # chaîne officielle MotoGP
NEWS_FEEDS = [("Motorsport.com", "https://fr.motorsport.com/rss/motogp/news/"),
              ("Paddock GP", "https://www.paddock-gp.com/feed/")]
CATS = ["MotoGP", "Moto2", "Moto3"]

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
MOIS_COURT = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]

# Nom français du Grand Prix et mots anglais des titres YouTube officiels (« MotoGP HIGHLIGHTS 2026 Malaysian GP »)
GP = {
    "QAT": ("GP du Qatar", ["qatar"]), "THA": ("GP de Thaïlande", ["thai", "thailand"]),
    "BRA": ("GP du Brésil", ["brazil", "brazilian"]), "USA": ("GP des États-Unis", ["americas", "united states", "usa", "austin"]),
    "AME": ("GP des Amériques", ["americas", "austin"]), "ARG": ("GP d'Argentine", ["argentin"]),
    "SPA": ("GP d'Espagne", ["spanish", "spain", "jerez"]), "FRA": ("GP de France", ["french", "france", "le mans"]),
    "CAT": ("GP de Catalogne", ["catalan", "catalunya", "barcelona"]), "ITA": ("GP d'Italie", ["italian", "italy", "mugello"]),
    "HUN": ("GP de Hongrie", ["hungarian", "hungary", "balaton"]), "CZE": ("GP de Tchéquie", ["czech", "brno"]),
    "NED": ("GP des Pays-Bas", ["dutch", "assen", "netherlands"]), "GER": ("GP d'Allemagne", ["german", "sachsenring"]),
    "GBR": ("GP de Grande-Bretagne", ["british", "silverstone"]), "ARA": ("GP d'Aragon", ["aragon"]),
    "RSM": ("GP de Saint-Marin", ["san marino", "misano"]), "EMI": ("GP d'Émilie-Romagne", ["emilia"]),
    "AUT": ("GP d'Autriche", ["austrian", "austria", "red bull ring"]), "JPN": ("GP du Japon", ["japanese", "japan", "motegi"]),
    "INA": ("GP d'Indonésie", ["indonesian", "indonesia", "mandalika"]), "AUS": ("GP d'Australie", ["australian", "phillip island"]),
    "MAL": ("GP de Malaisie", ["malaysian", "malaysia", "sepang"]), "POR": ("GP du Portugal", ["portuguese", "portugal", "portimao"]),
    "VAL": ("GP de Valence", ["valencia", "valencian"]), "IND": ("GP d'Inde", ["india"]), "KAZ": ("GP du Kazakhstan", ["kazakhstan"]),
}
SESSION_FR = {"FP1": "Essais libres 1", "FP2": "Essais libres 2", "FP3": "Essais libres 3", "PR": "Practice",
              "Q1": "Qualifs 1", "Q2": "Qualifs 2", "Q": "Qualifications", "SPR": "Sprint", "WUP": "Warm-up", "RAC": "Grand Prix"}
SESSION_SHORT = {"FP1": "EL1", "FP2": "EL2", "FP3": "EL3", "PR": "Practice", "Q1": "Q1", "Q2": "Q2", "Q": "Qualifs",
                 "SPR": "Sprint", "WUP": "Warm-up", "RAC": "Course"}
# Séances dont le résultat compte (et spoile) : qualifs (pole), sprint, course
GROUP = {"Q1": "q", "Q2": "q", "Q": "q", "SPR": "s", "RAC": "r"}
GROUP_FR = {"q": "les qualifs", "s": "le sprint", "r": "la course"}
COUNTRY_FR = {"FR": ("France", "Les Français"), "CA": ("Canada", "Les Canadiens")}

SESSION = requests.Session()
SESSION.headers["User-Agent"] = "Mozilla/5.0 (BadaBingMotoGP)"


# =========================================================================
# OUTILS
# =========================================================================
_FAKE = None  # point d'entrée pour des données inventées (tests)


def get_json(url, params=None, timeout=12):
    if _FAKE:
        return _FAKE(url, params)
    try:
        r = SESSION.get(url, params=params, timeout=timeout)
        if r.status_code == 200:
            return r.json()
        log.warning("HTTP %s sur %s", r.status_code, url)
    except Exception as e:
        log.warning("%s sur %s", type(e).__name__, url)
    return None


def get_text(url, timeout=12, **kw):
    if _FAKE:
        return _FAKE(url, None, text=True) or ""
    try:
        r = SESSION.get(url, timeout=timeout, **kw)
        if r.status_code == 200:
            return r.text
    except Exception as e:
        log.warning("%s sur %s", type(e).__name__, url)
    return ""


def disk_get(name):
    try:
        with open(os.path.join(CACHE_DIR, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def disk_put(name, value):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(os.path.join(CACHE_DIR, name), "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False)
    except Exception as e:
        log.warning("Cache disque : %s", type(e).__name__)


def parse_dt(iso):
    if not iso:
        return None
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None


def norm(text):
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return text.lower()


def esc(text):
    return html.escape(str(text or ""))


def bucket(seconds):
    return int(time.time() // seconds)


def now_utc():
    return datetime.now(timezone.utc)


def flag(iso):
    iso = (iso or "").upper()
    if len(iso) != 2 or not iso.isalpha():
        return "🏁"
    return chr(0x1F1E6 + ord(iso[0]) - 65) + chr(0x1F1E6 + ord(iso[1]) - 65)


def fr_hour(dt):
    return f"{dt.hour}h{dt.minute:02d}"


def day_num(d):
    return "1er" if d.day == 1 else str(d.day)


def fr_day(d, short=False):
    j = JOURS[d.weekday()]
    return f"{j[:3]}. {day_num(d)}" if short else f"{j} {day_num(d)} {MOIS[d.month - 1]}"


def fr_range(d1, d2):
    """« 6 → 8 nov. », « 30 oct. → 1er nov. »."""
    if d1.month == d2.month:
        return f"{day_num(d1)} → {day_num(d2)} {MOIS_COURT[d2.month - 1]}"
    return f"{day_num(d1)} {MOIS_COURT[d1.month - 1]} → {day_num(d2)} {MOIS_COURT[d2.month - 1]}"


def nth(n):
    return "1er" if n == 1 else f"{n}e"


def fr_num(x, digits=1):
    return f"{x:.{digits}f}".replace(".", ",")


def gap_secs(txt):
    try:
        return float(str(txt).replace(",", "."))
    except (TypeError, ValueError):
        return None


def strip_html(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def rel_time(dt, ref=None):
    ref = ref or now_utc()
    mins = int((ref - dt).total_seconds() // 60)
    if mins < 1:
        return "à l'instant"
    if mins < 60:
        return f"il y a {mins} min"
    if mins < 1440:
        return f"il y a {mins // 60} h"
    days = mins // 1440
    return "hier" if days == 1 else f"il y a {days} jours"


# =========================================================================
# DONNÉES MOTOGP (API officielle)
# =========================================================================
@st.cache_data(ttl=86400, show_spinner=False)
def seasons():
    data = get_json(f"{API}/results/seasons") or []
    return [{"id": s["id"], "year": s["year"], "current": s.get("current")} for s in data]


def current_season():
    ss = seasons()
    cur = [s for s in ss if s.get("current")] or sorted(ss, key=lambda s: -s["year"])[:1]
    return cur[0] if cur else None


@st.cache_data(ttl=86400, show_spinner=False)
def categories(season_id):
    data = get_json(f"{API}/results/categories", {"seasonUuid": season_id}) or []
    out = {}
    for c in data:
        name = re.sub(r"[^A-Za-z0-9]", "", c.get("name", ""))
        if name in CATS:
            out[name] = c["id"]
    return out


def _event(e, rnd):
    code = e.get("short_name", "")
    name = GP.get(code, (None,))[0] or ("GP " + (e.get("additional_name") or e.get("name", "")).title())
    return {"id": e["id"], "code": code, "name": name, "sponsor": e.get("sponsored_name", ""), "round": rnd,
            "circuit": (e.get("circuit") or {}).get("name", ""), "place": (e.get("circuit") or {}).get("place", ""),
            "iso": (e.get("country") or {}).get("iso", ""), "start": date.fromisoformat(e["date_start"]),
            "end": date.fromisoformat(e["date_end"]), "status": e.get("status", "")}


@st.cache_data(ttl=600, show_spinner=False)
def events(season_id, _bucket=None):
    """Grands Prix de la saison (sans les essais privés), dans l'ordre."""
    data = get_json(f"{API}/results/events", {"seasonUuid": season_id})
    if data is None:
        data = disk_get(f"events_{season_id}.json") or []
    else:
        disk_put(f"events_{season_id}.json", data)
    gps = sorted([e for e in data if not e.get("test")], key=lambda e: e["date_start"])
    return [_event(e, i + 1) for i, e in enumerate(gps)]


WEATHER_FR = {"clear": "Soleil", "sunny": "Soleil", "cloudy": "Nuageux", "partly cloudy": "Éclaircies", "overcast": "Couvert",
              "rain": "Pluie", "light rain": "Pluie fine", "heavy rain": "Forte pluie", "drizzle": "Bruine"}
TRACK_FR = {"dry": "piste sèche", "wet": "piste mouillée", "damp": "piste humide", "mixed": "piste mixte"}


def _session(s):
    code = (s.get("type") or "") + (str(s["number"]) if s.get("number") else "")
    cond = s.get("condition") or {}
    return {"id": s["id"], "code": code, "label": SESSION_FR.get(code, code), "short": SESSION_SHORT.get(code, code),
            "utc": parse_dt(s.get("date")), "status": s.get("status", ""), "group": GROUP.get(code),
            "track": cond.get("track", ""), "weather": WEATHER_FR.get(norm(cond.get("weather")), cond.get("weather", "")), "air": cond.get("air", ""),
            "ground": cond.get("ground", "")}


@st.cache_data(ttl=120, show_spinner=False, max_entries=500)
def _sessions_live(event_id, cat_id, _bucket):
    return get_json(f"{API}/results/sessions", {"eventUuid": event_id, "categoryUuid": cat_id})


def sessions(ev, cat_id):
    """Séances d'un GP pour une catégorie. Un GP terminé ne bouge plus : gardé sur disque."""
    name = f"sess_{ev['id']}_{cat_id}.json"
    data = disk_get(name) if ev["status"] == "FINISHED" else None
    if data is None:
        data = _sessions_live(ev["id"], cat_id, bucket(120))
        if data is None:
            data = disk_get(name) or []
        elif ev["status"] == "FINISHED" or all(s.get("status") == "FINISHED" for s in data):
            disk_put(name, data)
    out = sorted([s for s in (_session(s) for s in data) if s["utc"]], key=lambda s: s["utc"])
    if out and not any(session_done(s) for s in out):
        # L'API publie parfois des horaires bouche-trou hors des dates du GP : on les recale sur le week-end, marqués provisoires
        shift = (ev["start"] - out[0]["utc"].date()).days
        if abs(shift) >= 2:
            for s in out:
                s["utc"] = s["utc"] + timedelta(days=shift)
                s["provisional"] = True
    return out


def _row(c):
    r = c.get("rider") or {}
    gap = c.get("gap") or {}
    bl = c.get("best_lap") or {}
    return {"pos": c.get("position"), "rid": r.get("riders_api_uuid") or r.get("id", ""), "name": r.get("full_name", ""),
            "num": r.get("number"), "iso": (r.get("country") or {}).get("iso", ""),
            "team": (c.get("team") or {}).get("name", ""), "maker": (c.get("constructor") or {}).get("name", ""),
            "time": c.get("time") or "", "gap": gap_secs(gap.get("first")), "laps_down": int(gap.get("lap") or 0),
            "laps": c.get("total_laps") or 0, "points": c.get("points") or 0, "status": c.get("status", ""),
            "best": bl.get("time", ""), "speed": c.get("top_speed") or c.get("average_speed")}


@st.cache_data(ttl=60, show_spinner=False, max_entries=300)
def _classif_live(session_id, _bucket):
    return get_json(f"{API}/results/session/{session_id}/classification", {"test": "false"})


def classification(sess):
    """Classement d'une séance [{pos, rid, name, …}] (vide si pas encore courue). Une séance finie est gardée sur disque."""
    if not sess:
        return []
    name = f"cl_{sess['id']}.json"
    data = disk_get(name) if sess["status"] == "FINISHED" else None
    if data is None:
        data = _classif_live(sess["id"], bucket(60))
        if data is None:
            data = disk_get(name) or {}
        elif sess["status"] == "FINISHED" and data.get("classification"):
            disk_put(name, data)
    rows = [_row(c) for c in (data or {}).get("classification") or []]
    return rows


def records(sess):
    data = disk_get(f"cl_{sess['id']}.json") or _classif_live(sess["id"], bucket(60)) or {}
    return data.get("records") or []


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def riders_info(year):
    """Pilotes MotoGP de la saison (photo, numéro, équipe, couleur, naissance) : {id: {...}}."""
    cats = get_json(f"{API}/categories", {"seasonYear": year}) or []
    out = {}
    for c in cats:
        name = re.sub(r"[^A-Za-z0-9]", "", c.get("name", ""))
        if name not in CATS:
            continue
        teams = get_json(f"{API}/teams", {"categoryUuid": c["id"], "seasonYear": year}) or []
        for t in teams:
            for r in t.get("riders") or []:
                step = r.get("current_career_step") or {}
                tm = step.get("team") or {}
                pics = step.get("pictures") or {}
                out[r["id"]] = {
                    "id": r["id"], "name": f"{r.get('name', '')} {r.get('surname', '')}".strip(), "first": r.get("name", ""),
                    "last": r.get("surname", ""), "iso": (r.get("country") or {}).get("iso", ""),
                    "country": (r.get("country") or {}).get("name", ""), "birth": r.get("birth_date", ""),
                    "city": r.get("birth_city", ""), "age": r.get("years_old"), "num": step.get("number"),
                    "team": step.get("sponsored_team") or tm.get("name", ""), "color": t.get("color") or tm.get("color") or "#444",
                    "text": t.get("text_color") or "#fff", "maker": (t.get("constructor") or {}).get("name", ""),
                    "photo": (pics.get("profile") or {}).get("main") or "", "cat": name, "start": r.get("start_year"),
                    "in_grid": step.get("in_grid", True), "type": step.get("type", ""),
                }
    if out:
        disk_put(f"riders_{year}.json", out)
    return out or disk_get(f"riders_{year}.json") or {}


def rider_of(rid, row=None):
    info = (st.session_state.get("riders_map") or {}).get(rid)
    if info:
        return info
    row = row or {}
    return {"id": rid, "name": row.get("name", "?"), "last": (row.get("name") or "?").split(" ")[-1], "first": "",
            "iso": row.get("iso", ""), "num": row.get("num"), "team": row.get("team", ""), "color": "#555", "text": "#fff",
            "maker": row.get("maker", ""), "photo": "", "cat": ""}


# Couleurs des constructeurs (repli quand l'équipe n'est pas connue)
MAKER_COLOR = {"Ducati": "#c8102e", "Aprilia": "#5f259f", "KTM": "#ff6600", "Yamaha": "#0a3a9b", "Honda": "#c4161c",
               "Kalex": "#2b6cb0", "Boscoscuro": "#8b0000", "Triumph": "#333", "CFMOTO": "#1e88e5", "Husqvarna": "#0f2b56",
               "GASGAS": "#b71c1c", "Forward": "#444"}


def color_of(rid, row=None):
    info = rider_of(rid, row)
    c = info.get("color") or "#555"
    if c in ("#555", "#444", "") and (row or {}).get("maker"):
        c = MAKER_COLOR.get(row["maker"], c)
    return c


# =========================================================================
# LE WEEK-END : séances de toutes les catégories, résultats
# =========================================================================
def weekend(ev, cat_ids):
    """{catégorie: [séances]} pour un GP, chargé en parallèle."""
    with ThreadPoolExecutor(max_workers=3) as pool:
        res = list(pool.map(lambda c: sessions(ev, cat_ids[c]), [c for c in CATS if c in cat_ids]))
    return dict(zip([c for c in CATS if c in cat_ids], res))


def season_results(evs, cat_id):
    """Pour chaque GP commencé : séances de la catégorie et classements des qualifs, sprint et course.
    [(ev, sessions, {code: classement})]"""
    started = [e for e in evs if e["start"] <= date.today() + timedelta(days=1)]

    def one(ev):
        ss = sessions(ev, cat_id)
        res = {}
        for s in ss:
            if s["group"] and s["status"] in ("FINISHED", "STARTED", "IN-PROGRESS", "CURRENT") or (
                    s["group"] and s["utc"] and s["utc"] < now_utc()):
                res[s["code"]] = classification(s)
        return ev, ss, res

    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(one, started))


def session_done(s):
    return s["status"] == "FINISHED"


def session_live(s, ref=None):
    ref = ref or now_utc()
    if session_done(s) or not s["utc"]:
        return False
    return s["utc"] <= ref <= s["utc"] + timedelta(hours=1, minutes=15)


def ev_state(ev, sess_list, ref=None):
    """'pre' (à venir), 'live' (week-end en cours), 'post' (terminé)."""
    ref = ref or now_utc()
    if ev["status"] == "FINISHED" or (sess_list and all(session_done(s) for s in sess_list)):
        return "post"
    if sess_list and sess_list[0]["utc"] and sess_list[0]["utc"] <= ref:
        return "live"
    if ev["start"] <= ref.date() <= ev["end"]:
        return "live"
    return "pre" if ev["start"] > ref.date() else "post"


def seen_key(ev, group):
    return f"{ev['start'].year % 100:02d}{ev['code']}{group}"


# =========================================================================
# PAS DE SPOIL : ce qu'on a le droit de compter
# =========================================================================
def spoil_cut(results, seen, show_all, include_last=False):
    """Séances à ne pas compter (clés « 26QATr ») : celles du dernier GP couru que l'appareil n'a pas encore ouvertes.
    Tout le reste compte (les GP plus anciens sont supposés vus, comme les nuits NBA)."""
    if show_all or include_last:
        return set()
    last = None
    for ev, ss, res in results:
        if any(s["group"] and session_done(s) for s in ss):
            last = (ev, ss)
    if not last:
        return set()
    ev, ss = last
    return {seen_key(ev, s["group"]) for s in ss if s["group"] and session_done(s)} - set(seen)


def counted(results, cut):
    """Séances de course qui comptent (sprint + course finis, hors coupure) : [(ev, code, classement)]."""
    out = []
    for ev, ss, res in results:
        for s in ss:
            if s["code"] in ("SPR", "RAC") and session_done(s) and seen_key(ev, s["group"]) not in cut and res.get(s["code"]):
                out.append((ev, s["code"], res[s["code"]]))
    return out


def poles(results, cut):
    """{GP code: id du poleman} (Q2 en MotoGP, Q ailleurs)."""
    out = {}
    for ev, ss, res in results:
        for code in ("Q2", "Q"):
            s = next((x for x in ss if x["code"] == code and session_done(x)), None)
            if s and seen_key(ev, "q") not in cut and res.get(code):
                first = next((r for r in res[code] if r["pos"] == 1), None)
                if first:
                    out[ev["code"]] = first["rid"]
                break
    return out


def standings(results, cut):
    """Classement des pilotes calculé par le site (pour pouvoir s'arrêter avant le dernier GP)."""
    table = {}
    pole = poles(results, cut)
    for ev, code, rows in counted(results, cut):
        for r in rows:
            t = table.setdefault(r["rid"], {"rid": r["rid"], "name": r["name"], "iso": r["iso"], "num": r["num"], "team": r["team"],
                                            "maker": r["maker"], "pts": 0, "wins": 0, "pod": 0, "swins": 0, "spod": 0,
                                            "poles": 0, "dnf": 0, "races": 0, "last": [], "best": None, "row": r})
            t["pts"] += r["points"] or 0
            t["team"], t["maker"], t["num"] = r["team"] or t["team"], r["maker"] or t["maker"], r["num"] or t["num"]
            if code == "RAC":
                t["races"] += 1
                t["last"].append((ev["code"], r["pos"]))
                if r["pos"] == 1:
                    t["wins"] += 1
                if r["pos"] and r["pos"] <= 3:
                    t["pod"] += 1
                if not r["pos"]:
                    t["dnf"] += 1
                if r["pos"] and (t["best"] is None or r["pos"] < t["best"]):
                    t["best"] = r["pos"]
            else:
                if r["pos"] == 1:
                    t["swins"] += 1
                if r["pos"] and r["pos"] <= 3:
                    t["spod"] += 1
    for rid in pole.values():
        if rid in table:
            table[rid]["poles"] += 1
    rows = sorted(table.values(), key=lambda t: (-t["pts"], -t["wins"], -t["pod"], t["name"]))
    for i, t in enumerate(rows):
        t["rank"] = i + 1
        t["gap"] = rows[0]["pts"] - t["pts"]
    return rows


def team_standings(results, cut, key="team"):
    """Équipes (somme des pilotes) ou constructeurs (meilleur pilote de la marque à chaque séance)."""
    table = {}
    for ev, code, rows in counted(results, cut):
        best = {}
        for r in rows:
            name = r[key]
            if not name:
                continue
            if key == "maker":
                if name not in best or (r["points"] or 0) > best[name]:
                    best[name] = r["points"] or 0
            else:
                t = table.setdefault(name, {"name": name, "pts": 0, "wins": 0, "maker": r["maker"]})
                t["pts"] += r["points"] or 0
                t["wins"] += 1 if (r["pos"] == 1 and code == "RAC") else 0
        for name, p in best.items():
            t = table.setdefault(name, {"name": name, "pts": 0, "wins": 0, "maker": name})
            t["pts"] += p
            t["wins"] += 1 if any(r["pos"] == 1 and r["maker"] == name for r in rows) and code == "RAC" else 0
    rows = sorted(table.values(), key=lambda t: (-t["pts"], t["name"]))
    for i, t in enumerate(rows):
        t["rank"] = i + 1
        t["gap"] = rows[0]["pts"] - t["pts"]
    return rows


# =========================================================================
# INDICE « À VOIR » (voulu flou : ne dit pas qui gagne ni comment)
# =========================================================================
WATCH_LEVELS = {3: ("🔥🔥🔥", "Immanquable", "Course de dingue : à voir en entier."),
                2: ("🔥🔥", "À voir", "Belle course, le résumé ne suffira pas."),
                1: ("🔥", "Pas mal", "Quelques beaux moments."),
                0: ("😐", "Sans plus", "Le résumé suffit."),
                -1: ("💤", "Soporifique", "Course sans histoire : le résumé suffit largement.")}


def watch_score(rows, grid=None, sess=None, stakes=0):
    """Note sur 100 mêlant plusieurs ingrédients qui se compensent : fin serrée, peloton groupé, remontée,
    chutes et abandons, pluie, enjeu au championnat."""
    fin = [r for r in rows if r["pos"]]
    if len(fin) < 3:
        return None
    score = 0
    g2, g3 = fin[1]["gap"] or 0, fin[2]["gap"] or 0
    score += 34 if g2 < 0.25 else 27 if g2 < 0.7 else 20 if g2 < 1.5 else 12 if g2 < 3 else 5 if g2 < 6 else 0
    score += 10 if g3 < 2 else 6 if g3 < 5 else 0
    g5 = fin[min(4, len(fin) - 1)]["gap"] or 0
    score += 8 if g5 < 6 else 4 if g5 < 12 else 0
    if grid:
        start = {r["rid"]: r["pos"] for r in grid if r["pos"]}
        w = start.get(fin[0]["rid"])
        if w and w >= 6:
            score += 12
        elif w and w >= 3:
            score += 5
        gains = sorted((start.get(r["rid"], r["pos"]) - r["pos"] for r in fin[:10]), reverse=True)
        if gains and gains[0] >= 8:
            score += 6
    out = sum(1 for r in rows if not r["pos"] and r["status"] != "NOTSTARTED")
    score += 10 if out >= 5 else 6 if out >= 3 else 2 if out >= 1 else 0
    if sess and norm(sess.get("track")) not in ("", "dry"):
        score += 10
    score += stakes
    if g2 > 8:
        score -= 10
    return max(0, min(100, score))


def watch_level(score):
    if score is None:
        return None
    for cut, lvl in ((60, 3), (44, 2), (28, 1), (14, 0)):
        if score >= cut:
            return lvl
    return -1


def stakes_of(table):
    """Enjeu au championnat (bonus d'intérêt) : deux premiers proches."""
    if len(table) < 2:
        return 0
    gap = table[0]["pts"] - table[1]["pts"]
    return 8 if gap <= 15 else 4 if gap <= 40 else 0


# =========================================================================
# ACTUS (flux RSS) — avec bouclier anti-spoil
# =========================================================================
MOTO_RE = re.compile(r"moto\s?gp|moto ?2|moto ?3|marquez|bagnaia|bezzecchi|quartararo|zarco|acosta|martin\b", re.I)


@st.cache_data(ttl=600, show_spinner=False)
def news(_bucket=None):
    items = []
    for source, url in NEWS_FEEDS:
        text = get_text(url, headers={"User-Agent": "Mozilla/5.0"})
        if not text:
            continue
        try:
            root = ET.fromstring(text.encode("utf-8") if isinstance(text, str) else text)
        except ET.ParseError:
            continue
        for it in root.iter("item"):
            title = html.unescape(it.findtext("title") or "").strip()
            cats = " ".join(html.unescape(c.text or "") for c in it.findall("category"))
            if source == "Paddock GP" and not MOTO_RE.search(title + " " + cats):
                continue  # Paddock GP parle aussi de F1, WSBK…
            if re.match(r"^\s*(F1|WSBK|WorldSBK|Formule|Rallye|Dakar|IndyCar|WEC)\b", title, re.I):
                continue
            try:
                pub = parsedate_to_datetime(it.findtext("pubDate") or "")
            except (TypeError, ValueError):
                pub = None
            img = ""
            enc = it.find("enclosure")
            if enc is not None and "image" in (enc.get("type") or "image"):
                img = enc.get("url", "")
            if not img:
                for el in it:
                    if el.tag.endswith("content") and el.get("url"):
                        img = el.get("url")
                        break
            desc = strip_html(it.findtext("description") or "")
            desc = re.sub(r"\s*(Continuez de lire|Lire la suite|The post .*)$", "", desc)
            if not img:
                m = re.search(r"<img[^>]+src=[\"']([^\"']+)", it.findtext("description") or "")
                img = m.group(1) if m else ""
            items.append({"title": title, "link": (it.findtext("link") or "").strip(), "pub": pub.isoformat() if pub else "",
                          "desc": desc[:280], "img": img, "source": source})
    items.sort(key=lambda x: x["pub"], reverse=True)
    if items:
        disk_put("news.json", items)
    return items or disk_get("news.json") or []


def spoil_since(results, seen, show_all):
    """Heure à partir de laquelle les actus peuvent révéler un résultat pas encore vu (début de la 1re séance non vue)."""
    if show_all:
        return None
    first = None
    started = [(ev, ss) for ev, ss, res in results if any(s["group"] and s["utc"] and s["utc"] <= now_utc() for s in ss)]
    for ev, ss in started[-1:]:
        for s in ss:
            if s["group"] and s["utc"] and s["utc"] <= now_utc() and seen_key(ev, s["group"]) not in seen:
                if first is None or s["utc"] < first[0]:
                    first = (s["utc"], ev, s)
    return first


# =========================================================================
# VIDÉOS (YouTube : chaîne officielle MotoGP)
# =========================================================================
YOUTUBE_FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={}"


def _walk_videos(o):
    if isinstance(o, dict):
        if "videoRenderer" in o:
            yield o["videoRenderer"]
        for x in o.values():
            yield from _walk_videos(x)
    elif isinstance(o, list):
        for x in o:
            yield from _walk_videos(x)


@st.cache_data(ttl=1200, show_spinner=False, max_entries=300)
def yt_search(query):
    """Recherche YouTube (page publique, sans clé) : [{id, title, channel, mins}]."""
    if _FAKE:
        return _FAKE("yt_search", {"q": query}) or []
    try:
        r = SESSION.get("https://www.youtube.com/results", params={"search_query": query, "hl": "en", "gl": "US"},
                        headers={"Accept-Language": "en-US,en"}, cookies={"CONSENT": "YES+1", "SOCS": "CAI"}, timeout=12)
        m = re.search(r"var ytInitialData = (\{.*?\});</script>", r.text)
        data = json.loads(m.group(1)) if m else {}
    except Exception as e:
        log.warning("Recherche YouTube indisponible (%s)", type(e).__name__)
        return []
    out = []
    for v in _walk_videos(data):
        title = "".join(x.get("text", "") for x in (v.get("title") or {}).get("runs") or [])
        owner = ((v.get("ownerText") or {}).get("runs") or [{}])[0]
        channel = ((owner.get("navigationEndpoint") or {}).get("browseEndpoint") or {}).get("browseId", "")
        length = (v.get("lengthText") or {}).get("simpleText", "")
        parts = [int(x) for x in length.split(":") if x.isdigit()]
        secs = sum(x * 60 ** i for i, x in enumerate(reversed(parts)))
        if v.get("videoId") and title:
            out.append({"id": v["videoId"], "title": title, "channel": channel, "mins": max(1, round(secs / 60)) if secs else None})
    return out


@st.cache_data(ttl=900, show_spinner=False)
def channel_feed(_bucket=None):
    text = get_text(YOUTUBE_FEED_URL.format(MOTOGP_CHANNEL))
    out = []
    if not text:
        return out
    ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return out
    for e in root.findall("a:entry", ns):
        vid = e.findtext("yt:videoId", default="", namespaces=ns)
        if vid:
            out.append({"id": vid, "title": e.findtext("a:title", default="", namespaces=ns), "channel": MOTOGP_CHANNEL, "mins": None})
    return out


_VID_LOCK = threading.Lock()


@st.cache_resource(show_spinner=False)
def found_videos():
    return disk_get("videos.json") or {}


def _gp_words(ev):
    return GP.get(ev["code"], (None, [norm(ev["place"])]))[1]


def _match(v, ev, kind):
    t = norm(v["title"])
    if v.get("channel") != MOTOGP_CHANNEL or str(ev["start"].year) not in t:
        return False
    if not any(w in t for w in _gp_words(ev)):
        return False
    if kind == "race":
        return "motogp highlights" in t or ("race" in t and "highlights" in t and "sprint" not in t)
    if kind == "sprint":
        return "sprint" in t and ("highlights" in t or "moments" in t)
    if kind == "quali":
        return "qualifying" in t and ("highlights" in t or "moments" in t or "q2" in t)
    if kind == "best":
        return "best motogp moments" in t or "viral moments" in t
    return False


def gp_videos(ev, wanted):
    """Vidéos officielles d'un GP {kind: vidéo} pour les séances finies (wanted : 'race', 'sprint', 'quali').
    Titres jamais affichés (ils peuvent spoiler)."""
    store = found_videos()
    out = {}
    missing = []
    for k in wanted:
        key = f"{ev['start'].year}{ev['code']}_{k}"
        if store.get(key):
            out[k] = store[key]
        else:
            missing.append(k)
    if not missing:
        return out
    pool = channel_feed(bucket(900))
    word = _gp_words(ev)[0].title()
    for k in missing:
        cand = [v for v in pool if _match(v, ev, k)]
        if not cand:
            q = {"race": "MotoGP HIGHLIGHTS", "sprint": "Tissot Sprint", "quali": "Qualifying highlights", "best": "Best MotoGP Moments"}[k]
            cand = [v for v in yt_search(f"{q} {ev['start'].year} {word} GP") if _match(v, ev, k)]
        if cand:
            v = cand[0]
            out[k] = v
            with _VID_LOCK:
                store[f"{ev['start'].year}{ev['code']}_{k}"] = {"id": v["id"], "mins": v.get("mins"), "title": v["title"]}
                disk_put("videos.json", dict(store))
    return out


def yt_url(v):
    return f"https://www.youtube.com/watch?v={v['id']}"


def yt_search_url(ev, what="highlights"):
    q = f"MotoGP {ev['start'].year} {_gp_words(ev)[0]} GP {what}"
    return "https://www.youtube.com/results?search_query=" + quote(q)


def video_buttons(ev, sess_list, cls="vb"):
    """Boutons vidéo (sans titre) pour les séances finies du GP."""
    done = {s["code"] for s in sess_list if session_done(s)}
    wanted = [k for k, c in (("race", "RAC"), ("sprint", "SPR")) if c in done]
    if not wanted:
        return ""
    vids = gp_videos(ev, wanted)
    out = []
    for k, label in (("race", "▶ Résumé du GP"), ("sprint", "▶ Résumé du sprint")):
        if k not in wanted:
            continue
        v = vids.get(k)
        if v:
            mins = f" <small>{v['mins']} min</small>" if v.get("mins") else ""
            out.append(f"<a class='{cls}' href='{yt_url(v)}' target='_blank' rel='noopener'>{label}{mins}</a>")
        else:
            what = "race highlights" if k == "race" else "sprint highlights"
            out.append(f"<a class='{cls} ghost' href='{yt_search_url(ev, what)}' target='_blank' rel='noopener'>🔎 {label[2:]}</a>")
    return "".join(out)


# =========================================================================
# PETITS MORCEAUX DE HTML
# =========================================================================
def link(href, text, cls="lk", title=""):
    """Lien interne : intercepté par le script de la page et passé au site par le champ caché (pas de rechargement)."""
    t = f" title='{esc(title)}'" if title else ""
    return f"<a class='{cls}' href='#go?{href}'{t}>{text}</a>"


def rider_link(rid, text):
    return link(f"vue=pilotes&pilote={rid}", text, "rl")


def gp_link(ev, text, cls="lk"):
    return link(f"vue=gp&gp={ev['code']}", text, cls)


def photo(info, size=44, cls="ph"):
    if info.get("photo"):
        return (f"<img class='{cls}' src='{esc(info['photo'])}' width='{size}' height='{size}' loading='lazy' "
                f"style='background:{esc(info.get('color', '#333'))}' alt=''>")
    num = esc(info.get("num") or "")
    return (f"<span class='{cls} phn' style='width:{size}px;height:{size}px;background:{esc(info.get('color', '#333'))}'>"
            f"{num}</span>")


def num_badge(info):
    if not info.get("num"):
        return ""
    return (f"<span class='nb' style='background:{esc(info.get('color', '#333'))};color:{esc(info.get('text', '#fff'))}'>"
            f"{esc(info['num'])}</span>")


def rider_cell(r, with_team=True, with_link=True):
    info = rider_of(r["rid"], r)
    name = esc(info.get("name") or r["name"])
    nm = rider_link(r["rid"], name) if with_link else name
    team = f"<span class='tm'>{esc(r.get('team') or info.get('team'))}</span>" if with_team else ""
    return (f"<span class='rc'>{num_badge(info) or ''}<span class='fl'>{flag(r.get('iso') or info.get('iso'))}</span>"
            f"<span class='rn'><b>{nm}</b>{team}</span></span>")


def status_fr(r):
    st_ = r.get("status", "")
    if st_ == "NOTSTARTED":
        return "Non partant"
    if st_ in ("OUTSTND", "NOTFINISHFIRST") or not r["pos"]:
        return "Abandon"
    if st_ == "NOTCLASSIFIED":
        return "Non classé"
    return "—"


def gap_txt(r, leader_time="", race=True):
    if not r["pos"]:
        lap = f" (tour {r['laps']})" if r.get("laps") else ""
        return status_fr(r) + lap
    if r["pos"] == 1:
        return esc(r["time"] or r.get("best") or "")
    if r.get("laps_down"):
        return f"+{r['laps_down']} tour" + ("s" if r["laps_down"] > 1 else "")
    if r["gap"] is not None:
        return "+" + fr_num(r["gap"], 3) + " s"
    return esc(r.get("best") or r.get("time") or "")


def results_table(rows, code, fav=None, limit=None):
    race = code in ("SPR", "RAC")
    head = "<tr><th>#</th><th>Pilote</th><th class='r'>" + ("Écart" if race else "Meilleur tour") + "</th>" + \
           ("<th class='r'>Pts</th>" if race else "<th class='r'>Écart</th>") + "</tr>"
    body = []
    for r in rows[:limit] if limit else rows:
        cls = []
        if r["pos"] and r["pos"] <= 3:
            cls.append(f"p{r['pos']}")
        if fav and r["rid"] == fav:
            cls.append("fav")
        if not r["pos"]:
            cls.append("out")
        stripe = color_of(r["rid"], r)
        pos = r["pos"] or "–"
        if race:
            right = f"<td class='r g'>{gap_txt(r)}</td><td class='r pts'>{r['points'] or ''}</td>"
        else:
            g = "" if r["pos"] == 1 or r["gap"] is None else "+" + fr_num(r["gap"], 3)
            right = f"<td class='r g'>{esc(r['best']) if r['pos'] else status_fr(r)}</td><td class='r g'>{g}</td>"
        body.append(f"<tr class='{' '.join(cls)}'><td class='ps' style='border-left:4px solid {esc(stripe)}'>{pos}</td>"
                    f"<td>{rider_cell(r)}</td>{right}</tr>")
    return f"<table class='res'>{head}{''.join(body)}</table>"


def podium_html(rows):
    top = [r for r in rows if r["pos"] in (1, 2, 3)]
    top.sort(key=lambda r: r["pos"])
    if len(top) < 3:
        return ""
    order = [top[1], top[0], top[2]]
    out = []
    for r in order:
        info = rider_of(r["rid"], r)
        g = "" if r["pos"] == 1 else ("+" + fr_num(r["gap"], 3) + " s" if r["gap"] is not None else "")
        out.append(f"<div class='pd pd{r['pos']}' style='--c:{esc(color_of(r['rid'], r))}'>{photo(info, 64)}"
                   f"<div class='pdn'>{rider_link(r['rid'], esc(info.get('last') or r['name']))}</div>"
                   f"<div class='pdt'>{esc(r['team'])}</div><div class='pdg'>{g}</div><div class='pdb'>{r['pos']}</div></div>")
    return f"<div class='podium'>{''.join(out)}</div>"


_UID = [0]


def uid(prefix="t"):
    _UID[0] += 1
    return f"{prefix}{_UID[0]}"


def hidden(label, content, cls="tg", open_cls=""):
    """Bloc caché par une case à cocher CSS (jamais `checked` dans le HTML : React la verrouillerait)."""
    i = uid("h")
    return (f"<div class='hb {open_cls}'><input type='checkbox' id='{i}' class='{cls}'>"
            f"<label for='{i}' class='hbl'>{label}</label><label for='{i}' class='hbc'>🙈 Cacher</label>"
            f"<div class='hbx'>{content}</div></div>")


def local(dt, zone):
    return dt.astimezone(zone)


def when_txt(dt, zone, today=None):
    d = local(dt, zone)
    today = today or now_utc().astimezone(zone).date()
    if d.date() == today:
        j = "Aujourd'hui"
    elif d.date() == today + timedelta(days=1):
        j = "Demain"
    elif d.date() == today - timedelta(days=1):
        j = "Hier"
    else:
        j = fr_day(d.date(), short=True).capitalize()
    return f"{j} {fr_hour(d)}"


# =========================================================================
# SCRIPTS DE LA PAGE (cookies, liens internes, rechargement)
# =========================================================================
KEEP = ("gp_fav", "gp_opt", "gp_vues", "gp_hello")


def run_js(code):
    if hasattr(st, "iframe"):
        st.iframe(code, height=1)
    elif components:
        components.html(code, height=0)


def set_cookie(name, value):
    keep = f"try {{ parent.localStorage.setItem('{name}', '{value}'); }} catch (e) {{}}" if name in KEEP else ""
    run_js(f"<script>parent.document.cookie='{name}={value}; path=/; max-age=31536000; SameSite=Lax';{keep}</script>")


def page_script(known, live):
    """Liens internes (#go?…) envoyés au site par le champ caché, réglages relus dans le navigateur (Streamlit Cloud
    ne transmet pas au serveur les cookies écrits par la page), rechargement pendant les séances."""
    run_js(f"""<script>
const P = window.parent, D = P.document;
D.documentElement.lang = 'fr';
P.__gpGo = cmd => {{
  const inp = D.querySelector('.st-key-go_to input');
  if (!inp) return false;
  const setter = Object.getOwnPropertyDescriptor(P.HTMLInputElement.prototype, 'value').set;
  inp.focus();
  setter.call(inp, cmd + '&_=' + Date.now());
  inp.dispatchEvent(new P.Event('input', {{bubbles: true}}));
  inp.dispatchEvent(new P.KeyboardEvent('keydown', {{key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true}}));
  inp.blur();
  return true;
}};
try {{
  const K = {json.dumps(known)};
  const ck = n => (D.cookie.split('; ').find(c => c.startsWith(n + '=')) || '').slice(n.length + 1);
  const get = n => ck(n) || P.localStorage.getItem(n) || '';
  const v = {{fav: get('gp_fav'), opt: get('gp_opt'), vues: get('gp_vues'), hello: get('gp_hello')}};
  const diff = (v.fav && v.fav !== K.fav) || (v.opt && v.opt !== K.opt) || (v.vues && v.vues !== K.vues) || (v.hello && !K.hello);
  if (!K.restored && diff && !P.__gpRestoreSent) {{
    P.__gpRestoreSent = true;
    setTimeout(() => P.__gpGo('restore=1&' + new URLSearchParams(v).toString()), 30);
  }}
}} catch (e) {{}}
if (P.__gpClick) D.removeEventListener('click', P.__gpClick, true);
P.__gpClick = e => {{
  const g = e.target.closest && e.target.closest('a[href^="#go?"]');
  if (!g) return;
  e.preventDefault();
  P.__gpGo(g.getAttribute('href').slice(4));
  D.querySelector('[data-testid="stMain"]')?.scrollTo({{top: 0}});
}};
D.addEventListener('click', P.__gpClick, true);
if (P.__gpAway) D.removeEventListener('visibilitychange', P.__gpAway);
clearInterval(P.__gpTick);
P.__gpAway = () => {{
  if (D.hidden) P.__gpHiddenAt = Date.now();
  else if (P.__gpHiddenAt && Date.now() - P.__gpHiddenAt > 180000) P.location.reload();
}};
D.addEventListener('visibilitychange', P.__gpAway);
if ({'true' if live else 'false'}) P.__gpTick = setInterval(() => {{
  if (!D.hidden && !D.querySelector('.tg:checked, details[open]')) P.location.reload();
}}, 90000);
</script>""")


def read_seen(raw):
    return {x for x in (raw or "").split(".") if re.fullmatch(r"\d\d[A-Z]{3}[qsr]", x)}


def write_seen(seen):
    keep = sorted(seen, key=lambda k: (k[:2], k))[-120:]
    set_cookie("gp_vues", ".".join(keep))


def opts_code(o):
    return ("i1" if o.get("indice", True) else "i0") + ("s1" if o.get("scores") else "s0") + "c" + o.get("pays", "FR")


def opts_from(raw, tzname=""):
    raw = raw or ""
    m = re.search(r"c(FR|CA)", raw)
    pays = m.group(1) if m else ("CA" if tzname.startswith("America/") else "FR")
    return {"indice": "i0" not in raw, "scores": "s1" in raw, "pays": pays}


def apply_restore(q):
    ss = st.session_state
    ss.restored = True
    if q.get("fav"):
        ss.fav = q["fav"]
    if q.get("opt"):
        ss.opts = opts_from(q["opt"], ss.get("tzname", ""))
    if q.get("vues"):
        ss.seen = ss.seen | read_seen(q["vues"])
    if q.get("hello"):
        ss.hello_done = True


VIEWS = ("brief", "gp", "calendrier", "classement", "pilotes", "actus")
TABS = [("brief", "Le brief", ":material/coffee:"), ("gp", "Grand Prix", ":material/sports_motorsports:"),
        ("calendrier", "Calendrier", ":material/calendar_month:"), ("classement", "Classement", ":material/leaderboard:"),
        ("pilotes", "Pilotes", ":material/person:"), ("actus", "Actus", ":material/newspaper:")]


def apply_go(cmd):
    q = dict(parse_qsl(cmd, keep_blank_values=True))
    ss = st.session_state
    if q.get("restore"):
        apply_restore(q)
        return
    if q.get("gp"):
        ss.gp = q["gp"]
    if q.get("pilote") is not None and "pilote" in q:
        ss.rider = q["pilote"] or None
    if q.get("cat") in CATS:
        ss.cat = q["cat"]
    if q.get("vue") in VIEWS:
        ss.view = q["vue"]


# =========================================================================
# STYLE
# =========================================================================
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600;700&family=Barlow+Condensed:wght@500;600;700;800&display=swap');
:root {--bg:#0d0f14; --card:#161a22; --card2:#1d222c; --line:#2a303c; --tx:#eef1f6; --mu:#9aa3b2; --acc:#e10600;
       --gold:#f5c542; --ok:#3ecf8e;}
html, body, [data-testid="stAppViewContainer"] {background:var(--bg) !important;}
.stApp, .stMarkdown, p, div {font-family:'Barlow', sans-serif;}
.block-container {max-width:1240px; padding-top:1.2rem !important;}
header[data-testid="stHeader"] {height:0; background:transparent;}
div[data-testid="stElementContainer"]:has(> iframe[data-testid="stIFrame"]), div:has(> .st-key-gobox) {
  position:absolute !important; width:1px; height:1px; overflow:hidden; left:-9999px;}
.st-key-gobox {position:absolute !important; left:-9999px; top:0; width:1px; height:1px; overflow:hidden;}
a {color:var(--tx);}
.stMarkdown a.lk, .stMarkdown a.rl, .stMarkdown a.vb, .stMarkdown a.c, .stMarkdown a.rt, .stMarkdown a.nw {text-decoration:none !important;}
.stMarkdown a.rl, .stMarkdown a.c, .stMarkdown a.rt, .stMarkdown a.nw {color:inherit !important;}
.stMarkdown a.lk {color:var(--gold) !important;}
.stMarkdown a.rl:hover, .stMarkdown a.lk:hover {text-decoration:underline !important;}
.brand {font-family:'Barlow Condensed'; font-weight:800; font-size:30px; letter-spacing:.5px; line-height:1; white-space:nowrap;}
.brand i {color:var(--acc); font-style:normal;}
.st-key-hdr [data-testid="stHorizontalBlock"] {flex-wrap:nowrap !important; align-items:center;}
.st-key-hdr [data-testid="stColumn"]:last-child {flex:0 0 64px !important; min-width:64px !important;}
.st-key-tabs {position:sticky; top:0; z-index:50; background:var(--bg); padding:6px 0 4px; border-bottom:1px solid var(--line);}
.st-key-tabs button {white-space:nowrap;}
.pg-h {margin:14px 0 8px;}
.pg-h .k {color:var(--acc); font:700 13px 'Barlow Condensed'; letter-spacing:2px; text-transform:uppercase;}
.pg-h h2 {font:800 30px/1.1 'Barlow Condensed'; margin:2px 0; padding:0; color:var(--tx);}
.pg-h p {color:var(--mu); margin:2px 0 0;}
.note {color:var(--mu); font-size:13px;}
.lk, .rl {text-decoration:none; color:inherit;}
.lk:hover, .rl:hover {text-decoration:underline;}
.pill {display:inline-block; padding:2px 9px; border-radius:20px; font-size:12px; font-weight:700; white-space:nowrap;
       background:var(--card2); color:var(--mu); border:1px solid var(--line);}
.pill.live {background:#3a0d0d; color:#ff5a5a; border-color:#7a1a1a;}
.pill.pre {background:#3a300d; color:var(--gold); border-color:#6b5715;}
.pill.post {color:#cfd5df;}
/* ---------- en-tête du GP (bandeau à la carte) ---------- */
.gph {position:relative; overflow:hidden; border-radius:18px; padding:20px 22px; margin:6px 0 14px;
      background:linear-gradient(135deg, #2a0d0d 0%, #14171f 55%, #0d0f14 100%); border:1px solid #3a1d1d;}
.gph:before {content:''; position:absolute; inset:0; background:repeating-linear-gradient(115deg, transparent 0 22px,
      rgba(255,255,255,.025) 22px 44px); pointer-events:none;}
.gph .fl0 {font-size:46px; float:right; line-height:1;}
.gph .k {color:var(--acc); font:700 13px 'Barlow Condensed'; letter-spacing:2px; text-transform:uppercase;}
.gph h1 {font:800 40px/1 'Barlow Condensed'; margin:4px 0; padding:0; color:#fff;}
.gph .sub {color:var(--mu); font-size:15px;}
.gph .vids {margin-top:12px; display:flex; flex-wrap:wrap; gap:8px;}
.vb {display:inline-block; padding:8px 14px; border-radius:10px; background:var(--acc); color:#fff !important; font-weight:700;
     text-decoration:none; white-space:nowrap;}
.vb small {opacity:.8; font-weight:500;}
.vb.ghost {background:transparent; border:1px solid var(--line); color:var(--mu) !important;}
/* ---------- séances ---------- */
.ses-grid {display:grid; grid-template-columns:repeat(auto-fill, minmax(270px, 1fr)); gap:10px;}
.ses {position:relative; background:var(--card); border:1px solid var(--line); border-radius:14px; padding:12px 14px; min-width:0;
      display:flex; flex-direction:column; gap:6px;}
.ses.race {border-color:#5a2020; background:linear-gradient(160deg, #221216, var(--card) 60%);}
.ses.live {border-color:#c62828; box-shadow:0 0 0 1px #c62828 inset;}
.ses .top {display:flex; justify-content:space-between; align-items:baseline; gap:8px;}
.ses .nm {font:700 20px 'Barlow Condensed'; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.ses .hr {color:var(--mu); font-size:13px; white-space:nowrap;}
.ses .meta {color:var(--mu); font-size:12px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.ses .wt {font-size:13px; white-space:nowrap;}
.ses .wt b {font-weight:700;}
.hb {position:relative;}
.hb > input {position:absolute; opacity:0; pointer-events:none;}
.hbl {display:inline-flex; align-items:center; gap:6px; cursor:pointer; padding:6px 12px; border-radius:10px; font-weight:700;
      border:1px dashed #555c6b; color:var(--mu); background:repeating-linear-gradient(135deg, #1b1f28 0 8px, #20252f 8px 16px);
      white-space:nowrap; user-select:none;}
.hbl:hover {color:var(--tx); border-color:var(--mu);}
.hbc {display:none; cursor:pointer; color:var(--mu); font-size:12px; margin-left:8px; white-space:nowrap;}
.hbx {display:none; margin-top:8px;}
.hb > input:checked ~ .hbl {display:none;}
.hb > input:checked ~ .hbc {display:inline-block;}
.hb > input:checked ~ .hbx, .all .hbx {display:block;}
.all .hbl, .all .hbc {display:none !important;}
.mini {display:flex; flex-direction:column; gap:3px;}
.mini .l {display:flex; align-items:center; gap:7px; font-size:14px; white-space:nowrap; overflow:hidden; min-width:0;}
.mini .l .p {font:800 16px 'Barlow Condensed'; width:18px; color:var(--gold);}
.mini .l .rn {overflow:hidden; text-overflow:ellipsis;}
.mini .l .g {margin-left:auto; color:var(--mu); font-size:12px;}
.mini .me {color:var(--gold);}
/* ---------- tableaux ---------- */
table.res {width:100%; border-collapse:collapse; font-size:14px;}
table.res th {text-align:left; color:var(--mu); font-weight:600; font-size:12px; padding:6px 8px; border-bottom:1px solid var(--line);
              white-space:nowrap;}
table.res td {padding:6px 8px; border-bottom:1px solid #1f2430; white-space:nowrap;}
table.res .r {text-align:right;}
table.res .ps {font:800 17px 'Barlow Condensed'; width:30px;}
table.res tr.p1 .ps {color:var(--gold);} table.res tr.p2 .ps {color:#cfd5df;} table.res tr.p3 .ps {color:#d08a4a;}
table.res tr.fav td {background:#2a2410;}
table.res tr.out td {color:var(--mu);}
table.res .g {color:var(--mu); font-variant-numeric:tabular-nums;}
table.res .pts {font-weight:700;}
.rc {display:inline-flex; align-items:center; gap:7px; min-width:0;}
.rn {display:flex; flex-direction:column; line-height:1.15; min-width:0;}
.rn b {white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.rn .tm {color:var(--mu); font-size:11px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:220px;}
.nb {display:inline-block; min-width:26px; text-align:center; font:800 13px/20px 'Barlow Condensed'; border-radius:5px; padding:0 3px;}
.fl {font-size:15px;}
.ph {border-radius:50%; object-fit:cover; object-position:top; flex:none;}
.phn {display:inline-flex; align-items:center; justify-content:center; color:#fff; font:800 16px 'Barlow Condensed';}
.podium {display:grid; grid-template-columns:1fr 1.15fr 1fr; gap:10px; align-items:end; margin:6px 0 14px;}
.pd {background:linear-gradient(180deg, color-mix(in srgb, var(--c) 45%, #000) 0%, var(--card) 100%); border-radius:14px;
     padding:12px 8px 10px; text-align:center; border:1px solid var(--line); min-width:0; position:relative;}
.pd1 {padding-top:18px; border-color:#6b5715;}
.pd .ph {border:2px solid rgba(255,255,255,.25);}
.pdn {font:800 20px 'Barlow Condensed'; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; margin-top:4px;}
.pdt {color:var(--mu); font-size:11px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.pdg {color:var(--mu); font-size:12px;}
.pdb {position:absolute; top:6px; left:8px; font:800 22px 'Barlow Condensed'; color:var(--gold);}
.pd2 .pdb {color:#cfd5df;} .pd3 .pdb {color:#d08a4a;}
/* ---------- brief ---------- */
.bf-grid {display:grid; grid-template-columns:1fr 1fr; gap:12px; align-items:stretch; margin-top:12px;}
.bf-grid .full {grid-column:1 / -1;}
.card {background:var(--card); border:1px solid var(--line); border-radius:16px; padding:14px 16px; min-width:0;}
.card h3 {font:800 22px 'Barlow Condensed'; margin:0 0 4px; padding:0; color:var(--tx); white-space:nowrap; overflow:hidden;
          text-overflow:ellipsis;}
.card .tease {color:var(--mu); font-size:14px; margin-bottom:8px;}
.card .ico {font-size:26px; float:right;}
.facts {margin:0; padding:0; list-style:none;}
.facts li {padding:6px 0; border-bottom:1px solid #222836; font-size:15px;}
.facts li:last-child {border:none;}
.mv {display:flex; align-items:center; gap:8px; padding:4px 0; font-size:14px; white-space:nowrap; min-width:0;}
.mv .rk {font:800 17px 'Barlow Condensed'; width:22px;}
.mv .ar {width:34px; font-weight:700; text-align:center;}
.mv .up {color:var(--ok);} .mv .dn {color:#ff6b6b;} .mv .eq {color:var(--mu);}
.mv .pt {margin-left:auto; color:var(--mu);}
.fav-band {display:flex; align-items:center; gap:14px; border-radius:16px; padding:12px 16px; margin:10px 0;
           background:linear-gradient(90deg, color-mix(in srgb, var(--c) 55%, #000), var(--card) 75%); border:1px solid var(--line);}
.fav-band .ph {border:2px solid rgba(255,255,255,.3);}
.fav-band .t {font:800 22px 'Barlow Condensed'; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.fav-band .s {color:#d9dee7; font-size:14px;}
/* ---------- calendrier ---------- */
.cal {display:grid; grid-template-columns:repeat(auto-fill, minmax(280px, 1fr)); gap:10px;}
.cal .c {background:var(--card); border:1px solid var(--line); border-radius:14px; padding:12px 14px; display:flex; gap:12px;
         align-items:center; min-width:0; text-decoration:none;}
.cal .c.next {border-color:#6b5715; background:linear-gradient(160deg, #2a2410, var(--card) 70%);}
.cal .c.live {border-color:#c62828;}
.cal .c.done {opacity:.75;}
.cal .r {font:800 26px 'Barlow Condensed'; color:var(--mu); width:30px; text-align:center;}
.cal .f {font-size:30px;}
.cal .t {min-width:0;}
.cal .t b {display:block; font:700 19px 'Barlow Condensed'; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.cal .t span {display:block; color:var(--mu); font-size:12px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
/* ---------- classement ---------- */
table.std {width:100%; border-collapse:collapse; font-size:14px;}
table.std th {color:var(--mu); font-size:12px; font-weight:600; text-align:right; padding:6px 6px; border-bottom:1px solid var(--line);
              white-space:nowrap;}
table.std th.l, table.std td.l {text-align:left;}
table.std td {text-align:right; padding:6px 6px; border-bottom:1px solid #1f2430; white-space:nowrap;}
table.std td.pts {font-weight:800; font-size:16px;}
table.std tr.fav td {background:#2a2410;}
table.std .rk {font:800 17px 'Barlow Condensed'; text-align:center; width:30px;}
.lp {display:inline-block; min-width:22px; padding:0 4px; margin-left:2px; border-radius:4px; background:var(--card2);
     font-size:12px; text-align:center;}
.lp.w {background:#6b5715; color:#fff;} .lp.pd {background:#2f3642;} .lp.x {background:#3a1414; color:#ff8080;}
.scroll {overflow-x:auto;}
/* ---------- pilotes ---------- */
.rg {display:grid; grid-template-columns:repeat(auto-fill, minmax(170px, 1fr)); gap:10px;}
.rt {position:relative; overflow:hidden; border-radius:14px; padding:10px; text-decoration:none; min-width:0; display:block;
     background:linear-gradient(170deg, color-mix(in srgb, var(--c) 70%, #000), #12151c 85%); border:1px solid var(--line);}
.rt img {display:block; width:100%; height:150px; object-fit:contain; object-position:bottom;}
.rt .n {position:absolute; top:6px; right:10px; font:800 34px 'Barlow Condensed'; color:rgba(255,255,255,.35);}
.rt b {display:block; font:800 19px 'Barlow Condensed'; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; color:#fff;}
.rt span {display:block; font-size:11px; color:#c9cfda; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.rhead {display:flex; gap:18px; align-items:flex-end; border-radius:18px; padding:16px 20px 0; margin:8px 0 14px; overflow:hidden;
        background:linear-gradient(120deg, color-mix(in srgb, var(--c) 75%, #000), #12151c 80%); border:1px solid var(--line);}
.rhead img {height:190px; width:auto;}
.rhead .i {padding-bottom:16px; min-width:0;}
.rhead .num {font:800 54px/1 'Barlow Condensed'; color:rgba(255,255,255,.4);}
.rhead h1 {font:800 40px/1 'Barlow Condensed'; color:#fff; margin:0; padding:0;}
.rhead .s {color:#dfe3ea; font-size:14px;}
.tiles {display:grid; grid-template-columns:repeat(auto-fill, minmax(130px, 1fr)); gap:8px; margin:6px 0 12px;}
.tile {background:var(--card); border:1px solid var(--line); border-radius:12px; padding:10px 12px; min-width:0;}
.tile b {display:block; font:800 28px/1 'Barlow Condensed';}
.tile span {color:var(--mu); font-size:12px; white-space:nowrap;}
/* ---------- actus ---------- */
.news {display:grid; grid-template-columns:repeat(auto-fill, minmax(300px, 1fr)); gap:12px;}
.nw {background:var(--card); border:1px solid var(--line); border-radius:14px; overflow:hidden; display:flex; flex-direction:column;
     text-decoration:none; min-width:0;}
.nw img {width:100%; height:160px; object-fit:cover; display:block; background:#222;}
.nw .b {padding:10px 12px;}
.nw .t {font:700 18px/1.2 'Barlow Condensed'; color:var(--tx);}
.nw .d {color:var(--mu); font-size:13px; margin-top:4px;}
.nw .m {color:var(--mu); font-size:11px; margin-top:6px; white-space:nowrap;}
.nwx {position:relative;}
.nwx > input {position:absolute; opacity:0; pointer-events:none;}
.nwx .nw {filter:blur(9px); pointer-events:none; user-select:none;}
.nwx > input:checked ~ .nw, .all .nwx .nw {filter:none; pointer-events:auto;}
.nwx > label {position:absolute; inset:0; z-index:2; display:flex; flex-direction:column; align-items:center; justify-content:center;
              text-align:center; gap:4px; cursor:pointer; color:#fff; font-weight:700; padding:16px; background:rgba(10,12,16,.35);}
.nwx > label small {color:#d0d5de; font-weight:500;}
.nwx > input:checked ~ label, .all .nwx > label {display:none;}
.shield {background:#2a2410; border:1px solid #6b5715; border-radius:12px; padding:10px 14px; margin:6px 0 12px; font-size:14px;}
.foot {color:#666e7c; font-size:12px; text-align:center; margin:30px 0 10px;}
@media (max-width: 700px) {
  .bf-grid {grid-template-columns:1fr;}
  .gph h1 {font-size:32px;} .gph .fl0 {font-size:34px;}
  .rhead img {height:130px;} .rhead h1 {font-size:30px;}
  .rn .tm {max-width:130px;}
  .st-key-tabs [data-testid="stHorizontalBlock"] {flex-wrap:nowrap !important; overflow-x:auto; gap:4px;}
  .st-key-tabs [data-testid="stColumn"] {min-width:max-content !important; flex:none !important; width:auto !important;}
  .ses-grid {grid-auto-rows:auto;}
}
</style>
"""


def page_head(title, sub="", kicker=""):
    k = f"<div class='k'>{esc(kicker)}</div>" if kicker else ""
    s = f"<p>{sub}</p>" if sub else ""
    st.markdown(f"<div class='pg-h'>{k}<h2>{esc(title)}</h2>{s}</div>", unsafe_allow_html=True)


def page_footer():
    st.markdown("<div class='foot'>Bada Bing MotoGP · site de fans, non affilié à MotoGP™ / Dorna · données : motogp.com · "
                f"actus : Motorsport.com, Paddock GP · v{APP_VERSION[-3:]}</div>", unsafe_allow_html=True)


# =========================================================================
# MORCEAUX DU WEEK-END
# =========================================================================
def mini_result(rows, fav=None, n=3, race=True):
    """Les 3 premiers (+ mon pilote) en lignes courtes."""
    out = []
    shown = [r for r in rows if r["pos"] and r["pos"] <= n]
    me = next((r for r in rows if fav and r["rid"] == fav), None)
    for r in shown + ([me] if me and me not in shown else []):
        info = rider_of(r["rid"], r)
        if r["pos"]:
            g = "" if r["pos"] == 1 else (("+" + fr_num(r["gap"], 3) + " s") if r["gap"] is not None else "")
            if not race and r["pos"] == 1:
                g = esc(r.get("best") or "")
        else:
            g = status_fr(r)
        cls = "l me" if me is r else "l"
        out.append(f"<div class='{cls}'><span class='p'>{r['pos'] or '–'}</span>{num_badge(info)}<span class='fl'>{flag(r['iso'])}</span>"
                   f"<span class='rn'>{rider_link(r['rid'], esc(info.get('name') or r['name']))}</span><span class='g'>{g}</span></div>")
    return f"<div class='mini'>{''.join(out)}</div>"


def session_tile(ev, s, rows, zone, fav, show_idx, stakes, grid=None, today=None):
    live = session_live(s)
    race = s["code"] in ("SPR", "RAC")
    cls = "ses" + (" race" if race else "") + (" live" if live else "")
    if session_done(s):
        state = "<span class='pill post'>✓ Terminée</span>"
    elif live:
        state = "<span class='pill live'>● En cours</span>"
    else:
        state = f"<span class='hr'>{when_txt(s['utc'], zone, today)}{' (prov.)' if s.get('provisional') else ''}</span>"
    meta = []
    if session_done(s) or live:
        meta.append(when_txt(s["utc"], zone, today))
    if s.get("weather"):
        meta.append(f"{s['weather']} · {TRACK_FR.get(norm(s['track']), 'piste ' + norm(s['track']))}" + (f" · {s['air']}" if s.get("air") else ""))
    body = ""
    if session_done(s) and rows:
        wt = ""
        if race and show_idx:
            lvl = watch_level(watch_score(rows, grid, s, stakes))
            if lvl is not None:
                ic, nm, _ = WATCH_LEVELS[lvl]
                wt = f"<div class='wt'>{ic} <b>{nm}</b></div>"
        body = wt + hidden("👁 Voir le résultat", mini_result(rows, fav, 3, race))
    elif live and rows:
        body = hidden("👁 Voir le classement en direct", mini_result(rows, fav, 3, race))
    elif session_done(s):
        body = "<div class='meta'>Résultat pas encore publié</div>"
    return (f"<div class='{cls}'><div class='top'><span class='nm'>{esc(s['label'])}</span>{state}</div>"
            f"<div class='meta'>{esc(' · '.join(meta))}</div>{body}</div>")


def gp_header(ev, sess_list, zone, kicker="", extra=""):
    state = ev_state(ev, sess_list)
    pill = {"pre": "<span class='pill pre'>À venir</span>", "live": "<span class='pill live'>● Week-end en cours</span>",
            "post": "<span class='pill post'>✓ Terminé</span>"}[state]
    vids = video_buttons(ev, sess_list)
    vids = f"<div class='vids'>{vids}</div>" if vids else ""
    return (f"<div class='gph'><span class='fl0'>{flag(ev['iso'])}</span><div class='k'>{esc(kicker or 'Manche ' + str(ev['round']))}</div>"
            f"<h1>{esc(ev['name'])}</h1><div class='sub'>{esc(ev['circuit'])} · {esc(fr_range(ev['start'], ev['end']))} &nbsp;{pill}</div>"
            f"{extra}{vids}</div>")


def grid_of(res):
    """Grille de départ approchée : Q2 (1-12) puis Q1 (13 et +)."""
    q2 = res.get("Q2") or res.get("Q") or []
    q1 = res.get("Q1") or []
    if not q2:
        return []
    seen = {r["rid"] for r in q2}
    rows = [dict(r) for r in q2 if r["pos"]]
    n = len(rows)
    for r in q1:
        if r["pos"] and r["rid"] not in seen:
            n += 1
            rows.append(dict(r, pos=n))
    return rows


# =========================================================================
# LE WEEK-END EN BREF (faits, sans phrases creuses)
# =========================================================================
def weekend_facts(ev, ss, res, before, after, fav):
    facts = []
    rac, spr = res.get("RAC") or [], res.get("SPR") or []
    grid = grid_of(res)
    if grid:
        p = grid[0]
        facts.append(f"⏱️ Pole pour <b>{rider_link(p['rid'], esc(p['name']))}</b>" + (f" en {esc(p['best'])}" if p.get("best") else "") + ".")
    if spr and any(r["pos"] == 1 for r in spr):
        w = next(r for r in spr if r["pos"] == 1)
        facts.append(f"⚡ Sprint : victoire de <b>{rider_link(w['rid'], esc(w['name']))}</b>"
                     + (f", {fr_num(spr[1]['gap'], 3)} s devant {esc(spr[1]['name'])}." if len(spr) > 1 and spr[1]["gap"] is not None else "."))
    if rac and any(r["pos"] == 1 for r in rac):
        w = next(r for r in rac if r["pos"] == 1)
        sec = next((r for r in rac if r["pos"] == 2), None)
        start = next((g["pos"] for g in grid if g["rid"] == w["rid"]), None)
        extra = f" (parti {nth(start)})" if start and start >= 4 else ""
        facts.append(f"🏁 Grand Prix : <b>{rider_link(w['rid'], esc(w['name']))}</b> gagne{extra}"
                     + (f", {fr_num(sec['gap'], 3)} s devant {esc(sec['name'])}." if sec and sec["gap"] is not None else "."))
        pod = [r for r in rac if r["pos"] in (2, 3)]
        if len(pod) == 2:
            facts.append(f"🥈 Podium complété par {esc(pod[0]['name'])} et {esc(pod[1]['name'])}.")
        if grid:
            start = {g["rid"]: g["pos"] for g in grid}
            gains = sorted(((start.get(r["rid"], 99) - r["pos"], r) for r in rac if r["pos"] and r["rid"] in start), key=lambda x: -x[0])
            if gains and gains[0][0] >= 7:
                g, r = gains[0]
                facts.append(f"🚀 Remontée de <b>{esc(r['name'])}</b> : {nth(start[r['rid']])} sur la grille, {nth(r['pos'])} à l'arrivée.")
        outs = [r for r in rac if not r["pos"] and r["status"] != "NOTSTARTED"]
        if outs:
            names = ", ".join(esc(r["name"].split(" ")[-1]) for r in outs[:5])
            facts.append(f"💥 {len(outs)} abandon{'s' if len(outs) > 1 else ''} en course : {names}.")
    if before and after:
        lead_b, lead_a = before[0], after[0]
        if lead_b["rid"] != lead_a["rid"]:
            facts.append(f"👑 Nouveau leader du championnat : <b>{esc(lead_a['name'])}</b>.")
        elif len(after) > 1:
            facts.append(f"📊 {esc(lead_a['name'])} reste en tête du championnat, {after[1]['gap']} pts devant {esc(after[1]['name'])}.")
    if fav:
        me = next((r for r in rac if r["rid"] == fav), None) or next((r for r in spr if r["rid"] == fav), None)
        if me:
            what = "en course" if me in rac else "au sprint"
            facts.append(f"⭐ Mon pilote, {esc(me['name'])} : " + (f"{nth(me['pos'])} {what}." if me["pos"] else f"abandon {what}."))
    return facts


def moves_html(before, after, fav, n=8):
    pos_b = {t["rid"]: t["rank"] for t in before}
    out = []
    for t in after[:n]:
        b = pos_b.get(t["rid"])
        if b is None or b == t["rank"]:
            ar = "<span class='ar eq'>=</span>"
        elif b > t["rank"]:
            ar = f"<span class='ar up'>▲{b - t['rank']}</span>"
        else:
            ar = f"<span class='ar dn'>▼{t['rank'] - b}</span>"
        info = rider_of(t["rid"], t["row"])
        star = " style='color:var(--gold)'" if fav == t["rid"] else ""
        out.append(f"<div class='mv'><span class='rk'>{t['rank']}</span>{ar}{num_badge(info)}<span{star}>"
                   f"{rider_link(t['rid'], esc(info.get('name') or t['name']))}</span><span class='pt'>{t['pts']} pts</span></div>")
    return "".join(out)


def ours_html(ev, res_all, pays, fav):
    """Les pilotes du pays (toutes catégories) sur le week-end."""
    out = []
    for cat, res in res_all.items():
        for code, label in (("SPR", "Sprint"), ("RAC", "Course")):
            for r in res.get(code) or []:
                if r["iso"] == pays:
                    pos = nth(r['pos']) if r["pos"] else status_fr(r).lower()
                    out.append(f"<li>{flag(r['iso'])} <b>{rider_link(r['rid'], esc(r['name']))}</b> <span class='note'>({cat})</span> "
                               f": {label.lower()} {pos}" + (f", {r['points']} pts" if r["points"] else "") + "</li>")
    return f"<ul class='facts'>{''.join(out)}</ul>" if out else ""


# =========================================================================
# PAGES
# =========================================================================
def pick_event(evs, code=None, ref=None):
    """GP demandé, sinon le week-end en cours, sinon le dernier couru (les jours qui suivent), sinon le prochain."""
    if code:
        e = next((e for e in evs if e["code"] == code), None)
        if e:
            return e
    today = (ref or now_utc()).date()
    cur = [e for e in evs if e["start"] - timedelta(days=1) <= today <= e["end"] + timedelta(days=1)]
    if cur:
        return cur[0]
    past = [e for e in evs if e["end"] < today]
    nxt = [e for e in evs if e["start"] > today]
    if past and (today - past[-1]["end"]).days <= 3 or not nxt:
        return past[-1] if past else evs[0]
    return nxt[0]


def mark_seen(ev, sess_lists):
    """Ouvrir un GP le marque comme vu (séances de qualifs, sprint et course déjà finies)."""
    ss = st.session_state
    keys = {seen_key(ev, s["group"]) for lst in sess_lists for s in lst if s["group"] and session_done(s)}
    if keys - ss.seen:
        ss.seen = ss.seen | keys
        write_seen(ss.seen)


def brief_page(ctx):
    ss = st.session_state
    evs, zone, fav = ctx["evs"], ctx["zone"], ss.get("fav")
    ev = pick_event(evs)
    cat_ids = ctx["cat_ids"]
    wk = weekend(ev, cat_ids)
    sess = wk.get("MotoGP") or []
    res = {s["code"]: classification(s) for s in sess if (session_done(s) or session_live(s))}
    state = ev_state(ev, sess)
    results = ctx["results"]
    cut = ctx["cut"]
    allcls = " all" if ctx["show_all"] else ""

    head = gp_header(ev, sess, zone, kicker="Le brief · " + ("ce week-end" if state != "post" else "le dernier Grand Prix"))
    parts = [head]

    # Mon pilote
    if fav:
        info = rider_of(fav)
        table = ctx["table"]
        me = next((t for t in table if t["rid"] == fav), None)
        nxt = next((s for s in sess if not session_done(s) and s["utc"] and s["utc"] > now_utc()), None)
        if not nxt:
            nev = next((e for e in evs if e["start"] > now_utc().date()), None)
            nxt_txt = f"Prochain GP : {esc(nev['name'])}, {esc(fr_range(nev['start'], nev['end']))}" if nev else "Saison terminée"
        else:
            nxt_txt = f"Prochaine séance : {esc(nxt['label'])}, {when_txt(nxt['utc'], zone)}"
        rank = f"{nth(me['rank'])} au championnat · {me['pts']} pts" if me else "Pas encore de points"
        parts.append(f"<div class='fav-band' style='--c:{esc(info.get('color', '#444'))}'>{photo(info, 58)}<div style='min-width:0'>"
                     f"<div class='t'>⭐ {rider_link(fav, esc(info.get('name')))}</div><div class='s'>{esc(info.get('team', ''))} · {rank}"
                     f"{'' if not cut else ' <span class=note>(avant le dernier GP)</span>'}</div>"
                     f"<div class='s'>{nxt_txt}</div></div></div>")

    # Séances du week-end
    grid = grid_of(res)
    tiles = [session_tile(ev, s, res.get(s["code"]) or [], zone, fav, ss.opts["indice"], stakes_of(ctx["table"]),
                          grid if s["code"] in ("SPR", "RAC") else None) for s in sess]
    if tiles:
        parts.append(f"<div class='ses-grid'>{''.join(tiles)}</div>")
    else:
        parts.append("<p class='note'>Horaires pas encore publiés.</p>")

    # Rubriques cachées
    cards = []
    if any(session_done(s) and s["group"] for s in sess):
        before = standings([x for x in results if x[0]["id"] != ev["id"]], set())
        after = standings(results, set())
        facts = weekend_facts(ev, sess, res, before, after, fav)
        if facts:
            cards.append(("📝", "Le week-end en bref", "Pole, sprint, course, abandons : l'essentiel en quelques lignes.",
                          f"<ul class='facts'>{''.join('<li>' + f + '</li>' for f in facts)}</ul>", ""))
        mv = moves_html(before, after, fav)
        if mv:
            cards.append(("📈", "Ce qui a bougé au championnat", "Le top 8 du championnat après ce week-end.", mv, ""))
        rac = res.get("RAC") or res.get("SPR")
        if rac and any(r["pos"] == 1 for r in rac):
            label = "Le podium du Grand Prix" if res.get("RAC") else "Le podium du sprint"
            cards.append(("🏆", label, "Les trois premiers, avec les photos.", podium_html(rac), "full"))
        pays = ss.opts["pays"]
        ours = ours_html(ev, {c: {s["code"]: classification(s) for s in wk.get(c, []) if session_done(s) and s["code"] in ("SPR", "RAC")}
                              for c in CATS}, pays, fav)
        title = COUNTRY_FR[pays][1]
        if ours:
            cards.append((flag(pays), f"{title} du week-end", "MotoGP, Moto2, Moto3 : comment s'en sont sortis les nôtres ?", ours, "full"))
    html_cards = []
    for ico, title, tease, content, cls in cards:
        html_cards.append(f"<div class='card {cls}'><span class='ico'>{ico}</span><h3>{esc(title)}</h3>"
                          f"<div class='tease'>{esc(tease)}</div>{hidden('👁 Voir', content)}</div>")

    # Actus (avec bouclier) et prochain GP
    items = news(bucket(600))[:4]
    if items:
        html_cards.append(f"<div class='card full'><span class='ico'>📰</span><h3>Les dernières actus</h3>"
                          f"{news_html(items, ctx)}<div style='margin-top:8px'>{link('vue=actus', 'Toutes les actus ›')}</div></div>")
    nev = next((e for e in evs if e["start"] > max(now_utc().date(), ev["end"])), None)
    if nev:
        nsess = sessions(nev, cat_ids["MotoGP"])
        key = [s for s in nsess if s["code"] in ("Q2", "SPR", "RAC")]
        lines = "".join(f"<div class='mv'><b style='width:90px'>{esc(s['short'])}</b><span>{when_txt(s['utc'], zone)}</span></div>"
                        for s in key)
        if key and key[0].get("provisional"):
            lines += "<div class='note'>Horaires provisoires (pas encore confirmés par MotoGP).</div>"
        html_cards.append(f"<div class='card'><span class='ico'>{flag(nev['iso'])}</span><h3>Le prochain GP</h3>"
                          f"<div class='tease'>{gp_link(nev, esc(nev['name']))} · {esc(nev['circuit'])} · "
                          f"{esc(fr_range(nev['start'], nev['end']))}</div>{lines or '<span class=note>Horaires à venir</span>'}</div>")
    parts.append(f"<div class='bf-grid{allcls}'>{''.join(html_cards)}</div>")
    st.markdown(f"<div class='bf{allcls}'>{''.join(parts)}</div>", unsafe_allow_html=True)
    st.markdown(f"<div style='margin-top:10px'>{gp_link(ev, 'Tout le week-end, les 3 catégories et les classements complets ›')}</div>",
                unsafe_allow_html=True)


def gp_page(ctx):
    ss = st.session_state
    evs, zone, fav = ctx["evs"], ctx["zone"], ss.get("fav")
    ev = pick_event(evs, ss.get("gp"))
    i = evs.index(ev)
    with st.container(key="gpnav"):
        c1, c2, c3 = st.columns([1, 6, 1])
        if c1.button("‹", key="gp_prev", disabled=i == 0, use_container_width=True):
            ss.gp = evs[i - 1]["code"]
            st.rerun()
        names = [f"{flag(e['iso'])} {e['round']}. {e['name']}" for e in evs]
        pick = c2.selectbox("Grand Prix", names, index=i, label_visibility="collapsed", key=f"gp_sel_{ev['code']}")
        if names.index(pick) != i:
            ss.gp = evs[names.index(pick)]["code"]
            st.rerun()
        if c3.button("›", key="gp_next", disabled=i == len(evs) - 1, use_container_width=True):
            ss.gp = evs[i + 1]["code"]
            st.rerun()
    cat_ids = ctx["cat_ids"]
    cat = ss.get("cat", "MotoGP")
    cat = st.segmented_control("Catégorie", CATS, default=cat, key="cat_pick", label_visibility="collapsed") or cat
    ss.cat = cat
    wk = weekend(ev, cat_ids)
    sess = wk.get(cat) or []
    st.markdown(gp_header(ev, wk.get("MotoGP") or sess, zone), unsafe_allow_html=True)
    if any(session_done(s) for s in sess):
        mark_seen(ev, list(wk.values()))
    res = {s["code"]: classification(s) for s in sess if session_done(s) or session_live(s)}
    grid = grid_of(res)
    allcls = " all" if ctx["show_all"] else ""
    tiles = [session_tile(ev, s, res.get(s["code"]) or [], zone, fav, ss.opts["indice"], stakes_of(ctx["table"]),
                          grid if s["code"] in ("SPR", "RAC") else None) for s in sess]
    st.markdown(f"<div class='bf{allcls}'><div class='ses-grid'>{''.join(tiles)}</div></div>", unsafe_allow_html=True)

    # Classements complets, séance par séance (cachés)
    done = [s for s in sess if (session_done(s) or session_live(s)) and res.get(s["code"])]
    if done:
        page_head("Les classements complets", "Cliquer sur une séance pour la déplier.", "Résultats")
        for s in reversed(done):
            rows = res[s["code"]]
            race = s["code"] in ("SPR", "RAC")
            extra = ""
            if race and ss.opts["indice"]:
                lvl = watch_level(watch_score(rows, grid, s, stakes_of(ctx["table"])))
                if lvl is not None:
                    ic, nm, tip = WATCH_LEVELS[lvl]
                    extra = f"<p class='note'>{ic} <b>{nm}</b> · {esc(tip)}</p>"
            content = (podium_html(rows) if race else "") + f"<div class='scroll'>{results_table(rows, s['code'], fav)}</div>"
            with st.expander(f"{s['label']} · {when_txt(s['utc'], zone)}" + (" · ● en cours" if session_live(s) else "")):
                st.markdown(f"<div class='bf{allcls}'>{extra}{hidden('👁 Voir le classement', content)}</div>", unsafe_allow_html=True)
    rec = next((s for s in sess if s["code"] == "RAC"), None)
    if rec and session_done(rec):
        recs = records(rec)
        lines = []
        for r in recs:
            who = esc(((r.get("rider") or {}).get("full_name")) or "")
            t = esc((r.get("bestLap") or {}).get("time") or "")
            kind = {"bestLap": "Record de la piste", "poleLap": "Record de pole", "fastestLap": "Meilleur tour en course"}.get(r.get("type"), "")
            if kind and who and r.get("type") != "fastestLap":
                lines.append(f"<li>{kind} : {t} ({who}, {r.get('year', '')})</li>")
        if lines:
            st.markdown(f"<div class='card' style='margin-top:12px'><h3>📏 Records du circuit</h3><ul class='facts'>{''.join(lines)}</ul></div>",
                        unsafe_allow_html=True)


def calendar_page(ctx):
    evs, zone = ctx["evs"], ctx["zone"]
    today = now_utc().date()
    page_head(f"Calendrier {ctx['year']}", f"{len(evs)} Grands Prix · heures de {ctx['zone_name']}", "La saison")
    nxt = next((e for e in evs if e["end"] >= today), None)
    out = []
    for e in evs:
        if e["start"] <= today <= e["end"]:
            cls, st_ = "c live", "<span class='pill live'>● En cours</span>"
        elif e["end"] < today:
            cls, st_ = "c done", "<span class='pill post'>✓ Couru</span>"
        else:
            cls, st_ = ("c next" if e is nxt else "c"), ""
        days = (e["start"] - today).days
        when = f" · dans {days} j" if e is nxt and days > 0 else ""
        out.append(f"<a class='{cls}' href='#go?vue=gp&gp={e['code']}'><span class='r'>{e['round']}</span><span class='f'>{flag(e['iso'])}</span>"
                   f"<span class='t'><b>{esc(e['name'])}</b><span>{esc(fr_range(e['start'], e['end']))}{when} · {esc(e['circuit'])}</span>"
                   f"{st_}</span></a>")
    st.markdown(f"<div class='cal'>{''.join(out)}</div>", unsafe_allow_html=True)
    # Horaires du prochain GP
    if nxt:
        sess = sessions(nxt, ctx["cat_ids"]["MotoGP"])
        if sess:
            page_head(f"Les horaires du {nxt['name']}", f"MotoGP · heures de {ctx['zone_name']}", "Le prochain week-end")
            rows = "".join(f"<tr><td class='l'><b>{esc(s['label'])}</b></td><td class='l'>{when_txt(s['utc'], zone)}</td></tr>" for s in sess)
            if sess[0].get("provisional"):
                rows += "<tr><td class='l note' colspan='2'>Horaires provisoires (pas encore confirmés par MotoGP).</td></tr>"
            st.markdown(f"<table class='std'>{rows}</table>", unsafe_allow_html=True)
            st.download_button("📅 Ajouter les séances à mon agenda", ics_calendar(nxt, sess), file_name=f"motogp_{nxt['code']}.ics",
                               mime="text/calendar", key="ics")


def ics_calendar(ev, sess):
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//BadaBing MotoGP//FR"]
    for s in sess:
        if not s["utc"]:
            continue
        dur = 45 if s["code"] in ("SPR", "Q1", "Q2", "WUP") else 60 if s["code"] in ("FP1", "FP2", "PR") else 60
        a = s["utc"].astimezone(timezone.utc)
        b = a + timedelta(minutes=dur)
        lines += ["BEGIN:VEVENT", f"UID:{s['id']}@badabing-motogp", f"DTSTAMP:{a:%Y%m%dT%H%M%SZ}", f"DTSTART:{a:%Y%m%dT%H%M%SZ}",
                  f"DTEND:{b:%Y%m%dT%H%M%SZ}", f"SUMMARY:MotoGP · {s['label']} · {ev['name']}", f"LOCATION:{ev['circuit']}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)


def cut_note(cut, ctx):
    if not cut:
        return ""
    ev = next((e for e, ss, r in ctx["results"] if any(seen_key(e, s["group"]) in cut for s in ss if s["group"])), None)
    parts = [GROUP_FR[g] for g in "qsr" if any(k.endswith(g) for k in cut)]
    what = " et ".join([", ".join(parts[:-1]), parts[-1]]) if len(parts) > 1 else parts[0]
    return f"Sans spoil : classement arrêté avant {what} du {ev['name'] if ev else 'dernier GP'} (pas encore ouvert sur cet appareil)."


def standings_page(ctx):
    ss = st.session_state
    cat = ss.get("cat", "MotoGP")
    c1, c2 = st.columns([3, 2])
    with c1:
        cat = st.segmented_control("Catégorie", CATS, default=cat, key="cat_std", label_visibility="collapsed") or cat
    ss.cat = cat
    results = ctx["results"] if cat == "MotoGP" else season_results(ctx["evs"], ctx["cat_ids"][cat])
    cut = spoil_cut(results, ss.seen, ctx["show_all"])
    if cut:
        with c2:
            if st.toggle("Inclure le dernier GP", key="inc_last"):
                cut = set()
    kind = st.segmented_control("Classement", ["Pilotes", "Équipes", "Constructeurs"], default="Pilotes", key="std_kind",
                                label_visibility="collapsed") or "Pilotes"
    page_head(f"Championnat {cat} {ctx['year']} · {kind.lower()}", cut_note(cut, ctx), "Classement")
    fav = ss.get("fav")
    if kind == "Pilotes":
        table = standings(results, cut)
        evs_done = [ev["code"] for ev, ss_, r in results if r.get("RAC") and any(s["code"] == "RAC" and session_done(s)
                                                                                    and seen_key(ev, "r") not in cut for s in ss_)]
        head = ("<tr><th class='rk'>#</th><th class='l'>Pilote</th><th>Pts</th><th>Écart</th><th>Vict.</th><th>Podiums</th>"
                "<th>Sprints</th><th>Poles</th><th class='l'>3 derniers GP</th></tr>")
        rows = []
        for t in table:
            last = []
            for code, p in t["last"][-3:]:
                c = "w" if p == 1 else "pd" if p and p <= 3 else "x" if not p else ""
                last.append(f"<span class='lp {c}' title='{esc(GP.get(code, (code,))[0])}'>{p or 'ab'}</span>")
            rows.append(f"<tr class='{'fav' if t['rid'] == fav else ''}'><td class='rk'>{t['rank']}</td>"
                        f"<td class='l'>{rider_cell(t['row'])}</td><td class='pts'>{t['pts']}</td>"
                        f"<td>{'–' if not t['gap'] else '-' + str(t['gap'])}</td><td>{t['wins']}</td><td>{t['pod']}</td>"
                        f"<td>{t['swins']}</td><td>{t['poles']}</td><td class='l'>{''.join(last)}</td></tr>")
        st.markdown(f"<div class='scroll'><table class='std'>{head}{''.join(rows)}</table></div>", unsafe_allow_html=True)
        st.caption(f"{len(evs_done)} Grands Prix comptés. Sprint (MotoGP) et course comptent ; « Sprints » = victoires en sprint.")
    else:
        table = team_standings(results, cut, "team" if kind == "Équipes" else "maker")
        rows = "".join(f"<tr><td class='rk'>{t['rank']}</td><td class='l'><b>{esc(t['name'])}</b>"
                       f"{'' if kind == 'Constructeurs' else ' <span class=note>' + esc(t['maker']) + '</span>'}</td>"
                       f"<td class='pts'>{t['pts']}</td><td>{'–' if not t['gap'] else '-' + str(t['gap'])}</td></tr>" for t in table)
        st.markdown(f"<table class='std'><tr><th class='rk'>#</th><th class='l'>{kind[:-1]}</th><th>Pts</th><th>Écart</th></tr>{rows}</table>",
                    unsafe_allow_html=True)
        if kind == "Constructeurs":
            st.caption("Constructeurs : seul le meilleur pilote de chaque marque marque des points à chaque sprint et course.")


def rider_results(results, rid, cut):
    """Résultats GP par GP d'un pilote : [(ev, qualif, sprint, course, points)]."""
    out = []
    for ev, ss, res in results:
        grid = grid_of(res) if seen_key(ev, "q") not in cut else []
        q = next((g["pos"] for g in grid if g["rid"] == rid), None)
        sp = rc = None
        pts = 0
        found = False
        for code in ("SPR", "RAC"):
            if seen_key(ev, GROUP[code]) in cut:
                continue
            r = next((x for x in res.get(code) or [] if x["rid"] == rid), None)
            if r:
                found = True
                pts += r["points"] or 0
                if code == "SPR":
                    sp = r
                else:
                    rc = r
        if found or q:
            out.append((ev, q, sp, rc, pts))
    return out


def pos_svg(rows):
    """Courbe des places en course, GP après GP (1 en haut)."""
    pts = [(i, r[3]["pos"]) for i, r in enumerate(rows) if r[3]]
    if len(pts) < 2:
        return ""
    w, h, n = 640, 150, max(len(rows) - 1, 1)
    X = lambda i: 20 + i * (w - 40) / n
    Y = lambda p: 14 + (min(p or 25, 25) - 1) * (h - 28) / 24
    path, dots = [], []
    for i, p in pts:
        path.append(f"{X(i):.0f},{Y(p):.0f}")
    for i, r in enumerate(rows):
        rc = r[3]
        if not rc:
            continue
        p = rc["pos"]
        col = "#f5c542" if p == 1 else "#cfd5df" if p and p <= 3 else "#ff6b6b" if not p else "#8a93a3"
        dots.append(f"<circle cx='{X(i):.0f}' cy='{Y(p):.0f}' r='5' fill='{col}'><title>{esc(r[0]['name'])} : "
                    f"{nth(p) if p else 'abandon'}</title></circle>")
    grid = "".join(f"<line x1='20' x2='{w - 20}' y1='{Y(p):.0f}' y2='{Y(p):.0f}' stroke='#262c38'/>"
                   f"<text x='2' y='{Y(p) + 4:.0f}' fill='#7a8394' font-size='10'>{p}</text>" for p in (1, 5, 10, 15, 20))
    return (f"<svg viewBox='0 0 {w} {h}' width='100%' style='max-width:{w}px'>{grid}"
            f"<polyline points='{' '.join(path)}' fill='none' stroke='#e10600' stroke-width='2'/>{''.join(dots)}</svg>")


def rider_page(ctx, rid):
    ss = st.session_state
    info = rider_of(rid)
    cat = info.get("cat") or "MotoGP"
    results = ctx["results"] if cat == "MotoGP" else season_results(ctx["evs"], ctx["cat_ids"][cat])
    cut = spoil_cut(results, ss.seen, ctx["show_all"])
    table = standings(results, cut)
    me = next((t for t in table if t["rid"] == rid), None)
    c1, c2 = st.columns([1, 1])
    if c1.button("‹ Tous les pilotes", key="back_riders"):
        ss.rider = None
        st.rerun()
    is_fav = ss.get("fav") == rid
    if c2.button("★ Mon pilote" if is_fav else "☆ Suivre ce pilote", key="follow", type="primary" if is_fav else "secondary"):
        ss.fav = None if is_fav else rid
        set_cookie("gp_fav", ss.fav or "")
        st.rerun()
    age = f"{info['age']} ans · " if info.get("age") else ""
    born = f"né à {esc(info.get('city'))} ({esc(info.get('country'))})" if info.get("city") else esc(info.get("country", ""))
    img = f"<img src='{esc(info['photo'])}' alt=''>" if info.get("photo") else ""
    st.markdown(f"<div class='rhead' style='--c:{esc(info.get('color', '#444'))}'>{img}<div class='i'>"
                f"<div class='num'>#{esc(info.get('num') or '')}</div><h1>{flag(info.get('iso'))} {esc(info.get('name'))}</h1>"
                f"<div class='s'>{esc(info.get('team', ''))} · {esc(info.get('maker', ''))} · {cat}</div>"
                f"<div class='s'>{age}{born}{' · en Grand Prix depuis ' + str(info['start']) if info.get('start') else ''}</div></div></div>",
                unsafe_allow_html=True)
    if cut:
        st.markdown(f"<p class='note'>{esc(cut_note(cut, ctx))}</p>", unsafe_allow_html=True)
    rows = rider_results(results, rid, cut)
    if me:
        tiles = [(nth(me['rank']), "au championnat"), (me["pts"], "points"), (me["wins"], "victoires"), (me["pod"], "podiums"),
                 (me["swins"], "sprints gagnés"), (me["poles"], "poles"), (me["best"] or "–", "meilleur résultat"), (me["dnf"], "abandons")]
        st.markdown("<div class='tiles'>" + "".join(f"<div class='tile'><b>{v}</b><span>{esc(l)}</span></div>" for v, l in tiles) + "</div>",
                    unsafe_allow_html=True)
    svg = pos_svg(rows)
    if svg:
        page_head("Place en course, GP après GP", "", "La saison")
        st.markdown(svg, unsafe_allow_html=True)
    if rows:
        page_head("Résultats", "Grille · sprint · course · points", "GP par GP")
        def cell(r):
            if not r:
                return "–"
            return nth(r['pos']) if r["pos"] else "ab."
        trs = "".join(f"<tr><td class='l'>{flag(ev['iso'])} {gp_link(ev, esc(ev['name']))}</td><td>{nth(q) if q else '–'}</td>"
                      f"<td>{cell(sp)}</td><td>{cell(rc)}</td><td class='pts'>{pts}</td></tr>" for ev, q, sp, rc, pts in reversed(rows))
        st.markdown(f"<div class='scroll'><table class='std'><tr><th class='l'>Grand Prix</th><th>Grille</th><th>Sprint</th>"
                    f"<th>Course</th><th>Pts</th></tr>{trs}</table></div>", unsafe_allow_html=True)
    # Actus du pilote
    last = norm(info.get("last", ""))
    items = [n for n in news(bucket(600)) if last and len(last) > 2 and last in norm(n["title"] + " " + n["desc"])][:6]
    if items:
        page_head(f"Les actus de {info.get('name')}", "", "Actus")
        st.markdown(news_html(items, ctx), unsafe_allow_html=True)


def riders_page(ctx):
    ss = st.session_state
    if ss.get("rider"):
        rider_page(ctx, ss.rider)
        return
    cat = ss.get("cat", "MotoGP")
    cat = st.segmented_control("Catégorie", CATS, default=cat, key="cat_rid", label_visibility="collapsed") or cat
    ss.cat = cat
    riders = [r for r in ctx["riders"].values() if r.get("cat") == cat and r.get("in_grid", True)]
    order = {t["rid"]: t["rank"] for t in (ctx["table"] if cat == "MotoGP" else [])}
    riders.sort(key=lambda r: (order.get(r["id"], 99), r.get("team", ""), r.get("last", "")))
    page_head(f"Les pilotes {cat} {ctx['year']}", "Cliquer sur un pilote pour sa fiche · ⭐ = mon pilote", "Pilotes")
    pays = ss.opts["pays"]
    tiles = []
    for r in riders:
        star = "⭐ " if ss.get("fav") == r["id"] else ""
        img = f"<img src='{esc(r['photo'])}' loading='lazy' alt=''>" if r.get("photo") else "<div style='height:150px'></div>"
        tiles.append(f"<a class='rt' href='#go?vue=pilotes&pilote={r['id']}' style='--c:{esc(r.get('color', '#444'))}'>"
                     f"<span class='n'>{esc(r.get('num') or '')}</span>{img}<b>{star}{flag(r['iso'])} {esc(r.get('name'))}</b>"
                     f"<span>{esc(r.get('team', ''))}</span></a>")
    if not tiles:
        st.info("Liste des pilotes indisponible pour le moment.")
    st.markdown(f"<div class='rg'>{''.join(tiles)}</div>", unsafe_allow_html=True)
    ours = [r for r in ctx["riders"].values() if r.get("iso") == pays]
    if ours:
        page_head(f"{COUNTRY_FR[pays][1]} en Grand Prix", "Toutes catégories", flag(pays))
        st.markdown("<ul class='facts'>" + "".join(
            f"<li>{flag(pays)} {link('vue=pilotes&pilote=' + r['id'], '<b>' + esc(r['name']) + '</b>', 'rl')} · {esc(r.get('cat'))} · "
            f"{esc(r.get('team'))}</li>" for r in sorted(ours, key=lambda r: CATS.index(r.get("cat", "MotoGP")))) + "</ul>",
            unsafe_allow_html=True)


def news_html(items, ctx):
    spoil = ctx["spoil_from"]
    out = []
    for n in items:
        pub = parse_dt(n["pub"])
        when = rel_time(pub) if pub else ""
        img = f"<img src='{esc(n['img'])}' loading='lazy' alt=''>" if n.get("img") else ""
        card = (f"<a class='nw' href='{esc(n['link'])}' target='_blank' rel='noopener'>{img}<div class='b'>"
                f"<div class='t'>{esc(n['title'])}</div><div class='d'>{esc(n['desc'][:180])}</div>"
                f"<div class='m'>{esc(n['source'])} · {esc(when)}</div></div></a>")
        if spoil and pub and pub >= spoil[0]:
            i = uid("n")
            _, ev, s = spoil
            out.append(f"<div class='nwx'><input type='checkbox' id='{i}' class='tg'><label for='{i}'>⚠️ Peut révéler un résultat"
                       f"<small>publié après le début : {esc(s['label'])} du {esc(ev['name'])}</small><small>👁 Afficher</small></label>{card}</div>")
        else:
            out.append(card)
    return f"<div class='news'>{''.join(out)}</div>"


def news_page(ctx):
    ss = st.session_state
    items = news(bucket(600))
    page_head("Les actus MotoGP", "Motorsport.com France et Paddock GP · en français", "Actus")
    spoil = ctx["spoil_from"]
    if spoil:
        _, ev, s = spoil
        st.markdown(f"<div class='shield'>🛡️ <b>Bouclier anti-spoil</b> : les articles publiés après le début de "
                    f"<b>{esc(s['label'].lower() if s['code'] != 'RAC' else 'la course')}</b> du {esc(ev['name'])} sont floutés tant que "
                    f"tu n'as pas ouvert ce GP. Un clic sur un article l'affiche.</div>", unsafe_allow_html=True)
    who = st.segmented_control("Filtre", ["Tout", "Mon pilote", "MotoGP", "Moto2 / Moto3"], default="Tout", key="news_f",
                               label_visibility="collapsed") or "Tout"
    if who == "Mon pilote":
        last = norm(rider_of(ss.get("fav")).get("last", "")) if ss.get("fav") else ""
        items = [n for n in items if last and last in norm(n["title"] + " " + n["desc"])]
        if not ss.get("fav"):
            st.info("Choisis ton pilote dans ⚙️ (ou « Suivre ce pilote » sur sa fiche).")
    elif who == "MotoGP":
        items = [n for n in items if not re.search(r"moto ?[23]\b", n["title"], re.I)]
    elif who == "Moto2 / Moto3":
        items = [n for n in items if re.search(r"moto ?[23]\b", n["title"] + " " + n["link"], re.I)]
    if not items:
        st.markdown("<p class='note'>Aucune actu pour le moment.</p>", unsafe_allow_html=True)
        return
    allcls = " all" if ctx["show_all"] else ""
    if spoil and not ctx["show_all"] and st.toggle("👁 Afficher toutes les actus (même celles qui peuvent spoiler)", key="news_all"):
        allcls = " all"
    st.markdown(f"<div class='{allcls}'>{news_html(items[:40], ctx)}</div>", unsafe_allow_html=True)


# =========================================================================
# RÉGLAGES ET PAGE PRINCIPALE
# =========================================================================
def settings_panel(ctx):
    ss = st.session_state
    riders = sorted([r for r in ctx["riders"].values() if r.get("cat") == "MotoGP"], key=lambda r: r.get("last", ""))
    ids = [""] + [r["id"] for r in riders]
    names = {"": "— Aucun —", **{r["id"]: f"{flag(r['iso'])} {r['name']} (#{r.get('num') or '?'})" for r in riders}}
    cur = ss.get("fav") or ""
    if cur and cur not in ids:
        ids.append(cur)
        names[cur] = rider_of(cur).get("name", cur)
    pick = st.selectbox("⭐ Mon pilote", ids, index=ids.index(cur), format_func=lambda x: names.get(x, x), key="set_fav")
    if (pick or None) != ss.get("fav"):
        ss.fav = pick or None
        set_cookie("gp_fav", pick or "")
    o = dict(ss.opts)
    pays = st.radio("Pays (pour « nos pilotes »)", ["FR", "CA"], index=["FR", "CA"].index(o["pays"]), horizontal=True,
                    format_func=lambda x: "🇫🇷 France" if x == "FR" else "🇨🇦 Canada", key="set_pays")
    idx = st.toggle("Indice « à voir » des courses", value=o["indice"], key="set_idx")
    sc = st.toggle("Toujours montrer les résultats (pas de spoil caché)", value=o["scores"], key="set_sc")
    new = {"pays": pays, "indice": idx, "scores": sc}
    if new != o:
        ss.opts = new
        set_cookie("gp_opt", opts_code(new))
        st.rerun()
    st.caption(f"Heures affichées : {ctx['zone_name']} (d'après ton navigateur). Réglages gardés sur cet appareil.")


def user_zone():
    try:
        name = st.context.timezone or ""
    except Exception:
        name = ""
    try:
        return ZoneInfo(name), name or "Europe/Paris"
    except Exception:
        return ZoneInfo("Europe/Paris"), "Europe/Paris"


def zone_label(name):
    city = name.split("/")[-1].replace("_", " ")
    return {"Paris": "Paris", "Toronto": "Montréal", "Montreal": "Montréal"}.get(city, city)


def main():
    st.set_page_config(page_title="Bada Bing MotoGP", page_icon="🏍️", layout="wide", initial_sidebar_state="collapsed")
    st.markdown(CSS, unsafe_allow_html=True)
    ss = st.session_state
    _UID[0] = 0

    go_cmd = ss.get("go_to")
    ss.go_to = ""
    with st.container(key="gobox"):
        st.text_input("Aller à", key="go_to", label_visibility="collapsed")

    zone, tzname = user_zone()
    ss.tzname = tzname
    try:
        cookies = {} if os.environ.get("BB_SANS_COOKIES") else dict(st.context.cookies)
    except Exception:
        cookies = {}
    if "opts" not in ss:
        ss.opts = opts_from(cookies.get("gp_opt", ""), tzname)
        ss.fav = cookies.get("gp_fav") or None
        ss.seen = read_seen(cookies.get("gp_vues", ""))
        ss.hello_done = bool(cookies.get("gp_hello"))
        q = st.query_params
        ss.view = q.get("vue") if q.get("vue") in VIEWS else "brief"
        ss.gp = q.get("gp") or None
        ss.rider = q.get("pilote") or None
        ss.cat = q.get("cat") if q.get("cat") in CATS else "MotoGP"
    if go_cmd:
        apply_go(go_cmd)

    season = current_season()
    if not season:
        st.error("Le site officiel MotoGP ne répond pas. Réessaie dans quelques minutes.")
        return
    evs = events(season["id"], bucket(600))
    cat_ids = categories(season["id"])
    if not evs or "MotoGP" not in cat_ids:
        st.error("Calendrier MotoGP indisponible pour le moment.")
        return
    riders = riders_info(season["year"])
    ss["riders_map"] = riders
    results = season_results(evs, cat_ids["MotoGP"])
    show_all = ss.opts["scores"]
    cut = spoil_cut(results, ss.seen, show_all)
    ctx = {"evs": evs, "cat_ids": cat_ids, "zone": zone, "zone_name": zone_label(tzname), "year": season["year"],
           "riders": riders, "results": results, "cut": cut, "show_all": show_all, "table": standings(results, cut),
           "spoil_from": spoil_since(results, ss.seen, show_all)}

    # En-tête : marque + roue crantée
    hdr = st.container(key="hdr")
    h1, h2 = hdr.columns([10, 1])
    h1.markdown("<div class='brand'>BADA BING <i>MOTOGP</i></div>", unsafe_allow_html=True)
    with h2.popover("⚙️", use_container_width=True):
        settings_panel(ctx)

    if not ss.hello_done:
        st.info("👋 Bienvenue ! Ici, **aucun résultat n'est montré sans ton accord** : chaque classement est caché derrière "
                "« 👁 Voir », le championnat s'arrête avant le dernier GP tant que tu ne l'as pas ouvert, et les actus récentes "
                "sont floutées. Choisis ton pilote dans ⚙️.")
        set_cookie("gp_hello", "1")
        ss.hello_done = True

    with st.container(key="tabs"):
        cols = st.columns(len(TABS))
        for (key, label, icon), col in zip(TABS, cols):
            if col.button(label, icon=icon, key=f"tab_{key}", use_container_width=True,
                          type="primary" if ss.view == key else "tertiary"):
                ss.view = key
                if key == "pilotes":
                    ss.rider = None
                if key == "gp":
                    ss.gp = None
                st.rerun()

    view = ss.view
    st.query_params.clear()
    if view != "brief":
        st.query_params["vue"] = view
    if view == "gp" and ss.get("gp"):
        st.query_params["gp"] = ss.gp
    if view == "pilotes" and ss.get("rider"):
        st.query_params["pilote"] = ss.rider

    {"brief": brief_page, "gp": gp_page, "calendrier": calendar_page, "classement": standings_page,
     "pilotes": riders_page, "actus": news_page}[view](ctx)

    page_footer()
    # Rechargement auto pendant une séance (MotoGP, Moto2 ou Moto3)
    ev = pick_event(evs)
    live = False
    if ev["start"] - timedelta(days=1) <= now_utc().date() <= ev["end"]:
        wk = weekend(ev, cat_ids)
        live = any(session_live(s) or (not session_done(s) and s["utc"] and timedelta(0) < s["utc"] - now_utc() < timedelta(minutes=15))
                   for lst in wk.values() for s in lst)
    known = {"restored": bool(ss.get("restored")), "fav": ss.get("fav") or "", "opt": opts_code(ss.opts),
             "vues": ".".join(sorted(ss.seen)), "hello": bool(ss.get("hello_done"))}
    page_script(known, live)


if __name__ == "__main__":
    main()
