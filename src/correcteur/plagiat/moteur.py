"""Détection de similarité entre copies.

Usage typique ::

    from correcteur.plagiat import Analyse
    a = Analyse(k=12, w=8)
    a.depart(Path("TP1-Depart"))              # code fourni : ignoré
    for copie in copies:
        a.ajouter(copie.id, copie.dossier, motifs=["*.py"])
    paires = a.paires(seuil=0.5)
    rapport_html(a, paires, Path("plagiat.html"))

Les empreintes d'une copie ne dépendent que de son contenu : ``Cache`` les conserve
sur disque, une nouvelle version d'une seule copie ne recalcule que celle-ci.
"""

from __future__ import annotations

import fnmatch
import hashlib
import pickle
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import numpy as np

from . import empreintes as emp
from .normalisation import jetons as tokeniser
from .normalisation import langage

DOSSIERS_IGNORES = {".git", ".venv", "venv", "__pycache__", "build", ".dart_tool", ".gradle", ".idea",
                    "node_modules", "bin", "obj", ".correction", "__macosx", "generated"}


@dataclass
class Fichier:
    chemin: str          # relatif à la copie
    source: str
    debut: int           # indice du premier jeton dans la suite de la copie
    lignes: list[int]    # ligne de chaque jeton


@dataclass
class Soumission:
    ident: str
    fichiers: list[Fichier] = field(default_factory=list)
    ids: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.int64))
    hachages: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.uint64))
    positions: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.int64))

    def localiser(self, indice: int) -> tuple[Fichier, int]:
        """Jeton → (fichier, ligne)."""
        f = next(f for f in reversed(self.fichiers) if f.debut <= indice)
        return f, f.lignes[min(indice - f.debut, len(f.lignes) - 1)] if f.lignes else 1


@dataclass
class Paire:
    a: str
    b: str
    communes: int                 # empreintes partagées (hors départ et hors trop fréquentes)
    similarite: float             # communes / min(|A|, |B|) : part de la plus petite copie retrouvée dans l'autre
    jaccard: float
    plus_longue: int = 0          # plus longue séquence commune, en jetons
    region_a: tuple = ()          # (fichier, ligne début, ligne fin)
    region_b: tuple = ()


def fichiers_de(dossier: Path, motifs: Iterable[str] = ("*",), exclusions: Iterable[str] = ()) -> list[Path]:
    """Fichiers source reconnus (langage connu) correspondant aux motifs, hors dossiers générés."""
    motifs, exclusions = list(motifs), list(exclusions)
    trouves = []
    for p in sorted(dossier.rglob("*")):
        if not p.is_file() or langage(p) is None:
            continue
        rel = p.relative_to(dossier)
        if any(part.casefold() in DOSSIERS_IGNORES for part in rel.parts[:-1]):
            continue
        nom = rel.as_posix()
        if not any(fnmatch.fnmatch(p.name, m) or fnmatch.fnmatch(nom, m) for m in motifs):
            continue
        if any(fnmatch.fnmatch(p.name.lower(), e.lower()) or fnmatch.fnmatch(nom.lower(), e.lower())
               for e in exclusions):
            continue
        trouves.append(p)
    return trouves


class Analyse:
    def __init__(self, k: int = 12, w: int = 8, frequence_max: float = 0.5, cache: Path | None = None):
        """
        k : longueur des k-grammes en jetons (≈ une à deux lignes de code) ;
        w : fenêtre du winnowing ; toute séquence commune de ≥ k + w - 1 jetons est détectée ;
        frequence_max : une empreinte présente dans plus de cette fraction des copies est ignorée
                        (code imposé par l'énoncé, idiomes).
        """
        self.k, self.w, self.frequence_max = k, w, frequence_max
        self.vocabulaire: dict[str, int] = {}
        self.soumissions: dict[str, Soumission] = {}
        self.depart_hachages: set[int] = set()
        self.cache = cache
        if cache:
            cache.mkdir(parents=True, exist_ok=True)

    # ─── Construction ────────────────────────────────────────────────────────

    def _soumission(self, ident: str, fichiers: list[tuple[str, str]]) -> Soumission:
        s = Soumission(ident)
        valeurs: list[str] = []
        for chemin, source in fichiers:
            lang = langage(chemin)
            if lang is None:
                continue
            js = tokeniser(source, lang)
            s.fichiers.append(Fichier(chemin, source, len(valeurs), [j.ligne for j in js]))
            valeurs += [j.valeur for j in js]
            valeurs.append("\x00fin-de-fichier")  # un k-gramme ne chevauche pas deux fichiers…
        s.ids = emp.identifiants(valeurs, self.vocabulaire)
        fin = int(emp.identifiants(["\x00fin-de-fichier"])[0])
        h = emp.hachages_kgrammes(s.ids, self.k)
        # … on retire les k-grammes qui contiennent la marque de fin de fichier
        if len(h):
            contient_fin = np.convolve((s.ids == fin).astype(np.int8), np.ones(self.k, dtype=np.int8), "valid") > 0
            garde = ~contient_fin
            pos = np.arange(len(h))[garde]
            hh, p = emp.winnowing(h[garde], self.w)
            s.hachages, s.positions = hh, pos[p]
        return s

    def ajouter_textes(self, ident: str, fichiers: list[tuple[str, str]]) -> Soumission:
        """Ajoute une copie à partir de [(chemin relatif, source)]."""
        cle = None
        if self.cache:
            empreinte = hashlib.sha256(repr((self.k, self.w, fichiers)).encode()).hexdigest()[:24]
            cle = self.cache / f"{empreinte}.pickle"
            if cle.is_file():
                s = pickle.loads(cle.read_bytes())
                s.ident = ident
                self.soumissions[ident] = s
                return s
        s = self._soumission(ident, fichiers)
        self.soumissions[ident] = s
        if cle is not None:
            cle.write_bytes(pickle.dumps(s))
        return s

    def ajouter(self, ident: str, dossier: Path, motifs: Iterable[str] = ("*",),
                exclusions: Iterable[str] = ()) -> Soumission:
        fichiers = [(p.relative_to(dossier).as_posix(), p.read_text(encoding="utf-8-sig", errors="replace"))
                    for p in fichiers_de(dossier, motifs, exclusions)]
        return self.ajouter_textes(ident, fichiers)

    def depart(self, dossier: Path, motifs: Iterable[str] = ("*",)) -> None:
        """Code de départ fourni aux étudiants : ses empreintes sont ignorées."""
        fichiers = [(p.relative_to(dossier).as_posix(), p.read_text(encoding="utf-8-sig", errors="replace"))
                    for p in fichiers_de(dossier, motifs)]
        s = self._soumission("__depart__", fichiers)
        self.depart_hachages |= set(s.hachages.tolist())

    # ─── Comparaison ─────────────────────────────────────────────────────────

    def index(self) -> dict[int, list[str]]:
        idx: dict[int, set[str]] = defaultdict(set)
        for ident, s in self.soumissions.items():
            for h in set(s.hachages.tolist()) - self.depart_hachages:
                idx[h].add(ident)
        limite = max(2, int(self.frequence_max * len(self.soumissions)))
        return {h: sorted(ids) for h, ids in idx.items() if 2 <= len(ids) <= limite}

    def utiles(self, ident: str, index: dict[int, list[str]] | None = None) -> set[int]:
        s = self.soumissions[ident]
        ignores = self.depart_hachages
        return {h for h in s.hachages.tolist() if h not in ignores}

    def paires(self, seuil: float = 0.3, communes_min: int = 5, details: bool = True) -> list[Paire]:
        """Paires de copies dont la similarité (part de la plus petite copie) dépasse ``seuil``."""
        idx = self.index()
        compte: dict[tuple[str, str], int] = defaultdict(int)
        for ids in idx.values():
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    compte[(ids[i], ids[j])] += 1
        tailles = {i: len(self.utiles(i)) or 1 for i in self.soumissions}
        trouvees = []
        for (a, b), n in compte.items():
            if n < communes_min:
                continue
            sim = n / min(tailles[a], tailles[b])
            if sim < seuil:
                continue
            jac = n / (tailles[a] + tailles[b] - n)
            p = Paire(a, b, n, round(sim, 3), round(jac, 3))
            if details:
                self.detailler(p)
            trouvees.append(p)
        trouvees.sort(key=lambda p: (p.similarite, p.plus_longue), reverse=True)
        return trouvees

    def detailler(self, p: Paire) -> Paire:
        """Plus longue séquence commune (jetons normalisés) et sa position dans chaque copie."""
        sa, sb = self.soumissions[p.a], self.soumissions[p.b]
        longueur, i, j = emp.plus_longue_commune(sa.ids, sb.ids)
        p.plus_longue = longueur
        if longueur:
            fa, la = sa.localiser(i)
            _, la2 = sa.localiser(i + longueur - 1)
            fb, lb = sb.localiser(j)
            _, lb2 = sb.localiser(j + longueur - 1)
            p.region_a = (fa.chemin, la, la2)
            p.region_b = (fb.chemin, lb, lb2)
        return p

    def lignes_communes(self, a: str, b: str) -> tuple[dict[str, set[int]], dict[str, set[int]]]:
        """Lignes de chaque copie couvertes par une empreinte partagée (surlignage)."""
        sa, sb = self.soumissions[a], self.soumissions[b]
        communs = (set(sa.hachages.tolist()) & set(sb.hachages.tolist())) - self.depart_hachages
        return self._lignes(sa, communs), self._lignes(sb, communs)

    def _lignes(self, s: Soumission, hachages: set[int]) -> dict[str, set[int]]:
        lignes: dict[str, set[int]] = defaultdict(set)
        for h, pos in zip(s.hachages.tolist(), s.positions.tolist()):
            if h in hachages:
                for t in range(pos, pos + self.k):
                    f, l = s.localiser(t)
                    lignes[f.chemin].add(l)
        return lignes
