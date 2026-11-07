# Bada Bing MotoGP

Les Grands Prix MotoGP **sans spoil**, sur le modèle du site Bada Bing NBA : Le brief (page d'accueil), Grand Prix (séance par
séance, MotoGP / Moto2 / Moto3), Calendrier (+ agenda .ics), Classement (pilotes, équipes, constructeurs), Pilotes, Actus.

## Mettre en ligne (Streamlit Cloud)

share.streamlit.io → **Create app** → dépôt `kikitmt-bit/bada-bing-motogp`, branche `main`, fichier `app.py`. Aucune clé.

## Tester en local

```
pip install -r requirements.txt
python -m streamlit run app.py --server.port 8541
python tests/test_sans_spoil.py
```
