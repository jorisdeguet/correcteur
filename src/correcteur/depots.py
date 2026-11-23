"""Obtention des copies : dépôts git (GitHub Classroom ou autre) et archives zip/7z.

Une copie de travail est toujours **extraite à part** (``.correction/copies/<id>``) :
le clone de l'étudiant n'est jamais modifié, même quand un harnais, un CSV de données
ou un build y est ajouté.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Iterable

from . import console

DOSSIERS_JETABLES = {".venv", "venv", "__pycache__", "node_modules", "build", ".dart_tool", ".gradle",
                     ".idea", ".git", "__MACOSX"}


# ─── Dépôts git ──────────────────────────────────────────────────────────────

def lister_depots(racine: Path) -> list[Path]:
    """Sous-dossiers directs de ``racine`` qui sont des dépôts git (hors dossiers cachés)."""
    return sorted(p for p in racine.iterdir()
                  if p.is_dir() and not p.name.startswith((".", "_")) and (p / ".git").exists())


def lister_dossiers(racine: Path) -> list[Path]:
    """Sous-dossiers directs de ``racine`` (hors cachés et hors sorties ``_x``/``corrections``)."""
    return sorted(p for p in racine.iterdir()
                  if p.is_dir() and not p.name.startswith((".", "_")) and not p.name.startswith("correction"))


def exporter_commit(depot: Path, sha: str, destination: Path) -> Path:
    """Extrait le contenu du commit ``sha`` dans ``destination`` (``git archive``, sans toucher au clone).

    Si ``destination`` contient déjà ce commit (fichier témoin), rien n'est refait : les
    artefacts de build qui y ont été produits sont conservés.
    """
    temoin = destination / ".correction-commit"
    if temoin.is_file() and temoin.read_text().strip() == sha:
        return destination
    if destination.exists():
        supprimer(destination)
    destination.mkdir(parents=True)
    proc = subprocess.run(["git", "-C", str(depot), "archive", "--format=tar", sha],
                          capture_output=True, timeout=300)
    if proc.returncode:
        raise RuntimeError(f"git archive {sha} : {proc.stderr.decode(errors='replace').strip()}")
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as archive:
        archive.extractall(destination, filter="data")
    temoin.write_text(sha + "\n")
    return destination


def copier_dossier(source: Path, destination: Path, ignorer: Iterable[str] = DOSSIERS_JETABLES) -> Path:
    """Copie un dossier (sans .git, .venv, build…) ; remplace la destination."""
    if destination.exists():
        supprimer(destination)
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(*ignorer), symlinks=True)
    return destination


def supprimer(chemin: Path) -> None:
    """rmtree robuste (fichiers en lecture seule de .git ou de Gradle sous Windows)."""
    def _forcer(fonction, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        fonction(p)

    if chemin.is_symlink() or chemin.is_file():
        chemin.unlink()
    elif chemin.exists():
        if sys.version_info >= (3, 12):
            shutil.rmtree(chemin, onexc=_forcer)
        else:
            shutil.rmtree(chemin, onerror=_forcer)


def empreinte_dossier(dossier: Path, ignorer: Iterable[str] = DOSSIERS_JETABLES) -> str:
    """SHA-256 du contenu d'un dossier (chemins + octets), pour les copies sans git."""
    ignores = set(ignorer)
    h = hashlib.sha256()
    for racine, dossiers, fichiers in os.walk(dossier):
        dossiers[:] = sorted(d for d in dossiers if d not in ignores)
        for nom in sorted(fichiers):
            chemin = Path(racine) / nom
            h.update(chemin.relative_to(dossier).as_posix().encode())
            try:
                h.update(chemin.read_bytes())
            except OSError:
                pass
    return h.hexdigest()


# ─── Archives ────────────────────────────────────────────────────────────────

def empreinte_fichier(chemin: Path) -> str:
    h = hashlib.sha256()
    with chemin.open("rb") as f:
        for bloc in iter(lambda: f.read(1 << 20), b""):
            h.update(bloc)
    return h.hexdigest()


def chemin_sur(destination: Path, membre: str) -> Path:
    """Chemin d'extraction d'un membre d'archive ; refuse chemins absolus, ``..`` et sorties du dossier."""
    normalise = membre.replace("\\", "/")
    posix = PurePosixPath(normalise)
    windows = PureWindowsPath(membre)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        raise ValueError(f"Chemin absolu interdit dans l'archive : {membre}")
    if ".." in posix.parts:
        raise ValueError(f"Chemin parent interdit dans l'archive : {membre}")
    cible = destination.joinpath(*posix.parts)
    base = destination.resolve()
    if cible.resolve() != base and base not in cible.resolve().parents:
        raise ValueError(f"Chemin hors du dossier d'extraction : {membre}")
    return cible


def _ignore(membre: str, ignorer: set[str]) -> bool:
    return any(p.casefold() in ignorer for p in PurePosixPath(membre.replace("\\", "/")).parts)


def membres_archive(archive: Path) -> list[str]:
    if archive.suffix.lower() == ".7z":
        import py7zr
        with py7zr.SevenZipFile(archive) as z:
            return z.getnames()
    with zipfile.ZipFile(archive) as z:
        return z.namelist()


def extraire_archive(archive: Path, destination: Path, ignorer: Iterable[str] = (".venv", "__macosx")) -> Path:
    """Extrait une archive .zip ou .7z de façon sûre dans ``destination`` (remplacée atomiquement).

    Les dossiers ``ignorer`` (par défaut .venv et __MACOSX) sont omis ; les liens symboliques
    sont refusés. Un fichier témoin évite de ré-extraire une archive inchangée.
    """
    ignores = {i.casefold() for i in ignorer}
    empreinte = empreinte_fichier(archive)
    temoin = destination / ".correction-archive"
    if temoin.is_file() and temoin.read_text().strip() == empreinte:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".extraction-") as tmp:
        cible = Path(tmp) / "x"
        cible.mkdir()
        if archive.suffix.lower() == ".7z":
            import py7zr
            with py7zr.SevenZipFile(archive) as z:
                noms = [n for n in z.getnames() if not _ignore(n, ignores)]
                for n in noms:
                    chemin_sur(cible, n)
                for info in z.list():
                    if getattr(info, "is_symlink", False) and not _ignore(info.filename, ignores):
                        raise ValueError(f"Lien symbolique interdit dans l'archive : {info.filename}")
                if noms:
                    z.extract(path=cible, targets=noms)
        else:
            with zipfile.ZipFile(archive) as z:
                for info in z.infolist():
                    if _ignore(info.filename, ignores):
                        continue
                    chemin = chemin_sur(cible, info.filename)
                    if info.is_dir():
                        chemin.mkdir(parents=True, exist_ok=True)
                        continue
                    if (info.external_attr >> 16) & 0o170000 == 0o120000:
                        raise ValueError(f"Lien symbolique interdit dans l'archive : {info.filename}")
                    chemin.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(info) as src, chemin.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
        (cible / ".correction-archive").write_text(empreinte + "\n")
        if destination.exists():
            supprimer(destination)
        os.replace(cible, destination)
    return destination


def selectionner_archives(archives: Iterable[Path], identifiant) -> tuple[dict[str, Path], list[str]]:
    """Garde l'archive la plus récente (date de modification) par identifiant.

    ``identifiant(archive) -> str | None``. Retourne ({id: archive}, [messages d'archives écartées]).
    """
    retenues: dict[str, Path] = {}
    ecartees = []
    for archive in sorted(archives, key=lambda p: p.stat().st_mtime, reverse=True):
        ident = identifiant(archive)
        if ident is None:
            ecartees.append(f"{archive.name} (aucun identifiant dans le nom)")
        elif ident in retenues:
            ecartees.append(f"{archive.name} (doublon, gardée : {retenues[ident].name})")
        else:
            retenues[ident] = archive
    return retenues, ecartees


def lister_archives(dossier: Path) -> list[Path]:
    return sorted(p for p in dossier.iterdir() if p.is_file() and p.suffix.lower() in (".zip", ".7z"))


# ─── GitHub (gh) ─────────────────────────────────────────────────────────────

def depots_org(org: str, prefixe: str = "", limite: int = 2000) -> list[str]:
    """Noms des dépôts d'une organisation GitHub commençant par ``prefixe`` (via ``gh``)."""
    proc = subprocess.run(["gh", "repo", "list", org, "--limit", str(limite), "--json", "name"],
                          capture_output=True, text=True, encoding="utf-8", timeout=120)
    if proc.returncode:
        raise RuntimeError(f"gh repo list {org} : {proc.stderr.strip()}")
    noms = [d["name"] for d in json.loads(proc.stdout)]
    return sorted(n for n in noms if n.startswith(prefixe))


def synchroniser(org: str, prefixe: str, racine: Path) -> tuple[list[str], list[str], list[str]]:
    """Clone les nouveaux dépôts ``org/prefixe*`` dans ``racine`` et fait ``git fetch`` des autres.

    Le dossier de travail des clones existants n'est pas modifié (fetch seulement) :
    la correction utilise ``origin/HEAD`` à l'échéance.
    Retourne (clonés, mis à jour, en erreur).
    """
    racine.mkdir(parents=True, exist_ok=True)
    clones, maj, erreurs = [], [], []
    for nom in depots_org(org, prefixe):
        cible = racine / nom
        if (cible / ".git").exists():
            proc = subprocess.run(["git", "-C", str(cible), "fetch", "--prune", "--quiet", "origin"],
                                  capture_output=True, text=True, timeout=300)
            subprocess.run(["git", "-C", str(cible), "remote", "set-head", "origin", "--auto"],
                           capture_output=True, timeout=60)
            (maj if proc.returncode == 0 else erreurs).append(nom)
        else:
            proc = subprocess.run(["gh", "repo", "clone", f"{org}/{nom}", str(cible), "--", "--quiet"],
                                  capture_output=True, text=True, timeout=600)
            (clones if proc.returncode == 0 else erreurs).append(nom)
        if proc.returncode:
            console.attention(f"{nom} : {(proc.stderr or '').strip()[:200]}")
    return clones, maj, erreurs
