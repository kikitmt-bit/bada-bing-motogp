# Bada Bing MotoGP : notes pour Claude

Site Streamlit (`app.py`) en français, frère du site NBA (dépôt kikitmt-bit/bada-bing-nba) : les Grands Prix **sans spoil**.
Réponses à l'utilisateur en français. Incrémenter `APP_VERSION` à chaque changement.

## Données
- API officielle sans clé `api.motogp.pulselive.com/motogp/v1` : `results/seasons`, `results/events?seasonUuid=`,
  `results/categories?seasonUuid=`, `results/sessions?eventUuid=&categoryUuid=`, `results/session/<id>/classification`,
  `categories?seasonYear=` + `teams?categoryUuid=&seasonYear=` (pilotes : photo, numéro, couleur d'équipe).
  Les identifiants de catégorie diffèrent entre `results/categories` et `categories`. Id pilote = `riders_api_uuid` des résultats.
- L'API publie parfois des horaires bouche-trou hors des dates du GP : `sessions` les recale sur le week-end (« prov. »).
- Séances et classements finis gardés dans `.cache/` (ne bougent plus).
- Actus : RSS Motorsport.com France + Paddock GP (filtré MotoGP). Vidéos : chaîne officielle MotoGP (flux, sinon recherche
  YouTube sans clé), boutons sans titre (les titres spoilent).

## Pas de spoil
- Groupes qui spoilent par GP : `q` (qualifs), `s` (sprint), `r` (course) ; clé « 26QATr », cookie `gp_vues` (+ localStorage).
- Ouvrir la page d'un GP le marque vu. `spoil_cut` : classements, fiches et brief s'arrêtent avant les groupes non vus du
  DERNIER GP couru ; interrupteur « Inclure le dernier GP » ; ⚙️ « Toujours montrer les résultats » (classe `.all`).
- Résultats derrière des cases à cocher CSS (`hidden`). Ne jamais écrire `checked` dans le HTML.
- Actus publiées après le début de la 1re séance non vue du dernier GP : floutées (`spoil_since`).
- Indice « à voir » (`watch_score`) : flou, icône + nom seulement avant de révéler.
- `python tests/test_sans_spoil.py` avant chaque envoi.

## Navigation
Onglets `TABS` (`?vue=brief|gp|calendrier|classement|pilotes|actus`, `&gp=QAT`, `&pilote=<id>`). Liens internes `#go?…`
interceptés par `page_script` → champ caché `go_to` → `apply_go` (comme le site NBA). Réglages : `gp_fav`, `gp_opt`, relus
dans le navigateur et renvoyés (`restore=1`) car Streamlit Cloud ne transmet pas les cookies écrits par la page.
