"""Find the bill a free-text sentence is talking about.

Lobbying disclosures and recusal notices name bills loosely (« PPL aide à
mourir », « projet de loi simplification de la vie économique »). A bill is
reduced to the first content words of its title; a text refers to it when it
contains three of them in a row, or two in a row when that pair is a fixed
expression across the whole corpus.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from pv.text import fold

_STOP = set(
    """
    a au aux avec ce ces cette chez d dans de des dont du en entre et l la le les leur leurs ne nos
    notre ou par pas plus pour qu que qui s sa sans se ses son sous sur un une vers vos votre y
    afin ainsi tout tous toute toutes meme autres autre
    projet proposition loi lois ppl pjl resolution organique constitutionnelle europeenne
    visant tendant relative relatif relatives relatifs portant instaurant creant instituant
    renforcer ameliorer garantir lutter contre creer permettre faciliter instaurer reconnaitre
    proteger favoriser assurer mettre place oeuvre mieux adapter moderniser reformer encadrer
    simplifier soutenir developper accelerer retrouver offrir apportant
    mesures diverses dispositions france francais francaise francaises national nationale pays
    annees ans adoptee senat assemblee
    """.split()
)

_SIGNATURE_LEN = 6
# A two-word phrase counts as a bill's name only when its rarer word is found
# with the other one at least this often.
BIGRAM_MIN_COHESION = 0.5
BIGRAM_MIN_HITS = 5

# A two-word match only counts when the text is visibly about legislation:
# « prise en charge » or « Nouvelle-Calédonie » alone name a topic, not a bill.
_LEGISLATIVE_CUE = re.compile(
    r"\b(lois?|ppl|pjl|pjt|pjlo|proposition|projet de loi|texte|amendements?|article|resolution"
    r"|examen|legislatifs?|legislatives?|parlementaires?)\b"
)

_BUDGETS = [
    (
        re.compile(r"^projet de loi de finances pour (20\d\d)$"),
        r"\b(?:plf|pjlf|(?:projet de )?loi de finances)(?: initiale)?(?: pour)? ?{year}\b",
    ),
    (
        re.compile(r"^projet de loi de financement de la securite sociale pour (20\d\d)$"),
        r"\b(?:plfss|lfss|(?:projet de )?(?:loi de )?financement de la securite sociale)(?: pour)? ?{year}\b",
    ),
]


def content_tokens(text: str) -> list[str]:
    return [t for t in fold(text).split() if t not in _STOP and (len(t) > 2 or t.isdigit())]


@dataclass(frozen=True)
class BillPattern:
    bill_id: int
    year_min: int
    year_max: int
    trigrams: tuple[tuple[str, ...], ...]
    bigrams: tuple[tuple[str, ...], ...]
    regex: re.Pattern[str] | None  # budget bills are matched on their acronym + year
    years: tuple[str, ...] = ()


def bill_pattern(bill_id: int, title: str, first_vote: str, last_vote: str) -> BillPattern:
    key = fold(title)
    for head, template in _BUDGETS:
        m = head.match(key)
        if m:
            year = int(m.group(1))
            return BillPattern(bill_id, year - 1, year, (), (), re.compile(template.format(year=year)))
    sig = content_tokens(title)[:_SIGNATURE_LEN]
    trigrams = tuple(tuple(sig[i : i + 3]) for i in range(len(sig) - 2))
    bigrams = tuple(tuple(sig[i : i + 2]) for i in range(min(len(sig) - 1, 3)))
    years = tuple(t for t in sig if len(t) == 4 and t.isdigit() and t.startswith("20"))
    return BillPattern(bill_id, int(first_vote[:4]) - 1, int(last_vote[:4]), trigrams, bigrams, None, years)


class Corpus:
    """Texts indexed by content token, so a phrase lookup touches few of them."""

    def __init__(self, texts: list[tuple[int, int, str]]):
        """*texts*: (id, year, text)."""
        self.ids = [t[0] for t in texts]
        self.years = [t[1] for t in texts]
        self.folded = [fold(t[2]) for t in texts]
        self.cue = [bool(_LEGISLATIVE_CUE.search(f)) for f in self.folded]
        self.joined: list[str] = []
        self.postings: dict[str, list[int]] = defaultdict(list)
        for i, f in enumerate(self.folded):
            toks = [t for t in f.split() if t not in _STOP and (len(t) > 2 or t.isdigit())]
            self.joined.append(" " + " ".join(toks) + " ")
            for tok in set(toks):
                self.postings[tok].append(i)

    def _phrase_hits(self, phrase: tuple[str, ...]) -> list[int]:
        lists = [self.postings.get(t) for t in phrase]
        if any(not ln for ln in lists):
            return []
        lists.sort(key=len)
        cand = set(lists[0])
        for ln in lists[1:]:
            cand.intersection_update(ln)
            if not cand:
                return []
        needle = " " + " ".join(phrase) + " "
        return [i for i in cand if needle in self.joined[i]]

    def match(self, pat: BillPattern) -> list[int]:
        """Ids of the texts that refer to the bill, within its year window."""
        hits: set[int] = set()
        if pat.regex is not None:
            for i, f in enumerate(self.folded):
                if pat.year_min <= self.years[i] <= pat.year_max and pat.regex.search(f):
                    hits.add(i)
        else:
            for tri in pat.trigrams:
                hits.update(self._phrase_hits(tri))
            for bi in pat.bigrams:
                if min(len(t) for t in bi) < 4:
                    continue
                found = self._phrase_hits(bi)
                rarest = min(len(self.postings.get(t, ())) for t in bi)
                # Two words that nearly always travel together are a name
                # (« aide à mourir », « industrie verte »); two that merely
                # co-occur sometimes (« conditions d'accès ») are not.
                if len(found) >= BIGRAM_MIN_HITS and len(found) / rarest >= BIGRAM_MIN_COHESION:
                    hits.update(i for i in found if self.cue[i])
            hits = {i for i in hits if pat.year_min <= self.years[i] <= pat.year_max}
            # « Jeux olympiques de 2030 » is not « de 2024 »: a year in the bill's
            # name must be the year the text talks about.
            if pat.years:
                hits = {i for i in hits if any(f" {y} " in self.joined[i] for y in pat.years)}
        return [self.ids[i] for i in sorted(hits)]


def text_refers_to(text: str, pat: BillPattern) -> bool:
    """Single-text check (recusal notices): trigram match, or the budget regex."""
    f = fold(text)
    if pat.regex is not None:
        return bool(pat.regex.search(f))
    joined = " " + " ".join(content_tokens(text)) + " "
    if pat.years and not any(f" {y} " in joined for y in pat.years):
        return False
    return any(" " + " ".join(tri) + " " in joined for tri in pat.trigrams)
