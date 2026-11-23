"""Rapports Typst : échappement, blocs usuels, assemblage par groupe et compilation PDF.

Deux façons de produire un rapport :

1. **Balisage généré** (fiches très mises en page) : les fonctions ``texte``, ``brut``,
   ``rouge``… insèrent du contenu externe (code étudiant, sorties) sans qu'il soit
   interprété comme du Typst.
2. **Gabarit + données JSON** (``compiler_gabarit``) : Python écrit ``donnees.json`` et le
   gabarit fait ``#let d = json("donnees.json")``. Aucun échappement à gérer.

La compilation passe par le paquet Python ``typst`` (aucun exécutable à installer,
y compris sous Windows) et se rabat sur la commande ``typst`` si le paquet manque.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable

TAILLE_RE = re.compile(r"^\d+(?:\.\d+)?(?:pt|mm|cm|in)$")


# ─── Échappement et blocs ────────────────────────────────────────────────────

def chaine(texte: str) -> str:
    """Contenu d'une chaîne littérale Typst ("…")."""
    return (
        (texte or "").replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "")
        .replace("\t", "    ")
    )


def texte(t: str) -> str:
    """Texte externe affiché tel quel, sans syntaxe Typst."""
    return f'#text("{chaine(t)}")'


def brut(t: str, langage: str = "", bloc: bool = True) -> str:
    """Bloc de code ou de console (``raw``)."""
    lang = f', lang: "{langage}"' if langage else ""
    return f'#raw("{chaine(t)}"{lang}, block: {"true" if bloc else "false"})'


def rouge(t: str) -> str:
    return f"#text(fill: red)[{texte(t)}]"


def vert(t: str) -> str:
    return f"#text(fill: green.darken(30%))[{texte(t)}]"


def indice(t: str) -> str:
    """Indice automatique, en gris italique."""
    return f'#text(fill: luma(90))[_{texte(t)}_]'


def grille_points(lignes: Iterable[tuple[str, str]]) -> str:
    """Petite grille de points vide à remplir à la main : [(critère, "/ 3"), …]."""
    cellules = ['table.header([*Critère*], [*Points*])']
    cellules += [f"[{texte(l)}], [#h(10mm) {texte(p)}]" for l, p in lignes]
    return "#table(columns: (1fr, 24mm), inset: 2pt, stroke: .3pt, " + ", ".join(cellules) + ")"


def image(chemin: Path, racine: Path, **options: str) -> str:
    """``#image`` avec un chemin relatif à la racine du document."""
    rel = Path(chemin).resolve().relative_to(Path(racine).resolve()).as_posix()
    opts = "".join(f", {k}: {v}" for k, v in options.items())
    return f'#image("{chaine(rel)}"{opts})'


# Colonne dont le texte prend la plus grande taille qui tient dans la hauteur disponible.
FIT_COLUMN = (
    '#let fit-column(body) = layout(size => {\n'
    '  let sizes = (8pt, 7.5pt, 7pt, 6.5pt, 6pt, 5.5pt, 5pt, 4.5pt, 4pt, 3.5pt)\n'
    '  let chosen = sizes.find(s => measure(block(width: size.width, body(s))).height <= size.height)\n'
    '  if chosen == none {\n'
    '    text(fill: red, weight: "bold")[Trop long pour la page, la fin est coupée.]\n'
    '    body(sizes.last())\n'
    '  } else { body(chosen) }\n'
    '})'
)
CASE_A_COCHER = '#let cb = box(width: 5.5pt, height: 5.5pt, stroke: .5pt, baseline: 0.5pt) + h(3pt)'


def document(
    fiches: Iterable[str],
    *,
    genere_par: str,
    taille: str = "8pt",
    marge: str = "8mm",
    paysage: bool = False,
    saut: str = "odd",
    preambule: Iterable[str] = (),
    entete: str = "",
    compact: bool = True,
) -> str:
    """Assemble les fiches d'un groupe.

    ``saut="odd"`` : chaque fiche commence sur une page impaire (impression recto verso,
    une feuille par étudiant) ; ``saut="page"`` : simple saut de page.
    ``compact=False`` retire les réglages d'espacement (paragraphes, titres, fond des blocs de code).
    """
    if not TAILLE_RE.fullmatch(taille):
        raise ValueError(f"Taille Typst invalide : {taille!r} (ex. 8pt)")
    parties = [
        f"// Généré par {genere_par} ; ne pas modifier à la main.",
        f'#set page(paper: "us-letter", margin: {marge}, flipped: {"true" if paysage else "false"})',
        f'#set text(font: "Libertinus Serif", size: {taille}, lang: "fr")',
        "#set heading(numbering: none)",
        *([
            "#set par(spacing: 5pt)",
            "#show heading: set block(above: 6pt, below: 4pt)",
            "#show raw.where(block: true): set block(fill: luma(245), inset: 2pt, width: 100%)",
        ] if compact else []),
        CASE_A_COCHER,
        FIT_COLUMN,
        *preambule,
    ]
    if entete:
        parties.append(entete)
    separateur = '#pagebreak(to: "odd")' if saut == "odd" else "#pagebreak()"
    for i, fiche in enumerate(fiches):
        if i:
            parties.append(separateur)
        parties.append(fiche)
    return "\n\n".join(parties) + "\n"


# ─── Compilation ─────────────────────────────────────────────────────────────

class ErreurTypst(RuntimeError):
    pass


def compiler(source: Path, pdf: Path | None = None, racine: Path | None = None) -> Path:
    """Compile ``source`` (.typ) en PDF ; ``racine`` borne les chemins accessibles (défaut : dossier du .typ)."""
    source = Path(source)
    pdf = Path(pdf) if pdf else source.with_suffix(".pdf")
    racine = Path(racine) if racine else source.parent
    try:
        import typst as typst_py
    except ImportError:
        typst_py = None
    if typst_py is not None:
        try:
            typst_py.compile(str(source), output=str(pdf), root=str(racine))
            return pdf
        except Exception as e:  # le paquet lève ses propres exceptions
            raise ErreurTypst(f"Échec de compilation de {source.name} : {e}") from e
    if not shutil.which("typst"):
        raise ErreurTypst("Ni le paquet Python « typst » ni la commande typst ne sont disponibles.")
    proc = subprocess.run(["typst", "compile", "--root", str(racine), str(source), str(pdf)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode:
        raise ErreurTypst(f"Échec de compilation de {source.name} : {(proc.stderr or proc.stdout).strip()}")
    return pdf


def ecrire_et_compiler(contenu: str, source: Path, pdf: Path | None = None, racine: Path | None = None) -> Path:
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(contenu, encoding="utf-8")
    return compiler(source, pdf, racine)


def compiler_gabarit(gabarit: Path | str, donnees: Any, sortie: Path, nom_donnees: str = "donnees.json") -> Path:
    """Copie le gabarit à côté de ``sortie``, écrit les données en JSON et compile.

    ``gabarit`` : chemin d'un .typ, ou nom d'un gabarit fourni (``notes.typ``…).
    """
    chemin = Path(gabarit)
    if not chemin.is_file():
        chemin = Path(__file__).parent / "gabarits" / str(gabarit)
    sortie = Path(sortie)
    sortie.parent.mkdir(parents=True, exist_ok=True)
    source = sortie.with_suffix(".typ")
    shutil.copyfile(chemin, source)
    fichier_donnees = sortie.with_name(sortie.stem + "-" + nom_donnees)
    fichier_donnees.write_text(json.dumps(donnees, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    texte_source = source.read_text(encoding="utf-8").replace(f'"{nom_donnees}"', f'"{fichier_donnees.name}"')
    source.write_text(texte_source, encoding="utf-8")
    return compiler(source, sortie)


def nombre_pages(pdf: Path) -> int:
    """Nombre de pages d'un PDF (approximation par comptage des objets /Page)."""
    return len(re.findall(rb"/Type\s*/Page(?!s)", Path(pdf).read_bytes()))


def liste_commits(commits, maximum: int | None = 25, compte: bool = True) -> str:
    """Liste compacte « - (04/11 23:12) message » à partir de :func:`correcteur.git_info.historique`."""
    if commits is None:
        return "#text(fill: red)[Aucun dépôt Git trouvé.]"
    if not commits:
        return "#text(fill: red)[Aucun commit trouvé.]"
    affiches = commits if maximum is None else commits[:maximum]
    lignes = [f"- ({c.date_courte}) {texte(c.titre)}" for c in affiches]
    if len(commits) > len(affiches):
        lignes.append(f"- … {len(commits) - len(affiches)} commits plus anciens")
    return (f"*{len(commits)} commit(s)*\n" if compte else "") + "\n".join(lignes)
