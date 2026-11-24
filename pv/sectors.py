"""Economic sector taxonomy and the rules that map things onto it.

Four things get a sector: companies (curated registry first, name keywords
otherwise), bills (title keywords), lobbying domains (HATVP vocabulary) and
permanent committees. Every rule here is deliberately conservative: a sector
link feeds signals about named people, so a missed link is cheaper than a
wrong one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pv.text import fold


@dataclass(frozen=True)
class Sector:
    slug: str
    label: str
    short: str


SECTORS: dict[str, Sector] = {
    s.slug: s
    for s in [
        Sector("energie", "Énergie", "Énergie"),
        Sector("banque", "Banque & finance", "Banque"),
        Sector("assurance", "Assurance & mutuelles", "Assurance"),
        Sector("sante", "Santé & pharmacie", "Santé"),
        Sector("defense", "Défense & aéronautique", "Défense"),
        Sector("numerique", "Numérique & télécoms", "Numérique"),
        Sector("agriculture", "Agriculture & agroalimentaire", "Agriculture"),
        Sector("immobilier", "Immobilier & logement", "Immobilier"),
        Sector("btp", "BTP & infrastructures", "BTP"),
        Sector("transport", "Transports", "Transports"),
        Sector("automobile", "Automobile", "Automobile"),
        Sector("industrie", "Industrie & matériaux", "Industrie"),
        Sector("conso", "Luxe, distribution & consommation", "Conso"),
        Sector("medias", "Médias & culture", "Médias"),
        Sector("environnement", "Eau, déchets & environnement", "Environnement"),
        Sector("jeux", "Jeux d'argent", "Jeux"),
    ]
}


def label(slug: str) -> str:
    s = SECTORS.get(slug)
    return s.label if s else slug


# ---------------------------------------------------------------------------
# Bills
# ---------------------------------------------------------------------------

# Budget and catch-all texts touch every sector at once. A sector match on
# them says nothing about a specific holding, so they never produce
# sector-level links.
_TRANSVERSAL = re.compile(
    r"^projet de loi de finances\b"
    r"|\bfin de gestion\b|\bfin des gestion\b"
    r"|\bloi speciale\b"
    r"|\bfinances rectificative\b"
    r"|\breglement du budget\b|\bapprobation des comptes\b"
    r"|\bsimplification de la vie economique\b"
    r"|\bdiverses dispositions d adaptation au droit de l union\b"
    r"|\bprogrammation des finances publiques\b"
    # The 2023 "rectificative" social-security bill was the pensions reform.
    r"|\bfinancement rectificative de la securite sociale\b"
)

_SOCIAL_BUDGET = re.compile(r"\bfinancement de la securite sociale\b")

# Texts that name a sector without regulating it: inquiry committees and
# treaty ratifications.
_NOT_SECTORAL = re.compile(
    r"\bcommission d enquete\b"
    r"|^projet de loi autorisant (l approbation|la ratification|l adhesion)\b"
    r"|^projet de loi ratifiant\b"
)

# Titles that contain a sector keyword without being about that sector's
# economics. Checked before the positive rules.
_BILL_NEGATIVE: dict[str, re.Pattern[str]] = {
    "energie": re.compile(r"\bessais nucleaires\b"),
    "assurance": re.compile(r"\bassurance (chomage|maladie|vieillesse)\b"),
    "jeux": re.compile(r"\bjeux olympiques\b|\bjeux paralympiques\b"),
    "transport": re.compile(r"\btransport(s)? scolaires?\b"),
    "environnement": re.compile(r"\bassainissement cadastral\b"),
}

_BILL_RULES: dict[str, re.Pattern[str]] = {
    "energie": re.compile(
        r"\benergi|\benerget|\bnucleaire|\bhydroelectri|\bhydrocarbure|\bcarburant"
        r"|\bphotovolta|\beolien|\belectricite\b|\bpetrol|\bhydrogene\b|\bedf\b"
        r"|\bgaz\b|\braffinage\b"
    ),
    "banque": re.compile(
        r"\bbancaire|\bbanque|\bepargne\b|\bmarches financiers\b|\bsecteur financier\b"
        r"|\bfinancement des entreprises\b|\bcredit (a la consommation|immobilier)\b"
        r"|\bactifs numeriques\b|\bcrypto"
    ),
    "assurance": re.compile(
        r"\bassuran|\bmutuelles?\b|\bprevoyance\b|\bcomplementaires? sante\b|\borganismes complementaires\b"
    ),
    # Health as an industry (drugs, devices, care providers), not public health at large.
    "sante": re.compile(
        r"\bmedicament|\bpharmac|\bdispositifs medicaux\b|\bdeserts medicaux\b|\bacces aux soins\b"
        r"|\bcentres de sante\b|\binnovation therapeut|\bgrand age\b|\behpad\b"
        r"|\bsoignants par patient\b|\bprofession d infirmier\b|\bcliniques? privees?\b"
        r"|\bcomplementaires? sante\b"
    ),
    "defense": re.compile(
        r"\bprogrammation militaire\b|\barmement|\binteressant la defense\b|\bindustrie de defense\b"
        r"|\bbase industrielle et technologique\b|\bexportations? d armes\b"
    ),
    "numerique": re.compile(
        r"\bnumerique|\bcyber|\breseaux sociaux\b|\bplateformes? (en ligne|numerique)"
        r"|\bintelligence artificielle\b|\bdonnees personnelles\b|\btelecom"
        r"|\bdemarchage telephonique\b|\bfibre optique\b|\bcentres de donnees\b|\bhaine en ligne\b"
    ),
    "agriculture": re.compile(
        r"\bagricol|\bagricult|\bagroalimentaire|\bdenrees alimentaires\b|\balimentation\b"
        r"|\bpesticide|\bphyto|\bcultures vegetales\b|\bviticol|\belevage\b|\beleveurs\b"
        r"|\bmercosur\b|\bcadmium\b|\bforestier|\bpeche maritime\b|\bneonicotinoide"
    ),
    "immobilier": re.compile(
        r"\blogement|\blocatifs?\b|\blocatives?\b|\bloyers?\b|\bcopropriete|\burbanisme\b"
        r"|\bhabitat\b|\bbaux commerciaux\b|\bmeubles de tourisme\b|\bmarche immobilier\b"
        r"|\binvestissement (locatif|immobilier)\b|\bfiscalite immobiliere\b"
    ),
    "btp": re.compile(
        r"\bcommande publique\b|\bconstruction\b|\burbanisme\b"
        r"|\brelance .{0,30}logement\b|\btravaux publics\b|\bautorout"
    ),
    "transport": re.compile(
        r"\btransports?\b|\bferroviaire|\baerien|\bautorout|\bmaritime\b|\bsncf\b"
        r"|\baeroport|\bportuaire|\bvelique\b|\bnavigation aerienne\b"
    ),
    "automobile": re.compile(
        r"\bautomobile|\bvehicules?\b|\bzones a faibles emissions\b|\bflottes\b"
        r"|\bmobilites? (durables?|electriques?)\b|\bmalus\b|\bbornes de recharge\b"
    ),
    "industrie": re.compile(
        r"\bindustrie verte\b|\bsouverainete industrielle\b|\breindustrialisation\b|\bsiderurg"
        r"|\barcelormittal\b|\bperfluoroalkyl|\bpfas\b|\bplastiques?\b|\bindustrie textile\b"
        r"|\bindustries agroalimentaires\b|\bexploitation miniere\b|\bpolitique industrielle\b"
    ),
    "conso": re.compile(
        r"\bgrande distribution\b|\bvie chere\b|\btitre restaurant\b|\bdebits de boissons\b"
        r"|\bnegociations commerciales\b|\brelations commerciales\b|\bgrande consommation\b"
        r"|\bfournisseurs et distributeurs\b|\bbaux commerciaux\b|\bcommercants\b"
        r"|\bindustrie textile\b|\bfast fashion\b|\binfluence commerciale\b|\bconsommateurs?\b"
        r"|\bmarges\b|\balcool|\btabac\b|\bcosmetique"
    ),
    "medias": re.compile(
        r"\bmedias?\b|\baudiovisuel|\bpresse\b|\bdroits voisins\b|\bcinema\b"
        r"|\bjournalis|\bsport professionnel\b"
    ),
    "environnement": re.compile(
        r"\beau potable\b|\bassainissement\b|\bdechets?\b|\bemballages?\b|\brecyclage\b"
        r"|\beconomie circulaire\b"
    ),
    "jeux": re.compile(
        r"\bjeux d argent\b|\bparis sportifs\b|\bcasinos?\b|\bjeux de hasard\b"
        r"|\bfrancaise des jeux\b|\bloterie"
    ),
}


@dataclass(frozen=True)
class BillClass:
    sectors: tuple[str, ...]
    transversal: bool
    budget: bool  # social-security budget: sector-relevant but very broad


def classify_bill(title: str) -> BillClass:
    """Sectors a bill is about, from its title alone."""
    t = fold(title)
    if _TRANSVERSAL.search(t):
        return BillClass((), True, False)
    if _SOCIAL_BUDGET.search(t):
        return BillClass(("sante", "assurance"), False, True)
    if _NOT_SECTORAL.search(t):
        return BillClass((), False, False)
    out = []
    for slug, rule in _BILL_RULES.items():
        if not rule.search(t):
            continue
        neg = _BILL_NEGATIVE.get(slug)
        if neg is not None and neg.search(t):
            continue
        out.append(slug)
    return BillClass(tuple(out), False, False)


# ---------------------------------------------------------------------------
# Companies outside the curated registry
# ---------------------------------------------------------------------------

_FORM_SECTORS: dict[str, str] = {
    "sci": "immobilier",
    "sccv": "immobilier",
    "scpi": "immobilier",
    "sc immobiliere": "immobilier",
    "opci": "immobilier",
    "gfa": "agriculture",
    "gfv": "agriculture",
    "gfr": "agriculture",
    "gaec": "agriculture",
    "earl": "agriculture",
    "scea": "agriculture",
    "cuma": "agriculture",
    "sica": "agriculture",
    "gf": "agriculture",
}

_COMPANY_RULES: dict[str, re.Pattern[str]] = {
    "immobilier": re.compile(r"\bimmobili|\bfonciere\b|\bimmo\b|\bpromotion immobiliere\b|\blotissement"),
    "agriculture": re.compile(
        r"\bagricol|\bviticol|\bvignoble|\bvigneron|\bferme\b|\belevage\b|\bforestier"
        r"|\bgroupement foncier\b|\bterres? de liens?\b|\bcooperative laitiere\b|\bcave cooperative\b"
        r"|\bmethanisation\b|\bdomaine viticole\b"
    ),
    "sante": re.compile(
        r"\bpharmac|\bmedical|\bmedecin|\bclinique\b|\bsante\b|\blaboratoire|\bbiotech"
        r"|\bdentaire\b|\bveterinaire|\bradiologie\b|\bkinesi|\binfirmi|\behpad\b|\bambulance"
    ),
    "energie": re.compile(
        r"\benergi|\benergy\b|\bsolaire\b|\bphotovolta|\beolien|\bhydroelectri|\bmethanisation\b"
        r"|\bcentrales villageoises\b|\benr\b|\bwatt\b"
    ),
    "banque": re.compile(r"\bbanque\b|\bcredit\b|\bcapital investissement\b|\bgestion d actifs\b"),
    "assurance": re.compile(r"\bassurances?\b|\bmutuelle\b|\bcourtage\b"),
    "numerique": re.compile(r"\binformatique\b|\bdigital\b|\bnumerique\b|\bsoftware\b|\blogiciel|\bweb\b|\bdata\b"),
    "btp": re.compile(r"\bconstruction\b|\bbatiment\b|\btravaux\b|\bbtp\b|\bmaconnerie\b|\bterrassement\b"),
    "transport": re.compile(r"\btransports?\b|\btaxi\b|\blogistique\b"),
    "automobile": re.compile(r"\bautomobiles?\b|\bgarage\b|\bconcession(naire)? auto"),
    "medias": re.compile(r"\bpresse\b|\beditions?\b|\bmedias?\b|\bradio\b|\btelevision\b|\bjournal\b"),
    "conso": re.compile(r"\brestaurant|\bhotel\b|\bboulangerie\b|\bbrasserie\b|\btabac\b|\bsupermarche\b"),
}


def classify_company(folded_name: str, legal_form: str | None = None) -> tuple[str, ...]:
    """Best-effort sectors for a company the registry does not know."""
    out: list[str] = []
    if legal_form and legal_form in _FORM_SECTORS:
        out.append(_FORM_SECTORS[legal_form])
    for slug, rule in _COMPANY_RULES.items():
        if slug not in out and rule.search(folded_name):
            out.append(slug)
    return tuple(out)


# ---------------------------------------------------------------------------
# Lobbying registry vocabulary
# ---------------------------------------------------------------------------

AGORA_DOMAIN_SECTORS: dict[str, tuple[str, ...]] = {
    fold(k): v
    for k, v in {
        "Agriculture, agroalimentaire": ("agriculture",),
        "Agriculture": ("agriculture",),
        "Industrie agroalimentaire": ("agriculture",),
        "Sécurité et normes alimentaires": ("agriculture",),
        "Forêt": ("agriculture",),
        "Pêche": ("agriculture",),
        "Santé": ("sante",),
        "Système de santé et médico-social": ("sante",),
        "Soins et maladies": ("sante",),
        "Médicaments": ("sante",),
        "Remboursements": ("sante", "assurance"),
        "Energie": ("energie",),
        "Energies renouvelables": ("energie",),
        "Energies fossiles": ("energie",),
        "Energie nucléaire": ("energie",),
        "Construction, logement, aménagement du territoire": ("immobilier", "btp"),
        "Logement": ("immobilier",),
        "Construction": ("btp",),
        "Bâtiments et travaux publics": ("btp",),
        "Infrastructures": ("btp",),
        "Partenariats public/privé": ("btp",),
        "Transports, logistique": ("transport",),
        "Transport de voyageurs": ("transport",),
        "Transport de fret": ("transport",),
        "Impact des transports individuels": ("automobile",),
        "Impact des transports marchands et collectifs": ("transport",),
        "Transports alternatifs": ("transport",),
        "Sécurité routière": ("automobile",),
        "Numérique": ("numerique",),
        "Marché du numérique": ("numerique",),
        "Protection des données": ("numerique",),
        "E-commerce": ("numerique", "conso"),
        "Télécommunications": ("numerique",),
        "Infrastructures de télécommunications": ("numerique",),
        "Accès à l’Internet": ("numerique",),
        "Accès aux moyens de télécommunications": ("numerique",),
        "Banques, assurances, secteur financier": ("banque", "assurance"),
        "Banques": ("banque",),
        "Finances": ("banque",),
        "Marchés réglementés": ("banque",),
        "Assurances": ("assurance",),
        "Politique industrielle": ("industrie",),
        "Impact de l'activité industrielle": ("industrie",),
        "Produits chimiques": ("industrie",),
        "Normes de production": ("industrie",),
        "Défense, sécurité": ("defense",),
        "Défense": ("defense",),
        "Sécurité nationale": ("defense",),
        "Aéronautique, aérospatiale": ("defense",),
        "Industrie aéronautique": ("defense",),
        "Industrie aérospatiale": ("defense",),
        "Concurrence, consommation": ("conso",),
        "Publicité": ("conso", "medias"),
        "Tourisme/hôtellerie": ("conso",),
        "Médias": ("medias",),
        "Audiovisuel": ("medias",),
        "Presse écrite": ("medias",),
        "Cinéma": ("medias",),
        "Musique": ("medias",),
        "Livre": ("medias",),
        "Droit d'auteur": ("medias",),
        "Déchets": ("environnement",),
        "Eaux": ("environnement",),
        "Qualité de l'eau": ("environnement",),
        "Dépollution": ("environnement",),
        "Jeux d'argent": ("jeux",),
    }.items()
}


def sectors_for_domains(domains: list[str]) -> tuple[str, ...]:
    out: list[str] = []
    for d in domains:
        for s in AGORA_DOMAIN_SECTORS.get(fold(d), ()):
            if s not in out:
                out.append(s)
    return tuple(out)


# ---------------------------------------------------------------------------
# Permanent committees (Assemblée nationale + Sénat)
# ---------------------------------------------------------------------------

_COMMITTEE_RULES: list[tuple[re.Pattern[str], tuple[str, ...]]] = [
    (re.compile(r"\bdefense\b|\bforces armees\b"), ("defense",)),
    (re.compile(r"\bfinances\b"), ("banque", "assurance")),
    (
        re.compile(r"\baffaires economiques\b"),
        ("energie", "agriculture", "immobilier", "industrie", "conso", "numerique"),
    ),
    (re.compile(r"\baffaires sociales\b"), ("sante", "assurance")),
    (
        re.compile(r"\bdeveloppement durable\b|\bamenagement du territoire\b"),
        ("transport", "automobile", "environnement", "btp"),
    ),
    (re.compile(r"\baffaires culturelles\b|\bculture\b"), ("medias",)),
]


def committee_sectors(committee_label: str) -> tuple[str, ...]:
    t = fold(committee_label)
    for rule, sectors in _COMMITTEE_RULES:
        if rule.search(t):
            return sectors
    return ()
