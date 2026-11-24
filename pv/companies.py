"""Company identity: turn free-text HATVP names into stable company keys.

Declarants type company names by hand ("PEA TOTAL", "Total énergies SE",
"TOTALENERGIE"…). A curated registry of the companies that actually show up
in declarations maps those variants to one slug, with a ticker and sectors.
Anything outside the registry gets a slug derived from its name once legal
forms and account wrappers are removed.

Names that HATVP redacts ("SCI [Données non publiées]") or that are generic
("parts sociales") are kept as holdings but are *not identifiable*: they
never take part in cross-declaration or cross-person matching.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pv import sectors as sectors_mod
from pv.text import REDACTED, clean, fold


@dataclass(frozen=True)
class Known:
    slug: str
    name: str
    pattern: re.Pattern[str]
    ticker: str | None
    sectors: tuple[str, ...]
    kind: str  # 'listed' | 'coop_bank' | 'unlisted'


@dataclass(frozen=True)
class CompanyRef:
    slug: str  # '' when not identifiable
    name: str
    kind: str  # 'listed' | 'coop_bank' | 'unlisted' | 'fund' | 'other' | 'unnamed'
    sectors: tuple[str, ...]
    ticker: str | None = None
    legal_form: str | None = None

    @property
    def identifiable(self) -> bool:
        return bool(self.slug)


def _k(slug: str, name: str, pattern: str, ticker: str | None, sectors: str, kind: str = "listed") -> Known:
    return Known(slug, name, re.compile(pattern), ticker, tuple(s for s in sectors.split(",") if s), kind)


# Order matters: the first pattern that matches wins, so specific entries
# (Dassault Systèmes, GDF Suez) come before the generic ones they contain.
# fmt: off
REGISTRY: list[Known] = [
    # --- énergie
    _k("totalenergies", "TotalEnergies", r"\btotal ?energie|\btotal (se|sa|fina|gabon)\b|^total$|\belf aquitaine\b", "TTE.PA", "energie"),
    _k("engie", "Engie", r"\bengie\b|\bgdf\b|\bgaz de france\b", "ENGI.PA", "energie"),
    _k("edf", "EDF", r"\bedf\b|\belectricite de france\b", None, "energie", "unlisted"),
    _k("rubis", "Rubis", r"^rubis\b", "RUI.PA", "energie"),
    _k("technip-energies", "Technip Energies", r"\btechnip ?energies\b", "TE.PA", "energie"),
    _k("technipfmc", "TechnipFMC", r"\btechnip", "FTI", "energie"),
    _k("vallourec", "Vallourec", r"\bvallourec\b", "VK.PA", "industrie,energie"),
    _k("neoen", "Neoen", r"\bneoen\b", None, "energie"),
    _k("voltalia", "Voltalia", r"\bvoltalia\b", "VLTSA.PA", "energie"),
    _k("gtt", "GTT", r"^gtt\b|\bgaz ?transport\b", "GTT.PA", "energie"),
    _k("iberdrola", "Iberdrola", r"\biberdrola\b", "IBE.MC", "energie"),
    _k("francaise-de-l-energie", "La Française de l'Énergie", r"\bfrancaise (de l )?energie\b", "FDE.PA", "energie"),
    _k("viridien", "Viridien (CGG)", r"\bcgg\b|\bviridien\b", "VIRI.PA", "energie"),
    _k("maurel-et-prom", "Maurel & Prom", r"\bmaurel\b", "MAU.PA", "energie"),
    _k("eni", "ENI", r"^eni( spa)?$", "ENI.MI", "energie"),
    _k("shell", "Shell", r"\b(royal dutch )?shell\b", "SHELL.AS", "energie"),
    _k("enercoop", "Enercoop", r"\benercoop\b", None, "energie", "unlisted"),
    _k("energie-partagee", "Énergie Partagée", r"\benergie partagee\b", None, "energie", "unlisted"),
    _k("nexans", "Nexans", r"\bnexans\b", "NEX.PA", "industrie,energie"),
    _k("mcphy", "McPhy", r"\bmc ?phy\b", None, "energie"),
    # --- banque, finance, assurance
    _k("lcl", "LCL", r"^lcl\b|\bcredit lyonnais\b", None, "banque", "unlisted"),
    _k("credit-agricole", "Crédit Agricole", r"\bcredit agricole\b|\bc agricole\b|\bcrca\b|\bcrcam\b|\bcasa\b|^ca$|^cr ca\b|^ca (sa|centre|nord|sud|alsace|brie|ile|alpes|loire|touraine|atlantique|aquitaine|normandie|languedoc|pyrenees|des|du|de|d)\b", "ACA.PA", "banque", "coop_bank"),
    _k("bnp-paribas", "BNP Paribas", r"\bbnp\b|\bparibas\b|\bparisbas\b", "BNP.PA", "banque"),
    _k("societe-generale", "Société Générale", r"\bsociete generale\b|\bste generale\b|\bsoc ?gen\b|^sg$", "GLE.PA", "banque"),
    _k("credit-mutuel", "Crédit Mutuel", r"\bcredit mutuel\b|^cic\b|\barkea\b|^cmb\b", None, "banque", "coop_bank"),
    _k("caisse-d-epargne", "Caisse d'Épargne", r"\bcaisses? d ?epargne\b|\bcaisse epargne\b|\bsociete locale d epargne\b|^sle\b|\bcepac\b", None, "banque", "coop_bank"),
    _k("credit-cooperatif", "Crédit Coopératif", r"\bcredit cooperatif\b", None, "banque", "coop_bank"),
    _k("banque-populaire", "Banque Populaire", r"\bbanques? populaires?\b|\bbanque pop\b|^bred\b|\bcasden\b|^bpce\b|^bp (alc|aura|ge|gc|vf|mc|n|s|sud|nord|med|occitane|grand ouest)\b", None, "banque", "coop_bank"),
    _k("natixis", "Natixis", r"\bnatixis\b|^natexis\b", None, "banque", "unlisted"),
    _k("banco-santander", "Banco Santander", r"\bsantander\b", "SAN.MC", "banque"),
    _k("hsbc", "HSBC", r"^hsbc\b", "HSBA.L", "banque"),
    _k("intesa-sanpaolo", "Intesa Sanpaolo", r"\bintesa\b", "ISP.MI", "banque"),
    _k("dexia", "Dexia", r"^dexia\b", None, "banque", "unlisted"),
    _k("berkshire-hathaway", "Berkshire Hathaway", r"\bberkshire\b", "BRK-B", "banque,assurance"),
    _k("prosus", "Prosus", r"^prosus\b", "PRX.AS", "numerique"),
    _k("la-nef", "La Nef", r"^(la )?nef$|\bsociete financiere de la nef\b", None, "banque", "coop_bank"),
    _k("la-banque-postale", "La Banque Postale", r"\bbanque postale\b", None, "banque", "unlisted"),
    _k("amundi", "Amundi", r"^amundi( sa)?$", "AMUN.PA", "banque"),
    _k("axa", "AXA", r"^axa\b", "CS.PA", "assurance"),
    _k("scor", "SCOR", r"^scor\b", "SCR.PA", "assurance"),
    _k("cnp-assurances", "CNP Assurances", r"^cnp\b", None, "assurance", "unlisted"),
    _k("coface", "Coface", r"\bcoface\b", "COFA.PA", "assurance"),
    _k("allianz", "Allianz", r"^allianz\b", "ALV.DE", "assurance"),
    _k("ing", "ING", r"^ing( groep| group| direct)?$", "INGA.AS", "banque"),
    _k("eurazeo", "Eurazeo", r"\beurazeo\b", "RF.PA", "banque"),
    _k("wendel", "Wendel", r"^wendel\b", "MF.PA", "banque"),
    _k("tikehau", "Tikehau Capital", r"\btikehau\b", "TKO.PA", "banque"),
    _k("euronext", "Euronext", r"^euronext\b", "ENX.PA", "banque"),
    _k("edenred", "Edenred", r"\bedenred\b", "EDEN.PA", "conso"),
    _k("worldline", "Worldline", r"\bworldline\b|\bwordline\b", "WLN.PA", "numerique,banque"),
    # --- santé
    _k("sanofi", "Sanofi", r"\bsanofi\b", "SAN.PA", "sante"),
    _k("euroapi", "Euroapi", r"\beuroapi\b", "EAPI.PA", "sante"),
    _k("ipsen", "Ipsen", r"^ipsen\b", "IPN.PA", "sante"),
    _k("biomerieux", "bioMérieux", r"\bbio ?merieux\b", "BIM.PA", "sante"),
    _k("sartorius-stedim", "Sartorius Stedim", r"\bsartorius\b", "DIM.PA", "sante"),
    _k("essilorluxottica", "EssilorLuxottica", r"\bessilor|\bluxottica\b", "EL.PA", "sante,conso"),
    _k("eurofins", "Eurofins", r"\beurofins\b", "ERF.PA", "sante"),
    _k("emeis", "Emeis (Orpea)", r"\borpea\b|\bemeis\b", "EMEIS.PA", "sante"),
    _k("clariane", "Clariane (Korian)", r"\bkorian\b|\bclariane\b", "CLARI.PA", "sante"),
    _k("ramsay-sante", "Ramsay Santé", r"\bramsay\b", "GDS.PA", "sante"),
    _k("genfit", "Genfit", r"\bgenfit\b", "GNFT.PA", "sante"),
    _k("innate-pharma", "Innate Pharma", r"\binnate\b", "IPH.PA", "sante"),
    _k("carmat", "Carmat", r"\bcarmat\b", None, "sante"),
    _k("novacyt", "Novacyt", r"\bnovacyt\b", None, "sante"),
    _k("valneva", "Valneva", r"\bvalneva\b", "VLA.PA", "sante"),
    _k("abivax", "Abivax", r"\babivax\b", "ABVX.PA", "sante"),
    _k("nanobiotix", "Nanobiotix", r"\bnanobiotix\b", "NANO.PA", "sante"),
    _k("dbv-technologies", "DBV Technologies", r"^dbv\b", "DBV.PA", "sante"),
    _k("onxeo", "Onxeo", r"\bonxeo\b", None, "sante"),
    _k("medincell", "Medincell", r"\bmedincell\b", "MEDCL.PA", "sante"),
    _k("ab-science", "AB Science", r"^ab science\b", "AB.PA", "sante"),
    _k("inventiva", "Inventiva", r"^inventiva\b", "IVA.PA", "sante"),
    _k("celyad", "Celyad Oncology", r"^celyad\b", None, "sante"),
    _k("virbac", "Virbac", r"\bvirbac\b", "VIRP.PA", "sante"),
    _k("boiron", "Boiron", r"^boiron\b", "BOI.PA", "sante"),
    _k("novartis", "Novartis", r"\bnovartis\b", "NOVN.SW", "sante"),
    _k("novo-nordisk", "Novo Nordisk", r"\bnovo nordisk\b", "NOVO-B.CO", "sante"),
    _k("pfizer", "Pfizer", r"\bpfizer\b", "PFE", "sante"),
    _k("roche", "Roche", r"^roche( holding)?( bj| ag)?$", "ROG.SW", "sante"),
    _k("merck", "Merck", r"^merck\b", "MRK", "sante"),
    _k("bayer", "Bayer", r"^bayer\b", "BAYN.DE", "sante,agriculture"),
    _k("fresenius", "Fresenius", r"\bfresenius\b", "FRE.DE", "sante"),
    _k("astrazeneca", "AstraZeneca", r"\bastra ?zeneca\b", "AZN.L", "sante"),
    _k("moderna", "Moderna", r"^moderna\b", "MRNA", "sante"),
    # --- défense, aéronautique
    _k("dassault-systemes", "Dassault Systèmes", r"\bdassault syst", "DSY.PA", "numerique"),
    _k("dassault-aviation", "Dassault Aviation", r"\bdassault aviation\b", "AM.PA", "defense"),
    _k("thales", "Thales", r"\bthales\b", "HO.PA", "defense"),
    _k("safran", "Safran", r"\bsafran\b", "SAF.PA", "defense"),
    _k("airbus", "Airbus", r"\bairbus\b|^eads\b", "AIR.PA", "defense,transport"),
    _k("exail", "Exail Technologies", r"\bexail\b", "EXA.PA", "defense"),
    _k("naval-group", "Naval Group", r"\bnaval group\b", None, "defense", "unlisted"),
    # --- numérique, télécoms
    _k("orange", "Orange", r"^orange( sa)?$|\bfrance telecom\b", "ORA.PA", "numerique"),
    _k("iliad", "Iliad", r"^iliad\b", None, "numerique", "unlisted"),
    _k("capgemini", "Capgemini", r"\bcap ?gemini\b", "CAP.PA", "numerique"),
    _k("atos", "Atos", r"^atos\b", "ATO.PA", "numerique"),
    _k("sopra-steria", "Sopra Steria", r"\bsopra\b", "SOP.PA", "numerique"),
    _k("stmicroelectronics", "STMicroelectronics", r"\bst ?micro", "STMPA.PA", "numerique,industrie"),
    _k("soitec", "Soitec", r"\bsoitec\b", "SOI.PA", "numerique,industrie"),
    _k("ubisoft", "Ubisoft", r"\bubisoft\b", "UBI.PA", "numerique,medias"),
    _k("ovhcloud", "OVHcloud", r"^ovh", "OVH.PA", "numerique"),
    _k("teleperformance", "Teleperformance", r"\btele ?performance\b", "TEP.PA", "numerique"),
    _k("nokia", "Nokia", r"\bnokia\b", "NOKIA.HE", "numerique"),
    _k("deutsche-telekom", "Deutsche Telekom", r"\bdeutsche telekom\b", "DTE.DE", "numerique"),
    _k("alcatel-lucent", "Alcatel-Lucent", r"\balcatel\b", None, "numerique", "unlisted"),
    _k("asml", "ASML", r"^asml\b", "ASML.AS", "numerique,industrie"),
    _k("asm-international", "ASM International", r"^asm( international)?$", "ASM.AS", "numerique,industrie"),
    _k("micron", "Micron Technology", r"^micron\b", "MU", "numerique"),
    _k("cellnex", "Cellnex Telecom", r"\bcellnex\b", "CLNX.MC", "numerique"),
    _k("eutelsat", "Eutelsat", r"\beutelsat\b", "ETL.PA", "numerique"),
    _k("kalray", "Kalray", r"^kalray\b", "ALKAL.PA", "numerique"),
    _k("quadient", "Quadient", r"^quadient\b|^neopost\b", "QDT.PA", "numerique"),
    _k("sword-group", "Sword Group", r"^sword( group)?$", "SWP.PA", "numerique"),
    _k("solutions-30", "Solutions 30", r"\bsolutions 30\b", "S30.PA", "numerique"),
    _k("sap", "SAP", r"^sap( se| ag)?$", "SAP.DE", "numerique"),
    _k("microsoft", "Microsoft", r"\bmicrosoft\b", "MSFT", "numerique"),
    _k("apple", "Apple", r"^apple( inc)?$", "AAPL", "numerique"),
    _k("alphabet", "Alphabet (Google)", r"^alphabet\b|^google\b", "GOOGL", "numerique"),
    _k("amazon", "Amazon", r"^amazon\b", "AMZN", "numerique,conso"),
    _k("meta", "Meta", r"^meta( platforms)?$|^facebook\b", "META", "numerique"),
    _k("nvidia", "Nvidia", r"\bnvidia\b", "NVDA", "numerique"),
    _k("atari", "Atari", r"^atari\b", None, "numerique"),
    _k("archos", "Archos", r"^archos\b", None, "numerique"),
    _k("solocal", "Solocal", r"\bsolocal\b|\bpages jaunes\b", None, "numerique,medias"),
    # --- luxe, consommation, distribution, agroalimentaire
    _k("lvmh", "LVMH", r"\blvmh\b|\bmoet hennessy\b|\blouis vuitton\b", "MC.PA", "conso"),
    _k("hermes", "Hermès", r"^hermes\b", "RMS.PA", "conso"),
    _k("kering", "Kering", r"^kering\b|^ppr$", "KER.PA", "conso"),
    _k("l-oreal", "L'Oréal", r"\bl ?oreal\b|^oreal\b", "OR.PA", "conso"),
    _k("christian-dior", "Christian Dior", r"\bchristian dior\b|^dior\b", "CDI.PA", "conso"),
    _k("pernod-ricard", "Pernod Ricard", r"\bpernod\b", "RI.PA", "conso,agriculture"),
    _k("remy-cointreau", "Rémy Cointreau", r"\bremy cointreau\b", "RCO.PA", "conso,agriculture"),
    _k("danone", "Danone", r"\bdanone\b", "BN.PA", "agriculture,conso"),
    _k("nestle", "Nestlé", r"^nestle\b", "NESN.SW", "agriculture,conso"),
    _k("bonduelle", "Bonduelle", r"\bbonduelle\b", "BON.PA", "agriculture"),
    _k("bic", "BIC", r"^bic( sa)?$|\bsociete bic\b", "BB.PA", "conso"),
    _k("seb", "Groupe SEB", r"^(groupe )?seb( sa)?$", "SK.PA", "conso,industrie"),
    _k("carrefour", "Carrefour", r"^carrefour( sa)?$", "CA.PA", "conso"),
    _k("auchan", "Auchan", r"\bauchan\b", None, "conso", "unlisted"),
    _k("systeme-u", "Coopérative U (Système U)", r"\bsysteme u\b|\bcooperative u\b|^u (centrale|enseigne)\b", None, "conso", "unlisted"),
    _k("intermarche", "Intermarché (Les Mousquetaires)", r"\bintermarche\b|\bmousquetaires\b", None, "conso", "unlisted"),
    _k("e-leclerc", "E.Leclerc (Galec)", r"\bgalec\b|^e leclerc\b", None, "conso", "unlisted"),
    _k("lidl", "Lidl", r"^lidl\b", None, "conso", "unlisted"),
    _k("casino", "Casino Guichard-Perrachon", r"\bcasino guichard\b|^(groupe )?casino$", "CO.PA", "conso"),
    _k("rallye", "Rallye", r"^rallye\b", None, "conso"),
    _k("fnac-darty", "Fnac Darty", r"\bfnac\b|^darty\b", "FNAC.PA", "conso"),
    _k("coca-cola", "Coca-Cola", r"\bcoca cola\b", "KO", "conso,agriculture"),
    _k("puma", "Puma", r"^puma( se)?$", "PUM.DE", "conso"),
    _k("accor", "Accor", r"^accor\b", "AC.PA", "conso"),
    _k("pierre-et-vacances", "Pierre & Vacances", r"\bpierre et vacances\b|\bpierre vacances\b", "VAC.PA", "conso,immobilier"),
    _k("sodexo", "Sodexo", r"\bsodexo\b", "SW.PA", "conso"),
    _k("compagnie-des-alpes", "Compagnie des Alpes", r"\bcompagnie (des )?alpes\b", "CDA.PA", "conso"),
    _k("heineken", "Heineken", r"^heineken\b", "HEIA.AS", "conso,agriculture"),
    _k("carlsberg", "Carlsberg", r"^carlsberg\b", "CARL-B.CO", "conso,agriculture"),
    _k("laurent-perrier", "Laurent-Perrier", r"\blaurent perrier\b", "LPE.PA", "conso,agriculture"),
    _k("philip-morris", "Philip Morris", r"\bphilip morris\b", "PM", "conso"),
    _k("costco", "Costco", r"^costco\b", "COST", "conso"),
    _k("interparfums", "Interparfums", r"\binter ?parfums\b", "ITP.PA", "conso"),
    _k("maisons-du-monde", "Maisons du Monde", r"\bmaisons (du )?monde\b", "MDM.PA", "conso"),
    _k("elior", "Elior", r"^elior\b", "ELIOR.PA", "conso"),
    _k("inditex", "Inditex", r"^inditex\b", "ITX.MC", "conso"),
    _k("altria", "Altria", r"^altria\b", "MO", "conso"),
    _k("limagrain", "Limagrain (Vilmorin)", r"\blimagrain\b|\bvilmorin\b", None, "agriculture", "unlisted"),
    # --- industrie, matériaux, automobile
    _k("air-liquide", "Air Liquide", r"\bair liquide\b", "AI.PA", "industrie"),
    _k("saint-gobain", "Saint-Gobain", r"\b(saint|st) gobain\b", "SGO.PA", "industrie,btp"),
    _k("michelin", "Michelin", r"\bmichelin\b", "ML.PA", "automobile,industrie"),
    _k("arkema", "Arkema", r"\barkema\b", "AKE.PA", "industrie"),
    _k("schneider-electric", "Schneider Electric", r"\bschneider\b", "SU.PA", "industrie,energie"),
    _k("legrand", "Legrand", r"^legrand( sa| se)?$", "LR.PA", "industrie"),
    _k("alstom", "Alstom", r"\balstom\b", "ALO.PA", "transport,industrie"),
    _k("arcelormittal", "ArcelorMittal", r"\barcelor", "MT.AS", "industrie"),
    _k("eramet", "Eramet", r"^eramet\b", "ERA.PA", "industrie"),
    _k("aperam", "Aperam", r"^aperam\b", "APAM.AS", "industrie"),
    _k("haulotte", "Haulotte", r"\bhaulotte\b", "PIG.PA", "industrie"),
    _k("imerys", "Imerys", r"^imerys\b", "NK.PA", "industrie"),
    _k("rexel", "Rexel", r"^rexel\b", "RXL.PA", "industrie"),
    _k("linde", "Linde", r"^linde( plc)?$", "LIN", "industrie"),
    _k("siemens", "Siemens", r"^siemens\b", "SIE.DE", "industrie"),
    _k("spie", "Spie", r"^spie( sa)?$", "SPIE.PA", "btp,energie"),
    _k("elis", "Elis", r"^elis( sa)?$", "ELIS.PA", "conso"),
    _k("bureau-veritas", "Bureau Veritas", r"\bbureau veritas\b", "BVI.PA", "industrie"),
    _k("derichebourg", "Derichebourg", r"\bderichebourg\b", "DBG.PA", "environnement"),
    _k("forvia", "Forvia (Faurecia)", r"\bforvia\b|\bfaurecia\b", "FRVIA.PA", "automobile"),
    _k("valeo", "Valeo", r"^valeo\b", "FR.PA", "automobile"),
    _k("opmobility", "OPmobility (Plastic Omnium)", r"\bopmobility\b|\bplastic omnium\b", "OPM.PA", "automobile"),
    _k("renault", "Renault", r"^renault\b", "RNO.PA", "automobile"),
    _k("stellantis", "Stellantis", r"\bstellantis\b|^peugeot( sa)?$|^psa\b", "STLAP.PA", "automobile"),
    _k("tesla", "Tesla", r"^tesla\b", "TSLA", "automobile"),
    _k("ferrari", "Ferrari", r"^ferrari\b", "RACE.MI", "automobile"),
    _k("trigano", "Trigano", r"^trigano\b", "TRI.PA", "automobile"),
    _k("gaussin", "Gaussin", r"^gaussin\b", None, "automobile"),
    _k("crh", "CRH", r"^crh( plc)?$", "CRH", "btp,industrie"),
    _k("akzonobel", "AkzoNobel", r"\bakzo ?nobel\b", "AKZA.AS", "industrie"),
    _k("rio-tinto", "Rio Tinto", r"\brio tinto\b", "RIO.L", "industrie"),
    _k("barrick-gold", "Barrick Gold", r"\bbarrick\b", "GOLD", "industrie"),
    _k("chargeurs", "Chargeurs", r"^chargeurs\b", "CRI.PA", "industrie"),
    _k("thermador", "Thermador Groupe", r"^thermador\b", "THEP.PA", "industrie"),
    _k("rtx", "RTX (Raytheon)", r"^rtx( corp)?$|\braytheon\b", "RTX", "defense"),
    _k("beneteau", "Bénéteau", r"\bbeneteau\b", "BEN.PA", "conso"),
    _k("touax", "Touax", r"^touax\b", "TOUP.PA", "transport"),
    # --- BTP, immobilier, environnement
    _k("bouygues", "Bouygues", r"\bbouygues\b", "EN.PA", "btp,numerique,medias"),
    _k("vinci", "Vinci", r"^vinci( sa)?$", "DG.PA", "btp,transport"),
    _k("eiffage", "Eiffage", r"\beiffage\b", "FGR.PA", "btp,transport"),
    _k("veolia", "Veolia", r"\bveolia\b|\bvivendi environnement\b", "VIE.PA", "environnement,energie"),
    _k("suez", "Suez", r"^suez\b", None, "environnement", "unlisted"),
    _k("nexity", "Nexity", r"\bnexity\b", "NXI.PA", "immobilier"),
    _k("unibail-rodamco-westfield", "Unibail-Rodamco-Westfield", r"\bunibail\b", "URW.PA", "immobilier"),
    _k("klepierre", "Klépierre", r"\bklepierre\b", "LI.PA", "immobilier"),
    _k("gecina", "Gecina", r"^gecina\b", "GFC.PA", "immobilier"),
    _k("icade", "Icade", r"^icade\b", "ICAD.PA", "immobilier"),
    _k("covivio", "Covivio", r"\bcovivio\b", "COV.PA", "immobilier"),
    _k("kaufman-broad", "Kaufman & Broad", r"\bkaufman\b", "KOF.PA", "immobilier"),
    _k("terre-de-liens", "Terre de Liens", r"\bterres? de liens?\b", None, "agriculture", "unlisted"),
    # --- transports, tourisme, jeux
    _k("air-france-klm", "Air France-KLM", r"\bair france\b", "AF.PA", "transport"),
    _k("adp", "Aéroports de Paris", r"^adp\b|\baeroports? de paris\b", "ADP.PA", "transport"),
    _k("getlink", "Getlink", r"\bgetlink\b|\beurotunnel\b", "GET.PA", "transport"),
    _k("fdj", "FDJ (Française des Jeux)", r"^fdj\b|\bfrancaise des jeux\b", "FDJU.PA", "jeux"),
    # --- médias
    _k("vivendi", "Vivendi", r"^vivendi\b", "VIV.PA", "medias"),
    _k("universal-music-group", "Universal Music Group", r"\buniversal music\b|^umg\b", "UMG.AS", "medias"),
    _k("havas", "Havas", r"^havas\b", "HAVAS.AS", "medias"),
    _k("louis-hachette", "Louis Hachette Group", r"\blouis hachette\b", "ALHG.PA", "medias"),
    _k("tf1", "TF1", r"^tf1\b", "TFI.PA", "medias"),
    _k("m6", "M6", r"^m6\b|\bmetropole television\b", "MMT.PA", "medias"),
    _k("publicis", "Publicis", r"\bpublicis\b", "PUB.PA", "medias"),
    _k("lagardere", "Lagardère", r"^lagardere\b", "MMB.PA", "medias"),
    _k("canal-plus", "Canal+", r"^canal ?(plus)?$", "CAN.L", "medias"),
    _k("bollore", "Bolloré", r"^bollore\b", "BOL.PA", "medias,transport"),
    _k("jcdecaux", "JCDecaux", r"\bjc ?decaux\b", "DEC.PA", "medias"),
    _k("ol-groupe", "Eagle Football Group (OL)", r"\bol groupe\b|\beagle football\b|\bolympique lyonnais\b", None, "medias"),
]

# fmt: on

_BY_SLUG = {k.slug: k for k in REGISTRY}


def known(slug: str) -> Known | None:
    return _BY_SLUG.get(slug)


# ---------------------------------------------------------------------------
# Canonicalisation
# ---------------------------------------------------------------------------

_JUNK = re.compile(
    r"^(0|x+|nc|n a|na|ras|neant|aucun|aucune|aucuns|sans|sans objet|non|rien|nul|non concerne"
    r"|pas de participations?|je n ai pas.*|aucune participation.*|neant .*)$"
)

# Collective investment wrappers: not a company whose fortunes a vote can move.
_FUND = re.compile(
    r"\b(fcp|fcpi|fcpr|fcpe|fip|sicav|opcvm|etf|tracker|ucits|msci|lyxor|ishares|scpi|opci"
    r"|assurance vie|contrat d assurance|fonds? commun|fonds euros?|unites? de compte"
    r"|lbpam|cpr|bft|robeco|carmignac|comgest|dnca|echiquier|pictet|fidelity|blackrock|vanguard"
    r"|innocross|isf|sofica|corum|pierval|mirova|indosuez)\b"
    r"|^(amundi|lyxor|etoile?|et act|uni hoche|sg actions|bnpp|bnp paribas acces) \w"
)

# "<société> CR MUTUEL COMPTE TITRE": the bank is only where the account sits.
_BANKS = (
    r"(?:(?:cr|credit) mutuel|credit agricole|bnp(?: paribas)?|societe generale|caisse d epargne"
    r"|banque populaire|lcl|boursorama|boursobank|bourse direct|fortuneo)"
)
_CUSTODIAN = re.compile(
    rf"\b{_BANKS} (?:compte titres?|comptes titres|pea|cto)\b|\b(?:compte titres?|comptes titres|pea|cto) {_BANKS}\b"
)

_LEAD_WRAPPER = re.compile(
    r"^((pea pme|pea|pee|perco|per|cto|comptes? titres?|portefeuille|actions?|titres|parts? sociales?|parts?)"
    r"( (de|du|des|d|la|le|l|dans|chez|sur|au|aux|en))*( |$))+"
)
_TRAIL_WRAPPER = re.compile(
    r"( (pea pme|pea|cto|comptes? titres?|actions?( a| b)?|titres|parts? sociales?|nominatif( pur)?|prime|en direct))+$"
)
_CORP_SUFFIX = re.compile(r" (sa|se|nv|plc|ag|inc|corp|spa|ltd|group|groupe|holding|international|intl)$")

LEGAL_FORMS = (
    "sc immobiliere",
    "societe civile immobiliere",
    "societe civile",
    "groupement foncier agricole",
    "groupement forestier",
    "groupement foncier",
    "selarl",
    "selas",
    "sasu",
    "sarl",
    "eurl",
    "sccv",
    "scpi",
    "scop",
    "scic",
    "scea",
    "gaec",
    "earl",
    "cuma",
    "spfpl",
    "sica",
    "sci",
    "sas",
    "snc",
    "scp",
    "scm",
    "sca",
    "gfa",
    "gfv",
    "gfr",
    "gie",
    "sem",
    "spl",
    "sep",
    "sa",
    "sc",
    "gf",
)
_FORM_CANON = {
    "societe civile immobiliere": "sci",
    "sc immobiliere": "sci",
    "societe civile": "sc",
    "groupement foncier agricole": "gfa",
    "groupement forestier": "gf",
    "groupement foncier": "gfa",
}
_FORM_RE = re.compile(r"\b(" + "|".join(LEGAL_FORMS) + r")\b")

_STOP = {"de", "du", "des", "d", "la", "le", "les", "l", "et", "en", "au", "aux", "a"}
_GENERIC = {
    "",
    "parts sociales",
    "part sociale",
    "compte titre",
    "compte titres",
    "divers",
    "portefeuille",
    "cooperative",
    "cooperative agricole",
    "banque",
    "pme",
    "holding",
    "societe",
    "entreprise",
    "famille",
    "familiale",
    "immobilier",
    "immobiliere",
    "patrimoine",
    "investissement",
    "placement",
    "actions",
    "action",
    "titres",
    "autres",
    "autre",
    "voir commentaire",
    "idem",
    "indivision",
    "cours liquidation",
    "liquidation",
    "cours creation",
    "cours constitution",
    "familiale immobiliere",
}

_ACRONYM_KEEP = re.compile(r"^[A-Z0-9&]{2,5}$")
_LOWER_WORDS = {"de", "du", "des", "le", "la", "les", "et", "en", "au", "aux", "d", "l", "sur", "pour"}
# Short all-caps tokens that are words, not acronyms.
_SHORT_WORDS = set(
    "air mer sud est nord bois pays parc port fils val bel bon club ile rue vie eau vert pain vin mont pont "
    "amis ami art arts bio cap cite clos cote coop eco elan euro fer feu fils gaz gite gros haut jean jeux "
    "lac lien lieu lot lune main mas mode mur nef noir oise or paix parc paul pic plan pole pre pres pro "
    "rive roc roi rose sel ski sol soie sous tech terre tour tout trois un une vent verre vive voie vue "
    "zone auto bleu cafe ciel deux doux elle fort four gare goût halle home immo info jour juin love loup "
    "luxe mai mars mer midi mine moto new news next nous ocre onze opus ours pack page pays pure quai "
    "saint san seul sept site soft star sun team top tous vert vif ville zen".split()
)
_FORM_DISPLAY = {f.upper() for f in LEGAL_FORMS if " " not in f}


def prettify(raw: str) -> str:
    """Readable casing for a hand-typed company name."""
    raw = re.sub(r"\s+", " ", raw).strip(" -–,;.")
    if not raw:
        return raw
    if raw != raw.upper() and raw != raw.lower():
        return raw
    out = []
    for i, w in enumerate(raw.split(" ")):
        up = w.upper()
        if w.lower() in _LOWER_WORDS:
            out.append(w.lower() if i > 0 else w.capitalize())
        elif up in _FORM_DISPLAY or (
            raw == raw.upper() and _ACRONYM_KEEP.match(w) and len(w) <= 4 and w.lower() not in _SHORT_WORDS
        ):
            out.append(up)
        else:
            out.append("-".join(p[:1].upper() + p[1:].lower() for p in w.split("-")))
    return " ".join(out)


def canonicalize(raw: str | None) -> CompanyRef | None:
    """Resolve a declared company name. None when the entry is a placeholder."""
    if not raw:
        return None
    redacted = REDACTED in raw
    text = clean(raw)
    if not text:
        return CompanyRef("", "Société non publiée", "unnamed", ()) if redacted else None
    f = fold(text)
    if not f or _JUNK.match(f):
        return None

    core = " ".join(_CUSTODIAN.sub(" ", f).split())
    core = _TRAIL_WRAPPER.sub("", _LEAD_WRAPPER.sub("", core)).strip()
    if not core:
        return CompanyRef("", prettify(text), "unnamed", ())

    # One line listing a whole portfolio ("AXA, Sanofi, Total, Orange…").
    if text.count(",") >= 2:
        hits = {k.slug for part in text.split(",") for k in REGISTRY if k.pattern.search(fold(part))}
        if len(hits) >= 2:
            return CompanyRef("", "Portefeuille (plusieurs sociétés)", "unnamed", ())

    if _FUND.search(f):
        sectors = ("immobilier",) if re.search(r"\b(scpi|opci)\b", f) else ()
        slug = "" if redacted or core in _GENERIC or len(core) < 4 else core.replace(" ", "-")
        return CompanyRef(slug, prettify(text), "fund", sectors)

    for k in REGISTRY:
        if k.pattern.search(core):
            return CompanyRef(k.slug, k.name, k.kind, k.sectors, k.ticker)

    m = _FORM_RE.search(core)
    form = _FORM_CANON.get(m.group(1), m.group(1)) if m else None
    rest = _CORP_SUFFIX.sub("", " ".join(_FORM_RE.sub(" ", core).split()))
    tokens = [t for t in rest.split() if t not in _STOP]
    meaningful = [t for t in tokens if len(t) > 1]
    sectors = sectors_mod.classify_company(f, form)

    if redacted or not meaningful or " ".join(tokens) in _GENERIC:
        label = f"{form.upper()} (nom non publié)" if form and redacted else prettify(text)
        return CompanyRef("", label, "unnamed", sectors, legal_form=form)

    # A one-word name ("SCI Legrand") is too weak an identity on its own:
    # keep the legal form in the slug so it cannot merge with a namesake.
    slug = "-".join(tokens)
    if form and len(meaningful) == 1:
        slug = f"{form.replace(' ', '-')}-{slug}"
    if slug in _BY_SLUG:
        slug = f"autre-{slug}"
    return CompanyRef(slug, prettify(text), "other", sectors, legal_form=form)
