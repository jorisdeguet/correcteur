"""Bout en bout : faux dépôts étudiants → recette → PDF, notes, cache, exclusions, échéance."""

import csv

from correcteur import Fiche, Recette, typst
from correcteur.etat import Etat

SOLUTION = "def carre(x):\n    return x * x\n"
BOGUE = "def carre(x):\n    return x + x\n"
TEST = "from solution import carre\nprint('OK' if carre(3) == 9 else 'ECHEC')\n"


def recette_test(appels: list):
    r = Recette("TEST", version="1", description="Test", outils=["git"], delai=5)

    @r.correction
    def corriger(copie, ctx):
        appels.append(copie.id)
        (copie.dossier / "_test.py").write_text(TEST)
        res = copie.python("_test.py", delai=ctx.args.timeout)
        ok = "OK" in res.stdout
        return Fiche(f"= {typst.texte(copie.etudiant.nom_affiche)}\n{typst.brut(res.sortie)}",
                     note={"Q1": 1.0 if ok else 0.0, "Total": 1.0 if ok else 0.0, "Remarques": ""})
    return r


def test_bout_en_bout(tmp_path, depot, classe_csv):
    depot("TP-1234567-TREMBLAY", [("2026-11-04T20:00:00-05:00", {"solution.py": SOLUTION})])
    depot("TP-7654321-GAGNON", [("2026-11-04T20:00:00-05:00", {"solution.py": BOGUE}),
                                ("2026-11-05T08:00:00-05:00", {"solution.py": SOLUTION})])  # en retard
    depot("TP-1111111-ROY", [("2026-11-04T20:00:00-05:00", {"solution.py": SOLUTION})])
    depot("TP-9999999-INCONNU", [("2026-11-04T20:00:00-05:00", {"solution.py": SOLUTION})])
    argv = [str(tmp_path), "1050", "--classe", str(classe_csv), "--echeance", "2026-11-04T23:59-05:00",
            "--sans-diagnostic", "-j", "2"]

    appels: list = []
    recette_test(appels).main(argv)
    sortie = tmp_path / "corrections-TEST"
    assert (sortie / "groupe-1050.pdf").exists()
    assert sorted(appels) == ["1234567", "7654321"]  # groupe 1060 et inconnu ignorés
    with (sortie / "groupe-1050-notes.csv").open(encoding="utf-8-sig") as f:
        lignes = {l["Matricule"]: l for l in csv.DictReader(f, delimiter=";")}
    assert lignes["1234567"]["Total"] == "1"
    assert lignes["7654321"]["Total"] == "0"  # version à l'échéance, pas le commit en retard
    assert not (tmp_path / "TP-1234567-TREMBLAY" / "_test.py").exists()  # clone intact

    # Deuxième session : tout vient du cache.
    appels.clear()
    recette_test(appels).main(argv)
    assert appels == []

    # Prolongation d'un jour pour Gagnon : nouveau commit retenu, seule sa copie est refaite.
    Etat(tmp_path).prolonger("7654321", "+1j")
    recette_test(appels).main(argv)
    assert appels == ["7654321"]
    with (sortie / "groupe-1050-notes.csv").open(encoding="utf-8-sig") as f:
        assert {l["Matricule"]: l["Total"] for l in csv.DictReader(f, delimiter=";")}["7654321"] == "1"

    # Exclusion persistante.
    appels.clear()
    Etat(tmp_path).exclure("1234567", "test")
    recette_test(appels).main(argv + ["--refaire"])
    assert appels == ["7654321"]
