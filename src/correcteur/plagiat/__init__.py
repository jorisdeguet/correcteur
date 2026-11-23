"""Détection de similarité entre copies (winnowing + plus longue séquence commune)."""

from .moteur import Analyse, Paire, fichiers_de
from .rapport import ecrire as rapport_html

__all__ = ["Analyse", "Paire", "fichiers_de", "rapport_html"]
