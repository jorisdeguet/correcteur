import sys
import zipfile
from datetime import datetime, timedelta

import pytest

from correcteur import classe, depots, echeance, execution, notes, typst
from correcteur.etat import Entree, Etat


# ─── Échéance ────────────────────────────────────────────────────────────────

def test_commit_a_echeance_et_prolongation(depot):
    d = depot("A26-TP1-1234567-TREMBLAY", [
        ("2026-11-03T10:00:00-05:00", {"a.py": "x = 1\n"}),
        ("2026-11-04T23:30:00-05:00", {"a.py": "x = 2\n"}),
        ("2026-11-05T09:00:00-05:00", {"a.py": "x = 3\n"}),
    ])
    e = echeance.Echeances.depuis("2026-11-04T23:59-05:00", {"1234567": "+1j"})
    sha_normal = echeance.commit_a_echeance(d, e.echeance)
    sha_prolonge = echeance.commit_a_echeance(d, e.pour("1234567"))
    assert sha_normal != sha_prolonge
    assert echeance.commit_a_echeance(d, None) == sha_prolonge  # dernier commit
    assert len(echeance.commits_apres(d, sha_normal)) == 1
    assert echeance.commits_apres(d, sha_prolonge) == []
    trop_tot = echeance.lire_date("2026-11-01T00:00-05:00")
    assert echeance.commit_a_echeance(d, trop_tot) is None


def test_lire_date():
    d = echeance.lire_date("2026-11-04T23:59")
    assert (d.hour, d.minute, d.second) == (23, 59, 59) and d.tzinfo is not None
    assert echeance.lire_date("2026-11-04").hour == 23
    base = echeance.lire_date("2026-11-04T23:59-05:00")
    assert echeance.appliquer_prolongation(base, "+36h") == base + timedelta(hours=36)
    assert echeance.appliquer_prolongation(base, "+90min") == base + timedelta(minutes=90)


# ─── Exécution ───────────────────────────────────────────────────────────────

def test_executer_delai_tue_le_groupe(tmp_path):
    script = tmp_path / "boucle.py"
    script.write_text("import subprocess, sys, time\n"
                      "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                      "print('début', flush=True)\nwhile True: time.sleep(1)\n")
    r = execution.executer_python(script.name, cwd=tmp_path, delai=2)
    assert r.delai_depasse and not r.reussi
    assert "début" in r.stdout
    assert "délai dépassé" in r.statut


def test_executer_sortie_bornee_et_stdin(tmp_path):
    (tmp_path / "s.py").write_text("print(input().upper())\nprint('x' * 10000)\n")
    r = execution.executer_python("s.py", cwd=tmp_path, entree="bonjour\n", sortie_max=100, isole=True)
    assert r.reussi and r.stdout.startswith("BONJOUR") and "[sortie tronquée]" in r.stdout


def test_commande_introuvable():
    r = execution.executer(["commande-qui-n-existe-pas-42"])
    assert r.introuvable and not r.reussi


# ─── Classe ──────────────────────────────────────────────────────────────────

def test_lire_classe(classe_csv):
    c = classe.lire_classe(classe_csv)
    assert c["7654321"].prenom == "Béatrice" and c["7654321"].nom == "Gagnon"
    assert c["7654321"].nom_affiche == "Gagnon, Béatrice"
    assert c["1111111"].groupe == "1060"
    assert classe.matricule_dans("A26-SN1-TP1RACINE-1234567-TREMBLAY") == "1234567"
    assert classe.matricule_dans("12345678") is None


# ─── Dépôts et archives ──────────────────────────────────────────────────────

def test_exporter_commit_ne_touche_pas_le_clone(depot, tmp_path):
    d = depot("copie", [("2026-11-03T10:00:00", {"a.py": "1"}), ("2026-11-04T10:00:00", {"a.py": "2"})])
    premier = echeance.commit_a_echeance(d, echeance.lire_date("2026-11-03T12:00"))
    cible = depots.exporter_commit(d, premier, tmp_path / "travail")
    assert (cible / "a.py").read_text() == "1"
    assert (d / "a.py").read_text() == "2"
    (cible / "build.txt").write_text("artefact")
    depots.exporter_commit(d, premier, cible)  # même commit : artefacts conservés
    assert (cible / "build.txt").exists()


def test_extraction_refuse_les_chemins_dangereux(tmp_path):
    bon = tmp_path / "1234567.zip"
    with zipfile.ZipFile(bon, "w") as z:
        z.writestr("projet/q3.py", "print(1)")
        z.writestr("projet/.venv/lib/x.py", "")
    cible = depots.extraire_archive(bon, tmp_path / "x")
    assert (cible / "projet" / "q3.py").exists() and not (cible / "projet" / ".venv").exists()
    mauvais = tmp_path / "mauvais.zip"
    with zipfile.ZipFile(mauvais, "w") as z:
        z.writestr("../evasion.py", "")
    with pytest.raises(ValueError):
        depots.extraire_archive(mauvais, tmp_path / "y")
    assert not (tmp_path / "evasion.py").exists()


# ─── État ────────────────────────────────────────────────────────────────────

def test_etat_exclusions_et_cache(tmp_path):
    e = Etat(tmp_path)
    e.exclure("1234567", "abandon")
    e.prolonger("7654321", "+1j")
    e2 = Etat(tmp_path)  # nouvelle session
    assert e2.exclusions() == {"1234567": "abandon"}
    assert e2.config()["prolongations"] == {"7654321": "+1j"}
    assert e2.inclure("1234567") and e2.exclusions() == {}
    e2.ecrire("R", "1234567", "sha1", "v1", "o", Entree("ok", "fiche", {"Total": 3}))
    assert e2.lire("R", "1234567", "sha1", "v1", "o").note == {"Total": 3}
    assert e2.lire("R", "1234567", "sha2", "v1", "o") is None  # nouveau commit
    assert e2.lire("R", "1234567", "sha1", "v2", "o") is None  # nouvelle version de recette


# ─── Typst et notes ──────────────────────────────────────────────────────────

def test_typst_echappe_et_compile(tmp_path):
    fiche = typst.texte('#set page(width: 1pt) "guillemets" \\ ') + "\n\n" + typst.brut("print('#x')", "python")
    pdf = typst.ecrire_et_compiler(typst.document([fiche, fiche], genere_par="test"), tmp_path / "g.typ")
    assert pdf.exists() and typst.nombre_pages(pdf) == 3  # 2 fiches, la 2e sur page impaire


def test_notes_csv_et_pdf(tmp_path):
    e = classe.Etudiant("1234567", "Tremblay", "Alice", "1050")
    lignes = [(e, {"Q1": 1.5, "Total": 1.5, "Remarques": "ok"})]
    csv_ = notes.ecrire_csv(tmp_path / "n.csv", lignes).read_text(encoding="utf-8-sig")
    assert "1,5" in csv_ and "Tremblay, Alice" in csv_
    assert notes.page_notes(tmp_path / "n.pdf", "1050", lignes, "légende").exists()
