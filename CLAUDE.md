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
- Plus d'onglet « Le brief » (v-m004) : « Grand Prix » est la page d'accueil (week-end en cours), avec mon pilote, la frise,
  « Ce qu'il faut savoir », les actus et le prochain GP. Un GP est noté VU quand on dévoile un de ses résultats (le navigateur
  écrit la clé `data-k` dans `gp_vues`, pris en compte au chargement suivant) ou avec « Tout dévoiler » (tout de suite). `spoil_cut` : classements, fiches et brief s'arrêtent avant les groupes non vus du
  DERNIER GP couru ; interrupteur « Inclure le dernier GP » ; ⚙️ « Toujours montrer les résultats » (classe `.all`).
- Résultats derrière des cases à cocher CSS (`hidden`). Ne jamais écrire `checked` dans le HTML.
- « 👁 Tout dévoiler sur cette page » (`reveal_toggle`, brief et page GP) : ajoute la classe `.all` aux blocs de la page.
- Actus publiées après le début de la 1re séance non vue du dernier GP : floutées (`spoil_since`).
- Indice « à voir » (`watch_score`) : flou, icône + nom seulement avant de révéler.
- `python tests/test_sans_spoil.py` avant chaque envoi.

## Navigation
Onglets `TABS` (`?vue=gp|calendrier|classement|pilotes|actus`, `&gp=QAT`, `&pilote=<id>`). Liens internes `#go?…`
interceptés par `page_script` → champ caché `go_to` → `apply_go` (comme le site NBA). Réglages : `gp_fav`, `gp_opt`, relus
dans le navigateur et renvoyés (`restore=1`) car Streamlit Cloud ne transmet pas les cookies écrits par la page.

## Design (v-m002, demande de Kiki : « furieux, beau, élégant, précis », penser moto, pas NBA)
Carbone sombre + rouge MotoGP (#e10600), titres Saira Condensed 900 italique en capitales, vibreurs rouge/blanc (`.kerb`),
barres penchées (`.sec`). En-tête du GP (`gp_hero`) : photo du GP, nom en grand (2e ligne en contour), drapeau, tracé lumineux,
compte à rebours, bande de chiffres du circuit. Week-end en frise vendredi/samedi/dimanche (`timeline`). Cartes replays avec la
photo du GP. Calendrier en cartes photo + tracé. Classement : top 3 en cartes avec photo détourée + barres de points.
Pilotes : cartes photo détourée aux couleurs de l'équipe, numéro géant. Noms « M. Marquez » (`short_name` : Marc et Alex).
Mêmes règles d'affichage que la NBA : infos courtes sur une ligne (`nowrap` + `ellipsis`), `min-width:0` dans les grilles.

## v-m004 (retours de Kiki)
- Tuiles de séance : classement COMPLET dans la tuile (3 visibles, le reste en faisant défiler `.mini .ml`), plus de
  « classements complets » en bas. Replays dans les tuiles de course (`replay_buttons` : ▶ Résumé CANAL+ / ▶ MotoGP).
- Grand tracé dans l'en-tête et le calendrier ; photos du calendrier = photo `bg` du GP, peu assombrie.
- Classement : top 5 en cartes verticales (photo au centre, numéro derrière). Têtes rondes cadrées visage + torse (`.hd img`).
- Actus : une image par article (RSS, sinon `content:encoded`, sinon og:image de la page : `og_image`, `.cache/og.json`).
- Marque sans les barres ni « sans spoil » ; barre d'outils Streamlit (Share, étoile, crayon, GitHub) cachée en CSS.

## v-m005 (retours de Kiki)
- Plus de liste déroulante des GP (doublon avec le calendrier) : bande `.gpbar` « ‹ GP précédent · 📅 Tous les GP · GP suivant › ».
- Tracé du circuit : BLANC, sans néon, à côté du titre (`.ttl .trk`) et aligné avec le nom dans les cartes du calendrier.
  L'image du tracé a ~25 % de marge transparente : on l'agrandit avec des marges négatives.
- Boutons de tri (`st.segmented_control`) restylés en onglets penchés (`[data-testid="stButtonGroup"]`) ; classement :
  catégorie + type sur une seule ligne (`stdbar`).
- Photos des pilotes : `fix_photos` teste chaque photo et prend celle de la fiche du pilote (`riders/<id>`, carrière) si
  absente ou cassée (Chantra : « motogp.com//riders » en 404 ; Nakagami : absente).
- Images des actus : Paddock GP refuse ses images affichées depuis un autre site (403 avec un référent) et refuse les relais
  (wsrv.nl) : `<img referrerpolicy='no-referrer'>` en direct ; pour Paddock GP, l'og:image de l'article. Visuel de secours.

## v-m006 (retours de Kiki)
- Replays : SEULEMENT les résumés CANAL+ Sport (sprint et Grand Prix, + courses Moto2/Moto3), plus de bouton MotoGP officiel.
- Roue des réglages : icône discrète au bout des onglets (`st.popover` dans `tabs`), plus en haut à droite.
- Un résultat dévoilé RESTE dévoilé (plus de « 🙈 Cacher ») ; seul « Tout dévoiler » se désactive.
- Pilotes hors de la liste des équipes (remplaçants, wild-cards : Aleix Espargaró) : `rider_extra` lit leur fiche (photo, couleurs).

## Réveil du site
Robot GitHub `.github/workflows/reveil-site.yml` (copie de celui du site NBA) : toutes les heures, Playwright ouvre
`https://badabingmotogp.streamlit.app/?robot=1` et clique « Yes, get this app back up » si le site dort. Échoue (rouge) si la page
ouverte n'est pas le site (adresse à corriger dans `SITE_URL`). Lancement à la main : onglet Actions → « Réveil du site » → Run workflow.

## v-m007 : bannière du GP refaite (carte blanche de Kiki)
`gp_hero` = deux panneaux (`.hero2`) coupés en biais par un vibreur (`.cut`) : à gauche la photo du GP, vive (fondu seulement
en bas), manche + pays en haut, nom + dates + compte à rebours en bas ; à droite la FICHE CIRCUIT façon plan d'ingénieur (`.bp`,
fond bleu nuit quadrillé, balayage rouge animé) : tracé en grand au centre, 4 chiffres dans les coins avec équerres (longueur,
virages G/D, ligne droite, tours + sprint), pied : distance de course + record du tour (`lap_record`, seulement s'il date d'avant
ce GP : sinon spoil). Plus de bande de chiffres en dessous. Téléphone : photo puis fiche, l'une sous l'autre.
