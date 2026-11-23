"""Exécution bornée de commandes, identique sur macOS, Linux et Windows.

- jamais de ``shell=True`` : la commande est une liste d'arguments ;
- le processus est lancé dans son propre groupe et **tout le groupe** est tué au délai
  (``killpg`` sur posix, ``taskkill /T`` sur Windows) : un ``flutter`` ou un ``gradlew``
  ne laisse pas d'enfants orphelins ;
- la sortie est capturée dans des fichiers temporaires puis tronquée, ce qui évite
  de bloquer sur un tuyau plein quand un programme étudiant imprime en boucle.

Attention : ce n'est pas un bac à sable de sécurité. Le code étudiant tourne sous
votre compte.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

WINDOWS = os.name == "nt"
SORTIE_MAX = 200_000


@dataclass
class Resultat:
    """Résultat d'une commande."""

    commande: list[str]
    code: int | None
    stdout: str
    stderr: str
    duree: float
    delai_depasse: bool = False
    introuvable: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def reussi(self) -> bool:
        return self.code == 0 and not self.delai_depasse

    @property
    def statut(self) -> str:
        """Statut lisible : « code de retour 0 », « arrêt après 15 s (délai dépassé) »…"""
        if self.introuvable:
            return f"commande introuvable : {self.commande[0]}"
        if self.delai_depasse:
            return f"arrêt après {self.extra.get('delai', '?')} s (délai dépassé)"
        return f"code de retour {self.code}"

    @property
    def sortie(self) -> str:
        """stdout puis stderr."""
        return (self.stdout + ("\n" + self.stderr if self.stderr.strip() else "")).strip()


def _lire_borne(flux, maximum: int) -> str:
    flux.seek(0)
    donnees = flux.read(maximum * 4 + 4).decode("utf-8", errors="replace")
    if len(donnees) > maximum:
        return donnees[:maximum] + "\n[sortie tronquée]"
    return donnees


def _tuer_groupe(process: subprocess.Popen) -> None:
    if WINDOWS:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def resoudre(programme: str) -> str | None:
    """Chemin complet d'un exécutable ; sous Windows trouve aussi flutter.bat, gradlew.bat, npm.cmd…"""
    if os.path.sep in programme or (os.altsep and os.altsep in programme):
        return programme if Path(programme).exists() else None
    return shutil.which(programme)


def executer(
    commande: Sequence[str | os.PathLike],
    cwd: str | os.PathLike | None = None,
    delai: float | None = 60,
    env: Mapping[str, str] | None = None,
    env_extra: Mapping[str, str] | None = None,
    entree: str | None = None,
    sortie_max: int = SORTIE_MAX,
) -> Resultat:
    """Exécute une commande avec délai, sortie bornée et kill du groupe de processus.

    ``env`` remplace complètement l'environnement ; ``env_extra`` le complète.
    """
    args = [str(a) for a in commande]
    chemin = resoudre(args[0])
    if chemin is None:
        return Resultat(args, None, "", "", 0.0, introuvable=True)
    args[0] = chemin
    environnement = dict(os.environ if env is None else env)
    environnement.update(env_extra or {})
    options: dict = {}
    if WINDOWS:
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    else:
        options["start_new_session"] = True
    debut = time.perf_counter()
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        process = subprocess.Popen(
            args, cwd=cwd, env=environnement, stdout=out, stderr=err,
            stdin=subprocess.PIPE if entree is not None else subprocess.DEVNULL, **options,
        )
        depasse = False
        try:
            if entree is not None:
                process.communicate(entree.encode("utf-8"), timeout=delai)
            else:
                process.wait(timeout=delai)
        except subprocess.TimeoutExpired:
            depasse = True
            _tuer_groupe(process)
            process.wait()
        except BaseException:
            _tuer_groupe(process)
            process.wait()
            raise
        duree = time.perf_counter() - debut
        return Resultat(
            args, process.returncode, _lire_borne(out, sortie_max), _lire_borne(err, sortie_max),
            round(duree, 2), delai_depasse=depasse, extra={"delai": delai},
        )


ENV_PYTHON = {
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUNBUFFERED": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUTF8": "1",
    "MPLBACKEND": "Agg",
}


def env_minimal(dossier: Path, **extra: str) -> dict[str, str]:
    """Environnement réduit pour du code étudiant : PATH, HOME = dossier de la copie, Python en UTF-8."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(dossier), **ENV_PYTHON}
    if WINDOWS:
        for cle in ("SYSTEMROOT", "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PATHEXT", "COMSPEC"):
            if cle in os.environ:
                env[cle] = os.environ[cle]
        env["USERPROFILE"] = str(dossier)
    env.update(extra)
    return env


def executer_python(
    script: str | os.PathLike,
    *args: str | os.PathLike,
    cwd: str | os.PathLike,
    python: str | None = None,
    delai: float = 15,
    env_extra: Mapping[str, str] | None = None,
    isole: bool = False,
    entree: str | None = None,
    avant: Sequence[str | os.PathLike] = (),
    sortie_max: int = SORTIE_MAX,
) -> Resultat:
    """Lance ``python -u [avant…] script args…`` dans ``cwd``.

    ``isole=True`` utilise un environnement minimal (HOME pointé sur ``cwd``) ;
    sinon l'environnement courant complété de variables UTF-8 et MPLBACKEND=Agg.
    """
    commande = [python or sys.executable, "-u", *avant, script, *args]
    if isole:
        return executer(commande, cwd=cwd, delai=delai, env=env_minimal(Path(cwd), **(env_extra or {})),
                        entree=entree, sortie_max=sortie_max)
    return executer(commande, cwd=cwd, delai=delai, env_extra={**ENV_PYTHON, **(env_extra or {})},
                    entree=entree, sortie_max=sortie_max)
