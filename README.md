# politics-viz

Un radar des intérêts financiers déclarés par les responsables publics
français. Il croise les déclarations à la HATVP avec les votes de
l'Assemblée nationale, les rôles de rapporteur, les commissions, le
registre des déports et le lobbying déclaré, et fait remonter ce qui
mérite un second regard.

Un **signal** est une coïncidence documentée entre un intérêt déclaré et
un acte public. Ce n'est jamais la preuve d'une faute : chaque signal
renvoie à la déclaration et aux scrutins d'origine pour être vérifié.

## Démarrer

```bash
uv venv --python 3.12 && uv pip install -e '.[dev]'   # ou : python -m venv .venv && pip install -e '.[dev]'
.venv/bin/pv sync      # télécharge ~300 Mo de sources, construit la base (≈ 1 min 30)
.venv/bin/pv serve     # http://127.0.0.1:8000
.venv/bin/pytest
```

`pv sync` reconstruit toujours la base de zéro dans un fichier temporaire,
puis la met en place d'un coup : le site ne lit jamais une base à moitié
construite, et une synchronisation qui échoue laisse la précédente intacte.
Les téléchargements sont conditionnels (rien n'est retéléchargé si la
source n'a pas changé) ; `--offline` n'utilise que le cache, `--force`
l'ignore. À lancer une fois par jour en cron.

| Variable | Rôle | Défaut |
|---|---|---|
| `PV_DATA_DIR` | cache des sources, base, archive | `./data` |
| `POLITICS_DB` | fichier SQLite lu par le site | `$PV_DATA_DIR/pv.sqlite` |
| `PV_LEGACY_DB` | archive des déclarations retirées | `$PV_DATA_DIR/archive/legacy.sqlite` |

## Sources

| Source | Ce qu'on en tire |
|---|---|
| HATVP, `declarations.xml` | participations (valeur, nombre de titres, part du capital), activités rémunérées, mandats sociaux, employeur du conjoint, collaborateurs ; patrimoine des ministres |
| HATVP, `liste.csv` | identifiant stable de chaque déclarant, lien vers le PDF d'origine, identifiant Assemblée ou Sénat, dépôts pas encore publiés |
| HATVP, répertoire des représentants d'intérêts | 4 100 organisations, 113 000 actions de lobbying avec leur objet, dépenses par exercice |
| Assemblée nationale, acteurs et organes | députés en exercice, groupes, commissions, Gouvernement, **registre des déports** |
| Assemblée nationale, scrutins (16e et 17e législatures) | 14 000 scrutins publics, position de chaque député, écart à la position de son groupe |
| Assemblée nationale, dossiers législatifs | rapporteurs, auteurs, commission saisie |
| Sénat, `ODSEN_GENERAL` | sénateurs en exercice, groupes, commissions |

Ce qui n'existe pas en données ouvertes, et que le site ne prétend donc pas
montrer : la date d'une opération sur titres (seul l'écart entre deux
déclarations est connu), le patrimoine des parlementaires (consultable en
préfecture uniquement), les avoirs des proches, les scrutins du Sénat.
La page `/methode` du site détaille ces limites.

## Comment ça marche

```
sources ──► people ──► parliament ──► lobby ──► changes ──► signals ──► summary
            │          │              │         │           │
            │          │              │         │           └ croise tout, note de 0 à 100
            │          │              │         └ écarts entre déclarations successives
            │          │              └ lie organisations, sociétés, employeurs et textes
            │          └ rôles, déports, scrutins ; reconstitue les textes depuis les intitulés
            └ une personne par déclarant, quelle que soit l'orthographe selon la source
```

Trois décisions structurent le reste :

- **L'identité d'abord.** Une personne est rattachée par l'identifiant de
  l'index HATVP, sinon par nom et date de naissance exacts. Aucun
  rapprochement approximatif n'est fait sans seconde clé indépendante : un
  doublon vaut mieux qu'une fusion à tort.
- **Les sociétés par référentiel.** Les noms sont saisis à la main
  (« PEA Total », « TotalEnergies SE »). `pv/companies.py` tient un
  référentiel de plus de 220 sociétés avec leurs variantes, leur secteur et leur
  code de cotation. Les noms occultés par la HATVP ne sont jamais rapprochés.
- **Trois forces de lien, pas une.** Société nommée dans l'intitulé du
  texte, lobbying déclaré mentionnant le texte, ou simple secteur commun.
  Les lois de finances ne produisent aucun lien sectoriel.

## Arborescence

```
pv/
  config.py          chemins et URL des sources
  db.py              schéma SQLite
  text.py            normalisation (accents, dates, nombres)
  companies.py       référentiel de sociétés et canonicalisation des noms
  sectors.py         secteurs : textes, sociétés, domaines de lobbying, commissions
  labels.py          libellés et formats français
  sources/           lecture de chaque fichier amont (fetch, hatvp, assemblee, senat, agora, legacy)
  pipeline/          people, parliament, lobby, billmatch, changes, signals, run
  web/               app FastAPI, requêtes, gabarits Jinja, CSS et JS sans étape de build
tests/               un jeu de sources miniature passé dans tout le pipeline
docs/superpowers/    spécifications de la première version (historique)
```

## Docker

```bash
docker build -t politics-viz .
docker run --rm -v $PWD/data:/data politics-viz pv sync
docker run -p 8000:8000 -v $PWD/data:/data politics-viz
```

## Données et licence

Sources publiées sous licence ouverte Etalab. La HATVP retire une
déclaration à la fin du mandat ; celles qui avaient été collectées avant
leur retrait sont conservées dans `data/archive/legacy.sqlite`, signalées
comme archivées sur le site.
