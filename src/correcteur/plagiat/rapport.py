"""Rapport HTML autonome (aucune dépendance réseau) : paires triées, vue côte à côte surlignée."""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path
from typing import Callable

from .moteur import Analyse, Paire

STYLE = """
body{font:14px system-ui,sans-serif;margin:1.5rem;color:#1a1a1a}
h1{font-size:1.3rem} table{border-collapse:collapse;width:100%}
th,td{border-bottom:1px solid #ddd;padding:.3rem .5rem;text-align:left;vertical-align:top}
th{background:#f3f3f3;position:sticky;top:0}
.barre{display:inline-block;height:.7rem;background:#c0392b;vertical-align:middle}
details{margin:.3rem 0} summary{cursor:pointer}
.cote{display:grid;grid-template-columns:1fr 1fr;gap:.5rem}
pre{margin:0;font:12px ui-monospace,Menlo,Consolas,monospace;background:#fafafa;border:1px solid #e3e3e3;
    padding:.3rem;overflow:auto;max-height:36rem}
.l{display:block;white-space:pre} .c{background:#ffe08a} .r{background:#f5b7b1}
.n{color:#999;user-select:none;display:inline-block;width:3.2em;text-align:right;margin-right:.6em}
.note{color:#555;font-size:12px}
"""


def _code(fichiers, lignes_communes: dict[str, set[int]], region: tuple) -> str:
    morceaux = []
    for f in fichiers:
        communes = lignes_communes.get(f.chemin, set())
        if not communes and not (region and region[0] == f.chemin):
            continue
        lignes = []
        for n, texte in enumerate(f.source.splitlines(), 1):
            classe = "r" if region and region[0] == f.chemin and region[1] <= n <= region[2] else (
                "c" if n in communes else "")
            lignes.append(f'<span class="l {classe}"><span class="n">{n}</span>{html.escape(texte)}</span>')
        morceaux.append(f"<p><b>{html.escape(f.chemin)}</b></p><pre>{''.join(lignes)}</pre>")
    return "".join(morceaux) or "<p class=note>Aucune ligne commune à afficher.</p>"


def ecrire(analyse: Analyse, paires: list[Paire], chemin: Path, titre: str = "Similarité entre copies",
           nom: Callable[[str], str] = str, notes: list[str] | None = None) -> Path:
    lignes = []
    for p in paires:
        la, lb = analyse.lignes_communes(p.a, p.b)
        sa, sb = analyse.soumissions[p.a], analyse.soumissions[p.b]
        region = lambda r: f"{html.escape(r[0])} l.{r[1]}–{r[2]}" if r else "—"
        lignes.append(f"""
<tr><td><span class="barre" style="width:{p.similarite * 80:.0f}px"></span> {p.similarite:.0%}</td>
<td>{p.jaccard:.0%}</td><td>{p.plus_longue}</td>
<td>{html.escape(nom(p.a))}<br><span class=note>{region(p.region_a)}</span></td>
<td>{html.escape(nom(p.b))}<br><span class=note>{region(p.region_b)}</span></td></tr>
<tr><td colspan=5><details><summary>Voir côte à côte ({p.communes} empreintes communes)</summary>
<div class="cote"><div>{_code(sa.fichiers, la, p.region_a)}</div><div>{_code(sb.fichiers, lb, p.region_b)}</div></div>
</details></td></tr>""")
    corps = "".join(lignes) or "<tr><td colspan=5>Aucune paire au-dessus du seuil.</td></tr>"
    remarques = "".join(f"<li>{html.escape(n)}</li>" for n in (notes or []))
    page = f"""<!doctype html><html lang="fr"><meta charset="utf-8"><title>{html.escape(titre)}</title>
<style>{STYLE}</style><h1>{html.escape(titre)}</h1>
<p class=note>{len(analyse.soumissions)} copies · k = {analyse.k} jetons · fenêtre {analyse.w} ·
généré le {datetime.now():%Y-%m-%d %H:%M}. Similarité = part de la plus petite copie retrouvée dans l'autre
(identifiants, nombres et chaînes normalisés ; code de départ et fragments présents chez plus de
{analyse.frequence_max:.0%} des copies ignorés). Plus longue = plus longue séquence commune, en jetons.
<span class=c>Jaune</span> : lignes couvertes par une empreinte commune ; <span class=r>rose</span> : plus longue séquence commune.</p>
{f"<ul>{remarques}</ul>" if remarques else ""}
<table><tr><th>Similarité</th><th>Jaccard</th><th>Plus longue</th><th>Copie A</th><th>Copie B</th></tr>{corps}</table>
</html>"""
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(page, encoding="utf-8")
    return chemin
