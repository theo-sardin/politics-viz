"""Paths and upstream source definitions.

Everything that touches the filesystem or the network reads its locations
from here, so tests and Docker can redirect them with two env vars.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("PV_DATA_DIR", ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"
DB_PATH = Path(os.environ.get("POLITICS_DB", DATA_DIR / "pv.sqlite"))

LEGISLATURES = (16, 17)
CURRENT_LEGISLATURE = 17

_AN = "https://data.assemblee-nationale.fr/static/openData/repository"


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    filename: str
    label: str
    optional: bool = False


SOURCES: dict[str, Source] = {
    s.name: s
    for s in [
        Source(
            "hatvp_declarations",
            "https://www.hatvp.fr/livraison/merge/declarations.xml",
            "hatvp_declarations.xml",
            "HATVP — déclarations d'intérêts (XML fusionné)",
        ),
        Source(
            "hatvp_index",
            "https://www.hatvp.fr/livraison/opendata/liste.csv",
            "hatvp_liste.csv",
            "HATVP — index des déclarations",
        ),
        Source(
            "agora",
            "https://www.hatvp.fr/agora/opendata/agora_repertoire_opendata.json",
            "agora_repertoire.json",
            "HATVP — répertoire des représentants d'intérêts",
        ),
        Source(
            "an_acteurs",
            f"{_AN}/17/amo/tous_acteurs_mandats_organes_xi_legislature/"
            "AMO30_tous_acteurs_tous_mandats_tous_organes_historique.json.zip",
            "an_acteurs_historique.json.zip",
            "Assemblée nationale — acteurs, mandats, organes, déports",
        ),
        Source(
            "an_scrutins_17",
            f"{_AN}/17/loi/scrutins/Scrutins.json.zip",
            "an_scrutins_17.json.zip",
            "Assemblée nationale — scrutins publics (17e législature)",
        ),
        Source(
            "an_scrutins_16",
            f"{_AN}/16/loi/scrutins/Scrutins.json.zip",
            "an_scrutins_16.json.zip",
            "Assemblée nationale — scrutins publics (16e législature)",
        ),
        Source(
            "an_dossiers_17",
            f"{_AN}/17/loi/dossiers_legislatifs/Dossiers_Legislatifs.json.zip",
            "an_dossiers_17.json.zip",
            "Assemblée nationale — dossiers législatifs (17e législature)",
        ),
        Source(
            "an_dossiers_16",
            f"{_AN}/16/loi/dossiers_legislatifs/Dossiers_Legislatifs.json.zip",
            "an_dossiers_16.json.zip",
            "Assemblée nationale — dossiers législatifs (16e législature)",
            optional=True,
        ),
        Source(
            "senat_senateurs",
            "https://data.senat.fr/data/senateurs/ODSEN_GENERAL.csv",
            "senat_general.csv",
            "Sénat — sénateurs, groupes, commissions",
        ),
    ]
}

HATVP_BASE = "https://www.hatvp.fr"
HATVP_FILE_BASE = "https://www.hatvp.fr/livraison/dossiers/"
AN_BASE = "https://www.assemblee-nationale.fr"
