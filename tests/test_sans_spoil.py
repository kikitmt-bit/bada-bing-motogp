"""Vérifie qu'un résultat caché ne se lit pas dans la page : python motogp/tests/test_sans_spoil.py"""
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402

NOW = datetime.now(timezone.utc)
EV = {"id": "e1", "code": "QAT", "name": "GP du Qatar", "round": 20, "circuit": "Lusail", "place": "Lusail", "iso": "QA",
      "start": NOW.date() - timedelta(days=2), "end": NOW.date(), "status": "CURRENT", "sponsor": ""}
EV0 = dict(EV, id="e0", code="MAL", name="GP de Malaisie", start=EV["start"] - timedelta(days=7), end=EV["end"] - timedelta(days=7),
           status="FINISHED")


def sess(code, hours_ago, status="FINISHED", ev_shift=0):
    return {"id": code + str(ev_shift), "code": code, "label": app.SESSION_FR[code], "short": code, "status": status,
            "utc": NOW - timedelta(hours=hours_ago + ev_shift), "group": app.GROUP.get(code), "track": "Dry", "weather": "Soleil",
            "air": "", "ground": ""}


def row(pos, name, gap, pts):
    return {"pos": pos, "rid": name.lower(), "name": name, "num": pos, "iso": "ES", "team": "Team " + name, "maker": "Ducati",
            "time": "40:00.000" if pos == 1 else "", "gap": gap, "laps_down": 0, "laps": 20, "points": pts,
            "status": "INSTND" if pos else "OUTSTND", "best": "1:50.000", "speed": 0}


RAC = [row(1, "Zorro", 0, 25), row(2, "Bernardo", 0.1, 20), row(3, "Garcia", 0.3, 16), row(4, "Diego", 3, 13), row(None, "Tornado", 0, 0)]
RAC0 = [row(1, "Bernardo", 0, 25), row(2, "Zorro", 4, 20), row(3, "Garcia", 6, 16), row(4, "Diego", 9, 13)]


def visible(h):
    """HTML tel qu'on le voit avant de cliquer : on retire les blocs cachés (.hbx)."""
    while "<div class='hbx'>" in h:
        i = h.index("<div class='hbx'>")
        depth, j = 0, i
        while True:
            o, c = h.find("<div", j), h.find("</div>", j)
            if o != -1 and o < c:
                depth, j = depth + 1, o + 4
            else:
                depth, j = depth - 1, c + 6
                if depth == 0:
                    break
        h = h[:i] + h[j:]
    return re.sub(r"<[^>]+>", " ", h)


def main():
    errors = []
    tile = app.session_tile(EV, sess("RAC", 3), RAC, app.ZoneInfo("Europe/Paris"), None, True, 0)
    vis = visible(tile)
    for name in ("Zorro", "Bernardo", "Garcia", "Tornado", "+0,100", "Abandon"):
        if name in vis:
            errors.append(f"tuile de course : « {name} » visible sans cliquer")
    if "Zorro" not in tile:
        errors.append("tuile de course : le résultat caché manque")
    results = [(EV0, [sess("RAC", 0, ev_shift=170)], {"RAC": RAC0}), (EV, [sess("Q2", 26), sess("RAC", 3)], {"RAC": RAC})]
    cut = app.spoil_cut(results, set(), False)
    if cut != {app.seen_key(EV, "q"), app.seen_key(EV, "r")}:
        errors.append(f"coupure inattendue : {cut}")
    table = app.standings(results, cut)
    if table[0]["name"] != "Bernardo" or table[0]["pts"] != 25:
        errors.append("le classement sans spoil compte le dernier GP")
    seen = {app.seen_key(EV, "q"), app.seen_key(EV, "r")}
    if app.spoil_cut(results, seen, False):
        errors.append("un GP ouvert devrait compter")
    if app.standings(results, set())[0]["pts"] != 45:
        errors.append("classement complet faux")
    if app.spoil_cut(results, set(), True):
        errors.append("« Toujours montrer les résultats » devrait tout compter")
    sp = app.spoil_since(results, set(), False)
    if not sp or sp[1]["code"] != "QAT":
        errors.append("le bouclier des actus devrait partir du GP du Qatar")
    for e in errors:
        print("ÉCHEC :", e)
    print("OK : rien ne fuit." if not errors else f"{len(errors)} problème(s).")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
