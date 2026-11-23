"""``corr plagiat DOSSIER`` : similarité entre les copies d'un dossier.

Réutilise la découverte des copies des recettes : échéance, prolongations, exclusions,
liste de classe et extraction (le commit retenu est celui de l'échéance).
"""

from __future__ import annotations

import time
import webbrowser
from pathlib import Path

from .. import console
from ..recette import Recette
from .moteur import Analyse
from .rapport import ecrire


def principal(argv: list[str]) -> int:
    source = "archives" if "--archives" in argv else ("dossiers" if "--dossiers" in argv else "git")
    argv = [a for a in argv if a not in ("--archives", "--dossiers")]
    r = Recette("plagiat", version="1", description="Similarité entre copies (winnowing).",
                outils=["git"] if source == "git" else [], source=source)

    @r.options
    def options(p):
        p.add_argument("--archives", action="store_true", help="Copies en .zip/.7z plutôt qu'en dépôts git.")
        p.add_argument("--dossiers", action="store_true", help="Copies en dossiers sans git.")
        p.add_argument("--motifs", nargs="+", default=["*"], help="Fichiers comparés, ex. '*.py' 'racine.py'.")
        p.add_argument("--exclure-fichiers", nargs="+", default=[],
                       help="Fichiers ignorés, ex. 'theme.kt' 'color.kt' '*test*'.")
        p.add_argument("--depart", type=Path, help="Dossier du code de départ (ignoré dans la comparaison).")
        p.add_argument("--seuil", type=float, default=0.4, help="Similarité minimale rapportée (défaut 0.4).")
        p.add_argument("-k", type=int, default=12, help="Longueur des k-grammes en jetons (défaut 12).")
        p.add_argument("-w", type=int, default=8, help="Fenêtre du winnowing (défaut 8).")
        p.add_argument("--frequence-max", type=float, default=0.5,
                       help="Ignore les fragments présents dans plus de cette fraction des copies.")
        p.add_argument("--sans-navigateur", action="store_true", help="N'ouvre pas le rapport.")

    ctx, copies = r.session(argv)
    a = ctx.args
    debut = time.perf_counter()
    analyse = Analyse(k=a.k, w=a.w, frequence_max=a.frequence_max, cache=ctx.etat.dossier / "empreintes")
    if a.depart:
        analyse.depart(a.depart.expanduser().resolve(), a.motifs)
    noms = {}
    for c in copies:
        if c.dossier is None:
            continue
        analyse.ajouter(c.id, c.dossier, a.motifs, a.exclure_fichiers)
        noms[c.id] = f"{c.etudiant.nom_affiche} ({c.id}, gr. {c.groupe})"
    paires = analyse.paires(seuil=a.seuil)
    remarques = [f"Échéance : {a.echeances.echeance:%Y-%m-%d %H:%M}"] if a.echeances.echeance else []
    vides = [c.etudiant.nom_affiche for c in copies if c.dossier is None]
    if vides:
        remarques.append("Sans copie : " + ", ".join(vides))
    chemin = ecrire(analyse, paires, ctx.sortie / "plagiat.html",
                    titre=f"Similarité — {a.racine.name}", nom=lambda i: noms.get(i, i), notes=remarques)
    console.ok(f"{len(paires)} paire(s) ≥ {a.seuil:.0%} parmi {len(noms)} copies en "
               f"{time.perf_counter() - debut:.1f} s : {chemin}")
    for p in paires[:15]:
        console.info(f"  {p.similarite:5.0%}  LCS {p.plus_longue:4d}  {noms.get(p.a)}  ↔  {noms.get(p.b)}")
    if not a.sans_navigateur:
        webbrowser.open(chemin.as_uri())
    ctx.ordonnanceur.fermer()
    return 0
