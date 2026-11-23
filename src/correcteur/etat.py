"""Dossier ``.correction/`` : configuration, exclusions et cache, persistés entre les sessions.

Il est créé dans le dossier qui contient les copies (par exemple ``~/Corrections/A26-SN1-TP1``)::

    .correction/
        config.toml     échéance, prolongations, exclusions, groupes, liste de classe…
        etat.sqlite     résultats déjà calculés : (recette, étudiant) → copie, version, fiche, note
        copies/<id>/    copies de travail extraites (commit à l'échéance, archive)
        journaux/       sorties des commandes longues (builds)

Une correction déjà faite est réutilisée tant que la copie (SHA du commit ou empreinte de
l'archive), la version de la recette et ses options n'ont pas changé.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomli_w

NOM_DOSSIER = ".correction"

MODELE_CONFIG = """\
# Configuration de correction pour ce dossier de copies.
# Les options de ligne de commande ont priorité sur ce fichier.

# Liste de classe (CSV : Matricule, Nom, Groupe). Chemin relatif à ce dossier de copies.
# classe = "../A26-SN1-Github.csv"

# Échéance (heure locale si pas de fuseau). Sans échéance : dernier commit.
# echeance = "2026-11-04T23:59"

# Référence git corrigée (défaut : origin/HEAD si présent, sinon HEAD).
# reference = "origin/main"

# groupes = ["1050", "1060"]

[prolongations]
# "2538179" = "+1j"

[exclusions]
# "6309202" = "abandon du cours"

[ressources]
# Nombre de tâches simultanées par ressource (cpu = nombre de cœurs par défaut).
# flutter = 1
# gradle = 1
"""


class Etat:
    """Accès au dossier ``.correction`` d'un dossier de copies."""

    def __init__(self, racine: Path):
        self.racine = Path(racine).resolve()
        self.dossier = self.racine / NOM_DOSSIER
        self.dossier.mkdir(parents=True, exist_ok=True)
        if not (self.dossier / ".gitignore").exists():
            (self.dossier / ".gitignore").write_text("*\n")
        self.chemin_config = self.dossier / "config.toml"
        if not self.chemin_config.exists():
            self.chemin_config.write_text(MODELE_CONFIG, encoding="utf-8")
        self._verrou = threading.Lock()
        self._db = sqlite3.connect(self.dossier / "etat.sqlite", check_same_thread=False, timeout=30)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS resultats (
                recette TEXT, etudiant TEXT, copie TEXT, version TEXT, options TEXT,
                statut TEXT, fiche TEXT, note TEXT, donnees TEXT, duree REAL, horodatage REAL,
                PRIMARY KEY (recette, etudiant));
            CREATE TABLE IF NOT EXISTS observations (
                depot TEXT, sha TEXT, premiere_vue REAL, PRIMARY KEY (depot, sha));
        """)
        self._db.commit()

    # ─── Configuration ───────────────────────────────────────────────────────

    def config(self) -> dict[str, Any]:
        with self.chemin_config.open("rb") as f:
            return tomllib.load(f)

    def _ecrire_config(self, config: dict[str, Any]) -> None:
        entete = ("# Configuration de correction (voir correcteur/etat.py pour toutes les clés).\n"
                  "# Modifiable à la main ; corr exclure / corr prolonger la réécrivent.\n\n")
        self.chemin_config.write_text(entete + tomli_w.dumps(config), encoding="utf-8")

    def modifier_config(self, **valeurs: Any) -> None:
        with self._verrou:
            config = self.config()
            for cle, valeur in valeurs.items():
                if valeur is None:
                    config.pop(cle, None)
                else:
                    config[cle] = valeur
            self._ecrire_config(config)

    def exclusions(self) -> dict[str, str]:
        return {str(k): str(v) for k, v in self.config().get("exclusions", {}).items()}

    def exclure(self, matricule: str, raison: str = "") -> None:
        with self._verrou:
            config = self.config()
            config.setdefault("exclusions", {})[matricule] = raison or "exclu"
            self._ecrire_config(config)

    def inclure(self, matricule: str) -> bool:
        with self._verrou:
            config = self.config()
            present = config.get("exclusions", {}).pop(matricule, None) is not None
            self._ecrire_config(config)
            return present

    def prolonger(self, matricule: str, valeur: str) -> None:
        with self._verrou:
            config = self.config()
            config.setdefault("prolongations", {})[matricule] = valeur
            self._ecrire_config(config)

    # ─── Cache des résultats ─────────────────────────────────────────────────

    @staticmethod
    def empreinte_options(options: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(options, sort_keys=True, default=str).encode()).hexdigest()[:16]

    def lire(self, recette: str, etudiant: str, copie: str, version: str, options: str) -> "Entree | None":
        with self._verrou:
            ligne = self._db.execute(
                "SELECT statut, fiche, note, donnees, duree, horodatage FROM resultats "
                "WHERE recette=? AND etudiant=? AND copie=? AND version=? AND options=?",
                (recette, etudiant, copie, version, options)).fetchone()
        if ligne is None:
            return None
        statut, fiche, note, donnees, duree, horodatage = ligne
        return Entree(statut, fiche, json.loads(note) if note else None,
                      json.loads(donnees) if donnees else None, duree, horodatage)

    def ecrire(self, recette: str, etudiant: str, copie: str, version: str, options: str, entree: "Entree") -> None:
        with self._verrou:
            self._db.execute(
                "INSERT OR REPLACE INTO resultats VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (recette, etudiant, copie, version, options, entree.statut, entree.fiche,
                 json.dumps(entree.note, ensure_ascii=False) if entree.note is not None else None,
                 json.dumps(entree.donnees, ensure_ascii=False, default=str) if entree.donnees is not None else None,
                 entree.duree, time.time()))
            self._db.commit()

    def oublier(self, recette: str, etudiants: list[str] | None = None) -> int:
        """Invalide le cache d'une recette (tous les étudiants si ``etudiants`` est None)."""
        with self._verrou:
            if etudiants is None:
                n = self._db.execute("DELETE FROM resultats WHERE recette=?", (recette,)).rowcount
            else:
                n = sum(self._db.execute("DELETE FROM resultats WHERE recette=? AND etudiant=?",
                                         (recette, e)).rowcount for e in etudiants)
            self._db.commit()
            return n

    def resume(self) -> list[tuple[str, str, str, float]]:
        """[(recette, étudiant, statut, horodatage)] de tout ce qui est en cache."""
        with self._verrou:
            return self._db.execute(
                "SELECT recette, etudiant, statut, horodatage FROM resultats ORDER BY recette, etudiant").fetchall()

    # ─── Observations de commits (date de première vue) ──────────────────────

    def observer(self, depot: str, shas: list[str]) -> None:
        maintenant = time.time()
        with self._verrou:
            self._db.executemany("INSERT OR IGNORE INTO observations VALUES (?,?,?)",
                                 [(depot, s, maintenant) for s in shas])
            self._db.commit()

    def premiere_vue(self, depot: str, sha: str) -> float | None:
        with self._verrou:
            ligne = self._db.execute("SELECT premiere_vue FROM observations WHERE depot=? AND sha=?",
                                     (depot, sha)).fetchone()
        return ligne[0] if ligne else None

    def fermer(self) -> None:
        self._db.close()


@dataclass
class Entree:
    statut: str
    fiche: str | None
    note: dict | None = None
    donnees: Any = None
    duree: float = 0.0
    horodatage: float = 0.0
