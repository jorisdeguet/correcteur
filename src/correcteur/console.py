"""Sortie console uniforme (UTF-8 partout, y compris dans la console Windows).

Les messages sont du texte brut : un nom d'étudiant contenant des crochets n'est pas
interprété comme du balisage Rich.
"""

from __future__ import annotations

import sys
import threading

from rich.console import Console
from rich.text import Text

for _flux in (sys.stdout, sys.stderr):
    try:
        _flux.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

console = Console(highlight=False)
erreurs = Console(stderr=True, highlight=False)
_verrou = threading.Lock()


def info(message: str) -> None:
    with _verrou:
        console.print(Text(message))


def ok(message: str) -> None:
    with _verrou:
        console.print(Text(message, style="green"))


def attention(message: str) -> None:
    with _verrou:
        erreurs.print(Text(message, style="yellow"))


def erreur(message: str) -> None:
    with _verrou:
        erreurs.print(Text(message, style="red"))


def demander(invite: str) -> str:
    """input() protégé contre Ctrl-D."""
    try:
        return input(invite)
    except EOFError:
        return "q"
