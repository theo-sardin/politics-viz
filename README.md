# politics-viz

Un radar des intérêts financiers déclarés par les responsables publics
français. Il croise les déclarations à la HATVP avec les votes de
l'Assemblée nationale, les rôles de rapporteur, les commissions, le
registre des déports et le lobbying déclaré, et fait remonter ce qui
mérite un second regard.

Un **signal** est une coïncidence documentée entre un intérêt déclaré et
un acte public. Ce n'est jamais la preuve d'une faute : chaque signal
renvoie à la déclaration et aux scrutins d'origine pour être vérifié. Les
rapprochements sont automatiques et peuvent se tromper — voir
[Signaler une erreur](#signaler-une-erreur).

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
Les téléchargements sont conditionnels ; `--offline` n'utilise que le cache,
`--force` l'ignore.

| Variable | Rôle | Défaut |
|---|---|---|
| `PV_DATA_DIR` | cache des sources, base, portraits | `./data` |
| `POLITICS_DB` | fichier SQLite lu par le site | `$PV_DATA_DIR/pv.sqlite` |
| `PV_INDEXABLE` | `1` pour autoriser l'indexation par les moteurs de recherche | non défini |
| `PV_RATE_LIMIT` | `pv serve` : requêtes par minute et par adresse (0 = illimité) | `240` |
| `PV_REPO_URL` | dépôt vers lequel pointe « Signaler une erreur » | ce dépôt |

## Publication

Le site publié est un **export statique** : `pv export site --base /politics-viz`
écrit chaque page en fichier HTML (≈ 5 600 pages, 90 Mo), vérifie qu'aucun
lien interne n'est mort, et le résultat se sert depuis n'importe quel
hébergement de fichiers. C'est possible parce que chaque page ne dépend que
de son chemin : les filtres, les tris et la recherche s'exécutent dans le
navigateur.

`.github/workflows/publish.yml` fait tout cela sur GitHub Actions — tests,
`pv sync`, `pv export`, publication sur GitHub Pages — à chaque push sur
`main` et chaque nuit. Les sources et les portraits sont mis en cache d'une
exécution à l'autre.

Par défaut les pages demandent aux moteurs de recherche de ne pas les
indexer (`noindex`), puisqu'elles nomment des personnes. Définir
`PV_INDEXABLE=1` au moment de l'export lève cette réserve.

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
La page « Méthode » du site détaille ces limites.

## Comment ça marche

```
sources ──► people ──► parliament ──► lobby ──► changes ──► signals ──► summary ──► export
            │          │              │         │           │
            │          │              │         │           └ croise tout, note de 0 à 100
            │          │              │         └ écarts entre déclarations successives
            │          │              └ lie organisations, sociétés, employeurs et textes
            │          └ rôles, déports, scrutins ; reconstitue les textes depuis les intitulés
            └ une personne par déclarant, quelle que soit l'orthographe selon la source
```

Quatre décisions structurent le reste :

- **L'identité d'abord.** Une personne est rattachée par l'identifiant de
  l'index HATVP, sinon par nom et date de naissance exacts. Aucun
  rapprochement approximatif n'est fait sans seconde clé indépendante : un
  doublon vaut mieux qu'une fusion à tort.
- **Les sociétés par référentiel.** Les noms sont saisis à la main
  (« PEA Total », « TotalEnergies SE »). `pv/companies.py` tient un
  référentiel de plus de 220 sociétés avec leurs variantes, leur secteur et
  leur code de cotation. Les noms occultés par la HATVP ne sont jamais
  rapprochés.
- **Trois forces de lien, pas une.** Société nommée dans l'intitulé du
  texte, lobbying déclaré mentionnant le texte, ou simple secteur commun.
  Les lois de finances ne produisent aucun lien sectoriel.
- **Rien n'est conservé au-delà des sources.** La base est reconstruite de
  zéro à chaque synchronisation : une déclaration que la HATVP retire à la
  fin d'un mandat disparaît à la synchronisation suivante. Tout le contenu
  de `data/` se reconstruit avec `pv sync`.

## Arborescence

```
pv/
  config.py          chemins et URL des sources
  db.py              schéma SQLite
  text.py            normalisation (accents, dates, nombres)
  companies.py       référentiel de sociétés et canonicalisation des noms
  sectors.py         secteurs : textes, sociétés, domaines de lobbying, commissions
  labels.py          libellés et formats français
  sources/           lecture de chaque fichier amont (fetch, hatvp, assemblee, senat, agora)
  pipeline/          people, parliament, lobby, billmatch, changes, signals, photos, run
  web/               app FastAPI, requêtes, export statique, gabarits Jinja, CSS et JS sans étape de build
tests/               un jeu de sources miniature passé dans tout le pipeline, puis exporté
```

`pv serve` sert les mêmes pages dynamiquement, pour le développement ou un
hébergement classique. Dans ce mode l'application ajoute un ETag par page,
une limite de requêtes par adresse et des en-têtes de sécurité
(`pv/web/hardening.py`) ; derrière un proxy, lancer uvicorn avec
`--proxy-headers`. Dans les deux modes le site ne charge rien d'un autre
domaine : D3 est embarqué, les portraits officiels sont copiés en vignettes
par `pv sync`.

## Signaler une erreur

Un homonyme, une déclaration mal saisie, un texte mal classé : ouvrez un
ticket sur ce dépôt. La règle en cause sera corrigée et le site régénéré.
Les données elles-mêmes se rectifient à la source, auprès de la HATVP ou de
l'Assemblée nationale.

## Données

Sources publiées sous licence ouverte Etalab. Portraits : Assemblée
nationale et Sénat.
