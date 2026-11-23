"""Historique git d'une copie, pour les rapports."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class Commit:
    sha: str
    date: datetime | None
    auteur: str
    titre: str
    corps: str = ""

    @property
    def date_courte(self) -> str:
        """« 04/11 23:12 »."""
        return self.date.strftime("%d/%m %H:%M") if self.date else "?"


def historique(depot: Path, jusqua: str | None = None, maximum: int | None = None) -> list[Commit] | None:
    """Commits du plus récent au plus ancien, jusqu'à ``jusqua`` (SHA) inclus.

    Retourne None s'il n'y a pas de dépôt git, [] si l'historique est vide ou illisible.
    """
    if not (depot / ".git").exists():
        return None
    sep, fin = "\x1f", "\x1e"
    commande = ["git", "-C", str(depot), "log", f"--format=%H{sep}%aI{sep}%an{sep}%s{sep}%b{fin}"]
    if maximum:
        commande.append(f"-n{maximum}")
    if jusqua:
        commande.append(jusqua)
    try:
        proc = subprocess.run(commande, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode:
        return []
    commits = []
    for bloc in proc.stdout.split(fin):
        parties = bloc.strip("\n").split(sep)
        if len(parties) < 4:
            continue
        sha, date, auteur, titre = parties[:4]
        try:
            moment = datetime.fromisoformat(date)
        except ValueError:
            moment = None
        commits.append(Commit(sha, moment, auteur, titre, parties[4].strip() if len(parties) > 4 else ""))
    return commits


def lignes_modifiees(depot: Path, sha: str) -> int:
    """Lignes ajoutées + supprimées par un commit (gros commits suspects)."""
    try:
        proc = subprocess.run(["git", "-C", str(depot), "show", "--numstat", "--format=", sha],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return 0
    total = 0
    for ligne in proc.stdout.splitlines():
        a, _, reste = ligne.partition("\t")
        s, _, _ = reste.partition("\t")
        if a.isdigit() and s.isdigit():
            total += int(a) + int(s)
    return total
