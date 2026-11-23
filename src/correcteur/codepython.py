"""Code Python étudiant : choix de l'interpréteur, analyse statique, fichiers de questions.

L'exécution elle-même passe par :func:`correcteur.execution.executer_python`.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable

EXCLUS = {".venv", "venv", ".git", "__pycache__", ".idea", "__macosx"}


# ─── Interpréteurs ───────────────────────────────────────────────────────────

def python_du_venv(venv: Path) -> Path:
    """Exécutable Python d'un venv (bin/python ou Scripts/python.exe)."""
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def version(python: str, modules: Iterable[str] = ()) -> str:
    """« Python 3.14.0 / pandas 3.0.1 / numpy 2.3 » ou « » si l'interpréteur ne démarre pas."""
    mods = list(modules)
    code = "import sys; print(sys.version.split()[0])\n" + "".join(
        f"import {m}; print(getattr({m}, '__version__', '?'))\n" for m in mods)
    try:
        proc = subprocess.run([python, "-c", code], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode:
        return ""
    valeurs = proc.stdout.split()
    return " / ".join([f"Python {valeurs[0]}"] + [f"{m} {v}" for m, v in zip(mods, valeurs[1:])])


def interpreteurs(candidats: Iterable[str | Path], modules: Iterable[str] = ()) -> list[str]:
    """Candidats existants qui importent ``modules`` (sys.executable ajouté en dernier recours)."""
    mods = list(modules)
    retenus: list[str] = []
    for c in [*candidats, sys.executable]:
        p = str(c)
        if p in retenus or not Path(p).is_file():
            continue
        if not mods or subprocess.run([p, "-c", "import " + ", ".join(mods)],
                                      capture_output=True).returncode == 0:
            retenus.append(p)
    return retenus


# ─── Fichiers ────────────────────────────────────────────────────────────────

def lire(chemin: Path | None) -> str | None:
    """Source d'un fichier (utf-8, BOM toléré) ou None s'il n'existe pas."""
    if chemin is None or not Path(chemin).is_file():
        return None
    return Path(chemin).read_text(encoding="utf-8-sig", errors="replace")


def trouver(dossier: Path, nom: str) -> Path | None:
    """Fichier ``nom`` à la racine de préférence, sinon le moins profond (hors .venv, .git…)."""
    direct = dossier / nom
    if direct.is_file():
        return direct
    candidats = [p for p in dossier.rglob(nom) if not any(part.casefold() in EXCLUS for part in p.parts)]
    return min(candidats, key=lambda p: len(p.parts)) if candidats else None


QUESTION_RE = re.compile(r"^(?:question|qst|q)?[\s_-]*(\d+)(?!\d)(?:[\s_.-].*)?\.py$", re.IGNORECASE)


def fichiers_questions(dossier: Path, motif: re.Pattern = QUESTION_RE) -> dict[int, list[Path]]:
    """{numéro: [fichiers]} pour question_3.py, q4.py, Question 5 - final.py…"""
    trouves: dict[int, list[Path]] = {}
    for p in sorted(dossier.rglob("*.py")):
        if any(part.casefold() in EXCLUS for part in p.relative_to(dossier).parts):
            continue
        m = motif.fullmatch(p.name)
        if m:
            trouves.setdefault(int(m.group(1)), []).append(p)
    return trouves


def racine_projet(dossier: Path) -> Path:
    """Dossier à ouvrir dans l'IDE : le moins profond contenant .idea ou des fichiers de questions."""
    candidats = [p.parent for p in dossier.rglob(".idea") if p.is_dir()]
    candidats += [p.parent for ps in fichiers_questions(dossier).values() for p in ps]
    candidats = [p for p in candidats if not any(part.casefold() in EXCLUS for part in p.relative_to(dossier).parts)]
    return min(candidats, key=lambda p: len(p.parts)) if candidats else dossier


# ─── Analyse statique ────────────────────────────────────────────────────────

def nom_appel(noeud: ast.Call) -> str:
    f = noeud.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


def fonctions(source: str) -> dict[str, ast.FunctionDef]:
    """Fonctions de premier niveau, ou {} si erreur de syntaxe."""
    try:
        arbre = ast.parse(source)
    except SyntaxError:
        return {}
    return {n.name: n for n in arbre.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def segments(source: str, noms: Iterable[str]) -> dict[str, str | None]:
    """Code source de chaque fonction demandée (None si absente)."""
    noeuds = fonctions(source)
    return {n: (ast.get_source_segment(source, noeuds[n]) if n in noeuds else None) for n in noms}


def indices_fonction(source: str, nom: str) -> list[str]:
    """Indices automatiques : docstring, read_csv, print, appels d'autres questions, savefig, boucles."""
    noeuds = fonctions(source)
    noeud = noeuds.get(nom)
    if noeud is None:
        return ["fonction ABSENTE"]
    indices = []
    doc = ast.get_docstring(noeud)
    indices.append("docstring vide" if not doc or not doc.strip() else f"docstring {len(doc.split())} mots")
    appels = [nom_appel(n) for n in ast.walk(noeud) if isinstance(n, ast.Call)]
    appeles = {n.func.id for n in ast.walk(noeud) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    indices.append("read_csv" if "read_csv" in appels else "PAS de read_csv")
    if "print" in appels:
        indices.append("PRINT")
    autres = sorted(c for c in appeles if c in noeuds and c != nom)
    if autres:
        indices.append("appelle " + ", ".join(autres))
    for appel in (n for n in ast.walk(noeud) if isinstance(n, ast.Call) and nom_appel(n) == "savefig"):
        if appel.args and isinstance(appel.args[0], ast.Constant):
            indices.append(f"savefig({appel.args[0].value!r})")
        else:
            indices.append("savefig(…)")
    boucles = sum(isinstance(n, (ast.For, ast.While, ast.comprehension)) for n in ast.walk(noeud))
    if boucles:
        indices.append(f"{boucles} boucle(s)")
    return indices


def est_garde_main(noeud: ast.AST) -> bool:
    """``if __name__ == "__main__":``"""
    test = noeud.test if isinstance(noeud, ast.If) else None
    return (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name) and test.left.id == "__name__"
            and len(test.comparators) == 1 and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value == "__main__")


def notions_interdites(source: str, motifs: dict[str, re.Pattern]) -> list[str]:
    """Noms des motifs trouvés dans le code (commentaires retirés)."""
    code = "\n".join(l.split("#", 1)[0] for l in source.splitlines())
    return [n for n, m in motifs.items() if m.search(code)]


def substituer_globale(source: str, nom: str, valeur_python: str) -> str | None:
    """Remplace l'affectation globale ``nom = …`` (même sur plusieurs lignes) ; None si absente."""
    try:
        arbre = ast.parse(source)
    except SyntaxError:
        return None
    lignes = source.splitlines(keepends=True)
    for noeud in arbre.body:
        cibles = noeud.targets if isinstance(noeud, ast.Assign) else (
            [noeud.target] if isinstance(noeud, ast.AnnAssign) else [])
        if any(isinstance(c, ast.Name) and c.id == nom for c in cibles):
            debut, fin = noeud.lineno - 1, noeud.end_lineno
            return "".join(lignes[:debut]) + f"{nom} = {valeur_python}\n" + "".join(lignes[fin:])
    return None


def environnement_uv(dossier: Path, paquets: Iterable[str], python: str = "3.13") -> str | None:
    """Crée au besoin (avec uv) un venv pour exécuter le code étudiant ; retourne son interpréteur.

    Reproductible sur toutes les machines : ``environnement_uv(etat.dossier / "python", ["pandas", "matplotlib"])``.
    """
    from .execution import executer
    py = python_du_venv(Path(dossier))
    temoin = Path(dossier) / ".paquets"
    voulu = " ".join(sorted(paquets)) + f" python={python}"
    if py.exists() and temoin.is_file() and temoin.read_text() == voulu:
        return str(py)
    r = executer(["uv", "venv", "--allow-existing", "--python", python, str(dossier)], delai=600)
    if not r.reussi:
        return None
    r = executer(["uv", "pip", "install", "--python", str(py), *paquets], delai=900)
    if not r.reussi:
        return None
    temoin.write_text(voulu)
    return str(py)
