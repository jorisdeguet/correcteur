"""Ouvrir une copie dans un IDE (PyCharm, Android Studio, VS Code) et parcourir les copies au clavier."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Sequence

from . import console

MAC = sys.platform == "darwin"
WINDOWS = os.name == "nt"

_APPS_MAC = {"pycharm": ["PyCharm.app", "PyCharm CE.app", "PyCharm Professional Edition.app"],
             "studio": ["Android Studio.app"], "code": ["Visual Studio Code.app"]}
_SCRIPTS = {"pycharm": ["pycharm", "pycharm64.exe", "pycharm.cmd", "charm"],
            "studio": ["studio", "studio64.exe", "studio.cmd"], "code": ["code", "code.cmd"]}


def _toolbox_scripts() -> list[Path]:
    if MAC:
        return [Path.home() / "Library/Application Support/JetBrains/Toolbox/scripts"]
    if WINDOWS:
        return [Path(os.environ.get("LOCALAPPDATA", "")) / "JetBrains/Toolbox/scripts"]
    return [Path.home() / ".local/share/JetBrains/Toolbox/scripts"]


def trouver(ide: str) -> str | None:
    """Commande pour lancer ``ide`` (pycharm, studio, code) : script Toolbox, PATH, puis app macOS."""
    for dossier in _toolbox_scripts():
        for nom in _SCRIPTS[ide]:
            if (dossier / nom).exists():
                return str(dossier / nom)
    for nom in _SCRIPTS[ide]:
        if shutil.which(nom):
            return shutil.which(nom)
    if WINDOWS:
        for base in (Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "JetBrains",
                     Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Android"):
            if base.is_dir():
                for exe in base.glob(f"*/bin/{_SCRIPTS[ide][1]}"):
                    return str(exe)
    if MAC:
        for app in _APPS_MAC[ide]:
            for racine in (Path("/Applications"), Path.home() / "Applications"):
                if (racine / app).exists():
                    return str(racine / app)
    return None


def ouvrir(ide: str, projet: Path, fichiers: Sequence[Path] = ()) -> bool:
    """Ouvre ``projet`` (et des fichiers) dans l'IDE ; retourne False si l'IDE est introuvable."""
    commande = trouver(ide)
    if commande is None:
        console.erreur(f"{ide} introuvable (ni Toolbox, ni PATH, ni application installée).")
        return False
    args = [str(projet), *map(str, fichiers)]
    if commande.endswith(".app"):
        cmd = ["open", "-na", commande, "--args", *args]
    else:
        cmd = [commande, *args]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     **({"creationflags": 0x00000008} if WINDOWS else {"start_new_session": True}))
    return True


_SDK_RES = (
    re.compile(r'(project-jdk-name=")[^"]*(")'),
    re.compile(r'(<option name="sdkName" value=")[^"]*(")'),
    re.compile(r'(<orderEntry type="jdk" jdkName=")[^"]*(")'),
)


def definir_sdk_pycharm(projet: Path, sdk: str) -> None:
    """Pointe le projet PyCharm (.idea) vers un interpréteur déjà configuré dans PyCharm."""
    idea = projet / ".idea"
    for config in [idea / "misc.xml", *idea.glob("*.iml")]:
        if config.is_file():
            texte = config.read_text(encoding="utf-8")
            for motif in _SDK_RES:
                texte = motif.sub(lambda m: f"{m.group(1)}{sdk}{m.group(2)}", texte)
            config.write_text(texte, encoding="utf-8")


def parcourir(
    elements: Sequence,
    titre: Callable[[int, object], str],
    ouvrir_element: Callable[[object], None],
    actions: dict[str, tuple[str, Callable[[object], None]]] | None = None,
    depart: int = 0,
) -> None:
    """Boucle clavier commune : Entrée/n suivant, s sauter, p précédent, r rouvrir, q quitter.

    ``actions`` ajoute des touches : {"4": ("combinaisons Q4", fonction(element))}.
    """
    actions = actions or {}
    extra = "  ".join(f"[{k}] {libelle}" for k, (libelle, _) in actions.items())
    index, ouvert, affiche, auto = depart, None, None, True
    while 0 <= index < len(elements):
        element = elements[index]
        if affiche != index:
            console.info(f"\n[{index + 1}/{len(elements)}] {titre(index, element)}")
            affiche = index
        if auto and ouvert != index:
            ouvrir_element(element)
            ouvert = index
        if ouvert == index:
            invite = f"  [Entrée/n] suivant  [s] sauter le suivant  [p] précédent  [r] rouvrir  {extra}  [q] quitter : "
        else:
            invite = f"  (non ouvert)  [Entrée/o] ouvrir  [s] sauter  [p] précédent  {extra}  [q] quitter : "
        choix = console.demander(invite).strip().casefold()
        if choix in ("", "n", "o"):
            if ouvert == index and choix != "o":
                index += 1
            auto = True
        elif choix == "s":
            index += 1
            auto = False
        elif choix == "p":
            index = max(0, index - 1)
            auto = True
        elif choix == "r":
            ouvert, auto = None, True
        elif choix in actions:
            actions[choix][1](element)
        elif choix == "q":
            break
    console.info("Terminé.")
