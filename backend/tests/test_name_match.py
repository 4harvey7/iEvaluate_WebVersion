import pytest

from app.name_match import is_near_duplicate, search_words_match

MATCH = [
    (("rodz", "Licayan"), ("Rodz Harvey d", "Licayan")),
    (("rodz", "Licayan"), ("Rodz Harvey D.", "Licayan")),
    (("RODZ  HARVEY", "Licayan"), ("Rodz Harvey d", "Licayan")),
    (("Rodz H.", "Licayan"), ("Rodz Harvey d", "Licayan")),
    (("Rodz D.", "Licayan"), ("Rodz Harvey d", "Licayan")),
    (("Harvey", "Licayan"), ("Rodz Harvey d", "Licayan")),
    (("Rodz K.", "Licayan"), ("Rodz", "Licayan")),
    (("R.", "Licayan"), ("Rodz Harvey d", "Licayan")),
    (("Juan", "Dela Cruz"), ("Juan", "Delacruz")),
    (("Juan", "Dela Cruz"), ("Juan", "dela-cruz")),
    (("Juan", "Dela Cruz"), ("Juan", "Dela Cruz Jr.")),
    (("Juan", "Cruz"), ("Juan", "Dela Cruz")),
    (("José", "Peña"), ("Jose", "Pena")),
    (("Dr. Maria", "Santos"), ("Maria", "Santos")),
    (("Mary Ann", "Reyes"), ("Maryann", "Reyes")),
]

NO_MATCH = [
    (("rodz", "Licayan"), ("Ronald Kinggg", "licayan")),
    (("Ronald", "Licayan"), ("Rodz Harvey d", "Licayan")),
    (("Rodz K.", "Licayan"), ("Rodz Harvey d", "Licayan")),
    (("Maria", "Santos"), ("Jose", "Santos")),
    (("Juan", "Reyes"), ("Juan", "Santos")),
]


@pytest.mark.parametrize("a,b", MATCH)
def test_same_person(a, b):
    assert is_near_duplicate(*a, *b)
    assert is_near_duplicate(*b, *a)  # both directions


@pytest.mark.parametrize("a,b", NO_MATCH)
def test_different_people(a, b):
    assert not is_near_duplicate(*a, *b)
    assert not is_near_duplicate(*b, *a)


def test_search_words_match():
    assert search_words_match("rodz licayan", "Rodz Harvey d Licayan")
    assert search_words_match("rodz lic", "Rodz Harvey d Licayan")
    assert not search_words_match("rodz licayan", "Ronald Kinggg licayan")
