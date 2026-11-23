"""Diagnostic machine : versions des outils, appareils Android, ressources.

Chaque recette déclare les outils dont elle a besoin ; le diagnostic tourne au démarrage,
s'arrête si un outil requis manque et son résumé est imprimé en tête de rapport.
Les sondes sont lancées en parallèle (flutter --version peut prendre plusieurs secondes).
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from rich.table import Table

from . import console
from .execution import executer


@dataclass
class Outil:
    nom: str
    present: bool
    version: str = ""
    chemin: str = ""
    detail: str = ""


def _premiere_ligne(texte: str, motif: str | None = None) -> str:
    for ligne in texte.splitlines():
        ligne = ligne.strip()
        if ligne and (motif is None or re.search(motif, ligne, re.IGNORECASE)):
            return ligne
    return ""


def _sonde(nom: str, commande: list[str], motif: str | None = None, delai: float = 30) -> Outil:
    r = executer(commande, delai=delai)
    if r.introuvable:
        return Outil(nom, False)
    ligne = _premiere_ligne(r.stdout + "\n" + r.stderr, motif)
    return Outil(nom, r.code == 0 or bool(ligne), ligne[:90], r.commande[0])


def sdk_android() -> Path | None:
    """Dossier du SDK Android : variables d'environnement puis emplacements usuels (macOS, Windows, Linux)."""
    for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(var) and Path(os.environ[var]).is_dir():
            return Path(os.environ[var])
    candidats = [
        Path.home() / "Library" / "Android" / "sdk",
        Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Android" / "Sdk",
        Path.home() / "Android" / "Sdk",
    ]
    return next((c for c in candidats if c.is_dir()), None)


def _adb() -> Outil:
    adb = shutil.which("adb")
    sdk = sdk_android()
    if not adb and sdk:
        nom = "adb.exe" if os.name == "nt" else "adb"
        if (sdk / "platform-tools" / nom).exists():
            adb = str(sdk / "platform-tools" / nom)
    if not adb:
        return Outil("adb", False)
    o = _sonde("adb", [adb, "version"], r"Android Debug Bridge|Version")
    r = executer([adb, "devices"], delai=15)
    appareils = [l.split()[0] for l in r.stdout.splitlines()[1:] if l.strip().endswith("device")]
    o.detail = f"{len(appareils)} appareil(s) : {', '.join(appareils)}" if appareils else "aucun appareil"
    return o


def _python_typst() -> Outil:
    try:
        import typst as typst_py
        from importlib.metadata import version
        return Outil("typst (paquet Python)", True, version("typst"), getattr(typst_py, "__file__", ""))
    except Exception:
        return Outil("typst (paquet Python)", False)


SONDES = {
    "git": lambda: _sonde("git", ["git", "--version"]),
    "gh": lambda: _sonde("gh", ["gh", "--version"], r"gh version"),
    "uv": lambda: _sonde("uv", ["uv", "--version"]),
    "typst": _python_typst,
    "typst-cli": lambda: _sonde("typst-cli", ["typst", "--version"]),
    "java": lambda: _sonde("java", ["java", "-version"], r"version"),
    "flutter": lambda: _sonde("flutter", ["flutter", "--version"], r"^Flutter", delai=120),
    "dart": lambda: _sonde("dart", ["dart", "--version"], r"Dart SDK"),
    "adb": _adb,
    "firebase": lambda: _sonde("firebase", ["firebase", "--version"]),
    "node": lambda: _sonde("node", ["node", "--version"]),
    "npm": lambda: _sonde("npm", ["npm", "--version"]),
    "mvn": lambda: _sonde("mvn", ["mvn", "--version"], r"Apache Maven"),
    "dotnet": lambda: _sonde("dotnet", ["dotnet", "--version"]),
    "pycharm": lambda: Outil("pycharm", bool(_ide_present("pycharm")), chemin=_ide_present("pycharm") or ""),
    "studio": lambda: Outil("studio", bool(_ide_present("studio")), chemin=_ide_present("studio") or ""),
}
TOUS = list(SONDES)


def _ide_present(nom: str) -> str | None:
    from . import ide
    return ide.trouver(nom)


def systeme() -> dict[str, str]:
    """Système, Python, cœurs, mémoire, disque libre."""
    infos = {
        "système": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "python": f"{platform.python_version()} ({sys.executable})",
        "cœurs": str(os.cpu_count() or "?"),
    }
    try:
        if hasattr(os, "sysconf") and "SC_PHYS_PAGES" in os.sysconf_names:
            infos["mémoire"] = f"{os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES') / 2**30:.1f} Go"
    except (ValueError, OSError):
        pass
    libre = shutil.disk_usage(Path.cwd()).free / 2**30
    infos["disque libre"] = f"{libre:.0f} Go"
    sdk = sdk_android()
    if sdk:
        infos["SDK Android"] = str(sdk)
    return infos


def diagnostiquer(outils: list[str] | None = None) -> dict[str, Outil]:
    noms = outils or TOUS
    inconnus = [n for n in noms if n not in SONDES and n != "python"]
    if inconnus:
        raise ValueError(f"Outils inconnus : {inconnus} (connus : {TOUS})")
    with ThreadPoolExecutor(max_workers=8) as pool:
        resultats = dict(zip([n for n in noms if n in SONDES],
                             pool.map(lambda n: SONDES[n](), [n for n in noms if n in SONDES])))
    return resultats


def afficher(resultats: dict[str, Outil], requis: list[str] | None = None) -> None:
    table = Table(title="Diagnostic machine", show_lines=False)
    table.add_column("Outil")
    table.add_column("Version")
    table.add_column("Détail")
    for cle, valeur in systeme().items():
        table.add_row(cle, valeur, "")
    for nom, o in resultats.items():
        etat = (o.version or "présent") if o.present else ("[red]ABSENT (requis)[/red]" if requis and nom in requis else "[dim]absent[/dim]")
        table.add_row(nom, etat, o.detail or (o.chemin if o.present and not o.version else ""))
    console.console.print(table)


def resume_texte(resultats: dict[str, Outil]) -> str:
    """Une ligne pour l'en-tête des rapports."""
    s = systeme()
    morceaux = [s["système"], "Python " + platform.python_version()]
    morceaux += [f"{n} {o.version}" for n, o in resultats.items() if o.present and o.version]
    return " · ".join(morceaux)


def verifier(requis: list[str], afficher_table: bool = True) -> dict[str, Outil]:
    """Diagnostic des outils requis ; ``SystemExit`` si l'un manque."""
    resultats = diagnostiquer([r for r in requis if r != "python"])
    if afficher_table:
        afficher(resultats, requis)
    manquants = [n for n, o in resultats.items() if not o.present]
    if manquants:
        raise SystemExit(f"Outil(s) requis absent(s) : {', '.join(manquants)}. Lancez « corr doctor » pour le détail.")
    return resultats
