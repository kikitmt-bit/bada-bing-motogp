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
  virages, ligne droite, tours, et HORAIRES OFFICIELS (`times`, utilisés par `sessions` pour TOUTES les séances : l'API `results/sessions` donne l'heure
  LOCALE du circuit marquée +00:00, fausse de 3 h au Qatar, 8 h en Malaisie ; ça décalait le bouclier anti-spoil des actus).
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
`https://bada-bing-motogp.streamlit.app/?robot=1` et clique « Yes, get this app back up » si le site dort. Échoue (rouge) si la page
ouverte n'est pas le site (adresse à corriger dans `SITE_URL`). Lancement à la main : onglet Actions → « Réveil du site » → Run workflow.

## v-m007 : bannière du GP refaite (carte blanche de Kiki)
`gp_hero` = deux panneaux (`.hero2`) coupés en biais par un vibreur (`.cut`) : à gauche la photo du GP, vive (fondu seulement
en bas), manche + pays en haut, nom + dates + compte à rebours en bas ; à droite la FICHE CIRCUIT façon plan d'ingénieur (`.bp`,
fond gris graphite quadrillé, séparé de la photo par un trait rouge vertical net ; v-m008 : plus de vibreur en biais ni de balayage) : tracé en grand au centre, 4 chiffres dans les coins avec équerres (longueur,
virages G/D, ligne droite, tours + sprint), pied : distance de course + record du tour (`lap_record`, seulement s'il date d'avant
ce GP : sinon spoil). Plus de bande de chiffres en dessous. Téléphone : photo puis fiche, l'une sous l'autre.

## v-m008 (retours de Kiki)
- Plus de pilote favori (inutile en MotoGP : tout le monde suit les mêmes courses) : réglage, bouton « Suivre », filtre d'actus retirés.
- Plus de bandeau de bienvenue (il revenait sans cesse sur Streamlit Cloud).
- Classement : top 3 en podium (2 · 1 · 3, le 1er plus haut). Classe « pd » des 3 derniers GP renommée `pod` (conflit avec le podium).
- Téléphone : onglets sur 2 lignes (3 par ligne) ; tableau du classement réduit à # · pilote · pts · 3 derniers GP (`.hm` masqué).

## v-m009
- Tuiles : exactement 3 pilotes visibles (`.mini .ml` max-height 102 px = 3 lignes de 30 px), le reste en faisant défiler (arrêt net par ligne).
- Bannière (PC) : le plan déborde de 140 px sur la photo avec un fondu (`mask-image`), plus de trait rouge. Téléphone : inchangé (trait rouge horizontal).

## v-m010
- Fiche circuit : tracé recadré au plus près par le serveur (`track_tight`, Pillow, image intégrée, `.cache/trk_*.txt`), affiché dans
  une zone réservée selon sa forme : tracé haut = entre les 2 colonnes de chiffres (`.bp-tb.tall`), tracé large (ratio ≥ 1,25) =
  entre les 2 rangées (`.bp-tb.wide`) : le plus grand possible sans chevauchement. Pillow dans requirements.txt.
- Indice « à voir » (flammes) retiré : un seul GP à la fois, on le regarde de toute façon (réglage retiré aussi).

## v-m012 : podium du championnat façon habillage TV (`.lead3`, `.lb`)
3 bandeaux P1 · P2 · P3 (liseré or / argent / bronze) : fond sombre coupé en biais par la couleur de l'équipe, prénom + NOM,
équipe, points + écart (« Leader » pour le 1er) ; à droite le pilote cadré tête + buste devant son numéro géant en contour.
Lien vers la fiche du pilote. Téléphone : les 3 bandeaux empilés.

## v-m013 : replays Canal+ de TOUS les GP
Canal+ Sport publie des dizaines de vidéos par jour : la liste des dernières mises en ligne (`channel_uploads`, 6 pages = 300)
n'atteint que les GP récents. Pour les autres : `api_search` (API YouTube search, channelId + dates du GP, 100 unités de quota,
cache 6 h, appelée seulement pour un GP affiché dont la vidéo n'est pas connue ; vidéo trouvée gardée dans `.cache/videos.json`).
Lieux acceptés dans les titres : `GP_FR_ALIAS` (« Amériques », « Catalogne », « Saint-Marin »…). La clé est partagée avec le site NBA.

- Page de contrôle des replays : `?diag=BRA` (un GP : clé présente ?, réponse HTTP de YouTube, titres renvoyés, vidéo trouvée).
  Recherche API avec `regionCode=FR` (sinon les vidéos réservées à la France sont écartées : serveur aux USA).

## v-m016 : refonte téléphone (audit, étapes 1 à 4)
Tout dans le bloc `@media (max-width: 700px)` de fin de CSS ; le PC ne change presque pas.
- Onglets (téléphone) = barre FIXÉE à `--tabtop` px du haut (66 par défaut : la bande de Streamlit Cloud descend jusqu’à ~65 px, mesuré par Kiki avec ?barre=36 ; réglable par `?barre=`), PAS à 0 ; le titre « Bada Bing MotoGP » est fixé dans la bande au-dessus (non cliquable, donc sans souci) : sur Streamlit Cloud, la bande transparente du haut
  (Share, étoile, crayon, GitHub) avale les touchers en haut de l'écran, et « Manage app » couvre le bas droit. Icône au-dessus du mot, onglet actif en rouge, roue des réglages en petite icône grise au bout.
- Bannière compacte : photo moins haute, fiche circuit avec les 4 chiffres sur une ligne sous le tracé.
- « Tout dévoiler » (`.st-key-tools`) = pastille flottante en bas à GAUCHE (le coin bas droit est pris par « Manage app »).
- Bloc « Les replays » sous la bannière (`replays_block`, `.rpk` : Sprint, Grand Prix, Moto2, Moto3), plus de boutons dans les tuiles.
- Séances en lignes compactes : bouton « Voir » à droite de la ligne (`.ses .hbl` en absolu), météo une fois par jour (`.dwx`).
- « Ce qu'il faut savoir » (`.know`) en lignes avec « Voir ». Libellé des blocs cachés dans `<em class='lt'>`.
- Actus : `news_html` = actus sûres en clair + UN bloc « 🛡️ N actus masquées · Afficher quand même » (plus de cartes floutées) ;
  liste compacte sur téléphone (image à gauche, titre).
- Calendrier : à venir d'abord, horaires du prochain GP, puis « Déjà courus » (lignes compactes sur téléphone).
- Classement : podium en 3 mini-cartes côte à côte, noms complets sur 2 lignes, interrupteur « Compter le Qatar ».
- Fiche pilote : en-tête vertical (nom jamais coupé, `clamp`), stats en rangée qui défile, courbe lisible (`.xl` masqués,
  `.yl` agrandis), « GP par GP » avec noms courts (`gp_short`). Lien « ‹ Tous les pilotes » au style du site.
- Bande de navigation des GP : noms courts (« ‹ Malaisie · 📅 · Portugal › »).

## v-m023 : audit PC
- « Tout dévoiler » sur la ligne de navigation au-dessus de la bannière (`gptop`, colonnes) ; sur téléphone il reste flottant.
- Replays : tuiles avec la photo du GP (`.rpk` + `--img`), en flex (2 max par ligne quand il y en a peu).
- Frise sur PC (v-m025, demande de Kiki) : UNE LIGNE PAR JOUR, le jour à gauche (`.dh` en colonne de 120 px), ses séances à droite (4 max).
- Classement équipes / constructeurs : mêmes largeurs de colonnes (`table.std.tt`, points 26 %, écart 110 px).
- « Ce qu'il faut savoir » : grille 2 × 2 régulière sur PC. Classement : zéros en gris pâle (`zp`), barre de points plus épaisse.
- Calendrier : horaires en carte compacte (`.card.tms`), bouton agenda au style du site. Fiche pilote : photo tête + buste agrandie,
  note sans spoil en étiquette (`.cutpill`). Page élargie à 1440 px, pied de page plus lisible.

## v-m027 : résumé du GP du Qatar non trouvé
`_canal_match` assoupli : l'année peut manquer si la vidéo est publiée pendant le GP (`_in_window`), « résumé » / « temps forts »,
autre saison et qualifs/essais/pole exclus ; 3e recherche de secours (`api_search` « <GP> <catégorie> »). `?diag=QAT` liste aussi
toutes les vidéos Canal+ qui parlent du GP (pour repérer un titre non reconnu).
