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

APP_VERSION = "2026-11-m013"
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
              "rain": "Pluie", "partly-cloudy": "Éclaircies", "mostly cloudy": "Nuageux", "mostly-cloudy": "Nuageux", "fog": "Brouillard", "windy": "Venteux", "light rain": "Pluie fine", "heavy rain": "Forte pluie", "drizzle": "Bruine"}
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
    # Horaires officiels de diffusion (justes, même quand ceux des résultats sont des bouche-trous)
    times = (event_meta(ev).get("times") or {}).get(ACRONYM.get(CAT_OF.get(cat_id, ""), ""), {})
    if times:
        for s in out:
            if not session_done(s) and times.get(s["code"]):
                s["utc"] = times[s["code"]]
        out.sort(key=lambda s: s["utc"])
    if out and not any(session_done(s) for s in out):
        # L'API publie parfois des horaires bouche-trou hors des dates du GP : on les recale sur le week-end, marqués provisoires
        shift = (ev["start"] - out[0]["utc"].date()).days
        if abs(shift) >= 2:
            for s in out:
                s["utc"] = s["utc"] + timedelta(days=shift)
                s["provisional"] = True
    return out


ACRONYM = {"MotoGP": "MGP", "Moto2": "MT2", "Moto3": "MT3"}
CAT_OF = {}  # id de catégorie (résultats) -> nom, rempli au démarrage


def _photo_url(u):
    return quote(u, safe=":/@%?=&") if u else ""


@st.cache_data(ttl=3600, show_spinner=False)
def events_meta(year, _bucket=None):
    """Fiche de chaque GP (API « events ») : photos, tracé et chiffres du circuit, tours, horaires officiels. {code: {...}}"""
    data = get_json(f"{API}/events", {"seasonYear": year})
    if data is None:
        return disk_get(f"meta_{year}.json") or {}
    out = {}
    for e in data:
        code = e.get("shortname") or ""
        if e.get("kind") not in (None, "GP") or not code:
            continue
        cats = {c.get("id"): c.get("acronym") for c in e.get("categories") or []}
        assets = {}
        for a in e.get("assets") or []:
            if a.get("type") == "TOP" and a.get("quality") not in ("@2x", "@1x"):
                continue
            if a.get("type") == "TOP" and assets.get("TOP") and a.get("quality") == "@1x":
                continue
            assets[a.get("type")] = _photo_url(a.get("path"))
        circ = e.get("circuit") or {}
        trk = circ.get("track") or {}
        tassets = trk.get("assets") or {}
        laps = {}
        for c in e.get("event_categories") or []:
            acr = cats.get(c.get("category_id"))
            if acr:
                laps[acr] = {"laps": c.get("num_laps"), "sprint": c.get("sprint_num_laps"),
                             "km": ((c.get("distance") or {}).get("kiloMeters"))}
        times = {}
        for b in e.get("broadcasts") or []:
            if b.get("type") != "SESSION":
                continue
            acr = (b.get("category") or {}).get("acronym")
            dt = parse_dt(re.sub(r"([+-]\d\d)(\d\d)$", r"\1:\2", b.get("date_start") or ""))
            if acr and dt:
                times.setdefault(acr, {})[b.get("shortname")] = dt.isoformat()
        out[code] = {"bg": assets.get("BACKGROUND", ""), "top": assets.get("TOP", ""), "flag": assets.get("FLAG", ""),
                     "track": _photo_url((tassets.get("simple") or {}).get("path")),
                     "km": (trk.get("lenght_units") or {}).get("kiloMeters"), "width": trk.get("width"),
                     "straight": trk.get("longest_straight"), "left": trk.get("left_corners"), "right": trk.get("right_corners"),
                     "built": circ.get("constructed"), "city": circ.get("city") or "", "country": circ.get("country") or "",
                     "laps": laps, "times": times, "hashtag": e.get("hashtag") or ""}
    if out:
        disk_put(f"meta_{year}.json", out)
    return out


def event_meta(ev):
    m = dict((events_meta(ev["start"].year, bucket(3600)) or {}).get(ev["code"]) or {})
    if m.get("times"):
        m["times"] = {a: {k: parse_dt(v) for k, v in t.items()} for a, t in m["times"].items()}
    return m


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
        fix_photos(out, year)
        disk_put(f"riders_{year}.json", out)
    return out or disk_get(f"riders_{year}.json") or {}


def _url_ok(u):
    try:
        return bool(u) and SESSION.head(u, timeout=8, allow_redirects=True).status_code == 200
    except Exception:
        return False


def fix_photos(out, year):
    """Photo absente ou cassée dans la liste des équipes (ex. Chantra, Nakagami) : on prend celle de la fiche du pilote."""
    ids = list(out)
    with ThreadPoolExecutor(max_workers=12) as pool:
        ok = dict(zip(ids, pool.map(lambda i: _url_ok(out[i]["photo"]), ids)))
    bad = [i for i in ids if not ok[i]]

    def better(rid):
        d = get_json(f"{API}/riders/{rid}") or {}
        cands = []
        for c in d.get("career") or []:
            u = ((c.get("pictures") or {}).get("profile") or {}).get("main")
            if u:
                cands.append((c.get("season") == year, c.get("season") or 0, u))
        for _, _, u in sorted(cands, reverse=True):
            u = u.replace("motogp.com//", "motogp.com/")
            if _url_ok(u):
                return u
        return ""

    with ThreadPoolExecutor(max_workers=8) as pool:
        for rid, u in zip(bad, pool.map(better, bad)):
            out[rid]["photo"] = u


@st.cache_data(ttl=86400, show_spinner=False, max_entries=200)
def rider_extra(rid, year):
    """Pilote absent de la liste des équipes (remplaçant, wild-card, pilote d'essai) : sa fiche (photo, numéro, couleurs)."""
    d = get_json(f"{API}/riders/{rid}") or {}
    if not d:
        return {}
    car = sorted(d.get("career") or [], key=lambda c: (c.get("season") == year, c.get("season") or 0), reverse=True)
    photo, step = "", (car[0] if car else {})
    for c in car:
        u = (((c.get("pictures") or {}).get("profile") or {}).get("main") or "").replace("motogp.com//", "motogp.com/")
        if u and _url_ok(u):
            photo = u
            break
    tm = step.get("team") or {}
    return {"id": rid, "name": f"{d.get('name', '')} {d.get('surname', '')}".strip(), "first": d.get("name", ""),
            "last": d.get("surname", ""), "iso": (d.get("country") or {}).get("iso", ""), "country": (d.get("country") or {}).get("name", ""),
            "birth": d.get("birth_date", ""), "city": d.get("birth_city", ""), "age": d.get("years_old"), "num": step.get("number"),
            "team": step.get("sponsored_team") or tm.get("name", ""), "color": tm.get("color") or "#555", "text": tm.get("text_color") or "#fff",
            "maker": (tm.get("constructor") or {}).get("name", ""), "photo": photo,
            "cat": re.sub(r"[^A-Za-z0-9]", "", (step.get("category") or {}).get("name", "")), "start": d.get("start_year"),
            "in_grid": False}


def rider_of(rid, row=None):
    info = (st.session_state.get("riders_map") or {}).get(rid)
    if info:
        return info
    if rid:
        try:
            info = rider_extra(rid, now_utc().year)
        except Exception:
            info = {}
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
            if source == "Paddock GP":  # image de l'article hébergée chez eux (celles du texte viennent parfois d'ailleurs)
                img = ""
            if not img and source != "Paddock GP":
                body = (it.findtext("{http://purl.org/rss/1.0/modules/content/}encoded") or "") + (it.findtext("description") or "")
                m = re.search(r"<img[^>]+src=[\"']([^\"']+)", body)
                img = m.group(1) if m else ""
            items.append({"title": title, "link": (it.findtext("link") or "").strip(), "pub": pub.isoformat() if pub else "",
                          "desc": desc[:280], "img": img, "source": source})
    items.sort(key=lambda x: x["pub"], reverse=True)
    missing = [x for x in items[:60] if not x["img"] and x["link"]]
    if missing:  # image de l'article (balise og:image de la page)
        with ThreadPoolExecutor(max_workers=8) as pool:
            for x, im in zip(missing, pool.map(og_image, [x["link"] for x in missing])):
                x["img"] = im
    if items:
        disk_put("news.json", items)
    return items or disk_get("news.json") or []


_OG = {}


def og_image(url):
    """Image principale d'un article (og:image), gardée en mémoire et sur disque."""
    if not _OG:
        _OG.update(disk_get("og.json") or {})
    if url in _OG:
        return _OG[url]
    text = get_text(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
    m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', text) or \
        re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image', text)
    _OG[url] = html.unescape(m.group(1)) if m else ""
    if text:
        disk_put("og.json", dict(list(_OG.items())[-400:]))
    return _OG[url]


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
YOUTUBE_PLAYLIST_URL = "https://www.googleapis.com/youtube/v3/playlistItems"
CANAL_CHANNEL = "UC8ggH3zU61XO0nMskSQwZdA"  # CANAL+ Sport : résumés en français (« Le résumé de la course sprint du Grand Prix du Qatar 2026 - MotoGP »)
CANAL_HANDLE = "CANALPLUSSPORT"


def _secret(section, key, default=""):
    try:
        value = st.secrets[section][key]
        if value not in (None, ""):
            return value
    except Exception:
        pass
    return os.environ.get(f"{section}_{key}".upper()) or default


def _walk_videos(o):
    if isinstance(o, dict):
        for k in ("videoRenderer", "gridVideoRenderer"):
            if k in o:
                yield o[k]
        for x in o.values():
            yield from _walk_videos(x)
    elif isinstance(o, list):
        for x in o:
            yield from _walk_videos(x)


def _yt_page(url, params, lang="fr"):
    try:
        r = SESSION.get(url, params=params, headers={"Accept-Language": "fr-FR,fr" if lang == "fr" else "en-US,en"},
                        cookies={"CONSENT": "YES+1", "SOCS": "CAI"}, timeout=12, allow_redirects=False)
        if r.status_code != 200:
            return []
        m = re.search(r"var ytInitialData = (\{.*?\});</script>", r.text)
        data = json.loads(m.group(1)) if m else {}
    except Exception as e:
        log.warning("YouTube indisponible (%s)", type(e).__name__)
        return []
    out = []
    for v in _walk_videos(data):
        title = "".join(x.get("text", "") for x in (v.get("title") or {}).get("runs") or []) or (v.get("title") or {}).get("simpleText", "")
        owner = ((v.get("ownerText") or v.get("shortBylineText") or {}).get("runs") or [{}])[0]
        channel = ((owner.get("navigationEndpoint") or {}).get("browseEndpoint") or {}).get("browseId", "")
        length = (v.get("lengthText") or {}).get("simpleText", "")
        parts = [int(x) for x in length.split(":") if x.isdigit()]
        secs = sum(x * 60 ** i for i, x in enumerate(reversed(parts)))
        if v.get("videoId") and title:
            out.append({"id": v["videoId"], "title": title, "channel": channel, "mins": max(1, round(secs / 60)) if secs else None})
    return out


@st.cache_data(ttl=1800, show_spinner=False, max_entries=300)
def yt_search(query, lang="en"):
    """Recherche YouTube (page publique, sans clé) : filet de sécurité, peu fiable (YouTube limite les robots)."""
    if _FAKE:
        return _FAKE("yt_search", {"q": query}) or []
    hl = {"hl": "fr", "gl": "FR"} if lang == "fr" else {"hl": "en", "gl": "US"}
    return _yt_page("https://www.youtube.com/results", dict(search_query=query, **hl), lang)


@st.cache_data(ttl=1800, show_spinner=False, max_entries=100)
def channel_search(channel, query):
    """Recherche dans une chaîne (sans clé)."""
    out = _yt_page(f"https://www.youtube.com/channel/{channel}/search", {"query": query, "hl": "fr", "gl": "FR"})
    for v in out:
        v["channel"] = v["channel"] or channel
    return out


@st.cache_data(ttl=900, show_spinner=False)
def channel_feed(channel, _bucket=None):
    """Flux public de la chaîne (15 dernières vidéos)."""
    text = get_text(YOUTUBE_FEED_URL.format(channel))
    out = []
    if not text or "<feed" not in text:
        return out
    ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return out
    for e in root.findall("a:entry", ns):
        vid = e.findtext("yt:videoId", default="", namespaces=ns)
        if vid:
            out.append({"id": vid, "title": e.findtext("a:title", default="", namespaces=ns), "channel": channel, "mins": None})
    return out


@st.cache_data(ttl=1800, show_spinner=False)
def channel_uploads(channel, since_iso, _bucket=None):
    """Vidéos de la chaîne depuis `since_iso`, par l'API YouTube (clé [youtube] api_key) : la seule source complète,
    qui voit aussi les vidéos réservées à la France."""
    key = str(_secret("youtube", "api_key", ""))
    if not key:
        return []
    since = parse_dt(since_iso)
    out, token = [], None
    for _ in range(6):
        params = {"part": "snippet,contentDetails", "playlistId": "UU" + channel[2:], "maxResults": 50, "key": key}
        if token:
            params["pageToken"] = token
        try:
            r = SESSION.get(YOUTUBE_PLAYLIST_URL, params=params, timeout=10)
        except Exception:
            break
        if r.status_code != 200:
            log.warning("API YouTube : HTTP %s", r.status_code)
            break
        payload = r.json()
        page = []
        for item in payload.get("items") or []:
            sn = item.get("snippet") or {}
            vid = (sn.get("resourceId") or {}).get("videoId")
            if vid:
                page.append({"id": vid, "title": html.unescape(sn.get("title", "")), "channel": channel, "mins": None,
                             "published": sn.get("publishedAt", "")})
        out += page
        token = payload.get("nextPageToken")
        if not token or not page or (since and all((parse_dt(v["published"]) or since) < since for v in page[-5:])):
            break
    return out


@st.cache_data(ttl=6 * 3600, show_spinner=False, max_entries=300)
def api_search(channel, query, after_iso, before_iso):
    """Recherche dans une chaîne par l'API YouTube (clé [youtube] api_key), limitée aux dates du GP : trouve les vidéos
    anciennes que la liste des dernières mises en ligne n'atteint plus (Canal+ publie des dizaines de vidéos par jour).
    100 unités de quota par recherche : appelée seulement pour un GP affiché dont la vidéo n'est pas encore connue."""
    key = str(_secret("youtube", "api_key", ""))
    if not key:
        return []
    params = {"part": "snippet", "channelId": channel, "q": query, "type": "video", "maxResults": 25, "order": "relevance",
              "publishedAfter": after_iso, "publishedBefore": before_iso, "key": key}
    try:
        r = SESSION.get("https://www.googleapis.com/youtube/v3/search", params=params, timeout=12)
    except Exception:
        return []
    if r.status_code != 200:
        log.warning("Recherche API YouTube : HTTP %s", r.status_code)
        return []
    out = []
    for item in r.json().get("items") or []:
        vid = (item.get("id") or {}).get("videoId")
        sn = item.get("snippet") or {}
        if vid:
            out.append({"id": vid, "title": html.unescape(sn.get("title", "")), "channel": channel, "mins": None,
                        "published": sn.get("publishedAt", "")})
    return out


_VID_LOCK = threading.Lock()


@st.cache_resource(show_spinner=False)
def found_videos():
    return disk_get("videos.json") or {}


def remember(key, v):
    store = found_videos()
    with _VID_LOCK:
        store[key] = {"id": v["id"], "mins": v.get("mins"), "title": v.get("title", "")}
        disk_put("videos.json", dict(store))


def _gp_words(ev):
    return GP.get(ev["code"], (None, [norm(ev["place"])]))[1]


def fr_place(ev):
    """« qatar », « malaisie », « etats-unis » (pour reconnaître les titres de Canal+)."""
    name = norm(ev["name"])
    return re.sub(r"^gp (du |de la |de l'|de |d'|des )?", "", name).strip()


def gp_full(ev):
    return ev["name"].replace("GP ", "Grand Prix ", 1)


GP_FR_ALIAS = {"USA": ("ameriques", "etats-unis", "austin"), "CAT": ("catalogne", "barcelone"), "RSM": ("saint-marin", "misano"),
               "NED": ("pays-bas", "assen"), "GBR": ("grande-bretagne", "silverstone"), "GER": ("allemagne", "sachsenring"),
               "SPA": ("espagne", "jerez"), "ITA": ("italie", "mugello"), "AUT": ("autriche",), "JPN": ("japon", "motegi"),
               "INA": ("indonesie", "mandalika"), "AUS": ("australie", "phillip island"), "MAL": ("malaisie", "sepang"),
               "THA": ("thailande", "buriram"), "BRA": ("bresil", "goiania"), "CZE": ("tchequie", "republique tcheque", "brno"),
               "HUN": ("hongrie", "balaton"), "ARA": ("aragon",), "POR": ("portugal", "portimao"), "VAL": ("valence", "valencia"),
               "FRA": ("france", "le mans"), "QAT": ("qatar", "lusail")}


def _canal_match(v, ev, kind, cat):
    t = norm(v["title"])
    if "resume" not in t or str(ev["start"].year) not in t:
        return False
    places = {fr_place(ev)} | set(_gp_words(ev)) | set(GP_FR_ALIAS.get(ev["code"], ()))
    if not any(p and p in t for p in places):
        return False
    if any(w in t for w in ("qualif", "essais", "warm", "practice")):
        return False
    vcat = "moto2" if "moto2" in t or "moto 2" in t else "moto3" if "moto3" in t or "moto 3" in t else "motogp"
    if vcat != norm(cat):
        return False
    return ("sprint" in t) == (kind == "sprint")


def _official_match(v, ev, kind):
    t = norm(v["title"])
    if v.get("channel") not in ("", MOTOGP_CHANNEL) or str(ev["start"].year) not in t:
        return False
    if not any(w in t for w in _gp_words(ev)):
        return False
    if kind == "race":
        return "motogp highlights" in t or ("race" in t and "highlights" in t and "sprint" not in t)
    if kind == "sprint":
        return "sprint" in t and ("highlights" in t or "moments" in t)
    return False


def canal_query(ev, kind, cat):
    what = "course sprint du " if kind == "sprint" else ""
    return f"résumé de la {what}{gp_full(ev)} {ev['start'].year} {cat}".replace("de la Grand", "du Grand")


def canal_search_url(ev, kind, cat):
    """Recherche dans la chaîne Canal+ Sport, ouverte dans le navigateur du visiteur (en France, la vidéo sort en premier)."""
    return f"https://www.youtube.com/@{CANAL_HANDLE}/search?query=" + quote(canal_query(ev, kind, cat))


def official_search_url(ev, kind):
    what = "Tissot Sprint" if kind == "sprint" else "MotoGP HIGHLIGHTS"
    return "https://www.youtube.com/results?search_query=" + quote(f"{what} {ev['start'].year} {_gp_words(ev)[0]} GP")


def find_video(ev, source, kind, cat="MotoGP"):
    """Vidéo d'un GP. Sources, dans l'ordre : mémoire, API YouTube (clé), flux public, recherche dans la chaîne,
    recherche YouTube. Titres jamais affichés : ils spoilent."""
    key = f"{ev['start'].year}{ev['code']}_{source}_{kind}_{cat}"
    store = found_videos()
    if store.get(key):
        return store[key]
    recent = (now_utc().date() - ev["end"]).days <= 12  # la recherche sans clé n'est tentée que pour les GP récents
    channel = CANAL_CHANNEL if source == "canal" else MOTOGP_CHANNEL
    ok = (lambda v: _canal_match(v, ev, kind, cat)) if source == "canal" else (lambda v: _official_match(v, ev, kind))
    start = datetime.combine(ev["start"], datetime.min.time()).replace(tzinfo=timezone.utc) - timedelta(days=1)
    end = datetime.combine(ev["end"], datetime.min.time()).replace(tzinfo=timezone.utc) + timedelta(days=12)
    iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
    pools = []
    if recent:  # GP récent : les dernières mises en ligne suffisent (1 unité de quota par page)
        pools.append(lambda: channel_uploads(channel, iso(start), bucket(1800)))
    if source == "canal":  # sinon (et en secours) : recherche par l'API dans la chaîne, aux dates du GP
        pools.append(lambda: api_search(channel, canal_query(ev, kind, cat), iso(start), iso(end)))
        pools.append(lambda: api_search(channel, f"résumé {gp_full(ev)} {cat}", iso(start), iso(end)))
    pools.append(lambda: channel_feed(channel, bucket(900)))
    if recent:
        if source == "canal":
            pools.append(lambda: channel_search(CANAL_CHANNEL, canal_query(ev, kind, cat)))
            pools.append(lambda: [v for v in yt_search("CANAL+ Sport " + canal_query(ev, kind, cat), "fr") if v["channel"] == CANAL_CHANNEL])
        else:
            q = ("Tissot Sprint" if kind == "sprint" else "MotoGP HIGHLIGHTS") + f" {ev['start'].year} {_gp_words(ev)[0].title()} GP"
            pools.append(lambda: yt_search(q))
    for pool in pools:
        cand = [v for v in pool() if ok(v)]
        if cand:
            remember(key, cand[0])
            return cand[0]
    return None


def yt_url(v):
    return f"https://www.youtube.com/watch?v={v['id']}"


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


def head(info, size=40, cls="hd"):
    """Tête du pilote (photo détourée) dans un rond à la couleur de l'équipe."""
    c = esc(info.get("color") or "#333")
    if info.get("photo"):
        return (f"<span class='{cls}' style='--c:{c};width:{size}px;height:{size}px'><img src='{esc(info['photo'])}' "
                f"loading='lazy' alt=''></span>")
    return (f"<span class='{cls} hdn' style='--c:{c};width:{size}px;height:{size}px;font-size:{size * 0.42:.0f}px'>"
            f"{esc(info.get('num') or '')}</span>")


def short_name(info, row=None):
    """« M. Marquez » (Marc et Alex Márquez sont tous les deux en MotoGP)."""
    first = info.get("first") or ""
    last = info.get("last") or (row or {}).get("name", "").split(" ")[-1]
    if not first and row and " " in row.get("name", ""):
        first = row["name"].split(" ")[0]
    return f"{first[:1]}. {last}" if first else last


def num_badge(info):
    if not info.get("num"):
        return ""
    return (f"<span class='nb' style='background:{esc(info.get('color', '#333'))};color:{esc(info.get('text', '#fff'))}'>"
            f"{esc(info['num'])}</span>")


def rider_cell(r, with_team=True, with_link=True, size=34):
    info = rider_of(r["rid"], r)
    name = esc(info.get("name") or r["name"])
    nm = rider_link(r["rid"], name) if with_link else name
    team = f"<span class='tm'>{esc(r.get('team') or info.get('team'))}</span>" if with_team else ""
    return (f"<span class='rc'>{head(info, size)}<span class='rn'><b>{flag(r.get('iso') or info.get('iso'))} {nm}</b>{team}</span>"
            f"{num_badge(info)}</span>")


def status_fr(r):
    st_ = r.get("status", "")
    if st_ == "NOTSTARTED":
        return "Non partant"
    if st_ in ("OUTSTND", "NOTFINISHFIRST") or not r["pos"]:
        return "Abandon"
    if st_ == "NOTCLASSIFIED":
        return "Non classé"
    return "—"


def gap_txt(r):
    if not r["pos"]:
        lap = f" (tour {r['laps']})" if r.get("laps") else ""
        return status_fr(r) + lap
    if r["pos"] == 1:
        return esc(r["time"] or r.get("best") or "")
    if r.get("laps_down"):
        return f"+{r['laps_down']} tour" + ("s" if r["laps_down"] > 1 else "")
    if r["gap"] is not None:
        return "+" + fr_num(r["gap"], 3)
    return esc(r.get("best") or r.get("time") or "")


def results_table(rows, code, fav=None):
    race = code in ("SPR", "RAC")
    head_ = ("<tr><th>Pos.</th><th class='l'>Pilote</th><th class='r'>" + ("Écart" if race else "Meilleur tour") + "</th>"
             + ("<th class='r'>Pts</th>" if race else "<th class='r'>Écart</th>") + "</tr>")
    body = []
    for r in rows:
        cls = []
        if r["pos"] and r["pos"] <= 3:
            cls.append(f"p{r['pos']}")
        if fav and r["rid"] == fav:
            cls.append("fav")
        if not r["pos"]:
            cls.append("out")
        stripe = color_of(r["rid"], r)
        if race:
            right = f"<td class='r g'>{gap_txt(r)}</td><td class='r pts'>{r['points'] or ''}</td>"
        else:
            g = "" if r["pos"] == 1 or r["gap"] is None else "+" + fr_num(r["gap"], 3)
            right = f"<td class='r g'>{esc(r['best']) if r['pos'] else status_fr(r)}</td><td class='r g'>{g}</td>"
        body.append(f"<tr class='{' '.join(cls)}'><td class='ps'><span style='--c:{esc(stripe)}'>{r['pos'] or '–'}</span></td>"
                    f"<td class='l'>{rider_cell(r)}</td>{right}</tr>")
    return f"<table class='res'>{head_}{''.join(body)}</table>"


def podium_html(rows):
    top = sorted([r for r in rows if r["pos"] in (1, 2, 3)], key=lambda r: r["pos"])
    if len(top) < 3:
        return ""
    out = []
    for r in (top[1], top[0], top[2]):
        info = rider_of(r["rid"], r)
        g = "" if r["pos"] == 1 else ("+" + fr_num(r["gap"], 3) + " s" if r["gap"] is not None else "")
        img = f"<img src='{esc(info['photo'])}' loading='lazy' alt=''>" if info.get("photo") else f"<i>{esc(info.get('num') or '')}</i>"
        out.append(f"<div class='pd pd{r['pos']}' style='--c:{esc(color_of(r['rid'], r))}'><div class='pdi'>{img}</div>"
                   f"<div class='pdx'><span class='pdp'>{r['pos']}</span><div class='pdn'>{flag(r['iso'])} "
                   f"{rider_link(r['rid'], esc(short_name(info, r)))}</div><div class='pdt'>{esc(r['team'])}</div>"
                   f"<div class='pdg'>{g}</div></div></div>")
    return f"<div class='podium'>{''.join(out)}</div>"


_UID = [0]


def uid(prefix="t"):
    _UID[0] += 1
    return f"{prefix}{_UID[0]}"


def hidden(label, content, cls="tg", seen=""):
    """Bloc caché par une case à cocher CSS (jamais `checked` dans le HTML : React la verrouillerait).
    `seen` : clés « 26QATr » notées comme vues par le navigateur quand on dévoile (pour le classement sans spoil)."""
    i = uid("h")
    k = f" data-k='{esc(seen)}'" if seen else ""
    return (f"<div class='hb'{k}><input type='checkbox' id='{i}' class='{cls}'>"
            f"<label for='{i}' class='hbl'><span>👁</span>{label}</label>"
            f"<div class='hbx'>{content}</div></div>")


def hm(dt, zone):
    d = dt.astimezone(zone)
    return f"{d.hour}h{d.minute:02d}"


def when_txt(dt, zone, today=None):
    d = dt.astimezone(zone)
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


def countdown(dt):
    secs = int((dt - now_utc()).total_seconds())
    if secs <= 0:
        return "maintenant"
    d, h, m = secs // 86400, secs % 86400 // 3600, secs % 3600 // 60
    if d >= 2:
        return f"dans {d} jours"
    if d == 1:
        return f"dans 1 j {h} h"
    return f"dans {h} h {m:02d}" if h else f"dans {m} min"


def sec_title(title, sub="", right=""):
    s = f"<small>{sub}</small>" if sub else ""
    r = f"<span class='sr'>{right}</span>" if right else ""
    return f"<div class='sec'><h2>{esc(title)}</h2>{s}{r}</div>"


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
if (P.__gpChange) D.removeEventListener('change', P.__gpChange, true);
P.__gpChange = e => {{  // résultat dévoilé : la séance est notée « vue » (classement sans spoil à jour à la prochaine visite)
  const i = e.target, box = i.checked && i.closest && i.closest('[data-k]');
  if (!box) return;
  const ck = n => (D.cookie.split('; ').find(c => c.startsWith(n + '=')) || '').slice(n.length + 1);
  let cur = ck('gp_vues'); try {{ cur = cur || P.localStorage.getItem('gp_vues') || ''; }} catch (x) {{}}
  const all = new Set(cur.split('.').filter(Boolean));
  box.dataset.k.split(' ').forEach(k => k && all.add(k));
  const v = [...all].slice(-120).join('.');
  D.cookie = 'gp_vues=' + v + '; path=/; max-age=31536000; SameSite=Lax';
  try {{ P.localStorage.setItem('gp_vues', v); }} catch (x) {{}}
}};
D.addEventListener('change', P.__gpChange, true);
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
    if q.get("opt"):
        ss.opts = opts_from(q["opt"], ss.get("tzname", ""))
    if q.get("vues"):
        ss.seen = ss.seen | read_seen(q["vues"])
    if q.get("hello"):
        ss.hello_done = True


VIEWS = ("brief", "gp", "calendrier", "classement", "pilotes", "actus")
TABS = [("gp", "Grand Prix", ":material/sports_motorsports:"),
        ("calendrier", "Calendrier", ":material/calendar_month:"), ("classement", "Classement", ":material/emoji_events:"),
        ("pilotes", "Pilotes", ":material/groups:"), ("actus", "Actus", ":material/newspaper:")]


def apply_go(cmd):
    q = dict(parse_qsl(cmd, keep_blank_values=True))
    ss = st.session_state
    if q.get("restore"):
        apply_restore(q)
        return
    if q.get("gp"):
        ss.gp = q["gp"]
    if "pilote" in q:
        ss.rider = q["pilote"] or None
    if q.get("cat") in CATS:
        ss.cat = q["cat"]
    if q.get("vue") in VIEWS:
        ss.view = "gp" if q["vue"] == "brief" else q["vue"]


# =========================================================================
# STYLE : carbone, rouge MotoGP, italiques serrées, vibreurs
# =========================================================================
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Saira+Condensed:wght@500;600;700;800;900&family=Saira:wght@400;500;600;700&display=swap');
:root {--bg:#06070a; --bg2:#0c0e13; --card:rgba(255,255,255,.035); --card2:rgba(255,255,255,.06); --line:rgba(255,255,255,.09);
       --tx:#f3f5f9; --mu:#8d95a5; --red:#e10600; --red2:#ff3b30; --gold:#f3c34e; --silver:#d9dee6; --bronze:#d08a52;
       --ok:#35d07f; --D:'Saira Condensed', sans-serif; --B:'Saira', sans-serif;}
html, body, [data-testid="stAppViewContainer"], .stApp {background:
  radial-gradient(1200px 500px at 85% -10%, rgba(225,6,0,.16), transparent 60%),
  radial-gradient(900px 400px at -10% 30%, rgba(225,6,0,.06), transparent 60%),
  repeating-linear-gradient(45deg, rgba(255,255,255,.012) 0 2px, transparent 2px 6px),
  repeating-linear-gradient(-45deg, rgba(255,255,255,.012) 0 2px, transparent 2px 6px), var(--bg) !important;
  background-attachment:fixed !important;}
.stApp, .stMarkdown, .stMarkdown p, .stMarkdown div {font-family:var(--B);}
.block-container {max-width:1280px; padding-top:1rem !important; padding-bottom:2rem !important;}
header[data-testid="stHeader"] {height:0; background:transparent;}
[data-testid="stToolbar"], [data-testid="stToolbarActions"], [data-testid="stAppDeployButton"], .stAppDeployButton,
[data-testid="stDecoration"], [data-testid="stStatusWidget"], [class*="viewerBadge"], [data-testid="manage-app-button"],
#MainMenu, footer {display:none !important; visibility:hidden !important;}
div[data-testid="stElementContainer"]:has(> iframe[data-testid="stIFrame"]), div:has(> .st-key-gobox) {
  position:absolute !important; width:1px; height:1px; overflow:hidden; left:-9999px;}
.st-key-gobox {position:absolute !important; left:-9999px; top:0; width:1px; height:1px; overflow:hidden;}
.stMarkdown a {text-decoration:none !important;}
.stMarkdown a.rl, .stMarkdown a.cc, .stMarkdown a.rt, .stMarkdown a.nw, .stMarkdown a.rp, .stMarkdown a.nx {color:inherit !important;}
.stMarkdown a.lk {color:var(--red2) !important; font-weight:600;}
.stMarkdown a.rl:hover, .stMarkdown a.lk:hover {text-decoration:underline !important;}
/* ---------- en-tête du site ---------- */
.st-key-hdr [data-testid="stHorizontalBlock"] {flex-wrap:nowrap !important; align-items:center;}
.st-key-hdr [data-testid="stColumn"]:last-child {flex:0 0 58px !important; min-width:58px !important;}
.st-key-hdr [data-testid="stColumn"]:first-child {flex:1 1 auto !important; min-width:0 !important; width:auto !important;}
.brand {display:flex; align-items:center; gap:10px; white-space:nowrap;}
.brand .lg {font:900 italic 30px/1 var(--D); letter-spacing:.5px; color:#fff; text-transform:uppercase;}
.brand .lg i {color:var(--red); font-style:italic;}
.brand .sl {width:46px; height:22px; transform:skewX(-20deg);
  background:repeating-linear-gradient(90deg, var(--red) 0 6px, transparent 6px 10px);}
.brand small {color:var(--mu); font:600 12px var(--B); letter-spacing:3px; text-transform:uppercase; margin-left:4px;}
.gpbar {display:flex; justify-content:space-between; align-items:center; gap:10px; margin:2px 0 4px;}
.stMarkdown a.gpn {display:inline-flex; align-items:center; gap:8px; color:var(--mu) !important; font:800 15px var(--D);
  text-transform:uppercase; letter-spacing:.8px; white-space:nowrap; min-width:0; padding:6px 4px; transition:color .15s;}
.stMarkdown a.gpn em {font-style:italic; overflow:hidden; text-overflow:ellipsis;}
.stMarkdown a.gpn span {font-size:22px; line-height:1; color:var(--red);}
.stMarkdown a.gpn:hover {color:#fff !important; text-decoration:none !important;}
.stMarkdown a.gpn.c {color:#fff !important; border:1px solid var(--line); border-radius:6px; padding:5px 14px; transform:skewX(-12deg);}
.st-key-gpnav [data-testid="stHorizontalBlock"] {flex-wrap:nowrap !important; gap:8px;}
.st-key-gpnav [data-testid="stColumn"]:first-child, .st-key-gpnav [data-testid="stColumn"]:last-child {flex:0 0 52px !important;
  min-width:52px !important; width:52px !important;}
.st-key-gpnav [data-testid="stColumn"]:nth-child(2) {flex:1 1 auto !important; min-width:0 !important; width:auto !important;}
.st-key-tabs {position:sticky; top:0; z-index:50; padding:6px 0; margin-bottom:6px;
  background:linear-gradient(180deg, rgba(6,7,10,.97), rgba(6,7,10,.9)); backdrop-filter:blur(8px); border-bottom:1px solid var(--line);}
.st-key-tabs button {white-space:nowrap; font-family:var(--D) !important; letter-spacing:.5px; border-radius:6px !important;
  transform:skewX(-10deg);}
.st-key-tabs button p {font:700 16px var(--D) !important; text-transform:uppercase; letter-spacing:.8px;}
.st-key-tabs [data-testid="stPopover"] button {background:transparent !important; border:none !important; box-shadow:none !important;
  color:var(--mu) !important; transform:none;}
.st-key-tabs [data-testid="stPopover"] button:hover {color:#fff !important;}
.st-key-tabs [data-testid="stPopover"] button [data-testid="stIconMaterial"] {font-size:22px;}
.st-key-tabs [data-testid="stPopover"] button div[aria-hidden="true"] {display:none !important;}
.st-key-tabs button[kind="primary"], .st-key-tabs button[data-testid="stBaseButton-primary"] {background:var(--red) !important;
  border-color:var(--red) !important; box-shadow:0 6px 24px rgba(225,6,0,.35);}
/* ---------- titres de section ---------- */
.sec {display:flex; align-items:baseline; gap:12px; margin:26px 0 12px; position:relative; padding-left:20px; flex-wrap:wrap;}
.sec:before {content:''; position:absolute; left:0; top:4px; bottom:4px; width:8px; background:var(--red); transform:skewX(-20deg);}
.sec h2 {font:900 italic 28px/1 var(--D) !important; text-transform:uppercase; letter-spacing:.5px; margin:0 !important;
  padding:0 !important; color:#fff !important;}
.sec small {color:var(--mu); font-size:13px;}
.sec .sr {margin-left:auto; font-size:13px;}
.note {color:var(--mu); font-size:13px;}
[data-testid="stButtonGroup"] [role="radiogroup"] {display:inline-flex; gap:2px; padding:3px; border-radius:8px; flex-wrap:nowrap;
  background:rgba(255,255,255,.03); border:1px solid var(--line);}
[data-testid="stButtonGroup"] button[data-variant="segmented_control"] {background:transparent !important; border:none !important;
  border-radius:5px !important; padding:5px 16px !important; min-height:0 !important; transform:skewX(-12deg); box-shadow:none !important;
  margin:0 !important; transition:background .15s;}
[data-testid="stButtonGroup"] button[data-variant="segmented_control"] p {font:800 15px var(--D) !important; text-transform:uppercase;
  letter-spacing:.8px; color:var(--mu); transform:skewX(12deg); white-space:nowrap;}
[data-testid="stButtonGroup"] button[data-variant="segmented_control"]:hover p {color:#fff;}
[data-testid="stButtonGroup"] button[aria-checked="true"] {background:var(--red) !important; box-shadow:0 4px 18px rgba(225,6,0,.35) !important;}
[data-testid="stButtonGroup"] button[aria-checked="true"] p {color:#fff !important;}
.pill {display:inline-flex; align-items:center; gap:5px; padding:3px 10px; border-radius:4px; font:700 12px var(--B);
  white-space:nowrap; text-transform:uppercase; letter-spacing:.8px; background:var(--card2); color:var(--silver);}
.pill.live {background:var(--red); color:#fff; animation:pulse 1.6s infinite;}
.pill.pre {background:rgba(243,195,78,.14); color:var(--gold);}
.pill.post {background:rgba(255,255,255,.08);}
@keyframes pulse {0%,100% {box-shadow:0 0 0 0 rgba(225,6,0,.6);} 50% {box-shadow:0 0 0 6px rgba(225,6,0,0);}}
.kerb {height:6px; background:repeating-linear-gradient(90deg, var(--red) 0 22px, #fff 22px 44px);}
/* ---------- grand en-tête du GP ---------- */
.hero {position:relative; border-radius:18px; overflow:hidden; margin:8px 0 6px; background:#000; isolation:isolate;
  border:1px solid var(--line); box-shadow:0 30px 80px rgba(0,0,0,.5);}
.hero-bg {position:absolute; inset:0; background:var(--img) center/cover no-repeat; z-index:-2; transform:scale(1.03);
  filter:saturate(1.1) contrast(1.05);}
.hero:before {content:''; position:absolute; inset:0; z-index:-1;
  background:linear-gradient(90deg, rgba(4,5,8,.97) 0%, rgba(4,5,8,.85) 38%, rgba(4,5,8,.35) 70%, rgba(4,5,8,.6) 100%),
             linear-gradient(0deg, rgba(4,5,8,.95) 0%, transparent 45%);}
.hero-in {display:flex; justify-content:space-between; align-items:center; gap:20px; padding:28px 32px 18px; min-height:330px;}
.hero-l {min-width:0; flex:1 1 auto; display:flex; flex-direction:column; gap:8px;}
.ttl {display:flex; align-items:center; gap:26px; min-width:0;}
.ttl .trk {height:230px; width:auto; max-width:300px; margin:-55px -40px -55px -30px; object-fit:contain; flex:none; opacity:.95;
  filter:drop-shadow(0 2px 10px rgba(0,0,0,.6));}
.rnd {display:inline-flex; align-items:center; gap:8px; align-self:flex-start; background:var(--red); color:#fff;
  padding:4px 14px 4px 12px; transform:skewX(-14deg); font:800 13px var(--D); letter-spacing:2px; text-transform:uppercase;}
.rnd b {font:900 italic 20px/1 var(--D);} .rnd i {opacity:.7; font-style:normal;}
.ctry {display:flex; align-items:center; gap:10px; color:#dfe3ea; font:600 14px var(--B); letter-spacing:2px; text-transform:uppercase;}
.ctry img {height:20px; border-radius:2px; box-shadow:0 2px 8px rgba(0,0,0,.6);}
.hero h1 {font:900 italic 64px/.88 var(--D) !important; text-transform:uppercase; color:#fff !important; margin:4px 0 0 !important;
  padding:0 !important; letter-spacing:-.5px; text-shadow:0 6px 30px rgba(0,0,0,.6);}
.hero h1 em {display:block; font-style:italic; color:transparent; -webkit-text-stroke:1.5px #fff; letter-spacing:0;}
.hsub {color:#c9ced8; font-size:15px;}
.hstate {display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-top:4px;}
.hcd {font:800 italic 20px var(--D); color:#fff; text-transform:uppercase; letter-spacing:.5px;}
.hcd span {color:var(--red2);}
.hero-r {flex:0 0 44%; min-height:300px; align-self:stretch; display:flex; align-items:center; justify-content:center; position:relative; margin:-18px 0;}
.hero-r img {position:absolute; inset:0; margin:auto; width:100%; height:100%; max-width:520px; object-fit:contain;
  filter:drop-shadow(0 0 3px #fff) drop-shadow(0 0 16px rgba(225,6,0,.9)) drop-shadow(0 0 40px rgba(225,6,0,.45));}
.hstats {display:grid; grid-template-columns:repeat(5, 1fr); border-top:1px solid rgba(255,255,255,.1);
  background:rgba(0,0,0,.55); backdrop-filter:blur(6px);}
.hstats div {padding:12px 16px; border-right:1px solid rgba(255,255,255,.08); min-width:0;}
.hstats div:last-child {border-right:none;}
.hstats b {display:block; font:800 italic 26px/1 var(--D); color:#fff; white-space:nowrap;}
.hstats b small {font-size:15px; color:var(--mu); font-style:normal;}
.hstats span {color:var(--mu); font-size:11px; text-transform:uppercase; letter-spacing:1.4px; white-space:nowrap;}
/* ---------- bannière du GP : photo + fiche circuit façon plan ---------- */
.hero2 {position:relative; display:grid; grid-template-columns:minmax(0, 1.12fr) minmax(0, 1fr); height:340px; margin:6px 0 8px;
  border-radius:18px; overflow:hidden; border:1px solid var(--line); background:#05070b; box-shadow:0 30px 80px rgba(0,0,0,.55);}
.hero2.solo {grid-template-columns:1fr;}
.hero2 .ph {position:relative; background:var(--img) center/cover no-repeat; min-width:0; isolation:isolate;}
.hero2 .ph:before {content:''; position:absolute; inset:0; z-index:-1;
  background:linear-gradient(0deg, rgba(3,4,7,.92) 0%, rgba(3,4,7,.45) 45%, rgba(3,4,7,.05) 80%),
             linear-gradient(90deg, rgba(3,4,7,.55) 0%, transparent 60%);}
.ph-in {position:absolute; left:28px; right:110px; bottom:20px; top:20px; display:flex; flex-direction:column; justify-content:space-between;}
.ph-top {display:flex; flex-direction:column; gap:8px; align-items:flex-start;}
.ph-bot {display:flex; flex-direction:column; gap:4px; min-width:0;}
.hero2 h1 {font:900 italic 54px/.9 var(--D) !important; text-transform:uppercase; color:#fff !important; margin:2px 0 4px !important;
  padding:0 !important; letter-spacing:-.5px; text-shadow:0 6px 30px rgba(0,0,0,.7); white-space:nowrap;}
.hero2 h1 em {display:block; font-style:italic; color:transparent; -webkit-text-stroke:1.5px #fff;}
.hero2 .hsub {display:flex; align-items:center; gap:10px; color:#e3e7ee; font:600 15px var(--B); white-space:nowrap;}
.hcd {margin-top:6px; display:flex; flex-direction:column; line-height:1;}
.hcd small {color:#c9ced8; font:700 11px var(--B); letter-spacing:2px; text-transform:uppercase;}
.hcd b {font:900 italic 34px/1.05 var(--D); color:#fff; text-transform:uppercase; white-space:nowrap;}
.hcd b span {color:var(--red2);}
.bp {position:relative; overflow:hidden; min-width:0; margin-left:-140px; padding-left:140px;
  -webkit-mask-image:linear-gradient(90deg, transparent 0, #000 140px); mask-image:linear-gradient(90deg, transparent 0, #000 140px);
  background:radial-gradient(110% 90% at 50% 50%, #20242c 0%, #14171d 55%, #0b0d11 100%);}
.bp-grid {position:absolute; inset:0;
  background:linear-gradient(rgba(255,255,255,.035) 1px, transparent 1px) 0 0/20px 20px,
             linear-gradient(90deg, rgba(255,255,255,.035) 1px, transparent 1px) 0 0/20px 20px,
             linear-gradient(rgba(255,255,255,.07) 1px, transparent 1px) 0 0/100px 100px,
             linear-gradient(90deg, rgba(255,255,255,.07) 1px, transparent 1px) 0 0/100px 100px;}
.bp-tb {position:absolute; z-index:1;}
.bp-tb.tall {top:50px; bottom:38px; left:345px; right:205px;}
.bp-tb.wide {top:118px; bottom:112px; left:165px; right:25px;}
.bp-t {position:absolute; left:0; top:0; width:100%; height:100%; object-fit:contain;
  filter:drop-shadow(0 4px 14px rgba(0,0,0,.6));}
.bp-h {position:absolute; top:14px; left:140px; right:0; text-align:center; z-index:2; white-space:nowrap; overflow:hidden;
  text-overflow:ellipsis;}
.bp-h span {display:block; color:var(--red2); font:700 10px var(--B); letter-spacing:3px; text-transform:uppercase;}
.bp-h b {color:#e6e9ee; font:700 13px var(--B); letter-spacing:1px; text-transform:uppercase;}
.bp-s {position:absolute; z-index:2; padding:6px 10px; min-width:0; white-space:nowrap;}
.bp-s:before {content:''; position:absolute; width:14px; height:14px; border-color:rgba(255,255,255,.45); border-style:solid;}
.bp-s span {display:block; color:#8d95a5; font:700 10px var(--B); letter-spacing:2px; text-transform:uppercase;}
.bp-s b {font:900 italic 30px/1 var(--D); color:#fff;}
.bp-s b small {font:600 13px var(--B); color:#9aa1ad; font-style:normal; margin-left:2px;}
.bp-s.tl {top:54px; left:158px;} .bp-s.tl:before {left:0; top:0; border-width:2px 0 0 2px;}
.bp-s.tr {top:54px; right:18px; text-align:right;} .bp-s.tr:before {right:0; top:0; border-width:2px 2px 0 0;}
.bp-s.bl {bottom:40px; left:158px;} .bp-s.bl:before {left:0; bottom:0; border-width:0 0 2px 2px;}
.bp-s.br {bottom:40px; right:18px; text-align:right;} .bp-s.br:before {right:0; bottom:0; border-width:0 2px 2px 0;}
.bp-f {position:absolute; bottom:12px; left:140px; right:0; text-align:center; z-index:2; color:#9aa1ad; font:500 12px var(--B);
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.bp-f b {color:#fff; font-weight:700;}
/* ---------- barre d'outils (tout dévoiler) ---------- */
.st-key-tools {margin:6px 0 0;}
.st-key-tools label p {font-weight:600;}
/* ---------- replays ---------- */
.rps {display:grid; grid-template-columns:repeat(auto-fill, minmax(250px, 1fr)); gap:12px;}
.rp {position:relative; display:flex; flex-direction:column; justify-content:flex-end; min-height:170px; border-radius:14px;
  overflow:hidden; padding:14px; isolation:isolate; border:1px solid var(--line); transition:transform .15s, box-shadow .15s;}
.rp:before {content:''; position:absolute; inset:0; z-index:-2; background:var(--img) center/cover; filter:saturate(.9) brightness(.55);
  transition:transform .4s;}
.rp:after {content:''; position:absolute; inset:0; z-index:-1; background:linear-gradient(0deg, rgba(0,0,0,.9) 10%, rgba(0,0,0,.15) 70%);}
.rp:has(.rpa):hover {transform:translateY(-3px); box-shadow:0 14px 40px rgba(225,6,0,.25);}
.rp:has(.rpa):hover:before {transform:scale(1.06);}
.rp .rpa {position:absolute; inset:0; z-index:1;}
.rp .alt {position:relative; z-index:2;}
.rp .src, .rp .dur, .rp .play {pointer-events:none;}
.rp .src {position:absolute; top:12px; left:12px; padding:3px 9px; font:800 12px var(--D); letter-spacing:1px; border-radius:4px;
  text-transform:uppercase;}
.rp.canal .src {background:#000; color:#fff; border:1px solid rgba(255,255,255,.35);}
.rp.off_ .src, .rp.motogp .src {background:var(--red); color:#fff;}
.rp .play {position:absolute; top:34%; left:50%; transform:translate(-50%, -50%); width:54px; height:54px; border-radius:50%;
  background:rgba(225,6,0,.92); display:flex; align-items:center; justify-content:center; box-shadow:0 8px 30px rgba(225,6,0,.5);}
.rp .play:after {content:''; margin-left:5px; border-left:18px solid #fff; border-top:11px solid transparent; border-bottom:11px solid transparent;}
.rp.find .play {background:rgba(255,255,255,.14); box-shadow:none; border:1px solid rgba(255,255,255,.4);}
.rp.off {opacity:.55;} .rp.off .play {display:none;}
.rp b {font:800 italic 22px/1.05 var(--D); color:#fff; text-transform:uppercase;}
.rp small {display:block; color:var(--red2); font:700 12px var(--B); letter-spacing:1.5px; text-transform:uppercase;}
.rp .dur {position:absolute; top:12px; right:12px; background:rgba(0,0,0,.7); color:#fff; font:600 12px var(--B); padding:2px 7px;
  border-radius:4px;}
.rp .alt {color:#c9ced8; font-size:12px; margin-top:4px;}
.rp .alt a {color:#fff !important; text-decoration:underline !important;}
/* ---------- le week-end (frise par jour) ---------- */
.tl {display:grid; grid-template-columns:repeat(3, 1fr); gap:14px;}
.tl .day {min-width:0; display:flex; flex-direction:column; gap:10px;}
.tl .dh {font:900 italic 22px var(--D); text-transform:uppercase; color:#fff; display:flex; align-items:baseline; gap:8px;
  border-bottom:2px solid var(--line); padding-bottom:6px;}
.tl .dh span {color:var(--mu); font:600 13px var(--B); text-transform:none; font-style:normal;}
.tl .dh.today {border-color:var(--red);}
.ses {position:relative; background:var(--card); border:1px solid var(--line); border-radius:12px; padding:12px 14px 12px 16px;
  min-width:0; overflow:hidden;}
.ses:before {content:''; position:absolute; left:0; top:0; bottom:0; width:4px; background:rgba(255,255,255,.12);}
.ses.race {background:linear-gradient(135deg, rgba(225,6,0,.16), var(--card) 55%); border-color:rgba(225,6,0,.35);}
.ses.race:before {background:repeating-linear-gradient(180deg, var(--red) 0 10px, #fff 10px 20px);}
.ses.q:before {background:var(--gold);}
.ses.live {border-color:var(--red); box-shadow:0 0 0 1px var(--red) inset, 0 0 30px rgba(225,6,0,.25);}
.ses .top {display:flex; align-items:center; gap:10px; min-width:0;}
.ses .t {font:800 italic 26px/1 var(--D); color:#fff; width:64px; flex:none; white-space:nowrap;}
.ses .nm {font:800 19px/1.1 var(--D); text-transform:uppercase; letter-spacing:.4px; white-space:nowrap; overflow:hidden;
  text-overflow:ellipsis; min-width:0;}
.ses .nm small {display:block; font:500 12px var(--B); color:var(--mu); text-transform:none; letter-spacing:0;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.ses .st {margin-left:auto; flex:none;}
.ses .wt {font-size:13px; margin-top:8px; white-space:nowrap;}
.ses .body {margin-top:8px;}
.ses .done {color:var(--ok); font-weight:700; font-size:18px;}
.rpbs {display:flex; gap:6px; margin-top:10px; flex-wrap:wrap;}
.rpb {display:inline-flex; align-items:center; gap:5px; padding:7px 12px; border-radius:6px; font:700 13px var(--B); white-space:nowrap;
  color:#fff !important; transition:transform .12s, box-shadow .12s;}
.rpb:hover {transform:translateY(-1px);}
.rpb.canal {background:#000; border:1px solid rgba(255,255,255,.55); box-shadow:0 4px 16px rgba(0,0,0,.4); padding:8px 14px 8px 10px;}
.rpb.canal:hover {border-color:#fff; box-shadow:0 6px 22px rgba(225,6,0,.35);}
.rpb .pl {width:22px; height:22px; border-radius:50%; background:var(--red); display:inline-flex; flex:none; position:relative;}
.rpb .pl:after {content:''; position:absolute; left:8px; top:6px; border-left:8px solid #fff; border-top:5px solid transparent;
  border-bottom:5px solid transparent;}
.rpb.canal b {font-weight:900; letter-spacing:.5px;}
.rpb.off {background:var(--red); box-shadow:0 4px 16px rgba(225,6,0,.35);}
.rpb small {opacity:.75; font-weight:500;}
/* ---------- blocs cachés ---------- */
.hb {position:relative;}
.hb > input {position:absolute; opacity:0; pointer-events:none;}
.hbl {display:inline-flex; align-items:center; gap:8px; cursor:pointer; padding:7px 14px; border-radius:6px; font:700 14px var(--B);
  color:#fff; background:linear-gradient(90deg, rgba(225,6,0,.25), rgba(225,6,0,.08)); border:1px solid rgba(225,6,0,.45);
  white-space:nowrap; user-select:none; transition:background .15s;}
.hbl span {filter:grayscale(1) brightness(2);}
.hbl:hover {background:linear-gradient(90deg, rgba(225,6,0,.45), rgba(225,6,0,.15));}
.hbc {display:none; cursor:pointer; color:var(--mu); font-size:12px; white-space:nowrap; position:absolute; right:0; top:-2px;}
.hbx {display:none; margin-top:4px;}
.hb > input:checked ~ .hbl {display:none;}
.hb > input:checked ~ .hbc {display:inline-block;}
.hb > input:checked ~ .hbx, .all .hbx {display:block; animation:rise .25s ease-out;}
.all .hbl, .all .hbc {display:none !important;}
@keyframes rise {from {opacity:0; transform:translateY(4px);} to {opacity:1; transform:none;}}
/* ---------- résultats ---------- */
.mini {position:relative; padding-top:18px;}
.mini .ml {display:flex; flex-direction:column; gap:6px; max-height:102px; overflow-y:auto; overscroll-behavior:contain;
  scroll-snap-type:y mandatory;
  padding-right:4px; scrollbar-width:thin; scrollbar-color:rgba(225,6,0,.6) transparent;}
.mini .more {color:var(--mu); font-size:11px; text-align:center; margin-top:4px; text-transform:uppercase; letter-spacing:1px;}
.mini .l {display:flex; align-items:center; gap:9px; font-size:14px; white-space:nowrap; min-width:0; flex:none; height:30px;
  scroll-snap-align:start;}
.mini .l .pt {width:22px; text-align:right; font:800 14px var(--D); color:#fff;}
.mini .l.out {opacity:.6;}
.mini .l .p {font:900 italic 20px var(--D); width:20px; text-align:center;}
.mini .l.p1 .p {color:var(--gold);} .mini .l.p2 .p {color:var(--silver);} .mini .l.p3 .p {color:var(--bronze);}
.mini .l .mn {overflow:hidden; text-overflow:ellipsis; font-weight:600; min-width:0; white-space:nowrap;}
.mini .l .g {margin-left:auto; color:var(--mu); font-size:12px; font-variant-numeric:tabular-nums;}
.mini .l.me .mn {color:var(--gold);}
.hd {position:relative; flex:none; border-radius:50%; overflow:hidden; display:inline-flex; align-items:center; justify-content:center;
  background:radial-gradient(circle at 50% 30%, color-mix(in srgb, var(--c) 85%, #fff), var(--c) 60%, color-mix(in srgb, var(--c) 50%, #000));
  box-shadow:0 0 0 2px rgba(255,255,255,.12);}
.hd img {position:absolute; width:240%; max-width:none; height:auto; left:50%; top:3%; transform:translateX(-50%);}
.hdn {align-items:center; color:#fff; font-family:var(--D); font-weight:900; font-style:italic;}
table.res {width:100%; border-collapse:collapse; font-size:14px;}
table.res th {text-align:left; color:var(--mu); font:600 11px var(--B); text-transform:uppercase; letter-spacing:1.2px; padding:8px;
  border-bottom:1px solid var(--line); white-space:nowrap;}
table.res td {padding:7px 8px; border-bottom:1px solid rgba(255,255,255,.05); white-space:nowrap;}
table.res tr:hover td {background:rgba(255,255,255,.03);}
table.res .r {text-align:right;} table.res .l {text-align:left;}
table.res .ps span {display:inline-block; min-width:30px; text-align:center; font:900 italic 18px var(--D); border-left:4px solid var(--c);
  padding-left:4px;}
table.res tr.p1 .ps span {color:var(--gold);} table.res tr.p2 .ps span {color:var(--silver);} table.res tr.p3 .ps span {color:var(--bronze);}
table.res tr.fav td {background:rgba(243,195,78,.08);}
table.res tr.out td {color:var(--mu);}
table.res .g {color:var(--mu); font-variant-numeric:tabular-nums;}
table.res .pts {font:800 16px var(--D);}
.rc {display:inline-flex; align-items:center; gap:10px; min-width:0;}
.rn {display:flex; flex-direction:column; line-height:1.15; min-width:0;}
.rn b {white-space:nowrap; overflow:hidden; text-overflow:ellipsis; font-weight:600;}
.rn .tm {color:var(--mu); font-size:11px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:240px;}
.nb {display:inline-block; min-width:28px; text-align:center; font:900 italic 14px/20px var(--D); border-radius:4px; padding:0 4px;}
.podium {display:grid; grid-template-columns:1fr 1.18fr 1fr; gap:12px; align-items:end; margin:10px 0 16px;}
.pd {position:relative; border-radius:14px; overflow:hidden; min-width:0; border:1px solid var(--line);
  background:linear-gradient(180deg, color-mix(in srgb, var(--c) 70%, #000) 0%, #0c0e13 85%);}
.pd:before {content:''; position:absolute; inset:0; background:repeating-linear-gradient(115deg, transparent 0 18px, rgba(255,255,255,.03) 18px 36px);}
.pdi {height:150px; display:flex; align-items:flex-end; justify-content:center; position:relative;}
.pd1 .pdi {height:190px;}
.pdi img {height:100%; width:auto; object-fit:contain; filter:drop-shadow(0 10px 20px rgba(0,0,0,.5));}
.pdi i {font:900 italic 80px var(--D); color:rgba(255,255,255,.3);}
.pdx {position:relative; padding:10px 12px 12px; background:linear-gradient(0deg, rgba(0,0,0,.85), rgba(0,0,0,.4));}
.pdp {position:absolute; right:10px; top:-46px; font:900 italic 64px/1 var(--D); color:#fff; opacity:.9; text-shadow:0 4px 16px rgba(0,0,0,.6);}
.pd1 .pdp {color:var(--gold);} .pd2 .pdp {color:var(--silver);} .pd3 .pdp {color:var(--bronze);}
.pdn {font:900 italic 22px var(--D); text-transform:uppercase; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.pdt {color:#c3c9d3; font-size:11px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.pdg {color:var(--mu); font-size:12px; font-variant-numeric:tabular-nums;}
.pd1 {box-shadow:0 0 0 1px rgba(243,195,78,.5), 0 20px 50px rgba(243,195,78,.12);}
/* ---------- cartes du brief ---------- */
.bf-grid {display:grid; grid-template-columns:1fr 1fr; gap:14px; align-items:stretch;}
.bf-grid .full {grid-column:1 / -1;}
.card {position:relative; background:var(--card); border:1px solid var(--line); border-radius:16px; padding:16px 18px; min-width:0;
  overflow:hidden;}
.card h3 {font:900 italic 24px/1.05 var(--D) !important; text-transform:uppercase; margin:0 0 4px !important; padding:0 !important;
  color:#fff !important; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.card .tease {color:var(--mu); font-size:14px; margin-bottom:12px;}
.card .ico {position:absolute; right:14px; top:10px; font-size:34px; opacity:.9;}
.facts {margin:0; padding:0; list-style:none;}
.facts li {padding:8px 0; border-bottom:1px solid rgba(255,255,255,.06); font-size:15px;}
.facts li:last-child {border:none;}
.mv {display:flex; align-items:center; gap:10px; padding:5px 0; font-size:14px; white-space:nowrap; min-width:0;}
.mv .rk {font:900 italic 20px var(--D); width:24px; text-align:center;}
.mv .ar {width:36px; font:800 13px var(--B); text-align:center;}
.mv .up {color:var(--ok);} .mv .dn {color:var(--red2);} .mv .eq {color:var(--mu);}
.mv .pt {margin-left:auto; font:800 17px var(--D);}
.mv .rn2 {overflow:hidden; text-overflow:ellipsis; font-weight:600;}
.fav {position:relative; display:flex; align-items:stretch; gap:0; border-radius:16px; overflow:hidden; margin:14px 0 0; min-height:150px;
  background:linear-gradient(110deg, color-mix(in srgb, var(--c) 80%, #000) 0%, color-mix(in srgb, var(--c) 35%, #07080b) 45%, #0b0d12 100%);
  border:1px solid var(--line);}
.fav:before {content:attr(data-n); position:absolute; right:18px; top:-20px; font:900 italic 170px/1 var(--D); color:rgba(255,255,255,.06);}
.fav .fi {flex:0 0 170px; display:flex; align-items:flex-end; justify-content:center;}
.fav .fi img {height:160px; width:auto; filter:drop-shadow(0 10px 20px rgba(0,0,0,.5));}
.fav .fb {padding:16px 18px; min-width:0; position:relative;}
.fav .k {color:var(--gold); font:700 12px var(--B); letter-spacing:2px; text-transform:uppercase;}
.fav .t {font:900 italic 34px/1 var(--D); text-transform:uppercase; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.fav .s {color:#d9dee7; font-size:14px; margin-top:4px;}
.fav .nums {display:flex; gap:22px; margin-top:10px; flex-wrap:wrap;}
.fav .nums div b {display:block; font:900 italic 28px/1 var(--D);}
.fav .nums div span {color:#c3c9d3; font-size:11px; text-transform:uppercase; letter-spacing:1.2px;}
/* ---------- prochain GP ---------- */
.nx {position:relative; display:block; border-radius:16px; overflow:hidden; min-height:200px; padding:18px; isolation:isolate;
  border:1px solid var(--line);}
.nx:before {content:''; position:absolute; inset:0; z-index:-2; background:var(--img) center/cover; filter:brightness(.5);}
.nx:after {content:''; position:absolute; inset:0; z-index:-1; background:linear-gradient(90deg, rgba(0,0,0,.92), rgba(0,0,0,.2));}
.nx .k {color:var(--red2); font:700 12px var(--B); letter-spacing:2px; text-transform:uppercase;}
.nx b {display:block; font:900 italic 34px/1 var(--D); text-transform:uppercase; color:#fff; margin:4px 0;}
.nx .s {color:#d0d5de; font-size:14px;}
.nx .cd {font:800 italic 22px var(--D); color:#fff; margin-top:10px; text-transform:uppercase;}
.nx .tms {display:flex; gap:18px; margin-top:12px; flex-wrap:wrap;}
.nx .tms div {white-space:nowrap;} .nx .tms span {display:block; color:var(--mu); font-size:11px; text-transform:uppercase; letter-spacing:1.2px;}
.nx .tms b {font-size:20px; margin:0;}
.nx img.trk {position:absolute; right:0; top:50%; transform:translateY(-50%); height:120%; width:auto; max-width:42%;
  object-fit:contain; opacity:.92; filter:drop-shadow(0 2px 10px rgba(0,0,0,.6));}
/* ---------- calendrier ---------- */
.cal {display:grid; grid-template-columns:repeat(auto-fill, minmax(290px, 1fr)); gap:14px;}
.cc {position:relative; display:block; border-radius:14px; overflow:hidden; min-height:210px; isolation:isolate; border:1px solid var(--line);
  transition:transform .15s, box-shadow .15s;}
.cc:before {content:''; position:absolute; inset:0; z-index:-2; background:var(--img) center/cover; filter:brightness(.95) saturate(1.1);
  transition:transform .4s;}
.cc:after {content:''; position:absolute; inset:0; z-index:-1;
  background:linear-gradient(0deg, rgba(0,0,0,.92) 0%, rgba(0,0,0,.55) 38%, rgba(0,0,0,.05) 72%),
             linear-gradient(90deg, transparent 50%, rgba(0,0,0,.45) 100%);}
a.cc:hover {transform:translateY(-3px); box-shadow:0 16px 40px rgba(0,0,0,.6);}
a.cc:hover:before {transform:scale(1.05);}
.cc.done:before {filter:brightness(.85) grayscale(.35);}
.cc.next {box-shadow:0 0 0 2px var(--red), 0 18px 50px rgba(225,6,0,.3);}
.cc.live {box-shadow:0 0 0 2px var(--red);}
.cc .r {position:absolute; top:8px; left:14px; font:900 italic 46px/1 var(--D); color:rgba(255,255,255,.9); text-shadow:0 4px 18px rgba(0,0,0,.6);}
.cc .stt {position:absolute; top:12px; right:12px;}
.cc .trk {position:absolute; right:-8px; bottom:-14px; height:118px; width:auto; max-width:150px; object-fit:contain; opacity:.92;
  filter:drop-shadow(0 2px 8px rgba(0,0,0,.7));}
.cc .b {right:120px !important;}
.cc .b {position:absolute; left:14px; right:14px; bottom:12px;}
.cc .b .f {display:flex; align-items:center; gap:8px; color:#d0d5de; font-size:12px; text-transform:uppercase; letter-spacing:1.2px;}
.cc .b .f img {height:14px; border-radius:2px;}
.cc .b b {display:block; font:900 italic 24px/1.05 var(--D); text-transform:uppercase; color:#fff; white-space:nowrap; overflow:hidden;
  text-overflow:ellipsis;}
.cc .b span.c {display:block; color:var(--mu); font-size:12px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.times {width:100%; border-collapse:collapse;}
.times td {padding:9px 10px; border-bottom:1px solid rgba(255,255,255,.06); white-space:nowrap;}
.times td.n {font:800 18px var(--D); text-transform:uppercase;}
.times tr.race td {background:rgba(225,6,0,.08);} .times tr.race td.n {color:var(--red2);}
/* ---------- classement ---------- */
.top5 {display:grid; grid-template-columns:repeat(5, 1fr); gap:12px; margin-bottom:16px;}
.t5 {position:relative; border-radius:16px; overflow:hidden; height:330px; border:1px solid var(--line); min-width:0;
  background:linear-gradient(170deg, color-mix(in srgb, var(--c) 80%, #000) 0%, color-mix(in srgb, var(--c) 30%, #0b0d12) 60%, #0b0d12 100%);}
.t5:before {content:attr(data-n); position:absolute; left:0; right:0; top:6px; text-align:center; font:900 italic 150px/1 var(--D);
  color:rgba(255,255,255,.1);}
.t5 .p {position:absolute; top:10px; left:12px; font:900 italic 40px/1 var(--D); z-index:2;}
.t5.r1 .p {color:var(--gold);} .t5.r2 .p {color:var(--silver);} .t5.r3 .p {color:var(--bronze);}
.t5 img {position:absolute; left:50%; transform:translateX(-50%); bottom:92px; height:220px; width:auto;
  filter:drop-shadow(0 10px 20px rgba(0,0,0,.6));}
.t5 .b {position:absolute; left:0; right:0; bottom:0; padding:10px 12px 12px; background:linear-gradient(0deg, rgba(0,0,0,.95), rgba(0,0,0,.6));}
.t5 .n {font:900 italic 21px/1 var(--D); text-transform:uppercase; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.t5 .tm {color:#c3c9d3; font-size:11px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; margin-top:2px;}
.t5 .pts {font:900 italic 28px/1 var(--D); margin-top:6px; white-space:nowrap;} .t5 .pts small {font-size:13px; color:var(--mu);}
.top5.pod3 {grid-template-columns:repeat(3, 1fr); align-items:end; gap:16px; max-width:980px; margin:0 auto 18px;}
.pod3 .t5 {height:320px;} .pod3 .t5.r1 {height:370px; box-shadow:0 0 0 1px rgba(243,195,78,.55), 0 24px 60px rgba(243,195,78,.14);}
.pod3 .t5 img {height:230px;} .pod3 .t5.r1 img {height:270px;}
.pod3 .t5 .p {font-size:52px;} .pod3 .t5 .n {font-size:24px;} .pod3 .t5 .pts {font-size:32px;}
.top3 {display:grid; grid-template-columns:repeat(3, 1fr); gap:12px; margin-bottom:16px;}
.t3 {position:relative; border-radius:16px; overflow:hidden; min-height:200px; border:1px solid var(--line); display:flex; align-items:flex-end;
  background:linear-gradient(160deg, color-mix(in srgb, var(--c) 75%, #000), #0b0d12 80%);}
.t3:before {content:attr(data-n); position:absolute; right:10px; top:-14px; font:900 italic 120px/1 var(--D); color:rgba(255,255,255,.07);}
.t3 img {position:absolute; right:0; bottom:0; height:190px; width:auto; filter:drop-shadow(0 8px 20px rgba(0,0,0,.6));}
.t3 .b {position:relative; padding:14px; z-index:1; min-width:0; max-width:62%;}
.t3 .p {font:900 italic 44px/1 var(--D);} .t3.r1 .p {color:var(--gold);} .t3.r2 .p {color:var(--silver);} .t3.r3 .p {color:var(--bronze);}
.t3 .n {font:900 italic 24px/1 var(--D); text-transform:uppercase; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.t3 .tm {color:#c3c9d3; font-size:12px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.t3 .pts {font:900 italic 30px/1 var(--D); margin-top:8px;} .t3 .pts small {font-size:14px; color:var(--mu);}
table.std {width:100%; border-collapse:collapse; font-size:14px;}
table.std th {color:var(--mu); font:600 11px var(--B); text-transform:uppercase; letter-spacing:1.2px; text-align:right; padding:8px 6px;
  border-bottom:1px solid var(--line); white-space:nowrap;}
table.std th.l, table.std td.l {text-align:left;}
table.std td {text-align:right; padding:7px 6px; border-bottom:1px solid rgba(255,255,255,.05); white-space:nowrap;}
table.std tr:hover td {background:rgba(255,255,255,.03);}
table.std td.pts {font:900 italic 19px var(--D);}
table.std tr.fav td {background:rgba(243,195,78,.08);}
table.std .rk {font:900 italic 19px var(--D); text-align:center; width:34px;}
.bar {display:block; height:5px; border-radius:3px; background:linear-gradient(90deg, var(--c), color-mix(in srgb, var(--c) 40%, transparent));
  margin-top:4px;}
.lp {display:inline-block; min-width:24px; padding:1px 4px; margin-left:3px; border-radius:4px; background:var(--card2);
  font:700 12px var(--D); text-align:center;}
.lp.w {background:var(--gold); color:#000;} .lp.pod {background:#3b4250;} .lp.x {background:rgba(225,6,0,.3); color:#ff9a95;}
.scroll {overflow-x:auto;}
/* ---------- podium du championnat, façon habillage TV ---------- */
.lead3 {display:grid; grid-template-columns:repeat(3, minmax(0, 1fr)); gap:14px; margin:4px 0 18px;}
.stMarkdown a.lb {color:#fff !important;}
.lb {position:relative; display:block; height:210px; border-radius:14px; overflow:hidden; isolation:isolate; text-decoration:none;
  border:1px solid var(--line); transition:transform .15s, box-shadow .15s;
  background:linear-gradient(112deg, #0e1015 0 47%, color-mix(in srgb, var(--c) 92%, #000) 47% 100%);}
.lb:hover {transform:translateY(-3px); box-shadow:0 18px 40px rgba(0,0,0,.55);}
.lb:before {content:''; position:absolute; left:0; right:0; top:0; height:4px; z-index:4; background:var(--m);}
.lb.r1 {--m:var(--gold);} .lb.r2 {--m:var(--silver);} .lb.r3 {--m:var(--bronze);}
.lb.r1 {box-shadow:0 0 0 1px rgba(243,195,78,.45), 0 20px 50px rgba(243,195,78,.1);}
.lb:after {content:''; position:absolute; inset:0; z-index:-1; pointer-events:none;
  background:repeating-linear-gradient(112deg, transparent 0 14px, rgba(255,255,255,.035) 14px 28px),
             linear-gradient(0deg, rgba(0,0,0,.35), transparent 60%);}
.lb .num {position:absolute; right:-6px; top:-14px; z-index:0; font:900 italic 190px/1 var(--D); color:transparent;
  -webkit-text-stroke:2px rgba(255,255,255,.28); letter-spacing:-6px;}
.lb .pic {position:absolute; right:0; top:0; bottom:0; width:52%; overflow:hidden; z-index:1;}
.lb .pic img {position:absolute; left:50%; top:6%; width:150%; max-width:none; height:auto; transform:translateX(-50%);
  filter:drop-shadow(-6px 6px 16px rgba(0,0,0,.55));}
.lb .inf {position:absolute; left:18px; top:14px; bottom:14px; width:50%; z-index:2; display:flex; flex-direction:column; min-width:0;}
.lb .pos {font:900 italic 46px/1 var(--D); color:var(--m);}
.lb .fn {margin-top:auto; color:#c9ced8; font:600 13px var(--B); letter-spacing:1.5px; text-transform:uppercase; white-space:nowrap;}
.lb .ln {font:900 italic 30px/1 var(--D); text-transform:uppercase; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.lb .tm {display:flex; align-items:center; gap:6px; color:#9aa1ad; font-size:11px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
  margin-top:2px;}
.lb .tm i {width:10px; height:10px; flex:none; background:var(--c); transform:skewX(-15deg); box-shadow:0 0 0 1px rgba(255,255,255,.3);}
.lb .pt {display:flex; align-items:baseline; gap:6px; margin-top:8px; white-space:nowrap;}
.lb .pt b {font:900 italic 34px/1 var(--D);}
.lb .pt small {color:var(--mu); font:700 12px var(--B); text-transform:uppercase;}
.lb .gp {margin-left:6px; padding:2px 8px; border-radius:4px; background:rgba(255,255,255,.08); font:800 13px var(--D); color:#d9dee6;}
.lb .gp.lead {background:var(--gold); color:#000; text-transform:uppercase; letter-spacing:.8px;}
/* ---------- pilotes ---------- */
.rg {display:grid; grid-template-columns:repeat(auto-fill, minmax(190px, 1fr)); gap:12px;}
.rt {position:relative; overflow:hidden; border-radius:16px; text-decoration:none; min-width:0; display:block; height:270px;
  background:linear-gradient(165deg, color-mix(in srgb, var(--c) 85%, #000) 0%, color-mix(in srgb, var(--c) 30%, #0b0d12) 55%, #0b0d12 100%);
  border:1px solid var(--line); transition:transform .15s, box-shadow .15s;}
a.rt:hover {transform:translateY(-4px); box-shadow:0 18px 40px rgba(0,0,0,.6);}
.rt .n {position:absolute; top:-8px; right:6px; font:900 italic 96px/1 var(--D); color:rgba(255,255,255,.14);}
.rt img {position:absolute; left:50%; transform:translateX(-50%); bottom:58px; height:190px; width:auto;
  filter:drop-shadow(0 10px 18px rgba(0,0,0,.55)); transition:transform .3s;}
a.rt:hover img {transform:translateX(-50%) scale(1.04);}
.rt .b {position:absolute; left:0; right:0; bottom:0; padding:10px 12px; background:linear-gradient(0deg, rgba(0,0,0,.92), rgba(0,0,0,.5));}
.rt .b small {display:block; color:#c9ced8; font-size:12px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.rt .b b {display:block; font:900 italic 22px/1 var(--D); text-transform:uppercase; color:#fff; white-space:nowrap; overflow:hidden;
  text-overflow:ellipsis;}
.rt .b span {display:block; font-size:11px; color:var(--mu); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.rt .rk {position:absolute; top:10px; left:10px; background:rgba(0,0,0,.6); padding:2px 8px; border-radius:4px; font:800 13px var(--D);}
.rhead {position:relative; display:flex; gap:24px; align-items:flex-end; border-radius:18px; padding:20px 24px 0; margin:8px 0 14px;
  overflow:hidden; min-height:240px;
  background:linear-gradient(115deg, color-mix(in srgb, var(--c) 85%, #000) 0%, color-mix(in srgb, var(--c) 30%, #0b0d12) 50%, #0b0d12 100%);
  border:1px solid var(--line);}
.rhead:before {content:attr(data-n); position:absolute; right:30px; top:-30px; font:900 italic 260px/1 var(--D); color:rgba(255,255,255,.07);}
.rhead img {height:240px; width:auto; position:relative; filter:drop-shadow(0 10px 30px rgba(0,0,0,.6));}
.rhead .i {padding-bottom:22px; min-width:0; position:relative;}
.rhead .fn {font:600 18px var(--B); color:#e6e9ef; text-transform:uppercase; letter-spacing:3px;}
.rhead h1 {font:900 italic 64px/.9 var(--D) !important; color:#fff !important; margin:0 !important; padding:0 !important;
  text-transform:uppercase;}
.rhead .s {color:#dfe3ea; font-size:14px; margin-top:6px;}
.tiles {display:grid; grid-template-columns:repeat(auto-fill, minmax(130px, 1fr)); gap:10px; margin:6px 0 12px;}
.tile {background:var(--card); border:1px solid var(--line); border-radius:12px; padding:12px 14px; min-width:0; position:relative; overflow:hidden;}
.tile:before {content:''; position:absolute; left:0; top:0; width:30px; height:3px; background:var(--red);}
.tile b {display:block; font:900 italic 34px/1 var(--D);}
.tile span {color:var(--mu); font-size:11px; text-transform:uppercase; letter-spacing:1.2px; white-space:nowrap;}
/* ---------- actus ---------- */
.news {display:grid; grid-template-columns:repeat(auto-fill, minmax(290px, 1fr)); gap:14px;}
.nw {background:var(--card); border:1px solid var(--line); border-radius:14px; overflow:hidden; display:flex; flex-direction:column;
  text-decoration:none; min-width:0; height:100%; transition:transform .15s;}
a.nw:hover {transform:translateY(-3px);}
.nw .im {position:relative; height:170px; overflow:hidden;
  background:linear-gradient(135deg, rgba(225,6,0,.55), rgba(225,6,0,.08) 55%), repeating-linear-gradient(115deg, #12151c 0 18px, #171b23 18px 36px);}
.nw .im:before {content:'MOTOGP'; position:absolute; left:14px; bottom:10px; font:900 italic 34px var(--D); color:rgba(255,255,255,.18);}
.nw .im img {position:absolute; inset:0; width:100%; height:100%; object-fit:cover; display:block;}
.nw .b {padding:12px 14px;}
.nw .t {font:800 19px/1.15 var(--D); color:var(--tx);}
.nw .d {color:var(--mu); font-size:13px; margin-top:6px;}
.nw .m {color:var(--red2); font:700 11px var(--B); margin-top:8px; white-space:nowrap; text-transform:uppercase; letter-spacing:1px;}
.nwx {position:relative;}
.nwx > input {position:absolute; opacity:0; pointer-events:none;}
.nwx .nw {filter:blur(10px) brightness(.7); pointer-events:none; user-select:none;}
.nwx > input:checked ~ .nw, .all .nwx .nw {filter:none; pointer-events:auto;}
.nwx > label {position:absolute; inset:0; z-index:2; display:flex; flex-direction:column; align-items:center; justify-content:center;
  text-align:center; gap:6px; cursor:pointer; color:#fff; font:800 italic 20px var(--D); text-transform:uppercase; padding:16px;}
.nwx > label small {color:#d0d5de; font:500 13px var(--B); text-transform:none;}
.nwx > label em {font-style:normal; background:var(--red); padding:4px 12px; border-radius:4px; font:700 13px var(--B);}
.nwx > input:checked ~ label, .all .nwx > label {display:none;}
.shield {background:linear-gradient(90deg, rgba(225,6,0,.18), rgba(225,6,0,.04)); border:1px solid rgba(225,6,0,.35); border-radius:12px;
  padding:12px 16px; margin:6px 0 12px; font-size:14px;}
.foot {color:#5d6574; font-size:12px; text-align:center; margin:40px 0 10px;}
.foot .kerb {margin:0 auto 14px; width:120px; height:4px; transform:skewX(-20deg);}
[data-testid="stExpander"] details {background:var(--card); border:1px solid var(--line) !important; border-radius:12px !important;}
[data-testid="stExpander"] summary p {font:800 18px var(--D) !important; text-transform:uppercase; letter-spacing:.4px;}
@media (max-width: 900px) {
  .tl {grid-template-columns:1fr !important;}
  .hstats {grid-template-columns:repeat(3, 1fr) !important;} .hstats div:nth-child(n+4) {display:none;}
}
@media (max-width: 700px) {
  .block-container {padding-left:.8rem !important; padding-right:.8rem !important;}
  .brand .lg {font-size:24px;} .brand small {display:none;}
  .hero-in {flex-direction:column; padding:20px 18px 14px; min-height:0;}
  .hero h1 {font-size:44px;}
  .ttl {gap:4px;} .ttl .trk {height:130px; max-width:150px; margin:-32px -24px -32px -18px;}
  .hstats b {font-size:19px;} .hstats b small {display:none;} .hstats div {padding:10px;}
  .hstats span {font-size:9px; letter-spacing:.6px; overflow:hidden; text-overflow:ellipsis; display:block;}
  .brand .sl {display:none;}
  .hero2 {grid-template-columns:1fr; height:auto;}
  .hero2 .ph {height:auto;}
  .ph-in {position:relative; left:auto; right:auto; top:auto; bottom:auto; padding:16px 18px 18px; min-height:290px; gap:28px;}
  .hero2 h1 {font-size:40px;} .hcd b {font-size:26px;}
  .bp {height:250px; margin-left:0; padding-left:0; -webkit-mask-image:none; mask-image:none; border-top:3px solid var(--red);}
  .bp-h, .bp-f {left:0 !important;}
  .bp {height:300px !important;}
  .bp-tb.tall {left:130px; right:125px; top:50px; bottom:36px;}
  .bp-tb.wide {left:14px; right:14px; top:112px; bottom:88px;} .bp-h, .bp-f {padding-left:0;}
  .bp-s.tl, .bp-s.bl {left:10px !important;} .bp-s.tr, .bp-s.br {right:10px;} .bp-s b {font-size:22px;}
  .lead3 {grid-template-columns:1fr; gap:10px;}
  .lb {height:184px;} .lb .num {font-size:150px;} .lb .pos {font-size:36px;}
  .lb .ln {font-size:26px; line-height:1.12;} .lb .tm {margin-top:4px;} .lb .pt {margin-top:6px;} .lb .pt b {font-size:30px;}
  .bf-grid {grid-template-columns:1fr;}
  .top5.pod3 {gap:6px;} .pod3 .t5 {height:210px;} .pod3 .t5.r1 {height:250px;}
  .pod3 .t5 img {height:130px; bottom:78px;} .pod3 .t5.r1 img {height:165px;} .t5:before {font-size:70px;}
  .pod3 .t5 .p {font-size:30px;} .pod3 .t5 .n {font-size:15px;} .pod3 .t5 .tm {display:none;} .pod3 .t5 .pts {font-size:20px;}
  .t5 .b {padding:8px;}
  .rg {grid-template-columns:repeat(2, 1fr);} .rt {height:230px;} .rt img {height:150px;} .rt .n {font-size:70px;}
  .rhead {padding:14px 14px 0; gap:10px;} .rhead img {height:150px;} .rhead h1 {font-size:38px;} .rhead:before {font-size:150px;}
  .rn .tm {max-width:140px;}
  .podium {gap:6px;} .pdi {height:100px;} .pd1 .pdi {height:130px;} .pdn {font-size:16px;} .pdp {font-size:44px; top:-34px;}
  .fav .fi {flex-basis:110px;} .fav .fi img {height:120px;} .fav .t {font-size:26px;}
  .st-key-tabs [data-testid="stHorizontalBlock"] {flex-wrap:wrap !important; gap:6px !important;}
  .st-key-tabs [data-testid="stColumn"] {flex:0 0 calc(33.33% - 4px) !important; min-width:0 !important; width:calc(33.33% - 4px) !important;}
  .st-key-tabs button p {font-size:14px !important;}
  .st-key-tabs button {padding:4px 6px !important;}
  table.std .hm {display:none;}
  table.std td, table.std th {padding:6px 4px;}
  table.std .rc .nb, table.std .rn .tm {display:none;}
  table.std .rn b {max-width:118px;} table.std .rc .hd {width:28px !important; height:28px !important;}
}
</style>
"""


def page_footer():
    st.markdown("<div class='foot'><div class='kerb'></div>Bada Bing MotoGP · site de fans, non affilié à MotoGP™ / Dorna · "
                "données et photos : motogp.com · vidéos : CANAL+ Sport · actus : Motorsport.com, Paddock GP · "
                f"v{APP_VERSION[-3:]}</div>", unsafe_allow_html=True)


# =========================================================================
# MORCEAUX DU WEEK-END
# =========================================================================
def mini_result(rows, fav=None, race=True):
    """Classement complet de la séance, dans la tuile : les 3 premiers visibles, le reste en faisant défiler."""
    out = []
    for r in rows:
        info = rider_of(r["rid"], r)
        if r["pos"]:
            g = "" if r["pos"] == 1 else (("+" + fr_num(r["gap"], 3)) if r["gap"] is not None else "")
            if r["pos"] == 1:
                g = esc(r.get("best") if not race else r.get("time")) or ""
            if r.get("laps_down") and race:
                g = f"+{r['laps_down']} t."
        else:
            g = "ab." if r.get("status") != "NOTSTARTED" else "np"
        pts = f"<span class='pt'>{r['points']}</span>" if race and r.get("points") else ("<span class='pt'></span>" if race else "")
        cls = f"l p{r['pos'] or 0}" + (" me" if fav and r["rid"] == fav else "") + ("" if r["pos"] else " out")
        out.append(f"<div class='{cls}'><span class='p'>{r['pos'] or '–'}</span>{head(info, 30)}"
                   f"<span class='mn'>{flag(r['iso'])} {rider_link(r['rid'], esc(short_name(info, r)))}</span>"
                   f"<span class='g'>{g}</span>{pts}</div>")
    more = f"<div class='more'>↓ {len(rows) - 3} autres pilotes</div>" if len(rows) > 3 else ""
    return f"<div class='mini'><div class='ml'>{''.join(out)}</div>{more}</div>"


def session_tile(ev, s, rows, zone, fav, show_idx, stakes, grid=None, today=None, extra=""):
    live = session_live(s)
    race = s["code"] in ("SPR", "RAC")
    cls = "ses" + (" race" if race else " q" if s["group"] == "q" else "") + (" live" if live else "")
    if session_done(s):
        state = "<span class='done' title='Terminée'>✓</span>"
    elif live:
        state = "<span class='pill live'>● Direct</span>"
    else:
        state = ""
    sub = []
    if s.get("weather") and (session_done(s) or live):
        sub.append(f"{s['weather']} · {TRACK_FR.get(norm(s['track']), 'piste ' + norm(s['track']))}" + (f" · {s['air']}" if s.get("air") else ""))
    elif not session_done(s) and s["utc"] > now_utc():
        sub.append(countdown(s["utc"]) + (" · horaire provisoire" if s.get("provisional") else ""))
    key = seen_key(ev, s["group"]) if s["group"] else ""
    body = ""
    if session_done(s) and rows:
        wt = ""
        if False:  # indice « à voir » retiré (v-m010)
            lvl = watch_level(watch_score(rows, grid, s, stakes))
            if lvl is not None:
                ic, nm, _ = WATCH_LEVELS[lvl]
                wt = f"<div class='wt'>{ic} <b>{nm}</b></div>"
        body = wt + extra + f"<div class='body'>{hidden('Voir le résultat', mini_result(rows, fav, race), seen=key)}</div>"
    elif live and rows:
        body = f"<div class='body'>{hidden('Classement en direct', mini_result(rows, fav, race))}</div>"
    elif session_done(s):
        body = extra + "<div class='wt note'>Résultat pas encore publié</div>"
    return (f"<div class='{cls}'><div class='top'><span class='t'>{hm(s['utc'], zone)}</span>"
            f"<span class='nm'>{esc(s['label'])}<small>{esc(' · '.join(sub))}</small></span><span class='st'>{state}</span></div>"
            f"{body}</div>")


def timeline(ev, sess, res, zone, fav, show_idx, stakes, cat="MotoGP", pays="FR"):
    """Le week-end en 3 colonnes : vendredi, samedi, dimanche. Les courses portent leur bouton replay."""
    grid = grid_of(res)
    days = {}
    for s in sess:
        days.setdefault(s["utc"].astimezone(zone).date(), []).append(s)
    today = now_utc().astimezone(zone).date()
    cols = []
    for d in sorted(days):
        tiles = "".join(session_tile(ev, s, res.get(s["code"]) or [], zone, fav, show_idx, stakes,
                                     grid if s["code"] in ("SPR", "RAC") else None,
                                     extra=replay_buttons(ev, s, cat, pays)) for s in days[d])
        cls = "dh today" if d == today else "dh"
        cols.append(f"<div class='day'><div class='{cls}'>{JOURS[d.weekday()]}<span>{day_num(d)} {MOIS[d.month - 1]}</span></div>{tiles}</div>")
    n = len(cols)
    return f"<div class='tl' style='grid-template-columns:repeat({max(1, min(n, 3))}, 1fr)'>{''.join(cols)}</div>" if cols else ""


@st.cache_data(ttl=7 * 86400, show_spinner=False, max_entries=60)
def track_tight(url):
    """Tracé recadré au plus près du dessin (les images MotoGP ont 17 à 40 % de marge vide, variable selon le circuit),
    en image intégrée : il remplit alors exactement sa zone. Gardé sur disque."""
    import base64
    import hashlib
    import io
    name = "trk_" + hashlib.md5(url.encode()).hexdigest()[:12] + ".json"
    cached = disk_get(name)
    if cached:
        return cached["src"], cached["ratio"]
    try:
        from PIL import Image
        r = SESSION.get(url, timeout=12)
        im = Image.open(io.BytesIO(r.content)).convert("RGBA")
        box = im.getchannel("A").getbbox()
        if not box:
            return url, 1.0
        pad = 6
        im = im.crop((max(0, box[0] - pad), max(0, box[1] - pad), min(im.width, box[2] + pad), min(im.height, box[3] + pad)))
        im.thumbnail((700, 700))
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
        out = {"src": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(), "ratio": im.width / max(im.height, 1)}
        disk_put(name, out)
        return out["src"], out["ratio"]
    except Exception as e:
        log.warning("Tracé non recadré (%s)", type(e).__name__)
        return url, 1.0


def lap_record(ev, sess):
    """Record du tour du circuit (établi AVANT ce GP : un record battu ce week-end serait un spoil)."""
    for x in sess:
        if not session_done(x):
            continue
        for r in records(x):
            if r.get("type") == "bestLap" and r.get("year") and int(r["year"]) < ev["start"].year:
                t = ((r.get("bestLap") or {}).get("time") or "").lstrip("0")
                t = re.sub(r"^(\d+):", r"\1'", t)
                return t, ((r.get("rider") or {}).get("full_name") or ""), r["year"]
        break
    return None


def gp_hero(ev, sess, zone, nev=None, total=22):
    """Bannière du GP en deux panneaux coupés en biais : à gauche la photo (nom, dates, compte à rebours),
    à droite la fiche du circuit façon plan d'ingénieur (tracé en grand, chiffres annotés dans les coins)."""
    m = event_meta(ev)
    state = ev_state(ev, sess)
    pill = {"pre": "<span class='pill pre'>À venir</span>", "live": "<span class='pill live'>● Week-end en cours</span>",
            "post": "<span class='pill post'>✓ Terminé</span>"}[state]
    nxt = next((x for x in sess if not session_done(x) and x["utc"] and x["utc"] > now_utc() - timedelta(minutes=50)), None)
    cd = ""
    if nxt and state != "post":
        if session_live(nxt):
            cd = f"<div class='hcd'><small>En piste</small><b><span>●</span> {esc(nxt['label'])}</b></div>"
        else:
            cd = (f"<div class='hcd'><small>{esc(nxt['label'])} · {esc(when_txt(nxt['utc'], zone))}</small>"
                  f"<b>{esc(countdown(nxt['utc']))}</b></div>")
    elif state == "pre" and sess:
        cd = f"<div class='hcd'><small>Premier tour de roue</small><b>{esc(countdown(sess[0]['utc']))}</b></div>"
    name = gp_full(ev)
    parts = name.split(" ", 2)
    title = f"{esc(' '.join(parts[:2]))}<em>{esc(parts[2] if len(parts) > 2 else '')}</em>"
    flag_img = f"<img src='{esc(m['flag'])}' alt=''>" if m.get("flag") else flag(ev["iso"])
    city = m.get("city") or ev["place"]
    bg = m.get("bg") or m.get("top") or ""

    # Fiche du circuit (plan)
    laps = (m.get("laps") or {}).get("MGP") or {}
    def cell(pos, label, value, unit=""):
        u = f"<small>{unit}</small>" if unit else ""
        return f"<div class='bp-s {pos}'><span>{esc(label)}</span><b>{value}{u}</b></div>"
    cells = []
    if m.get("km"):
        cells.append(cell("tl", "Longueur", fr_num(float(m["km"]), 2), " km"))
    if m.get("left") is not None and m.get("right") is not None:
        lc, rc = int(float(m["left"])), int(float(m["right"]))
        cells.append(cell("tr", "Virages", str(lc + rc), f" {lc} G · {rc} D"))
    if m.get("straight"):
        cells.append(cell("bl", "Ligne droite", f"{int(float(m['straight'])):,}".replace(",", " "), " m"))
    if laps.get("laps"):
        cells.append(cell("br", "Tours", str(laps["laps"]), f" sprint {laps['sprint']}" if laps.get("sprint") else ""))
    foot = []
    if laps.get("km"):
        foot.append(f"Course <b>{fr_num(float(laps['km']), 1)} km</b>")
    rec = lap_record(ev, sess)
    if rec:
        foot.append(f"Record <b>{esc(rec[0])}</b> · {esc(rec[1])} ({esc(rec[2])})")
    elif m.get("built"):
        foot.append(f"Construit en <b>{esc(m['built'])}</b>")
    trk = ""
    if m.get("track"):
        src, ratio = track_tight(m["track"])
        shape = "wide" if ratio >= 1.25 else "tall"  # tracé large : entre les 2 rangées de chiffres ; haut : entre les 2 colonnes
        trk = f"<div class='bp-tb {shape}'><img class='bp-t' src='{src}' alt='Tracé du circuit'></div>"
    bp = (f"<div class='bp'><div class='bp-grid'></div>"
          f"<div class='bp-h'><span>Fiche circuit</span><b>{esc(ev['circuit'])}</b></div>{trk}{''.join(cells)}"
          f"<div class='bp-f'>{' · '.join(foot)}</div></div>") if trk else ""

    return (f"<div class='hero2{'' if bp else ' solo'}'><div class='ph' style=\"--img:url('{esc(bg)}')\"><div class='ph-in'>"
            f"<div class='ph-top'><div class='rnd'>Manche <b>{ev['round']}</b><i>/ {total}</i></div>"
            f"<div class='ctry'>{flag_img} {esc(city)} · {esc(m.get('country') or '')}</div></div>"
            f"<div class='ph-bot'><h1>{title}</h1><div class='hsub'>{esc(fr_range(ev['start'], ev['end']))} {pill}</div>{cd}</div></div></div>"
            f"{bp}</div>")



def replay_buttons(ev, s, cat, pays):
    """Bouton replay d'une course finie : le résumé CANAL+ Sport (en français). Sans titre (spoil)."""
    if s["code"] not in ("SPR", "RAC") or not session_done(s) or (cat != "MotoGP" and s["code"] != "RAC"):
        return ""
    kind = "sprint" if s["code"] == "SPR" else "race"
    canal = find_video(ev, "canal", kind, cat)
    href = yt_url(canal) if canal else canal_search_url(ev, kind, cat)
    mins = f" <small>{canal['mins']} min</small>" if canal and canal.get("mins") else ""
    what = "du sprint" if kind == "sprint" else "de la course"
    return (f"<div class='rpbs'><a class='rpb canal' href='{esc(href)}' target='_blank' rel='noopener'>"
            f"<span class='pl'></span>Résumé {what} <b>CANAL+</b>{mins}</a></div>")


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
# LE WEEK-END EN BREF
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
        out.append(f"<div class='mv'><span class='rk'>{t['rank']}</span>{ar}{head(info, 28)}<span class='rn2'{star}>"
                   f"{rider_link(t['rid'], esc(info.get('name') or t['name']))}</span><span class='pt'>{t['pts']}</span></div>")
    return "".join(out)


def ours_html(ev, res_all, pays, fav):
    out = []
    for cat, res in res_all.items():
        for code, label in (("SPR", "Sprint"), ("RAC", "Course")):
            for r in res.get(code) or []:
                if r["iso"] == pays:
                    pos = nth(r["pos"]) if r["pos"] else status_fr(r).lower()
                    info = rider_of(r["rid"], r)
                    out.append(f"<div class='mv'>{head(info, 30)}<span class='rn2'>{rider_link(r['rid'], esc(r['name']))} "
                               f"<span class='note'>{cat} · {label.lower()}</span></span><span class='pt'>{pos}</span></div>")
    return "".join(out)


# =========================================================================
# PAGES
# =========================================================================
def pick_event(evs, code=None, ref=None):
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
    ss = st.session_state
    keys = {seen_key(ev, s["group"]) for lst in sess_lists for s in lst if s["group"] and session_done(s)}
    if keys - ss.seen:
        ss.seen = ss.seen | keys
        write_seen(ss.seen)


def reveal_toggle(key, ctx, ev=None, wk=None):
    """« Tout dévoiler » : montre d'un coup tous les résultats de la page (et note le GP comme vu)."""
    if ctx["show_all"]:
        return " all"
    with st.container(key="tools"):
        on = st.toggle("👁 Tout dévoiler", key=key, help="Montre d'un coup tous les résultats cachés du week-end.")
    if on and ev and wk:
        mark_seen(ev, list(wk.values()))
    return " all" if on else ""


def fav_band(ctx, sess, evs, cut):
    ss = st.session_state
    fav = ss.get("fav")
    if not fav:
        return ""
    info = rider_of(fav)
    me = next((t for t in ctx["table"] if t["rid"] == fav), None)
    zone = ctx["zone"]
    nxt = next((s for s in sess if not session_done(s) and s["utc"] and s["utc"] > now_utc()), None)
    if nxt:
        nxt_txt = f"Prochaine séance : {esc(nxt['label'])}, {when_txt(nxt['utc'], zone)}"
    else:
        nev = next((e for e in evs if e["start"] > now_utc().date()), None)
        nxt_txt = f"Prochain GP : {esc(nev['name'])}, {esc(fr_range(nev['start'], nev['end']))}" if nev else "Saison terminée"
    img = f"<div class='fi'><img src='{esc(info['photo'])}' alt=''></div>" if info.get("photo") else ""
    nums = ""
    if me:
        nums = "".join(f"<div><b>{v}</b><span>{l}</span></div>" for v, l in
                       ((nth(me["rank"]), "championnat"), (me["pts"], "points"), (me["wins"], "victoires"), (me["pod"], "podiums")))
    note = " <span class='note'>(avant le dernier GP)</span>" if cut else ""
    return (f"<div class='fav' data-n='{esc(info.get('num') or '')}' style='--c:{esc(info.get('color', '#444'))}'>{img}<div class='fb'>"
            f"<div class='k'>⭐ Mon pilote{note}</div><div class='t'>{rider_link(fav, esc(info.get('name')))}</div>"
            f"<div class='s'>{esc(info.get('team', ''))}</div><div class='nums'>{nums}</div><div class='s'>{nxt_txt}</div></div></div>")


def next_gp_card(nev, ctx):
    m = event_meta(nev)
    sess = sessions(nev, ctx["cat_ids"]["MotoGP"])
    key = [s for s in sess if s["code"] in ("Q2", "SPR", "RAC")]
    tms = "".join(f"<div><span>{esc(s['short'])}</span><b>{when_txt(s['utc'], ctx['zone'])}</b></div>" for s in key)
    img = m.get("bg") or m.get("top") or ""
    trk = f"<img class='trk' src='{esc(m['track'])}' alt=''>" if m.get("track") else ""
    first = sess[0]["utc"] if sess else None
    cd = f"<div class='cd'>Premier tour de roue {countdown(first)}</div>" if first and first > now_utc() else ""
    return (f"<a class='nx' href='#go?vue=gp&gp={nev['code']}' style=\"--img:url('{esc(img)}')\"><div class='k'>Le prochain GP · manche "
            f"{nev['round']}</div><b>{flag(nev['iso'])} {esc(nev['name'])}</b><div class='s'>{esc(nev['circuit'])} · "
            f"{esc(fr_range(nev['start'], nev['end']))}</div>{cd}<div class='tms'>{tms}</div>{trk}</a>")





def gp_page(ctx):
    """Le Grand Prix (page d'accueil : le week-end en cours, sinon le dernier couru ou le prochain)."""
    ss = st.session_state
    evs, zone, fav = ctx["evs"], ctx["zone"], ss.get("fav")
    ev = pick_event(evs, ss.get("gp"))
    i = evs.index(ev)
    prv = link(f"vue=gp&gp={evs[i - 1]['code']}", f"<span>‹</span><em>{esc(evs[i - 1]['name'])}</em>", "gpn") if i > 0 else "<span></span>"
    nxt = link(f"vue=gp&gp={evs[i + 1]['code']}", f"<em>{esc(evs[i + 1]['name'])}</em><span>›</span>", "gpn r") if i < len(evs) - 1 else "<span></span>"
    st.markdown(f"<div class='gpbar'>{prv}{link('vue=calendrier', '📅 Tous les GP', 'gpn c')}{nxt}</div>", unsafe_allow_html=True)
    cat_ids, results, cut = ctx["cat_ids"], ctx["results"], ctx["cut"]
    wk = weekend(ev, cat_ids)
    msess = wk.get("MotoGP") or []
    st.markdown(gp_hero(ev, msess, zone, total=len(evs)), unsafe_allow_html=True)
    allcls = reveal_toggle(f"reveal_{ev['code']}", ctx, ev, wk)
    current = ev is pick_event(evs)
    if current and fav:
        st.markdown(fav_band(ctx, msess, evs, cut), unsafe_allow_html=True)

    st.markdown(sec_title("Le week-end", f"heures de {ctx['zone_name']} · ▶ = replay"), unsafe_allow_html=True)
    cat = ss.get("cat", "MotoGP")
    cat = st.segmented_control("Catégorie", CATS, default=cat, key="cat_pick", label_visibility="collapsed") or cat
    ss.cat = cat
    sess = wk.get(cat) or []
    res = {s["code"]: classification(s) for s in sess if session_done(s) or session_live(s)}
    st.markdown(f"<div class='bf{allcls}'>{timeline(ev, sess, res, zone, fav, ss.opts['indice'], stakes_of(ctx['table']), cat, ss.opts['pays'])}</div>",
                unsafe_allow_html=True)

    parts = []
    mres = res if cat == "MotoGP" else {s["code"]: classification(s) for s in msess if session_done(s) or session_live(s)}
    if any(session_done(s) and s["group"] for s in msess):
        keys = " ".join(sorted({seen_key(ev, s["group"]) for s in msess if s["group"] and session_done(s)}))
        before = standings([x for x in results if x[0]["start"] < ev["start"]], set())
        after = standings([x for x in results if x[0]["start"] <= ev["start"]], set())
        cards = []
        facts = weekend_facts(ev, msess, mres, before, after, fav)
        if facts:
            cards.append(("📝", "Le week-end en bref", "Pole, sprint, course, abandons : l'essentiel.",
                          f"<ul class='facts'>{''.join('<li>' + f + '</li>' for f in facts)}</ul>", ""))
        mv = moves_html(before, after, fav)
        if mv:
            cards.append(("📈", "Le championnat", "Le top 8 après ce week-end, et qui a bougé.", mv, ""))
        rac = mres.get("RAC") or mres.get("SPR")
        if rac and any(r["pos"] == 1 for r in rac):
            label = "Le podium du Grand Prix" if mres.get("RAC") else "Le podium du sprint"
            cards.append(("🏆", label, "Les trois premiers, sur la marche.", podium_html(rac), "full"))
        pays = ss.opts["pays"]
        ours = ours_html(ev, {c: {s["code"]: classification(s) for s in wk.get(c, []) if session_done(s) and s["code"] in ("SPR", "RAC")}
                              for c in CATS}, pays, fav)
        if ours:
            cards.append((flag(pays), f"{COUNTRY_FR[pays][1]} du week-end", "MotoGP, Moto2, Moto3 : comment s'en sont sortis les nôtres ?",
                          ours, "full"))
        if cards:
            html_cards = "".join(f"<div class='card {cls}'><span class='ico'>{ico}</span><h3>{esc(title)}</h3>"
                                 f"<div class='tease'>{esc(tease)}</div>{hidden('Voir', content, seen=keys)}</div>"
                                 for ico, title, tease, content, cls in cards)
            parts.append(sec_title("Ce qu'il faut savoir", "MotoGP · caché tant que tu ne cliques pas") + f"<div class='bf-grid'>{html_cards}</div>")
    if current:
        items = news(bucket(600))[:4]
        if items:
            parts.append(sec_title("Les actus", "", link("vue=actus", "Toutes les actus ›")) + news_html(items, ctx))
    nev = next((e for e in evs if e["start"] > max(now_utc().date(), ev["end"])), None)
    if nev and current:
        parts.append(sec_title("Et après") + next_gp_card(nev, ctx))
    rec = next((s for s in msess if s["code"] == "RAC"), None)
    if rec and session_done(rec):
        lines = []
        for r in records(rec):
            who = esc(((r.get("rider") or {}).get("full_name")) or "")
            t = esc((r.get("bestLap") or {}).get("time") or "")
            kind = {"bestLap": "Record de la piste", "poleLap": "Record de pole"}.get(r.get("type"), "")
            if kind and who:
                yr = f" ({r['year']})" if r.get("year") else ""
                lines.append(f"<li>📏 {kind} : <b>{t}</b> · {who}{yr}</li>")
        if lines:
            parts.append(sec_title("Records du circuit", "MotoGP") + f"<div class='card'><ul class='facts'>{''.join(lines)}</ul></div>")
    if parts:
        st.markdown(f"<div class='bf{allcls}'>{''.join(parts)}</div>", unsafe_allow_html=True)


def calendar_page(ctx):
    evs, zone = ctx["evs"], ctx["zone"]
    today = now_utc().date()
    nxt = next((e for e in evs if e["end"] >= today), None)
    st.markdown(sec_title(f"La saison {ctx['year']}", f"{len(evs)} Grands Prix · clique sur un GP pour l'ouvrir"), unsafe_allow_html=True)
    out = []
    for e in evs:
        m = event_meta(e)
        if e["start"] <= today <= e["end"]:
            cls, stt = "cc live", "<span class='pill live'>● En cours</span>"
        elif e["end"] < today:
            cls, stt = "cc done", "<span class='pill post'>✓ Couru</span>"
        elif e is nxt:
            cls, stt = "cc next", f"<span class='pill pre'>{countdown(datetime.combine(e['start'], datetime.min.time()).replace(tzinfo=timezone.utc))}</span>"
        else:
            cls, stt = "cc", ""
        fl = f"<img src='{esc(m['flag'])}' alt=''>" if m.get("flag") else flag(e["iso"])
        trk = f"<img class='trk' src='{esc(m['track'])}' alt=''>" if m.get("track") else ""
        img = m.get("bg") or m.get("top") or ""
        out.append(f"<a class='{cls}' href='#go?vue=gp&gp={e['code']}' style=\"--img:url('{esc(img)}')\"><span class='r'>{e['round']}</span>"
                   f"<span class='stt'>{stt}</span>{trk}<div class='b'><div class='f'>{fl} {esc(fr_range(e['start'], e['end']))}</div>"
                   f"<b>{esc(e['name'])}</b><span class='c'>{esc(e['circuit'])}</span></div></a>")
    st.markdown(f"<div class='cal'>{''.join(out)}</div>", unsafe_allow_html=True)
    if nxt:
        sess = sessions(nxt, ctx["cat_ids"]["MotoGP"])
        if sess:
            st.markdown(sec_title(f"Les horaires du {nxt['name']}", f"MotoGP · heures de {ctx['zone_name']}"), unsafe_allow_html=True)
            rows = "".join(f"<tr class='{'race' if s['code'] in ('SPR', 'RAC') else ''}'><td class='n'>{esc(s['label'])}</td>"
                           f"<td>{when_txt(s['utc'], zone)}</td><td class='note'>{countdown(s['utc']) if s['utc'] > now_utc() else ''}</td></tr>"
                           for s in sess)
            st.markdown(f"<div class='card'><table class='times'>{rows}</table></div>", unsafe_allow_html=True)
            st.download_button("📅 Ajouter les séances à mon agenda", ics_calendar(nxt, sess), file_name=f"motogp_{nxt['code']}.ics",
                               mime="text/calendar", key="ics")


def ics_calendar(ev, sess):
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//BadaBing MotoGP//FR"]
    for s in sess:
        if not s["utc"]:
            continue
        dur = 45 if s["code"] in ("SPR", "Q1", "Q2", "WUP") else 60
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
    return f"Sans spoil : arrêté avant {what} du {ev['name'] if ev else 'dernier GP'} (pas encore ouvert sur cet appareil)."


def standings_page(ctx):
    ss = st.session_state
    cat = ss.get("cat", "MotoGP")
    with st.container(key="stdbar"):
        c1, c2, c3 = st.columns([3, 4, 3], vertical_alignment="center")
    with c1:
        cat = st.segmented_control("Catégorie", CATS, default=cat, key="cat_std", label_visibility="collapsed") or cat
    ss.cat = cat
    with c2:
        kind = st.segmented_control("Classement", ["Pilotes", "Équipes", "Constructeurs"], default="Pilotes", key="std_kind",
                                    label_visibility="collapsed") or "Pilotes"
    results = ctx["results"] if cat == "MotoGP" else season_results(ctx["evs"], ctx["cat_ids"][cat])
    cut = spoil_cut(results, ss.seen, ctx["show_all"])
    if cut:
        with c3:
            if st.toggle("Inclure le dernier GP", key="inc_last"):
                cut = set()
    st.markdown(sec_title(f"Championnat {cat} {ctx['year']}", esc(cut_note(cut, ctx)) or kind), unsafe_allow_html=True)
    fav = ss.get("fav")
    if kind == "Pilotes":
        table = standings(results, cut)
        top = []
        for t in table[:3]:  # le podium du championnat, façon habillage TV
            info = rider_of(t["rid"], t["row"])
            img = f"<img src='{esc(info['photo'])}' loading='lazy' alt=''>" if info.get("photo") else ""
            first = esc(info.get("first") or t["name"].split(" ")[0])
            last = esc(info.get("last") or t["name"].split(" ")[-1])
            gap = "<span class='gp lead'>Leader</span>" if not t["gap"] else f"<span class='gp'>−{t['gap']}</span>"
            top.append(f"<a class='lb r{t['rank']}' href='#go?vue=pilotes&pilote={t['rid']}' style='--c:{esc(color_of(t['rid'], t['row']))}'>"
                       f"<span class='num'>{esc(info.get('num') or '')}</span><span class='pic'>{img}</span>"
                       f"<span class='inf'><span class='pos'>P{t['rank']}</span><span class='fn'>{flag(t['iso'])} {first}</span>"
                       f"<span class='ln'>{last}</span><span class='tm'><i></i>{esc(t['team'])}</span>"
                       f"<span class='pt'><b>{t['pts']}</b><small>pts</small>{gap}</span></span></a>")
        st.markdown(f"<div class='lead3'>{''.join(top)}</div>", unsafe_allow_html=True)
        lead = max(table[0]["pts"], 1) if table else 1
        head_ = ("<tr><th class='rk'>#</th><th class='l'>Pilote</th><th>Pts</th><th class='hm'>Écart</th><th class='hm'>Vict.</th>"
                 "<th class='hm'>Podiums</th><th class='hm'>Sprints</th><th class='hm'>Poles</th><th class='l'>3 derniers GP</th></tr>")
        rows = []
        for t in table:
            last = []
            for code, p in t["last"][-3:]:
                c = "w" if p == 1 else "pod" if p and p <= 3 else "x" if not p else ""
                last.append(f"<span class='lp {c}' title='{esc(GP.get(code, (code,))[0])}'>{p or 'ab'}</span>")
            col = esc(color_of(t["rid"], t["row"]))
            rows.append(f"<tr class='{'fav' if t['rid'] == fav else ''}'><td class='rk'>{t['rank']}</td>"
                        f"<td class='l'>{rider_cell(t['row'])}</td><td class='pts' style='--c:{col}'>{t['pts']}"
                        f"<span class='bar' style='width:{max(4, round(100 * t['pts'] / lead))}%'></span></td>"
                        f"<td class='hm'>{'–' if not t['gap'] else '-' + str(t['gap'])}</td><td class='hm'>{t['wins']}</td><td class='hm'>{t['pod']}</td>"
                        f"<td class='hm'>{t['swins']}</td><td class='hm'>{t['poles']}</td><td class='l'>{''.join(last)}</td></tr>")
        st.markdown(f"<div class='card scroll'><table class='std'>{head_}{''.join(rows)}</table></div>", unsafe_allow_html=True)
        st.caption("Sprint (MotoGP) et course comptent · « Sprints » = victoires en sprint.")
    else:
        table = team_standings(results, cut, "team" if kind == "Équipes" else "maker")
        lead = max(table[0]["pts"], 1) if table else 1
        rows = "".join(f"<tr><td class='rk'>{t['rank']}</td><td class='l'><b>{esc(t['name'])}</b>"
                       f"{'' if kind == 'Constructeurs' else ' <span class=note>' + esc(t['maker']) + '</span>'}</td>"
                       f"<td class='pts' style='--c:{MAKER_COLOR.get(t['maker'], '#e10600')}'>{t['pts']}"
                       f"<span class='bar' style='width:{max(4, round(100 * t['pts'] / lead))}%'></span></td>"
                       f"<td>{'–' if not t['gap'] else '-' + str(t['gap'])}</td></tr>" for t in table)
        st.markdown(f"<div class='card'><table class='std'><tr><th class='rk'>#</th><th class='l'>{kind[:-1]}</th><th>Pts</th>"
                    f"<th>Écart</th></tr>{rows}</table></div>", unsafe_allow_html=True)
        if kind == "Constructeurs":
            st.caption("Constructeurs : seul le meilleur pilote de chaque marque marque des points à chaque sprint et course.")


def rider_results(results, rid, cut):
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


def pos_svg(rows, color="#e10600"):
    """Courbe des places en course, GP après GP (1 en haut), avec aire dégradée."""
    pts = [(i, r[3]["pos"]) for i, r in enumerate(rows) if r[3]]
    if len(pts) < 2:
        return ""
    w, h, n = 760, 190, max(len(rows) - 1, 1)
    X = lambda i: 34 + i * (w - 54) / n
    Y = lambda p: 16 + (min(p or 25, 25) - 1) * (h - 40) / 24
    line = " ".join(f"{X(i):.0f},{Y(p):.0f}" for i, p in pts)
    area = f"{X(pts[0][0]):.0f},{h - 22} " + line + f" {X(pts[-1][0]):.0f},{h - 22}"
    dots, labels = [], []
    for i, r in enumerate(rows):
        labels.append(f"<text x='{X(i):.0f}' y='{h - 6}' fill='#6f7787' font-size='10' text-anchor='middle'>{esc(r[0]['code'])}</text>")
        rc = r[3]
        if not rc:
            continue
        p = rc["pos"]
        col = "#f3c34e" if p == 1 else "#d9dee6" if p and p <= 3 else "#ff3b30" if not p else "#9aa3b2"
        dots.append(f"<circle cx='{X(i):.0f}' cy='{Y(p):.0f}' r='{6 if p and p <= 3 else 4.5}' fill='{col}' stroke='#06070a' stroke-width='2'>"
                    f"<title>{esc(r[0]['name'])} : {nth(p) if p else 'abandon'}</title></circle>")
    grid = "".join(f"<line x1='30' x2='{w - 14}' y1='{Y(p):.0f}' y2='{Y(p):.0f}' stroke='rgba(255,255,255,.06)'/>"
                   f"<text x='4' y='{Y(p) + 4:.0f}' fill='#6f7787' font-size='10'>{nth(p)}</text>" for p in (1, 5, 10, 15, 20))
    return (f"<div class='card'><svg viewBox='0 0 {w} {h}' width='100%'><defs><linearGradient id='ga' x1='0' y1='0' x2='0' y2='1'>"
            f"<stop offset='0' stop-color='{color}' stop-opacity='.45'/><stop offset='1' stop-color='{color}' stop-opacity='0'/></linearGradient></defs>"
            f"{grid}<polygon points='{area}' fill='url(#ga)'/><polyline points='{line}' fill='none' stroke='{color}' stroke-width='2.5' "
            f"stroke-linejoin='round'/>{''.join(dots)}{''.join(labels)}</svg></div>")


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
    age = f"{info['age']} ans · " if info.get("age") else ""
    born = f"né à {esc(info.get('city'))} ({esc(info.get('country'))})" if info.get("city") else esc(info.get("country", ""))
    img = f"<img src='{esc(info['photo'])}' alt=''>" if info.get("photo") else ""
    st.markdown(f"<div class='rhead' data-n='{esc(info.get('num') or '')}' style='--c:{esc(info.get('color', '#444'))}'>{img}<div class='i'>"
                f"<div class='fn'>{flag(info.get('iso'))} {esc(info.get('first'))}</div><h1>{esc(info.get('last') or info.get('name'))}</h1>"
                f"<div class='s'>{esc(info.get('team', ''))} · {esc(info.get('maker', ''))} · {cat}</div>"
                f"<div class='s'>{age}{born}{' · en Grand Prix depuis ' + str(info['start']) if info.get('start') else ''}</div></div></div>",
                unsafe_allow_html=True)
    if cut:
        st.markdown(f"<p class='note'>{esc(cut_note(cut, ctx))}</p>", unsafe_allow_html=True)
    rows = rider_results(results, rid, cut)
    if me:
        tiles = [(nth(me["rank"]), "au championnat"), (me["pts"], "points"), (me["wins"], "victoires"), (me["pod"], "podiums"),
                 (me["swins"], "sprints gagnés"), (me["poles"], "poles"), (nth(me["best"]) if me["best"] else "–", "meilleur résultat"),
                 (me["dnf"], "abandons")]
        st.markdown("<div class='tiles'>" + "".join(f"<div class='tile'><b>{v}</b><span>{esc(l)}</span></div>" for v, l in tiles) + "</div>",
                    unsafe_allow_html=True)
    svg = pos_svg(rows, info.get("color") or "#e10600")
    if svg:
        st.markdown(sec_title("Sa saison", "Place à l'arrivée de chaque Grand Prix") + svg, unsafe_allow_html=True)
    if rows:
        def cell(r):
            if not r:
                return "–"
            return nth(r["pos"]) if r["pos"] else "<span style='color:var(--red2)'>ab.</span>"
        trs = "".join(f"<tr><td class='l'>{flag(ev['iso'])} {gp_link(ev, esc(ev['name']))}</td><td>{nth(q) if q else '–'}</td>"
                      f"<td>{cell(sp)}</td><td>{cell(rc)}</td><td class='pts'>{pts}</td></tr>" for ev, q, sp, rc, pts in reversed(rows))
        st.markdown(sec_title("GP par GP", "Grille · sprint · course · points") + f"<div class='card scroll'><table class='std'><tr>"
                    f"<th class='l'>Grand Prix</th><th>Grille</th><th>Sprint</th><th>Course</th><th>Pts</th></tr>{trs}</table></div>",
                    unsafe_allow_html=True)
    last = norm(info.get("last", ""))
    items = [n for n in news(bucket(600)) if last and len(last) > 2 and last in norm(n["title"] + " " + n["desc"])][:6]
    if items:
        st.markdown(sec_title(f"Les actus de {info.get('name')}") + news_html(items, ctx), unsafe_allow_html=True)


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
    st.markdown(sec_title(f"La grille {cat} {ctx['year']}", "Clique sur un pilote pour sa fiche · ⭐ = mon pilote"), unsafe_allow_html=True)
    pays = ss.opts["pays"]
    tiles = []
    for r in riders:
        star = "⭐ " if ss.get("fav") == r["id"] else ""
        img = f"<img src='{esc(r['photo'])}' loading='lazy' alt=''>" if r.get("photo") else ""
        rk = f"<span class='rk'>{nth(order[r['id']])}</span>" if r["id"] in order else ""
        tiles.append(f"<a class='rt' href='#go?vue=pilotes&pilote={r['id']}' style='--c:{esc(r.get('color', '#444'))}'>"
                     f"<span class='n'>{esc(r.get('num') or '')}</span>{rk}{img}<div class='b'><small>{star}{flag(r['iso'])} {esc(r.get('first'))}</small>"
                     f"<b>{esc(r.get('last') or r.get('name'))}</b><span>{esc(r.get('team', ''))}</span></div></a>")
    if not tiles:
        st.info("Liste des pilotes indisponible pour le moment.")
    st.markdown(f"<div class='rg'>{''.join(tiles)}</div>", unsafe_allow_html=True)
    ours = [r for r in ctx["riders"].values() if r.get("iso") == pays]
    if ours:
        lines = "".join(f"<div class='mv'>{head(r, 34)}<span class='rn2'>{link('vue=pilotes&pilote=' + r['id'], esc(r['name']), 'rl')}</span>"
                        f"<span class='note' style='margin-left:auto'>{esc(r.get('cat'))} · {esc(r.get('team'))}</span></div>"
                        for r in sorted(ours, key=lambda r: CATS.index(r.get("cat", "MotoGP"))))
        st.markdown(sec_title(f"{COUNTRY_FR[pays][1]} en Grand Prix", "Toutes catégories") + f"<div class='card'>{lines}</div>",
                    unsafe_allow_html=True)


def img_proxy(u):
    """Image d'un article. Chargée sans « référent » (referrerpolicy) : Paddock GP refuse ses images affichées depuis un
    autre site (403), mais les sert sans référent. Les relais d'images (wsrv.nl) sont refusés aussi."""
    return u


def news_html(items, ctx):
    spoil = ctx["spoil_from"]
    out = []
    for n in items:
        pub = parse_dt(n["pub"])
        when = rel_time(pub) if pub else ""
        img = (f"<div class='im'><img src='{esc(img_proxy(n['img']))}' loading='lazy' referrerpolicy='no-referrer' alt=''></div>"
               if n.get("img") else "<div class='im'></div>")
        card = (f"<a class='nw' href='{esc(n['link'])}' target='_blank' rel='noopener'>{img}<div class='b'>"
                f"<div class='t'>{esc(n['title'])}</div><div class='d'>{esc(n['desc'][:170])}</div>"
                f"<div class='m'>{esc(n['source'])} · {esc(when)}</div></div></a>")
        if spoil and pub and pub >= spoil[0]:
            i = uid("n")
            _, ev, s = spoil
            out.append(f"<div class='nwx'><input type='checkbox' id='{i}' class='tg'><label for='{i}'>⚠️ Peut spoiler"
                       f"<small>publié après le début : {esc(s['label'])} · {esc(ev['name'])}</small><em>👁 Afficher</em></label>{card}</div>")
        else:
            out.append(card)
    return f"<div class='news'>{''.join(out)}</div>"


def news_page(ctx):
    ss = st.session_state
    items = news(bucket(600))
    st.markdown(sec_title("Les actus", "Motorsport.com France et Paddock GP · en français"), unsafe_allow_html=True)
    spoil = ctx["spoil_from"]
    if spoil:
        _, ev, s = spoil
        st.markdown(f"<div class='shield'>🛡️ <b>Bouclier anti-spoil</b> : les articles publiés après le début de "
                    f"<b>{esc(s['label'])}</b> du {esc(ev['name'])} sont floutés tant que tu n'as pas ouvert ce GP. "
                    f"Un clic sur un article l'affiche.</div>", unsafe_allow_html=True)
    who = st.segmented_control("Filtre", ["Tout", "MotoGP", "Moto2 / Moto3"], default="Tout", key="news_f",
                               label_visibility="collapsed") or "Tout"
    if who == "MotoGP":
        items = [n for n in items if not re.search(r"moto ?[23]\b", n["title"], re.I)]
    elif who == "Moto2 / Moto3":
        items = [n for n in items if re.search(r"moto ?[23]\b", n["title"] + " " + n["link"], re.I)]
    allcls = " all" if ctx["show_all"] else ""
    if spoil and not ctx["show_all"] and st.toggle("👁 Tout dévoiler (même les actus qui peuvent spoiler)", key="news_all"):
        allcls = " all"
    if not items:
        st.markdown("<p class='note'>Aucune actu pour le moment.</p>", unsafe_allow_html=True)
        return
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
    o = dict(ss.opts)
    pays = st.radio("Pays (« nos pilotes », vidéos)", ["FR", "CA"], index=["FR", "CA"].index(o["pays"]), horizontal=True,
                    format_func=lambda x: "🇫🇷 France" if x == "FR" else "🇨🇦 Canada", key="set_pays")
    idx = o["indice"]
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
        ss.fav = None  # plus de pilote favori (v-m008)
        ss.seen = read_seen(cookies.get("gp_vues", ""))
        ss.hello_done = bool(cookies.get("gp_hello"))
        q = st.query_params
        ss.view = q.get("vue") if q.get("vue") in VIEWS and q.get("vue") != "brief" else "gp"
        ss.gp = q.get("gp") or None
        ss.rider = q.get("pilote") or None
        ss.cat = q.get("cat") if q.get("cat") in CATS else "MotoGP"
    if go_cmd:
        apply_go(go_cmd)

    season = current_season()
    if not season:
        st.error("Le site officiel MotoGP ne répond pas. Réessaie dans quelques minutes.")
        return
    cat_ids = categories(season["id"])
    CAT_OF.update({v: k for k, v in cat_ids.items()})
    evs = events(season["id"], bucket(600))
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

    st.markdown("<div class='brand'><span class='lg'>Bada Bing <i>MotoGP</i></span></div>", unsafe_allow_html=True)


    with st.container(key="tabs"):
        cols = st.columns([1] * len(TABS) + [0.32])
        with cols[-1].popover("", icon=":material/settings:", use_container_width=True, help="Réglages"):
            settings_panel(ctx)
        for (key, label, icon), col in zip(TABS, cols):
            if col.button(label, icon=icon, key=f"tab_{key}", use_container_width=True,
                          type="primary" if ss.view == key else "tertiary"):
                ss.view = key
                if key == "pilotes":
                    ss.rider = None
                if key == "gp":  # re-cliquer « Grand Prix » = le week-end en cours
                    ss.gp = None
                st.rerun()

    view = ss.view
    st.query_params.clear()
    if view != "gp":
        st.query_params["vue"] = view
    if view == "gp" and ss.get("gp"):
        st.query_params["gp"] = ss.gp
    if view == "pilotes" and ss.get("rider"):
        st.query_params["pilote"] = ss.rider

    {"gp": gp_page, "calendrier": calendar_page, "classement": standings_page,
     "pilotes": riders_page, "actus": news_page}[view](ctx)

    page_footer()
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
