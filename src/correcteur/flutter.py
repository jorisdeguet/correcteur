"""Flutter / Firebase : projets, build d'APK, --dart-define de dev, émulateurs Firebase.

Les builds passent par la ressource ``flutter`` (1 à la fois : verrous du pub cache, Gradle, RAM).
Le code des étudiants n'est jamais modifié : on travaille dans .correction/copies.
"""

from __future__ import annotations

import json
import os
import re
import signal
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import console
from .execution import WINDOWS, Resultat, executer, resoudre

IGNORES = {"build", ".dart_tool", ".git", ".gradle", "node_modules", "ios", ".idea", "macos", "windows", "linux", "web"}
ERREUR_RE = re.compile(r"(Error:|error:|FAILURE:|Exception:|No file or variants found|Target .* failed)")
APPEL_EMULATEUR_RE = re.compile(r"\.\s*use(\w*)Emulator\s*\(")
ENV_RE = re.compile(r"String\.fromEnvironment\(\s*['\"]ENV['\"]")
PORTS_DEFAUT = {"auth": 9099, "firestore": 8080, "storage": 9199, "functions": 5001, "database": 9000}


# ─── Projets ─────────────────────────────────────────────────────────────────

def est_flutter(pubspec: Path) -> bool:
    try:
        texte = pubspec.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "sdk: flutter" in texte or "\nflutter:" in texte


def projets(dossier: Path, android: bool = True) -> list[Path]:
    """Racines de projets Flutter (pubspec.yaml), avec android/ si ``android``."""
    trouves = []
    for courant, dossiers, fichiers in os.walk(dossier):
        dossiers[:] = sorted(d for d in dossiers if d not in IGNORES)
        if "pubspec.yaml" in fichiers and est_flutter(Path(courant) / "pubspec.yaml"):
            if not android or (Path(courant) / "android").is_dir():
                trouves.append(Path(courant))
    return trouves


def utilise_firestore(projet: Path) -> bool:
    if (projet / "firebase.json").is_file():
        return True
    try:
        return "cloud_firestore:" in (projet / "pubspec.yaml").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def apk(projet: Path, release: bool = False) -> Path:
    return projet / "build" / "app" / "outputs" / "flutter-apk" / ("app-release.apk" if release else "app-debug.apk")


def premiere_erreur(sortie: str) -> str:
    for ligne in sortie.splitlines():
        if ERREUR_RE.search(ligne):
            return ligne.strip()[:160]
    lignes = [l.strip() for l in sortie.splitlines() if l.strip()]
    return lignes[-1][:160] if lignes else ""


@dataclass
class Build:
    ok: bool
    etape: str                      # « pub get », « build », « clean »…
    duree: float = 0.0
    erreur: str = ""
    apk: str | None = None
    journal: str = ""               # sortie complète des commandes (fichier)

    def en_dict(self) -> dict:
        return dict(self.__dict__)


def construire_apk(projet: Path, *, release: bool = False, dart_defines: list[str] = (), nettoyer: bool = False,
                   journal: Path | None = None, delai_build: int = 1200) -> Build:
    """``flutter [clean] pub get build apk`` ; un build déjà fait dans la copie (témoin) est réutilisé.

    Le témoin ``.correction-build`` mémorise les options : mêmes options + APK présent → rien à faire.
    """
    temoin = projet / ".correction-build"
    signature = json.dumps({"release": release, "defines": list(dart_defines)})
    fichier = apk(projet, release)
    if not nettoyer and fichier.is_file() and temoin.is_file() and temoin.read_text(encoding="utf-8") == signature:
        return Build(True, "build déjà fait", apk=str(fichier))
    etapes = ([("clean", ["flutter", "clean"], 180)] if nettoyer else []) + [
        ("pub get", ["flutter", "pub", "get"], 300),
        ("build", ["flutter", "build", "apk", "--release" if release else "--debug", *dart_defines], delai_build),
    ]
    debut = time.perf_counter()
    sorties = []
    for nom, commande, delai in etapes:
        r = executer(commande, cwd=projet, delai=delai, env_extra={"CI": "true"})
        sorties.append(f"$ {' '.join(commande)}\n[{r.statut}]\n{r.sortie}")
        if journal:
            journal.parent.mkdir(parents=True, exist_ok=True)
            journal.write_text("\n\n".join(sorties), encoding="utf-8")
        if not r.reussi:
            erreur = r.statut if (r.delai_depasse or r.introuvable) else premiere_erreur(r.sortie)
            return Build(False, nom, round(time.perf_counter() - debut), erreur, journal=str(journal or ""))
    temoin.write_text(signature, encoding="utf-8")
    return Build(fichier.is_file(), "build", round(time.perf_counter() - debut),
                 "" if fichier.is_file() else "APK introuvable après le build", str(fichier) if fichier.is_file() else None,
                 str(journal or ""))


# ─── Code Dart et configuration de dev ───────────────────────────────────────

def sans_commentaires(texte: str) -> str:
    """Retire // et /* */ en respectant les chaînes (Dart, JSONC)."""
    sortie, i, n, guillemet = [], 0, len(texte), None
    while i < n:
        c = texte[i]
        if guillemet:
            sortie.append(c)
            if c == "\\" and i + 1 < n:
                sortie.append(texte[i + 1])
                i += 2
                continue
            if c == guillemet:
                guillemet = None
            i += 1
        elif c in "'\"":
            guillemet = c
            sortie.append(c)
            i += 1
        elif texte.startswith("//", i):
            j = texte.find("\n", i)
            i = n if j == -1 else j
        elif texte.startswith("/*", i):
            j = texte.find("*/", i + 2)
            i = n if j == -1 else j + 2
        else:
            sortie.append(c)
            i += 1
    return "".join(sortie)


def sources_dart(projet: Path):
    for f in sorted((projet / "lib").rglob("*.dart")) if (projet / "lib").is_dir() else []:
        try:
            yield f, sans_commentaires(f.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue


def emulateurs_utilises(projet: Path) -> list[str]:
    """['auth', 'firestore', …] si le code appelle useXXXEmulator (hors commentaires)."""
    return sorted({m.group(1).lower() or "?" for _, code in sources_dart(projet)
                   for m in APPEL_EMULATEUR_RE.finditer(code)})


def lire_jsonc(texte: str):
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", sans_commentaires(texte)))


def dart_defines_dev(depot: Path, projet: Path) -> tuple[list[str], str]:
    """``--dart-define`` de la configuration « DEV » de .vscode/launch.json (projet puis parents)."""
    d = projet
    while True:
        lj = d / ".vscode" / "launch.json"
        if lj.is_file():
            try:
                configs = lire_jsonc(lj.read_text(encoding="utf-8", errors="replace")).get("configurations", [])
            except (ValueError, AttributeError):
                configs = []
            dev = [c for c in configs if isinstance(c, dict) and re.search(r"\bdev\b", str(c.get("name", "")), re.I)]
            dev.sort(key=lambda c: (str(c.get("name", "")).strip().upper() != "DEV", len(str(c.get("name", "")))))
            for c in dev:
                args = [str(a) for a in (c.get("args") or c.get("toolArgs") or [])]
                defines = [a for a in args if a.startswith("--dart-define=")]
                defines += [f"--dart-define={args[k + 1]}" for k, a in enumerate(args)
                            if a == "--dart-define" and k + 1 < len(args)]
                if defines:
                    return defines, f"{lj.relative_to(depot).as_posix()} « {c.get('name')} »"
        if d == depot or d.parent == d:
            break
        d = d.parent
    for _, code in sources_dart(projet):
        if ENV_RE.search(code) and "android_dev" in code:
            return ["--dart-define=ENV=android_dev"], "ENV lu dans le code"
    return [], "aucun (kDebugMode / valeur par défaut)"


def firebase_json(depot: Path, projet: Path) -> Path | None:
    if (projet / "firebase.json").is_file():
        return projet / "firebase.json"
    candidats = [c for c in depot.rglob("firebase.json") if not IGNORES & set(c.relative_to(depot).parts)]
    return min(candidats, key=lambda c: len(c.parts)) if candidats else None


def ports_emulateurs(fb: Path | None, utilises: list[str]) -> dict[str, int]:
    ports: dict[str, int] = {}
    if fb:
        try:
            for nom, conf in json.loads(fb.read_text(encoding="utf-8", errors="replace")).get("emulators", {}).items():
                if isinstance(conf, dict) and "port" in conf and nom not in ("ui", "hub", "logging", "eventarc"):
                    ports[nom] = int(conf["port"])
        except (ValueError, OSError):
            pass
    for nom in utilises:
        if nom not in ports and nom in PORTS_DEFAUT:
            ports[nom] = PORTS_DEFAUT[nom]
    return ports


@dataclass
class ConfigDev:
    """Ce qu'il faut pour lancer un projet « en dev » avec les émulateurs Firebase."""
    emulateurs: list[str] = field(default_factory=list)
    dart_defines: list[str] = field(default_factory=list)
    provenance: str = ""
    firebase_json: Path | None = None

    @classmethod
    def detecter(cls, depot: Path, projet: Path) -> "ConfigDev":
        utilises = emulateurs_utilises(projet)
        if not utilises:
            return cls()
        defines, provenance = dart_defines_dev(depot, projet)
        return cls(utilises, defines, provenance, firebase_json(depot, projet))


# ─── Émulateurs Firebase (Windows et macOS) ──────────────────────────────────

def port_ouvert(port: int, hote: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((hote, port), timeout=0.5):
            return True
    except OSError:
        return False


class EmulateursFirebase:
    """``firebase emulators:start`` en arrière-plan, arrêt propre de tout le groupe de processus.

    Sous Windows une console séparée est ouverte (on y voit les journaux) ;
    sous macOS/Linux la sortie va dans ``journal``.
    """

    def __init__(self, journal: Path | None = None):
        self.process: subprocess.Popen | None = None
        self.journal = journal

    def demarrer(self, dossier: Path, ports: dict[str, int], attendre: list[str] | None = None,
                 seulement: bool = False, projet: str | None = None, delai: int = 120) -> bool:
        self.arreter()
        firebase = resoudre("firebase")
        if firebase is None:
            console.erreur("CLI firebase introuvable (npm i -g firebase-tools).")
            return False
        occupes = [p for p in ports.values() if port_ouvert(p)]
        if occupes:
            console.attention(f"Port(s) déjà occupé(s) : {occupes} — une autre instance tourne peut-être.")
        commande = [firebase, "emulators:start"]
        if seulement:
            commande += ["--only", ",".join(ports)]
        if projet:
            commande += ["--project", projet]
        console.info(f"  Émulateurs : {' '.join(commande[1:])} (dans {dossier})")
        if WINDOWS:
            self.process = subprocess.Popen(commande, cwd=dossier,
                                            creationflags=subprocess.CREATE_NEW_CONSOLE)  # type: ignore[attr-defined]
        else:
            sortie = open(self.journal, "w", encoding="utf-8") if self.journal else subprocess.DEVNULL
            self.process = subprocess.Popen(commande, cwd=dossier, stdout=sortie, stderr=subprocess.STDOUT,
                                            stdin=subprocess.DEVNULL, start_new_session=True)
        attente = {k: p for k, p in ports.items() if k in (attendre or ports)}
        fin = time.monotonic() + delai
        while attente and time.monotonic() < fin:
            for k, p in list(attente.items()):
                if port_ouvert(p):
                    del attente[k]
            if self.process.poll() is not None:
                break
            time.sleep(0.5)
        if attente:
            console.attention(f"Émulateurs non prêts après {delai} s : {attente}"
                              + (f" (journal : {self.journal})" if self.journal else ""))
            return False
        console.ok(f"Émulateurs prêts : {ports} (UI : http://127.0.0.1:4000)")
        return True

    def demarrer_pour(self, config: ConfigDev) -> dict[str, int]:
        """Démarre les émulateurs d'un projet (firebase.json + .firebaserc) ; retourne les ports."""
        if not config.emulateurs:
            return {}
        if config.firebase_json is None:
            console.attention("useXXXEmulator dans le code mais aucun firebase.json — émulateurs non démarrés.")
            return {}
        fb = config.firebase_json
        ports = ports_emulateurs(fb, config.emulateurs)
        try:
            declares = bool(json.loads(fb.read_text(encoding="utf-8", errors="replace")).get("emulators"))
        except ValueError:
            declares = False
        projet = None
        if not (fb.parent / ".firebaserc").exists():
            m = re.search(r'"projectId"\s*:\s*"([^"]+)"', fb.read_text(encoding="utf-8", errors="replace"))
            projet = m.group(1) if m else "demo-correction"
        self.demarrer(fb.parent, ports, attendre=config.emulateurs, seulement=not declares, projet=projet)
        return ports

    def arreter(self) -> None:
        p, self.process = self.process, None
        if p is None or p.poll() is not None:
            return
        console.info("  Arrêt des émulateurs Firebase…")
        if WINDOWS:
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
            return
        for sig, attente in ((signal.SIGINT, 20), (signal.SIGTERM, 5), (signal.SIGKILL, 5)):
            try:
                os.killpg(p.pid, sig)
                p.wait(timeout=attente)
                return
            except subprocess.TimeoutExpired:
                continue
            except OSError:
                return

    def __enter__(self) -> "EmulateursFirebase":
        return self

    def __exit__(self, *exc) -> None:
        self.arreter()


def executer_flutter_run(projet: Path, dart_defines: list[str] = (), appareil: str | None = None) -> int:
    """``flutter run`` interactif (hot reload) : hérite du terminal, attend la fin."""
    commande = [resoudre("flutter") or "flutter", "run", *dart_defines] + (["-d", appareil] if appareil else [])
    console.info(f"  $ {' '.join(commande)}")
    try:
        return subprocess.run(commande, cwd=projet).returncode
    except (OSError, KeyboardInterrupt):
        return 1


# ─── Déploiement interactif (builds en arrière-plan) ──────────────────────────

def deployer_interactif(copies: list, construire, *, sortie: Path, depuis: str | None = None,
                        titre=None, actions: dict | None = None) -> None:
    """Parcourt les copies dans l'ordre ; les builds tournent en fond (ressource ``flutter`` = 1).

    ``construire(copie)`` retourne ``(projet, ConfigDev, Build)``. Pour chaque copie :
    émulateurs Firebase si besoin (+ ``adb reverse``), désinstallation, installation, lancement
    sur tous les appareils. Les émulateurs sont arrêtés en changeant de copie et en quittant.
    """
    from . import android, ide
    from .ordonnanceur import Pipeline

    android.preparer_environnement()
    appareils = android.appareils()
    if not appareils:
        raise SystemExit("Aucun appareil adb connecté : démarrez un émulateur Android.")
    console.info(f"Appareils : {', '.join(appareils)}")
    depart = 0
    if depuis:
        depart = next((i for i, c in enumerate(copies) if depuis in (c.id, c.source.name)), None)
        if depart is None:
            raise SystemExit(f"{depuis} absent.")
    emulateurs = EmulateursFirebase(journal=sortie / "emulateurs.log")

    with emulateurs, Pipeline(copies, construire, ressource="flutter", limites={"flutter": 1}) as pipeline:
        def resultat(copie):
            i = copies.index(copie)
            if not pipeline.pret(i):
                console.info("  Attente du build de cette copie…")
            return pipeline.resultat(i)

        def deployer(copie):
            emulateurs.arreter()
            projet, config, build = resultat(copie)
            if not build.ok:
                console.erreur(f"  Build en échec ({build.etape}) : {build.erreur}  [{build.journal}]")
                return
            package, _ = android.package_et_activite(projet / "android")
            if not package:
                console.erreur("  Package Android introuvable.")
                return
            ports = emulateurs.demarrer_pour(config)
            if ports:
                android.rediriger_ports(appareils, list(ports.values()))
            for a in appareils:
                android.desinstaller(a, package)
                r = android.installer(a, Path(build.apk))
                if not r.reussi:
                    console.erreur(f"  Installation échouée sur {a} : {r.sortie.strip()[-300:]}")
                    continue
                r = android.lancer(a, package)
                if r.reussi and "Error" not in r.sortie:
                    console.ok(f"  {a} : {package} lancé")
                else:
                    console.erreur(f"  {a} : {package} non lancé — {r.sortie.strip()[-200:]}")

        def ouvrir_code(copie):
            ide.ouvrir("code", resultat(copie)[0] or copie.dossier)

        def titre_defaut(i, c):
            etat = "prêt" if pipeline.pret(i) else "build en cours"
            return f"{c.etudiant.nom_affiche} — {c.source.name}  [{etat}]\n  {c.dossier}"

        ide.parcourir(copies, titre or titre_defaut, deployer,
                      actions={"c": ("VS Code", ouvrir_code), "e": ("arrêter émulateurs", lambda c: emulateurs.arreter()),
                               **(actions or {})},
                      depart=depart)


__all__ = ["deployer_interactif", "Build", "ConfigDev", "EmulateursFirebase", "Resultat", "apk", "construire_apk", "dart_defines_dev",
           "emulateurs_utilises", "executer_flutter_run", "projets", "port_ouvert", "ports_emulateurs",
           "premiere_erreur", "utilise_firestore"]
