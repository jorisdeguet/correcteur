"""Petites fonctions de texte partagées : ANSI, troncature, code compact, sorties normalisées."""

from __future__ import annotations

import re
import unicodedata

ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def sans_ansi(texte: str | None) -> str:
    """Retire les séquences de couleur ANSI."""
    return ANSI_RE.sub("", texte or "")


def limiter_lignes(texte: str, maximum: int) -> str:
    """Garde les `maximum` premières lignes et indique combien ont été coupées."""
    lignes = (texte or "").strip().splitlines()
    if len(lignes) <= maximum:
        return "\n".join(lignes)
    return "\n".join(lignes[:maximum] + [f"[… {len(lignes) - maximum} lignes de plus]"])


def code_compact(source: str) -> str:
    """Retire les lignes vides et les espaces de fin pour réduire la hauteur des rapports."""
    return "\n".join(ligne.rstrip() for ligne in (source or "").splitlines() if ligne.strip())


def sortie_normalisee(texte: str) -> str:
    """Sortie comparable : sans ANSI, sans espaces de fin de ligne ni lignes vides aux extrémités."""
    return "\n".join(ligne.rstrip() for ligne in sans_ansi(texte).strip().splitlines())


def condenser(texte: str, limite: int = 130) -> str:
    """Une seule ligne, tronquée."""
    texte = " ".join((texte or "").split())
    return texte if len(texte) <= limite else texte[:limite] + "…"


def slug(texte: str) -> str:
    """« Parc Victoria » → « parc-victoria » (sans accents)."""
    texte = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", texte).strip("-")


def sans_accents_majuscules(texte: str) -> str:
    """« Élodie Côté » → « ELODIECOTE », pour comparer des noms de dépôts."""
    texte = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]", "", texte)
