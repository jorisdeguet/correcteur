"""Échéances et prolongations : quel commit corriger pour chaque étudiant.

Les dates s'écrivent en ISO 8601 ; sans fuseau, l'heure locale de la machine est utilisée ::

    echeance = "2026-11-04T23:59:59"
    [prolongations]
    2538179 = "+1j"                  # relatif à l'échéance : +1j, +36h, +90min
    6309202 = "2026-11-06T12:00"     # ou une date absolue

Attention : la date d'un commit est choisie par la machine de l'étudiant et peut être
falsifiée. ``commits_apres`` et la date de première observation (serveur) servent à
repérer les cas douteux.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

_RELATIF_RE = re.compile(r"^\+\s*(\d+(?:\.\d+)?)\s*(j|jours?|d|h|heures?|min|m)$", re.IGNORECASE)


def lire_date(texte: str | datetime) -> datetime:
    """Date ISO → datetime avec fuseau (local si absent). « 23:59 » est compris comme 23:59:59."""
    if isinstance(texte, datetime):
        d = texte
    else:
        t = texte.strip().replace(" ", "T", 1)
        d = datetime.fromisoformat(t)
        if re.search(r"T\d{2}:\d{2}$", t) or re.search(r"T\d{2}:\d{2}(?=[+-]\d{2}:?\d{2}$|Z$)", t):
            d = d.replace(second=59)
        if "T" not in t:
            d = d.replace(hour=23, minute=59, second=59)
    if d.tzinfo is None:
        d = d.astimezone()
    return d


def appliquer_prolongation(echeance: datetime, prolongation: str) -> datetime:
    """« +1j », « +36h », « +90min » ou date absolue."""
    m = _RELATIF_RE.match(prolongation.strip())
    if not m:
        return lire_date(prolongation)
    valeur, unite = float(m.group(1)), m.group(2).lower()
    if unite.startswith(("j", "d")):
        return echeance + timedelta(days=valeur)
    if unite.startswith("h"):
        return echeance + timedelta(hours=valeur)
    return echeance + timedelta(minutes=valeur)


@dataclass(frozen=True)
class Echeances:
    """Échéance commune et prolongations individuelles."""

    echeance: datetime | None = None
    prolongations: tuple[tuple[str, str], ...] = ()

    @classmethod
    def depuis(cls, echeance: str | None, prolongations: dict[str, str] | None = None) -> "Echeances":
        return cls(lire_date(echeance) if echeance else None,
                   tuple(sorted((str(k), str(v)) for k, v in (prolongations or {}).items())))

    def pour(self, matricule: str) -> datetime | None:
        """Échéance de cet étudiant (None = pas d'échéance : dernier commit)."""
        if self.echeance is None:
            return None
        for cle, valeur in self.prolongations:
            if cle == matricule:
                return appliquer_prolongation(self.echeance, valeur)
        return self.echeance

    def prolongee(self, matricule: str) -> bool:
        return any(cle == matricule for cle, _ in self.prolongations)


def _git(depot: Path, *args: str, delai: float = 30) -> str:
    proc = subprocess.run(["git", "-C", str(depot), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=delai)
    if proc.returncode:
        raise RuntimeError(f"git {' '.join(args)} : {(proc.stderr or proc.stdout).strip()}")
    return proc.stdout.strip()


def est_depot(dossier: Path) -> bool:
    return (dossier / ".git").exists()


def reference_par_defaut(depot: Path) -> str:
    """origin/HEAD s'il existe (dépôt récupéré par fetch), sinon HEAD."""
    try:
        return _git(depot, "rev-parse", "--abbrev-ref", "origin/HEAD")
    except (RuntimeError, subprocess.TimeoutExpired):
        return "HEAD"


def commit_a_echeance(depot: Path, echeance: datetime | None, reference: str | None = None) -> str | None:
    """SHA du dernier commit de ``reference`` daté (committer date) au plus tard à l'échéance.

    Retourne None si aucun commit ne précède l'échéance (ou dépôt vide).
    """
    ref = reference or "HEAD"
    try:
        if echeance is None:
            return _git(depot, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}") or None
        return _git(depot, "rev-list", "-1", f"--before={echeance.isoformat()}", ref) or None
    except (RuntimeError, subprocess.TimeoutExpired):
        return None


def commits_apres(depot: Path, sha: str | None, reference: str | None = None) -> list[tuple[str, str, str]]:
    """Commits de ``reference`` postérieurs à ``sha`` : [(sha court, date ISO, message)]."""
    ref = reference or "HEAD"
    plage = f"{sha}..{ref}" if sha else ref
    try:
        sortie = _git(depot, "log", "--format=%h%x1f%cI%x1f%s", plage)
    except (RuntimeError, subprocess.TimeoutExpired):
        return []
    return [tuple(l.split("\x1f", 2)) for l in sortie.splitlines() if l.count("\x1f") == 2]  # type: ignore[misc]


def date_commit(depot: Path, sha: str) -> datetime | None:
    try:
        return datetime.fromisoformat(_git(depot, "show", "-s", "--format=%cI", sha))
    except (RuntimeError, ValueError, subprocess.TimeoutExpired):
        return None
