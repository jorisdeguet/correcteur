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


def gros_commits(depot: Path, jusqua: str | None = None, seuil: int = 100,
                 motifs: tuple[str, ...] = ("*",)) -> list[tuple[Commit, int]]:
    """Commits qui ajoutent ou retirent au moins ``seuil`` lignes des fichiers ``motifs``
    (un seul appel git ; le commit initial, qui contient le code de départ, est ignoré)."""
    import fnmatch
    if not (depot / ".git").exists():
        return []
    commande = ["git", "-C", str(depot), "log", "--numstat", "--format=\x1e%H\x1f%aI\x1f%an\x1f%s\x1f%P"]
    if jusqua:
        commande.append(jusqua)
    try:
        proc = subprocess.run(commande, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return []
    trouves = []
    for bloc in proc.stdout.split("\x1e")[1:]:
        entete, _, stats = bloc.partition("\n")
        parties = entete.split("\x1f")
        if len(parties) < 5 or not parties[4].strip():
            continue  # commit racine
        total = 0
        for ligne in stats.splitlines():
            a, _, reste = ligne.partition("\t")
            s, _, fichier = reste.partition("\t")
            nom = fichier.rsplit("/", 1)[-1]
            if a.isdigit() and s.isdigit() and any(fnmatch.fnmatch(nom, m) or fnmatch.fnmatch(fichier, m)
                                                   for m in motifs):
                total += int(a) + int(s)
        if total >= seuil:
            try:
                date = datetime.fromisoformat(parties[1])
            except ValueError:
                date = None
            trouves.append((Commit(parties[0], date, parties[2], parties[3]), total))
    return trouves
