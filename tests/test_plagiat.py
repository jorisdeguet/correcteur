import numpy as np

from correcteur.plagiat import Analyse
from correcteur.plagiat import empreintes as emp
from correcteur.plagiat.normalisation import jetons

ORIGINAL = '''
def moyenne(notes):
    total = 0
    for n in notes:
        total += n
    return total / len(notes)

def mention(m):
    if m >= 90:
        return "Excellent"
    elif m >= 75:
        return "Bien"
    elif m >= 60:
        return "Passable"
    return "Echec"

for nom, notes in donnees.items():
    print(nom, round(moyenne(notes), 2), mention(moyenne(notes)))
'''

# Copie avec renommage, commentaires et messages différents : doit être détectée.
RENOMMEE = '''
# calcule la moyenne
def calc(liste):
    s = 0
    for x in liste:
        s += x
    return s / len(liste)

def appreciation(valeur):
    if valeur >= 90:
        return "Top"
    elif valeur >= 75:
        return "Bon"
    elif valeur >= 60:
        return "Ok"
    return "Non"

for k, v in donnees.items():
    print(k, round(calc(v), 2), appreciation(calc(v)))
'''

DIFFERENTE = '''
import statistics
resultats = {k: statistics.mean(v) for k, v in donnees.items()}
meilleur = max(resultats, key=resultats.get)
while True:
    break
print(f"Meilleur : {meilleur}")
'''


def test_normalisation_python_ignore_noms_et_commentaires():
    a = [j.valeur for j in jetons(ORIGINAL, "python")]
    b = [j.valeur for j in jetons(RENOMMEE, "python")]
    assert a == b


def test_normalisation_kotlin():
    js = [j.valeur for j in jetons('import a.b\n// c\nfun f(x: Int) = x + 1 /* z */', "kotlin")]
    assert js == ["fun", "V", "(", "V", ":", "V", ")", "=", "V", "+", "N"]


def test_plus_longue_commune_concorde_avec_dp():
    rng = np.random.default_rng(1)
    for _ in range(30):
        a = rng.integers(1, 5, rng.integers(1, 60))
        b = rng.integers(1, 5, rng.integers(1, 60))
        l1, i, j = emp.plus_longue_commune(a, b)
        l2, _, _ = emp._plus_longue_commune_dp(a, b)
        assert l1 == l2
        assert list(a[i:i + l1]) == list(b[j:j + l1])


def test_winnowing_garantie():
    h = np.arange(100, dtype=np.uint64)[::-1].copy()
    vals, pos = emp.winnowing(h, 4)
    # au moins une empreinte dans chaque fenêtre de 4
    assert all(any(p <= x < p + 4 for x in pos) for p in range(97))


def test_analyse_detecte_la_copie_et_pas_la_difference(tmp_path):
    a = Analyse(k=8, w=4, frequence_max=1.0)
    a.ajouter_textes("A", [("q3.py", ORIGINAL)])
    a.ajouter_textes("B", [("q3.py", RENOMMEE)])
    a.ajouter_textes("C", [("q3.py", DIFFERENTE)])
    paires = a.paires(seuil=0.3, communes_min=2)
    assert [(p.a, p.b) for p in paires] == [("A", "B")]
    assert paires[0].similarite > 0.9 and paires[0].plus_longue > 50
    assert paires[0].region_a[0] == "q3.py"


def test_code_de_depart_ignore(tmp_path):
    depart = tmp_path / "depart"
    depart.mkdir()
    (depart / "q3.py").write_text(ORIGINAL)
    a = Analyse(k=8, w=4, frequence_max=1.0)
    a.depart(depart)
    a.ajouter_textes("A", [("q3.py", ORIGINAL)])
    a.ajouter_textes("B", [("q3.py", RENOMMEE)])
    assert a.paires(seuil=0.3, communes_min=2) == []


def test_cache_des_empreintes(tmp_path):
    a = Analyse(k=8, w=4, cache=tmp_path / "c")
    s1 = a.ajouter_textes("A", [("q3.py", ORIGINAL)])
    b = Analyse(k=8, w=4, cache=tmp_path / "c")
    s2 = b.ajouter_textes("A", [("q3.py", ORIGINAL)])
    assert np.array_equal(s1.hachages, s2.hachages)
