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
- `events?seasonYear=` (`events_meta`, `event_meta`) : photo du GP (`bg`, `top`), drapeau, tracé blanc (`track`), longueur,
  virages, ligne droite, tours, et HORAIRES OFFICIELS (`times`, utilisés par `sessions` pour les séances à venir).
- Actus : RSS Motorsport.com France + Paddock GP (filtré MotoGP).
- **Replays (`replays_html`, `find_video`) : CANAL+ Sport d'abord** (chaîne UC8ggH3zU61XO0nMskSQwZdA, titres « Le résumé de la
  course sprint du Grand Prix du Qatar 2026 - MotoGP » / « Le résumé du Grand Prix … - Moto2 »), vidéo officielle MotoGP en second
  (Canada : l'inverse). Les résumés Canal+ sont réservés à la France : INVISIBLES depuis un serveur hors de France (Streamlit Cloud
  = USA) sauf par l'API YouTube (`[youtube] api_key`, `channel_uploads`). Sans clé ni vidéo trouvée : le bouton ouvre la recherche
  dans la chaîne Canal+ (`canal_search_url`) depuis le navigateur du visiteur. Vidéos trouvées gardées dans `.cache/videos.json`.
  Titres jamais affichés (ils spoilent). Le flux RSS YouTube répondait 404/500 en nov. 2026.

## Pas de spoil
- Groupes qui spoilent par GP : `q` (qualifs), `s` (sprint), `r` (course) ; clé « 26QATr », cookie `gp_vues` (+ localStorage).
- Ouvrir la page d'un GP le marque vu. `spoil_cut` : classements, fiches et brief s'arrêtent avant les groupes non vus du
  DERNIER GP couru ; interrupteur « Inclure le dernier GP » ; ⚙️ « Toujours montrer les résultats » (classe `.all`).
- Résultats derrière des cases à cocher CSS (`hidden`). Ne jamais écrire `checked` dans le HTML.
- « 👁 Tout dévoiler sur cette page » (`reveal_toggle`, brief et page GP) : ajoute la classe `.all` aux blocs de la page.
- Actus publiées après le début de la 1re séance non vue du dernier GP : floutées (`spoil_since`).
- Indice « à voir » (`watch_score`) : flou, icône + nom seulement avant de révéler.
- `python tests/test_sans_spoil.py` avant chaque envoi.

## Navigation
Onglets `TABS` (`?vue=brief|gp|calendrier|classement|pilotes|actus`, `&gp=QAT`, `&pilote=<id>`). Liens internes `#go?…`
interceptés par `page_script` → champ caché `go_to` → `apply_go` (comme le site NBA). Réglages : `gp_fav`, `gp_opt`, relus
dans le navigateur et renvoyés (`restore=1`) car Streamlit Cloud ne transmet pas les cookies écrits par la page.

## Design (v-m002, demande de Kiki : « furieux, beau, élégant, précis », penser moto, pas NBA)
Carbone sombre + rouge MotoGP (#e10600), titres Saira Condensed 900 italique en capitales, vibreurs rouge/blanc (`.kerb`),
barres penchées (`.sec`). En-tête du GP (`gp_hero`) : photo du GP, nom en grand (2e ligne en contour), drapeau, tracé lumineux,
compte à rebours, bande de chiffres du circuit. Week-end en frise vendredi/samedi/dimanche (`timeline`). Cartes replays avec la
photo du GP. Calendrier en cartes photo + tracé. Classement : top 3 en cartes avec photo détourée + barres de points.
Pilotes : cartes photo détourée aux couleurs de l'équipe, numéro géant. Noms « M. Marquez » (`short_name` : Marc et Alex).
Mêmes règles d'affichage que la NBA : infos courtes sur une ligne (`nowrap` + `ellipsis`), `min-width:0` dans les grilles.
