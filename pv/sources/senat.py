"""Sénat open data: sitting senators with their group and committee."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Senateur:
    matricule: str
    civilite: str | None
    prenom: str
    nom: str
    birth_date: str | None
    group: str | None
    committee: str | None
    departement: str | None
    profession: str | None


def read_senateurs(path: str | Path) -> list[Senateur]:
    """Parse ODSEN_GENERAL.csv (latin-1, '%'-prefixed comment lines)."""
    raw = Path(path).read_bytes().decode("latin-1")
    text = "\n".join(line for line in raw.splitlines() if not line.startswith("%"))
    out: list[Senateur] = []
    for r in csv.DictReader(io.StringIO(text)):
        if (r.get("État") or "").strip() != "ACTIF":
            continue
        matricule = (r.get("Matricule") or "").strip()
        nom = (r.get("Nom usuel") or "").strip()
        if not matricule or not nom:
            continue
        committee = (r.get("Commission permanente") or "").strip()
        out.append(
            Senateur(
                matricule=matricule,
                civilite=(r.get("Qualité") or "").strip() or None,
                prenom=(r.get("Prénom usuel") or "").strip(),
                nom=nom,
                birth_date=((r.get("Date naissance") or "").strip()[:10] or None),
                group=(r.get("Groupe politique") or "").strip() or None,
                committee=None if committee in ("", "Aucune") else committee[:1].upper() + committee[1:],
                departement=(r.get("Circonscription") or "").strip() or None,
                profession=(r.get("Description de la profession") or "").strip() or None,
            )
        )
    return out
