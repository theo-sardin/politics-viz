"""French labels and formatters shared by the signal engine and the templates."""

from __future__ import annotations

import re

SIGNAL_KINDS: dict[str, str] = {
    "mouvement_vote": "Mouvement de titres autour d'un vote",
    "rapporteur": "Rapporteur ou auteur d'un texte lié à ses intérêts",
    "vote_participation": "Vote sur un texte lié à une participation",
    "vote_activite": "Vote sur un texte lié à une activité rémunérée",
    "vote_famille": "Vote sur un texte lié à l'employeur du conjoint",
    "vote_collaborateur": "Vote sur un texte lié à l'employeur d'un collaborateur",
    "ministere": "Portefeuille ministériel et intérêts dans le même secteur",
    "commission": "Commission compétente sur un secteur détenu",
    "lien_activite": "Rémunéré par un représentant d'intérêts ou une société cotée",
    "lien_famille": "Conjoint employé par un représentant d'intérêts ou une société cotée",
    "lien_collaborateur": "Collaborateur employé par un représentant d'intérêts",
    "mouvement": "Mouvement de titres notable",
    "co_detention": "Société détenue par plusieurs responsables publics",
    "deport": "Déport déclaré",
}

SIGNAL_SHORT: dict[str, str] = {
    "mouvement_vote": "Mouvement + vote",
    "rapporteur": "Rapporteur",
    "vote_participation": "Détient et vote",
    "vote_activite": "Activité et vote",
    "vote_famille": "Conjoint et vote",
    "vote_collaborateur": "Collaborateur et vote",
    "ministere": "Ministère",
    "commission": "Commission",
    "lien_activite": "Activité rémunérée",
    "lien_famille": "Conjoint",
    "lien_collaborateur": "Collaborateur",
    "mouvement": "Mouvement",
    "co_detention": "Co-détention",
    "deport": "Déport",
}

LEVELS: dict[str, str] = {"fort": "Fort", "moyen": "Moyen", "faible": "Faible", "info": "Transparence"}

RELATIONS: dict[str, str] = {
    "holding": "Participation",
    "activite": "Activité professionnelle",
    "conseil": "Activité de conseil",
    "dirigeant": "Fonction dirigeante",
    "conjoint": "Employeur du conjoint",
    "collaborateur": "Employeur d'un collaborateur",
    "benevole": "Fonction bénévole",
}

STRENGTHS: dict[str, str] = {
    "named": "société nommée dans le texte",
    "lobby": "lobbying déclaré sur ce texte",
    "sector": "même secteur",
}

CHANGES: dict[str, str] = {
    "entree": "Entrée",
    "sortie": "Sortie",
    "renforcement": "Renforcement",
    "allegement": "Allègement",
}

CHANGE_BASIS: dict[str, str] = {
    "shares": "nombre de titres",
    "value": "valeur déclarée (nombre de titres non renseigné)",
    "presence": "ligne présente dans une seule des deux déclarations",
}

CHAMBERS: dict[str, str] = {
    "an": "Assemblée nationale",
    "senat": "Sénat",
    "gouv": "Gouvernement",
    "europe": "Parlement européen",
    "local": "Élus locaux et autres",
}

POSITIONS = {1: "pour", 2: "contre", 3: "abstention", 0: "non-votant"}

COMPANY_KINDS: dict[str, str] = {
    "listed": "Cotée",
    "coop_bank": "Banque mutualiste",
    "unlisted": "Non cotée",
    "other": "Société",
    "org": "Organisation",
    "fund": "Fonds",
    "unnamed": "Nom non publié",
}

INTEREST_KINDS: dict[str, str] = {
    "activite": "Activités professionnelles (5 dernières années)",
    "conseil": "Activités de conseil",
    "dirigeant": "Fonctions dirigeantes et mandats sociaux",
    "conjoint": "Activité du conjoint",
    "collaborateur": "Collaborateurs parlementaires",
    "benevole": "Fonctions bénévoles",
    "mandat": "Mandats électifs",
}

DOC_TYPES = {
    "DI": "Déclaration d'intérêts",
    "DIA": "Déclaration d'intérêts et d'activités",
    "DSP": "Déclaration de situation patrimoniale",
    "DSPFM": "Déclaration de patrimoine de fin de mandat",
}

_MONTHS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]


def fmt_eur(n: int | float | None) -> str:
    """Compact euro amount: 1 234 567 → '1,23 M€', 23 248 → '23 k€'."""
    if n is None:
        return "—"
    a = abs(n)
    sign = "−" if n < 0 else ""
    if a >= 1e9:
        return f"{sign}{a / 1e9:.2f}".replace(".", ",") + " Md€"
    if a >= 1e8:
        return f"{sign}{a / 1e6:.0f} M€"
    if a >= 1e7:
        return f"{sign}{a / 1e6:.1f}".replace(".", ",") + " M€"
    if a >= 1e6:
        return f"{sign}{a / 1e6:.2f}".rstrip("0").rstrip(".").replace(".", ",") + " M€"
    if a >= 10_000:
        return f"{sign}{a / 1e3:.0f} k€"
    if a >= 1_000:
        return f"{sign}{a / 1e3:.1f}".replace(".", ",").replace(",0", "") + " k€"
    return f"{sign}{a:.0f} €"


def fmt_int(n: int | float | None) -> str:
    if n is None:
        return "—"
    return f"{n:,.0f}".replace(",", " ")


def fmt_date(iso: str | None, *, short: bool = False) -> str:
    """'2025-03-23' → '23 mars 2025'; '2025-03' → 'mars 2025'."""
    if not iso:
        return "—"
    parts = iso[:10].split("-")
    try:
        if len(parts) == 1:
            return parts[0]
        month = _MONTHS[int(parts[1]) - 1]
        if len(parts) == 2:
            return f"{month} {parts[0]}"
        day = int(parts[2])
        dd = "1er" if day == 1 else str(day)
        return f"{dd} {month}" if short else f"{dd} {month} {parts[0]}"
    except (ValueError, IndexError):
        return iso


_BUDGET = re.compile(r"^projet de loi de (finances|financement de la sécurité sociale) pour (\d{4})$", re.IGNORECASE)
_PREFIXES = [
    (re.compile(r"^projet de loi (organique|constitutionnelle) ", re.IGNORECASE), r"PJL \1 "),
    (re.compile(r"^proposition de loi (organique|constitutionnelle) ", re.IGNORECASE), r"PPL \1 "),
    (re.compile(r"^projet de loi ", re.IGNORECASE), "PJL "),
    (re.compile(r"^proposition de loi ", re.IGNORECASE), "PPL "),
    (re.compile(r"^proposition de résolution européenne ", re.IGNORECASE), "Résolution européenne "),
    (re.compile(r"^proposition de résolution ", re.IGNORECASE), "Résolution "),
]
_FILLER = re.compile(
    r"^(PJL|PPL|Résolution(?: européenne)?)(?: organique| constitutionnelle)? "
    r"(?:visant à|tendant à|relati(?:f|ve|fs|ves) (?:à la|à l'|au|aux|à)|portant|pour|sur|de|d')\s*",
)


_ARTICLE = re.compile(r"^(?:la |le |les |l'|un |une |des |du )", re.IGNORECASE)
# What follows these is the bill's stated purpose or its small print, not its name.
_TAIL = re.compile(r" afin d[e']| et portant diverses| pour les années ", re.IGNORECASE)


def short_bill(title: str, limit: int = 72) -> str:
    """A bill title short enough for a card: 'PLFSS 2026', 'PPL aide à mourir…'."""
    t = title.strip()
    m = _BUDGET.match(t)
    if m:
        return ("PLF " if m.group(1).lower() == "finances" else "PLFSS ") + m.group(2)
    for rx, rep in _PREFIXES:
        if rx.match(t):
            t = rx.sub(rep, t, count=1)
            break
    m = _FILLER.match(t)
    if m:
        rest = _ARTICLE.sub("", t[m.end():], count=1)
        t = f"{m.group(1)} {rest}"
    t = _TAIL.split(t, maxsplit=1)[0]
    if len(t) > limit:
        t = t[:limit].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
    return t
