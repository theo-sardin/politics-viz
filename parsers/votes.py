"""Fetch AN roll-call votes from NosDéputés.

Legislature 16 has ~4,105 scrutins. The API exposes:
- ``/16/scrutins/json``       — list of all scrutins (no per-député detail)
- ``/16/scrutin/{n}/json``    — per-député vote positions for scrutin *n*

Fetching per-député positions requires one HTTP call per scrutin.
We do them sequentially with connection keep-alive (httpx pool) and
a small sleep to be polite.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

SCRUTINS_URL = "https://www.nosdeputes.fr/16/scrutins/json"
SCRUTIN_URL = "https://www.nosdeputes.fr/16/scrutin/{numero}/json"
SLEEP_BETWEEN = 0.15  # seconds between per-scrutin fetches


@dataclass
class VoteRecord:
    scrutin_numero: int
    date: str
    type: str | None
    titre: str
    sort: str | None
    n_pour: int
    n_contre: int
    n_abstention: int


@dataclass
class PositionRecord:
    parlementaire_slug: str
    position: str  # 'pour' | 'contre' | 'abstention'
    groupe: str | None


def fetch_scrutin_list(client: httpx.Client) -> list[int]:
    """Return all scrutin numbers for legislature 16, sorted ascending."""
    r = client.get(SCRUTINS_URL)
    r.raise_for_status()
    data = r.json()
    numeros = []
    for wrap in data.get("scrutins", []):
        s = wrap.get("scrutin", {})
        try:
            numeros.append(int(s["numero"]))
        except (KeyError, ValueError):
            continue
    numeros.sort()
    return numeros


def fetch_scrutin_detail(
    client: httpx.Client, numero: int
) -> tuple[VoteRecord, list[PositionRecord]]:
    """Fetch one scrutin's metadata + per-député positions."""
    r = client.get(SCRUTIN_URL.format(numero=numero))
    r.raise_for_status()
    data = r.json()
    votes = data.get("votes", [])
    if not votes:
        raise ValueError(f"scrutin {numero}: no votes array")
    # All vote entries share the same scrutin metadata.
    s = votes[0]["vote"]["scrutin"]
    record = VoteRecord(
        scrutin_numero=int(s["numero"]),
        date=s.get("date", ""),
        type=s.get("type"),
        titre=s.get("titre", ""),
        sort=s.get("sort"),
        n_pour=_int(s.get("nombre_pours")),
        n_contre=_int(s.get("nombre_contres")),
        n_abstention=_int(s.get("nombre_abstentions")),
    )
    positions = []
    for wrap in votes:
        v = wrap.get("vote", {})
        slug = v.get("parlementaire_slug")
        pos = v.get("position")
        if not slug or not pos:
            continue
        positions.append(
            PositionRecord(
                parlementaire_slug=slug,
                position=pos,
                groupe=v.get("parlementaire_groupe_acronyme"),
            )
        )
    return record, positions


def _int(s) -> int:
    try:
        return int(s)
    except (TypeError, ValueError):
        return 0
