import pytest

from pv.sectors import classify_bill, committee_sectors, sectors_for_domains


@pytest.mark.parametrize(
    "title, expected",
    [
        ("Projet de loi relatif à l'accélération de la production d'énergies renouvelables", {"energie"}),
        ("Projet de loi actualisant la programmation militaire pour les années 2024 à 2030", {"defense"}),
        ("Proposition de loi visant à lutter contre les fermetures abusives de comptes bancaires", {"banque"}),
        ("Proposition de loi visant à lutter contre les pénuries de médicaments", {"sante"}),
        ("Proposition de loi visant à la nationalisation d'ArcelorMittal France", {"industrie"}),
        ("Proposition de loi visant à abroger les zones à faibles émissions-mobilités", {"automobile"}),
    ],
)
def test_bill_sectors(title, expected):
    assert set(classify_bill(title).sectors) == expected


@pytest.mark.parametrize(
    "title",
    [
        "Proposition de loi relative au droit à l'aide à mourir",
        "Proposition de loi visant à reconnaître une présomption de légitime défense pour les forces de l'ordre",
        "Projet de loi relatif à l'organisation des jeux Olympiques et Paralympiques de 2030",
        "Proposition de loi visant à reconnaître les victimes de l'exposition aux essais nucléaires français",
        "Proposition de résolution tendant à la création d'une commission d'enquête sur les médias",
        "Projet de loi autorisant la ratification de la convention sur le transport maritime",
        "Projet de loi portant transposition de l'accord relatif à l'assurance chômage",
    ],
)
def test_a_keyword_alone_does_not_make_a_bill_sectoral(title):
    assert classify_bill(title).sectors == ()


@pytest.mark.parametrize(
    "title",
    [
        "Projet de loi de finances pour 2026",
        "Projet de loi de finances de fin de gestion pour 2025",
        "Projet de loi de simplification de la vie économique",
        "Projet de loi de financement rectificative de la sécurité sociale pour 2023",
    ],
)
def test_catch_all_bills_are_transversal(title):
    cls = classify_bill(title)
    assert cls.transversal and cls.sectors == ()


def test_social_security_budget_is_a_broad_health_bill():
    cls = classify_bill("Projet de loi de financement de la sécurité sociale pour 2026")
    assert cls.budget and not cls.transversal
    assert set(cls.sectors) == {"sante", "assurance"}


def test_committee_and_lobbying_vocabularies():
    assert committee_sectors("Commission de la défense nationale et des forces armées") == ("defense",)
    assert "sante" in committee_sectors("Commission des affaires sociales")
    assert committee_sectors("Commission des lois") == ()
    assert sectors_for_domains(["Médicaments", "Politique industrielle", "Société"]) == ("sante", "industrie")
