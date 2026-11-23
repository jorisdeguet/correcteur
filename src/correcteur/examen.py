"""Examens machine en Python : fichiers ``question_N.py`` exécutés en boîte noire.

Une fiche par étudiant sur une seule page : bande de notes, puis des cartes par question
(checklist, indices automatiques, code, sortie, et un complément propre à l'examen :
combinaisons, variantes, graphique…). La taille du code s'adapte à la hauteur disponible
(``fit-column``).

Utilisé par les recettes d'intra et de final ; tout ce qui est propre à un examen
(attendus, checklists, points, notions interdites, compléments) est passé en paramètre.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import typst as T
from .classe import Etudiant
from .codepython import fichiers_questions, lire, notions_interdites
from .execution import env_minimal, executer
from .texte import sans_ansi, sortie_normalisee

MPL_CONFIG = str(Path(tempfile.gettempdir()) / "correcteur-mpl")


def executer_question(path: Path, timeout: int, prefix_args: list[str] | None = None,
                      extra_args: list[str] | None = None, python: str | None = None,
                      sortie_max: int = 5000) -> tuple[str, str, str]:
    """Exécute un fichier de question dans son dossier, environnement minimal (HOME = dossier).

    Signature historique ``(stdout, stderr, statut)`` : utilisable comme ``run`` par les
    modules de variantes. Statut : « Code de retour : 0 » ou « Arrêt après 8 s (délai dépassé) ».
    """
    path = Path(path)
    env = env_minimal(path.parent, MPLCONFIGDIR=MPL_CONFIG)
    r = executer([python or sys.executable, "-u", *(prefix_args or []), path.name, *(extra_args or [])],
                 cwd=path.parent, delai=timeout, env=env, sortie_max=sortie_max)
    if r.delai_depasse:
        statut = f"Arrêt après {timeout} s (délai dépassé)"
    elif r.introuvable:
        statut = "Interpréteur introuvable"
    else:
        statut = f"Code de retour : {r.code}"
    return r.stdout, r.stderr, statut


def prechauffer_matplotlib(python: str | None = None) -> None:
    """Construit une fois le cache des polices (sinon chaque exécution isolée le reconstruit, > 10 s)."""
    executer([python or sys.executable, "-c", "import matplotlib.pyplot"], delai=300,
             env_extra={"MPLCONFIGDIR": MPL_CONFIG, "MPLBACKEND": "Agg"})


@dataclass
class Examen:
    titre: str                                   # « Intra sommatif A26 »
    checklists: dict[int, tuple[str, ...]]
    attendus: dict[int, str]                     # sortie attendue par question (comparée normalisée)
    points: dict[int, float]                     # toutes les questions, y compris papier (1, 2…)
    colonnes: tuple[tuple[int, ...], ...] = ((3, 5), (4, 6))
    interdits: dict[str, re.Pattern] = field(default_factory=dict)
    lignes_max: int = 30

    @property
    def questions(self) -> tuple[int, ...]:
        return tuple(n for col in self.colonnes for n in col)

    # ─── Exécution ───────────────────────────────────────────────────────────

    def executer(self, projet: Path, timeout: int,
                 lancer: Callable[[int, int, Path], tuple[str, str, str]] | None = None,
                 completer: Callable[[int, int, Path, dict], None] | None = None) -> dict[int, list[dict]]:
        """Exécute chaque fichier de question trouvé dans ``projet``.

        ``lancer(numero, indice, chemin)`` remplace l'exécution par défaut (ex. lanceur graphique) ;
        ``completer(numero, indice, chemin, entree)`` ajoute des informations (variantes, combinaisons…).
        """
        questions: dict[int, list[dict]] = {}
        for numero, chemins in fichiers_questions(projet).items():
            questions[numero] = []
            for indice, chemin in enumerate(chemins):
                stdout, stderr, statut = (lancer(numero, indice, chemin) if lancer
                                          else executer_question(chemin, timeout))
                sortie = sans_ansi(stdout).strip()
                erreurs = sans_ansi(stderr).strip().replace(f"{chemin.parent}{os.sep}", "")
                source = lire(chemin) or ""
                entree = {
                    "filename": str(chemin.relative_to(projet)),
                    "source": source,
                    "status": statut,
                    "crashed": bool(erreurs) or statut != "Code de retour : 0",
                    "identical": numero in self.attendus and sortie_normalisee(sortie) == self.attendus[numero],
                    "forbidden": notions_interdites(source, self.interdits),
                }
                if completer:
                    completer(numero, indice, chemin, entree)
                if erreurs:
                    sortie = f"{sortie}\n\nERREURS :\n{erreurs}".strip()
                lignes = sortie.splitlines()
                if len(lignes) > self.lignes_max:
                    cachees = len(lignes) - self.lignes_max
                    sortie = "\n".join(lignes[:self.lignes_max] + [f"[… {cachees} lignes de plus]"])
                entree["output"] = sortie or "(aucune sortie)"
                questions[numero].append(entree)
        return questions

    # ─── Rendu ───────────────────────────────────────────────────────────────

    def indices(self, numero: int, entree: dict) -> str:
        morceaux = ["plante" if entree["crashed"] else "s'exécute"]
        if numero in self.attendus:
            morceaux.append("sortie identique" if entree["identical"] else "sortie DIFFÉRENTE")
        elif "image" in entree:
            morceaux.append("graphique produit" if entree.get("image") else "AUCUN graphique")
        if entree["forbidden"]:
            morceaux.append("notions absentes ? " + ", ".join(entree["forbidden"]))
        morceaux += entree.get("structure", []) + entree.get("notes", [])
        return f'#text(fill: luma(90))[_Indice auto : {T.chaine(" · ".join(morceaux))}_]'

    def echelle(self, points: float) -> str:
        if points > 2:
            return " #h(4pt) ".join(str(i) for i in range(int(points) + 1))
        return " #h(5pt) ".join(str(i / 2).replace(".0", "").replace(".", ",") for i in range(0, int(2 * points) + 1))

    def carte(self, numero: int, entrees: list[dict], complement: Callable[[int, dict], str] | None = None) -> str:
        """Carte d'une question ; la taille ``s`` du code est fixée par fit-column."""
        if not entrees:
            contenu = T.rouge("Fichier de la question introuvable.")
        else:
            sections = []
            for e in entrees:
                extra = complement(numero, e) if complement else ""
                sections.append(f"""{self.indices(numero, e)}

*{T.texte(e["filename"])}*
{T.brut(e["source"], "python")}

*Exécution — {T.texte(e["status"])}*
{T.brut(e["output"])}
{extra}""")
            contenu = "\n\n".join(sections)
        liste = "\n".join(f"#cb {T.texte(item)} \\" for item in self.checklists.get(numero, ()))
        points = self.points[numero]
        return f"""#block(stroke: .5pt, inset: 4pt, radius: 2pt, width: 100%)[
  #grid(columns: (1fr, auto), align: horizon,
    text(size: 10pt, weight: "bold")[Question {numero}],
    text(size: 8pt)[{self.echelle(points)} #h(8pt) *#box(width: 10mm, stroke: (bottom: .5pt))[] / {points:g}*],
  )
  #text(size: 6.5pt)[
{liste}
  ]
  #text(size: s)[
    {contenu}
  ]
]"""

    def bande_notes(self) -> str:
        vide = "#box(width: 9mm, stroke: (bottom: .5pt))[]"
        notes = " #h(1fr) ".join(f"Q{n} : {vide} / {p:g}" for n, p in self.points.items())
        return f"""#block(stroke: .8pt, inset: 5pt, radius: 2pt, width: 100%)[
  #set text(size: 10pt)
  {notes} #h(1fr) *Total : #box(width: 12mm, stroke: (bottom: .8pt))[] / {sum(self.points.values()):g}*
]"""

    def fiche(self, etudiant: Etudiant, groupe: str, questions: dict[int, list[dict]],
              complement: Callable[[int, dict], str] | None = None) -> str:
        identite = f"{etudiant.nom_complet} — Matricule {etudiant.id} — Groupe {groupe}"
        colonnes = []
        for col in self.colonnes:
            cartes = "\n#v(4pt)\n".join(self.carte(n, questions.get(n, []), complement) for n in col)
            colonnes.append(f"fit-column(s => [\n{cartes}\n])")
        return f"""#grid(rows: (auto, auto, 1fr), row-gutter: 4pt,
text(size: 12pt, weight: "bold")[{T.texte(self.titre)} — {T.texte(identite)}],
[{self.bande_notes()}],
grid(columns: ({", ".join(["1fr"] * len(colonnes))}), column-gutter: 5pt,
{",".join(chr(10) + c for c in colonnes)},
),
)
"""
