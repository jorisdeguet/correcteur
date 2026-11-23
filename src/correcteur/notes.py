"""Notes proposées : CSV pour Excel/LibreOffice et page PDF récapitulative par groupe."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

from .classe import Etudiant
from . import typst


def format_note(valeur) -> str:
    """2,5 plutôt que 2.5 ; texte inchangé."""
    if isinstance(valeur, bool):
        return str(valeur)
    if isinstance(valeur, (int, float)):
        return f"{round(valeur, 2):g}".replace(".", ",")
    return "" if valeur is None else str(valeur)


def ecrire_csv(chemin: Path, lignes: list[tuple[Etudiant, dict]]) -> Path:
    """CSV séparé par « ; » avec virgule décimale (ouvre directement dans Excel en français)."""
    colonnes = list(lignes[0][1]) if lignes else []
    with Path(chemin).open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Matricule", "Nom", *colonnes])
        for etudiant, note in lignes:
            w.writerow([etudiant.id, etudiant.nom_affiche, *(format_note(note.get(c)) for c in colonnes)])
    return Path(chemin)


def page_notes(chemin_pdf: Path, groupe: str, lignes: list[tuple[Etudiant, dict]], legende: str = "",
               sur: float | int = 10) -> Path:
    """PDF paysage des notes proposées d'un groupe (gabarit ``notes.typ``)."""
    colonnes = list(lignes[0][1]) if lignes else []
    numeriques = [c for c in colonnes
                  if all(isinstance(n.get(c), (int, float)) and not isinstance(n.get(c), bool) for _, n in lignes)]
    moyennes = {c: sum(n[c] for _, n in lignes) / len(lignes) for c in numeriques} if lignes else {}
    total = moyennes.get("Total", 0)
    donnees = {
        "titre": f"Notes proposées — groupe {groupe} — {len(lignes)} étudiants — moyenne "
                 f"{format_note(total)} / {format_note(sur)}",
        "legende": legende,
        "colonnes": colonnes,
        "moyennes": moyennes,
        "lignes": [{"nom": e.nom_affiche, "matricule": e.id,
                    "notes": {c: n.get(c) for c in colonnes}} for e, n in lignes],
    }
    return typst.compiler_gabarit("notes.typ", donnees, chemin_pdf)


def total(note: dict, colonnes: Iterable[str] | None = None, minimum: float = 0.0) -> float:
    """Somme des colonnes numériques (hors Total/Remarques), bornée par ``minimum``."""
    cles = colonnes or [c for c in note if c not in ("Total", "Remarques")]
    return max(minimum, sum(v for c in cles if isinstance(v := note.get(c), (int, float))))
