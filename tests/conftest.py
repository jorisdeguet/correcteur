"""Fabriques de données factices : dépôts git datés, listes de classe, archives."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


def git(depot: Path, *args: str, date: str | None = None) -> str:
    env = dict(os.environ, GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="t@example.com")
    if date:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    return subprocess.run(["git", "-C", str(depot), *args], env=env, check=True,
                          capture_output=True, text=True).stdout.strip()


def faire_depot(chemin: Path, commits: list[tuple[str, dict[str, str]]]) -> Path:
    """commits = [(date ISO, {fichier: contenu}), …] dans l'ordre chronologique."""
    chemin.mkdir(parents=True)
    git(chemin, "init", "-q", "-b", "main")
    for i, (date, fichiers) in enumerate(commits):
        for nom, contenu in fichiers.items():
            (chemin / nom).parent.mkdir(parents=True, exist_ok=True)
            (chemin / nom).write_text(contenu, encoding="utf-8")
        git(chemin, "add", "-A")
        git(chemin, "commit", "-q", "-m", f"commit {i}", date=date)
    return chemin


@pytest.fixture
def depot(tmp_path):
    return lambda nom, commits: faire_depot(tmp_path / nom, commits)


@pytest.fixture
def classe_csv(tmp_path):
    chemin = tmp_path / "classe.csv"
    chemin.write_text(
        "Matricule;Nom;Groupe;Github\n"
        "1234567;\"Tremblay, Alice\";1050;alice\n"
        "7654321;\"Gagnon, Béatrice\";1050;bea\n"
        "1111111;\"Roy, Charles\";1060;charles\n",
        encoding="utf-8-sig")
    return chemin
