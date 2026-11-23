"""Android : SDK, adb (appareils, installation, lancement), package et activité de lancement.

Repris des ``execute.py`` 4N6/5N6, sans ``shell=True`` ni chemins Windows codés en dur.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .diagnostic import sdk_android
from .execution import Resultat, executer

ANDROID_NS = "{http://schemas.android.com/apk/res/android}"


def preparer_environnement() -> Path | None:
    """ANDROID_HOME / ANDROID_SDK_ROOT et platform-tools dans le PATH (pour adb, gradle, flutter)."""
    sdk = sdk_android()
    if sdk is None:
        return None
    os.environ["ANDROID_HOME"] = os.environ["ANDROID_SDK_ROOT"] = str(sdk)
    ajouts = [sdk / "platform-tools"] + [p for p in (sdk / "cmdline-tools" / "latest" / "bin",) if p.is_dir()]
    os.environ["PATH"] = os.pathsep.join([*map(str, ajouts), os.environ.get("PATH", "")])
    return sdk


def appareils() -> list[str]:
    """Identifiants des appareils/émulateurs à l'état « device »."""
    r = executer(["adb", "devices"], delai=15)
    if not r.reussi:
        return []
    ids = []
    for ligne in r.stdout.splitlines()[1:]:
        parties = ligne.split()
        if len(parties) >= 2 and parties[1] == "device":
            ids.append(parties[0])
    return ids


def adb(appareil: str, *args: str, delai: float = 60) -> Resultat:
    return executer(["adb", "-s", appareil, *args], delai=delai)


def desinstaller(appareil: str, package: str) -> None:
    adb(appareil, "uninstall", package, delai=30)


def installer(appareil: str, apk: Path, delai: float = 180) -> Resultat:
    return adb(appareil, "install", "-r", str(apk), delai=delai)


def activite_installee(appareil: str, package: str) -> str | None:
    """Activité MAIN/LAUNCHER réellement installée (``.MainActivity`` ou nom complet)."""
    r = adb(appareil, "shell", "cmd", "package", "resolve-activity", "--brief", "-a",
            "android.intent.action.MAIN", "-c", "android.intent.category.LAUNCHER", "-p", package, delai=10)
    for ligne in reversed(r.stdout.strip().splitlines()):
        if "/" in ligne and package in ligne:
            return ligne.strip().split("/", 1)[1]
    return None


def lancer(appareil: str, package: str, activite: str | None = None) -> Resultat:
    activite = activite or activite_installee(appareil, package) or ".MainActivity"
    if "." not in activite:
        activite = "." + activite
    return adb(appareil, "shell", "am", "start", "-n", f"{package}/{activite}", delai=20)


def rediriger_ports(appareils_: list[str], ports: list[int]) -> None:
    """``adb reverse`` : pour les apps qui visent localhost au lieu de 10.0.2.2."""
    for a in appareils_:
        for p in ports:
            adb(a, "reverse", f"tcp:{p}", f"tcp:{p}", delai=10)


def package_gradle(android: Path) -> str | None:
    for nom in ("app/build.gradle.kts", "app/build.gradle", "build.gradle.kts", "build.gradle"):
        f = android / nom
        if f.is_file():
            texte = f.read_text(encoding="utf-8", errors="replace")
            for motif in (r'applicationId\s*=?\s*[\'"]([^\'"]+)', r'namespace\s*[:=]?\s*[\'"]([^\'"]+)'):
                m = re.search(motif, texte)
                if m:
                    return m.group(1).strip()
    return None


def package_et_activite(android: Path) -> tuple[str | None, str | None]:
    """(package, activité de lancement) depuis les AndroidManifest.xml puis build.gradle."""
    package = None
    manifestes = sorted(android.rglob("AndroidManifest.xml"), key=lambda p: ("src/main" not in p.as_posix(), len(p.parts)))
    for m in manifestes:
        if "build" in m.relative_to(android).parts:
            continue
        try:
            racine = ET.parse(m).getroot()
        except (ET.ParseError, OSError):
            continue
        package = package or racine.get("package")
        for app in racine.findall("application"):
            for act in app.findall("activity") + app.findall("activity-alias"):
                for filtre in act.findall("intent-filter"):
                    actions = {a.get(ANDROID_NS + "name") for a in filtre.findall("action")}
                    cats = {c.get(ANDROID_NS + "name") for c in filtre.findall("category")}
                    if "android.intent.action.MAIN" in actions and "android.intent.category.LAUNCHER" in cats:
                        nom = act.get(ANDROID_NS + "name")
                        if nom:
                            return package or package_gradle(android), nom
    package = package or package_gradle(android)
    return package, ".MainActivity" if package else None


def gradlew(projet: Path) -> Path | None:
    """gradlew.bat sous Windows, gradlew ailleurs (rendu exécutable dans la copie de travail)."""
    nom = "gradlew.bat" if os.name == "nt" else "gradlew"
    f = projet / nom
    if not f.is_file():
        return None
    if os.name != "nt":
        f.chmod(f.stat().st_mode | 0o111)
    return f
