"""Empreintes par *winnowing* (Schleimer, Wilkerson, Aiken 2003 — l'algorithme de MOSS).

1. La suite de jetons est découpée en k-grammes, hachés en entiers 64 bits (numpy, vectorisé).
2. Dans chaque fenêtre de ``w`` k-grammes consécutifs, on garde le plus petit hachage.
   Garantie : toute séquence commune d'au moins ``w + k - 1`` jetons partage au moins une
   empreinte ; une séquence plus courte que ``k`` n'en partage aucune (pas de bruit).
3. Un index inversé ``empreinte → copies`` donne les paires candidates en un seul passage,
   sans comparer toutes les paires entre elles.

Plus longue séquence commune : tableau de suffixes (``pydivsufsort``, en C) sur les deux
suites de jetons concaténées ; repli en Python pur si le module est absent.
"""

from __future__ import annotations

import zlib

import numpy as np

_MASQUE = np.uint64(0xFFFFFFFFFFFFFFFF)
_BASE = np.uint64(1_000_003)


def identifiants(valeurs: list[str], memo: dict[str, int] | None = None) -> np.ndarray:
    """Jetons → entiers stables (CRC32 + 1) : identiques d'une exécution et d'une machine à l'autre."""
    memo = {} if memo is None else memo
    ids = np.empty(len(valeurs), dtype=np.int64)
    for i, v in enumerate(valeurs):
        ident = memo.get(v)
        if ident is None:
            ident = memo[v] = zlib.crc32(v.encode("utf-8")) + 1
        ids[i] = ident
    return ids


def hachages_kgrammes(ids: np.ndarray, k: int) -> np.ndarray:
    """Hachage polynomial de chaque k-gramme (débordement 64 bits voulu)."""
    n = len(ids) - k + 1
    if n <= 0:
        return np.empty(0, dtype=np.uint64)
    t = (ids.astype(np.uint64) * np.uint64(0x9E3779B97F4A7C15)) & _MASQUE
    h = np.zeros(n, dtype=np.uint64)
    with np.errstate(over="ignore"):
        for j in range(k):
            h = h * _BASE + t[j:j + n]
    return h


def winnowing(hachages: np.ndarray, w: int) -> tuple[np.ndarray, np.ndarray]:
    """Empreintes retenues : (hachages, positions du k-gramme), le minimum le plus à droite par fenêtre."""
    n = len(hachages)
    if n == 0:
        return hachages, np.empty(0, dtype=np.int64)
    if n <= w:
        i = n - 1 - int(np.argmin(hachages[::-1]))
        return hachages[i:i + 1], np.array([i])
    fenetres = np.lib.stride_tricks.sliding_window_view(hachages, w)
    # argmin sur la fenêtre renversée = minimum le plus à droite (règle de robustesse de l'article)
    pos = np.arange(n - w + 1) + (w - 1 - np.argmin(fenetres[:, ::-1], axis=1))
    pos = np.unique(pos)
    return hachages[pos], pos


def plus_longue_commune(a: np.ndarray, b: np.ndarray) -> tuple[int, int, int]:
    """Plus longue sous-séquence contiguë commune : (longueur, début dans a, début dans b)."""
    if len(a) == 0 or len(b) == 0:
        return 0, 0, 0
    try:
        from pydivsufsort import divsufsort, kasai
    except ImportError:
        return _plus_longue_commune_dp(a, b)
    s = np.concatenate([a.astype(np.int64) + 2, [1], b.astype(np.int64) + 2]).astype(np.int64)
    sa = np.asarray(divsufsort(s))
    lcp = np.asarray(kasai(s, sa))
    na = len(a)
    cote = sa < na  # True : suffixe commençant dans a
    voisins_differents = cote[:-1] != cote[1:]
    if not voisins_differents.any():
        return 0, 0, 0
    lcp_utiles = np.where(voisins_differents, lcp[:-1], 0)
    i = int(np.argmax(lcp_utiles))
    longueur = int(lcp_utiles[i])
    p, q = int(sa[i]), int(sa[i + 1])
    if p > q:
        p, q = q, p
    longueur = min(longueur, na - p)  # le séparateur unique borne déjà, par sécurité
    return longueur, p, q - na - 1


def _plus_longue_commune_dp(a: np.ndarray, b: np.ndarray) -> tuple[int, int, int]:
    """Programmation dynamique ligne par ligne (numpy) : O(n·m) mais sans dépendance C."""
    meilleur, fin_a, fin_b = 0, 0, 0
    precedent = np.zeros(len(b) + 1, dtype=np.int32)
    for i, x in enumerate(a, 1):
        courant = np.zeros_like(precedent)
        egal = b == x
        courant[1:][egal] = precedent[:-1][egal] + 1
        j = int(np.argmax(courant))
        if courant[j] > meilleur:
            meilleur, fin_a, fin_b = int(courant[j]), i, j
        precedent = courant
    return meilleur, fin_a - meilleur, fin_b - meilleur
