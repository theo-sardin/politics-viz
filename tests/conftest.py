"""A miniature copy of every upstream source, small enough to read.

The cast:

* **Alice Martin**, deputy (EPR, social affairs committee). Holds
  TotalEnergies; between her two declarations she adds shares and opens a
  Sanofi line. Rapporteur of the renewable-energy bill, votes on it. Her
  spouse works for Société Générale.
* **Bruno Petit**, deputy (EPR). Holds an SCI whose name HATVP redacts, and
  recused himself from the energy bill.
* **Chloé Durand**, departmental councillor with cooperative-bank shares.
* **Denis Leroy**, senator with no published declaration.

TotalEnergies declared lobbying that names the energy bill.
"""

from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from pv import config, db
from pv.pipeline import run

ENERGY_BILL = "projet de loi relatif à l'accélération de la production d'énergies renouvelables"
PLFSS = "projet de loi de financement de la sécurité sociale pour 2026"


def _items(tag: str, rows: list[str]) -> str:
    if not rows:
        return f"<{tag}><neant>true</neant></{tag}>"
    inner = "".join(f"<items>{r}</items>" for r in rows)
    return f"<{tag}><items>{inner}</items><neant>false</neant></{tag}>"


def _stake(name: str, value: int, shares: int | None = None, pct: str | None = None) -> str:
    out = f"<nomSociete>{name}</nomSociete><evaluation>{value}</evaluation>"
    if shares is not None:
        out += f"<nombreParts>{shares}</nombreParts>"
    if pct is not None:
        out += f"<capitalDetenu>{pct}</capitalDetenu>"
    return out


def _declaration(
    uuid, filed, nom, prenom, born, kind, code, label, *, modif=False, stakes=(), spouse=(), jobs=(), staff=()
) -> str:
    return f"""
    <declaration>
        <dateDepot>{filed}</dateDepot>
        <uuid>{uuid}</uuid>
        <general>
            <declarant><civilite>Mme</civilite><nom>{nom}</nom><prenom>{prenom}</prenom>
                <dateNaissance>{born}</dateNaissance></declarant>
            <typeDeclaration><id>{kind}</id></typeDeclaration>
            <declarationModificative>{"true" if modif else "false"}</declarationModificative>
            <qualiteMandat><codTypeMandatFichier>{code}</codTypeMandatFichier><typeMandat>{label}</typeMandat></qualiteMandat>
            <qualiteDeclarantForPDF>{label}/Rhône (69)</qualiteDeclarantForPDF>
            <dateDebutMandat>08/07/2024</dateDebutMandat>
        </general>
        {_items("participationFinanciereDto", list(stakes))}
        {_items("activProfConjointDto", [f"<employeurConjoint>{e}</employeurConjoint><activiteProf>{a}</activiteProf>" for e, a in spouse])}
        {_items("activProfCinqDerniereDto", list(jobs))}
        {_items("activCollaborateursDto", [f"<nom>{n}</nom><employeur>{e}</employeur><descriptionActivite>Secrétariat</descriptionActivite>" for n, e in staff])}
    </declaration>"""


def _write_zip(path: Path, files: dict[str, dict]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, doc in files.items():
            zf.writestr(name, json.dumps(doc, ensure_ascii=False))


def _acteur(uid, prenom, nom, born, mandats, civ="Mme"):
    return {
        "acteur": {
            "uid": {"#text": uid},
            "etatCivil": {"ident": {"civ": civ, "prenom": prenom, "nom": nom}, "infoNaissance": {"dateNais": born}},
            "profession": {"libelleCourant": "(34) - Médecin"},
            "uri_hatvp": None,
            "mandats": {"mandat": mandats},
        }
    }


def _mandat(kind, organe, role="Membre", leg="17", extra=None):
    m = {
        "typeOrgane": kind,
        "legislature": leg,
        "dateDebut": "2024-07-08",
        "dateFin": None,
        "infosQualite": {"libQualite": role},
        "organes": {"organeRef": organe},
    }
    m.update(extra or {})
    return m


def _scrutin(numero, date, title, pour, contre, dossier=None):
    def block(refs):
        return {"votant": [{"acteurRef": r} for r in refs]} if refs else None

    return {
        "scrutin": {
            "uid": f"VTANR5L17V{numero}",
            "numero": str(numero),
            "legislature": "17",
            "dateScrutin": date,
            "titre": title,
            "typeVote": {"codeTypeVote": "SPO"},
            "sort": {"code": "adopté"},
            "objet": {"dossierLegislatif": {"dossierRef": dossier} if dossier else None},
            "syntheseVote": {"decompte": {"pour": str(len(pour)), "contre": str(len(contre)), "abstentions": "0"}},
            "ventilationVotes": {
                "organe": {
                    "groupes": {
                        "groupe": [
                            {
                                "organeRef": "PO10",
                                "vote": {
                                    "positionMajoritaire": "pour",
                                    "decompteVoix": {
                                        "pour": str(len(pour)),
                                        "contre": str(len(contre)),
                                        "abstentions": "0",
                                    },
                                    "decompteNominatif": {
                                        "pours": block(pour),
                                        "contres": block(contre),
                                        "abstentions": None,
                                        "nonVotants": None,
                                    },
                                },
                            }
                        ]
                    }
                }
            },
        }
    }


@pytest.fixture(scope="session")
def sources(tmp_path_factory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("raw")

    xml = d / "declarations.xml"
    xml.write_text(
        "<?xml version='1.0' encoding='utf-8'?><declarations>"
        + _declaration(
            "a-1",
            "01/09/2024 10:00:00",
            "MARTIN",
            "Alice",
            "01/01/1980",
            "DIA",
            "depute",
            "Député",
            stakes=[_stake("TOTAL ENERGIES SE", 20000, 300)],
            spouse=[("Société Générale", "Analyste")],
            staff=[("DUPUIS Marc", "néant")],
        )
        + _declaration(
            "a-2",
            "01/03/2025 09:30:00",
            "MARTIN",
            "Alice",
            "01/01/1980",
            "DIA",
            "depute",
            "Député",
            stakes=[_stake("PEA TOTAL", 33000, 500), _stake("Sanofi", 15000, 150), _stake("néant", 0)],
            spouse=[("Société Générale", "Analyste")],
        )
        + _declaration(
            "b-1",
            "02/09/2024 11:00:00",
            "PETIT",
            "Bruno",
            "02/02/1975",
            "DIA",
            "depute",
            "Député",
            stakes=[_stake("SCI [Données non publiées]", 120000, 100, "50")],
        )
        + _declaration(
            "c-1",
            "03/05/2023 08:00:00",
            "DURAND",
            "Chloé",
            "03/03/1970",
            "DI",
            "departement",
            "Elu départemental",
            stakes=[_stake("Parts sociales Crédit Agricole", 300, 20)],
        )
        + "</declarations>",
        encoding="utf-8",
    )

    index = d / "liste.csv"
    head = (
        "civilite;prenom;nom;classement;type_mandat;qualite;type_document;departement;date_publication;"
        "date_depot;nom_fichier;url_dossier;open_data;statut_publication;id_origine;url_photo"
    )
    rows = [
        "Mme;Alice;MARTIN;x;depute;Députée du Rhône;dia;69;2024-10-01;2024-09-01;martin-alice-dia1.pdf;"
        "/pages_nominatives/martin-alice-1;martin-alice-dia1.xml;Livrée;1001;https://example.org/1001.jpg",
        "Mme;Alice;MARTIN;x;depute;Députée du Rhône;diam;69;2025-04-01;2025-03-01;martin-alice-diam2.pdf;"
        "/pages_nominatives/martin-alice-1;martin-alice-diam2.xml;Livrée;1001;https://example.org/1001.jpg",
        "M.;Bruno;PETIT;x;depute;Député du Rhône;dia;69;2024-10-02;2024-09-02;petit-bruno-dia3.pdf;"
        "/pages_nominatives/petit-bruno-2;petit-bruno-dia3.xml;Livrée;1002;",
        "M.;Bruno;PETIT;x;depute;Député du Rhône;diam;69;;2026-01-15;;/pages_nominatives/petit-bruno-2;;"
        "Déclaration déposée - publication à venir;1002;",
    ]
    index.write_text("﻿" + head + "\n" + "\n".join(rows) + "\n", encoding="utf-8")

    seat = {
        "election": {"lieu": {"departement": "Rhône", "numDepartement": "69", "numCirco": "1"}},
        "mandature": {"placeHemicycle": "12"},
    }
    acteurs = d / "acteurs.zip"
    _write_zip(
        acteurs,
        {
            "json/organe/PO1.json": {
                "organe": {"uid": "PO1", "codeType": "ASSEMBLEE", "libelle": "Assemblée nationale", "legislature": "17"}
            },
            "json/organe/PO10.json": {
                "organe": {
                    "uid": "PO10",
                    "codeType": "GP",
                    "libelle": "Ensemble pour la République",
                    "libelleAbrev": "EPR",
                    "legislature": "17",
                    "viMoDe": {"dateFin": None},
                }
            },
            "json/organe/PO20.json": {
                "organe": {"uid": "PO20", "codeType": "COMPER", "libelle": "Commission des affaires sociales"}
            },
            "json/acteur/PA1001.json": _acteur(
                "PA1001",
                "Alice",
                "Martin",
                "1980-01-01",
                [_mandat("ASSEMBLEE", "PO1", extra=seat), _mandat("GP", "PO10"), _mandat("COMPER", "PO20")],
            ),
            "json/acteur/PA1002.json": _acteur(
                "PA1002",
                "Bruno",
                "Petit",
                "1975-02-02",
                [_mandat("ASSEMBLEE", "PO1", extra=seat), _mandat("GP", "PO10")],
                civ="M.",
            ),
            "json/deport/D1.json": {
                "deport": {
                    "uid": "D1",
                    "refActeur": "PA1002",
                    "legislature": "17",
                    "dateCreation": "2025-04-01T10:00:00+00:00",
                    "portee": {"libelle": "Être présent sans prendre la parole ni voter"},
                    "cible": {
                        "type": {"libelle": "Texte"},
                        "referenceTextuelle": "Projet de loi relatif à l'accélération de la production d'énergies renouvelables",
                    },
                    "explication": "<p>Conjoint salari&eacute; du secteur.</p>",
                }
            },
        },
    )

    scrutins = d / "scrutins17.zip"
    _write_zip(
        scrutins,
        {
            "json/1.json": _scrutin(
                1,
                "2025-04-10",
                f"l'ensemble du {ENERGY_BILL} (première lecture).",
                ["PA1001"],
                ["PA1002"],
                dossier="DLR1",
            ),
            "json/2.json": _scrutin(
                2,
                "2025-04-09",
                f"l'amendement n° 4 de Mme Martin à l'article 2 du {ENERGY_BILL} (première lecture).",
                ["PA1001"],
                [],
            ),
            "json/3.json": _scrutin(
                3, "2025-11-04", f"l'article 7 du {PLFSS} (première lecture).", ["PA1001", "PA1002"], []
            ),
            "json/4.json": _scrutin(
                4, "2025-12-01", "la motion de censure déposée par M. X et 60 députés.", [], ["PA1001"]
            ),
        },
    )
    dossiers = d / "dossiers17.zip"
    _write_zip(
        dossiers,
        {
            "json/dossierParlementaire/DLR1.json": {
                "dossierParlementaire": {
                    "uid": "DLR1",
                    "legislature": "17",
                    "titreDossier": {"titre": ENERGY_BILL.capitalize(), "titreChemin": "energies_renouvelables"},
                    "procedureParlementaire": {"libelle": "Projet de loi ordinaire"},
                    "actesLegislatifs": {
                        "acteLegislatif": {
                            "codeActe": "AN1",
                            "actesLegislatifs": {
                                "acteLegislatif": [
                                    {
                                        "codeActe": "AN1-COM-FOND",
                                        "organeRef": "PO20",
                                        "actesLegislatifs": {
                                            "acteLegislatif": {
                                                "codeActe": "AN1-COM-FOND-NOMIN",
                                                "rapporteurs": {
                                                    "rapporteur": {
                                                        "acteurRef": "PA1001",
                                                        "typeRapporteur": "rapporteur",
                                                    }
                                                },
                                            }
                                        },
                                    }
                                ]
                            },
                        }
                    },
                }
            },
        },
    )

    senat = d / "senat.csv"
    senat.write_bytes(
        (
            "% commentaire\n"
            "Matricule,Qualité,Nom usuel,Prénom usuel,État,Date naissance,Groupe politique,Commission permanente,"
            "Circonscription,Description de la profession\n"
            "19999X,M.,Leroy,Denis,ACTIF,1960-05-05 00:00:00.0,Les Républicains,commission des finances,Ain,Avocat\n"
            "18888Y,M.,Ancien,Paul,ANCIEN,1940-01-01 00:00:00.0,,,,\n"
        ).encode("latin-1")
    )

    agora = d / "agora.json"
    agora.write_text(
        json.dumps(
            {
                "publications": [
                    {
                        "identifiantNational": "542051180",
                        "denomination": "TOTALENERGIES SE",
                        "categorieOrganisation": {"label": "Société commerciale"},
                        "activites": {"listSecteursActivites": [{"label": "Energie"}]},
                        "exercices": [
                            {
                                "publicationCourante": {
                                    "dateDebut": "01-01-2025",
                                    "montantDepense": "> = 1 000 000 euros et < 1 250 000 euros",
                                    "nombreSalaries": 4.0,
                                    "activites": [
                                        {
                                            "publicationCourante": {
                                                "objet": "Projet de loi relatif à l'accélération de la production d'énergies renouvelables : simplifier les procédures",
                                                "domainesIntervention": ["Energie"],
                                                "actionsRepresentationInteret": [
                                                    {
                                                        "reponsablesPublics": [
                                                            "Député, sénateur, collaborateur du Président de l'Assemblée nationale"
                                                        ],
                                                        "decisionsConcernees": ["Lois, y compris constitutionnelles"],
                                                    }
                                                ],
                                            }
                                        },
                                        {
                                            "publicationCourante": {
                                                "objet": "Présenter la stratégie du groupe",
                                                "domainesIntervention": ["Energie"],
                                                "actionsRepresentationInteret": [],
                                            }
                                        },
                                    ],
                                }
                            }
                        ],
                    },
                    {"identifiantNational": "111", "denomination": "FEDERATION DES TESTS", "exercices": []},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    return {
        "hatvp_declarations": xml,
        "hatvp_index": index,
        "agora": agora,
        "an_acteurs": acteurs,
        "an_scrutins_17": scrutins,
        "an_scrutins_16": None,
        "an_dossiers_17": dossiers,
        "an_dossiers_16": None,
        "senat_senateurs": senat,
    }


@pytest.fixture(scope="session")
def built_db(sources, tmp_path_factory) -> Path:
    """The fixture sources run through the whole pipeline, once per test session."""
    path = tmp_path_factory.mktemp("db") / "pv.sqlite"
    conn = db.connect(path)
    db.init_schema(conn)
    run.build(conn, sources, {})
    db.set_meta(conn, "synced_at", "2026-01-01T00:00:00+00:00")
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def conn(built_db) -> sqlite3.Connection:
    c = db.connect(built_db, readonly=True)
    yield c
    c.close()


@pytest.fixture
def client(built_db, monkeypatch):
    from fastapi.testclient import TestClient

    from pv.web.app import app

    monkeypatch.setattr(config, "DB_PATH", built_db)
    return TestClient(app)
