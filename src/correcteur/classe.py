"""Liste de classe : lecture CSV/Excel tolérante et identification des copies.

Format attendu (souplesse sur le séparateur et l'encodage) ::

    Matricule,Nom,Groupe,Github
    2466665,"Nazek, Malak",1050,malaknazek

« Nom » peut être « Nom, Prénom » ; les colonnes Prénom/Prenom sont aussi acceptées.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

MATRICULE_RE = re.compile(r"(?<!\d)(\d{7})(?!\d)")

_ALIAS = {
    "matricule": ("matricule", "da", "id", "numero", "numéro", "student id"),
    "nom": ("nom", "name", "nom complet", "etudiant", "étudiant"),
    "prenom": ("prenom", "prénom", "first name", "firstname"),
    "groupe": ("groupe", "group", "gr"),
    "github": ("github", "utilisateur github", "login"),
}


@dataclass(frozen=True)
class Etudiant:
    id: str
    nom: str
    prenom: str = ""
    groupe: str = ""
    github: str = ""
    extra: dict = field(default_factory=dict, compare=False, hash=False)

    @property
    def nom_complet(self) -> str:
        """« Prénom Nom » si le prénom est connu, sinon le nom tel quel."""
        return f"{self.prenom} {self.nom}".strip() if self.prenom else self.nom

    @property
    def nom_affiche(self) -> str:
        """« Nom, Prénom » (ordre alphabétique des listes)."""
        return f"{self.nom}, {self.prenom}" if self.prenom else self.nom

    @property
    def cle_tri(self) -> tuple:
        return (self.nom.casefold(), self.prenom.casefold(), self.id)


def matricule_dans(texte: str) -> str | None:
    """Premier nombre de exactement 7 chiffres dans un nom de dossier ou d'archive."""
    m = MATRICULE_RE.search(texte)
    return m.group(1) if m else None


def _colonne(entetes: list[str], cle: str) -> str | None:
    normalises = {e.strip().casefold(): e for e in entetes if e}
    for alias in _ALIAS[cle]:
        if alias in normalises:
            return normalises[alias]
    return None


def _lignes_csv(chemin: Path) -> list[dict[str, str]]:
    brut = chemin.read_bytes()
    for encodage in ("utf-8-sig", "cp1252"):
        try:
            texte = brut.decode(encodage)
            break
        except UnicodeDecodeError:
            continue
    premiere = texte.splitlines()[0] if texte else ""
    sep = ";" if premiere.count(";") > premiere.count(",") else ","
    return list(csv.DictReader(io.StringIO(texte), delimiter=sep))


def _lignes_excel(chemin: Path) -> list[dict[str, str]]:
    try:
        import openpyxl  # optionnel
    except ImportError as e:
        raise RuntimeError("Lecture Excel : installez openpyxl, ou exportez la liste en CSV.") from e
    feuille = openpyxl.load_workbook(chemin, read_only=True, data_only=True).active
    lignes = [["" if v is None else str(v) for v in ligne] for ligne in feuille.iter_rows(values_only=True)]
    if not lignes:
        return []
    entetes = lignes[0]
    return [dict(zip(entetes, ligne)) for ligne in lignes[1:]]


def lire_classe(chemin: str | Path) -> dict[str, Etudiant]:
    """Lit la liste de classe ; retourne {matricule: Etudiant}. Ignore les lignes sans matricule valide."""
    chemin = Path(chemin).expanduser()
    lignes = _lignes_excel(chemin) if chemin.suffix.lower() in (".xlsx", ".xlsm") else _lignes_csv(chemin)
    if not lignes:
        return {}
    entetes = list(lignes[0].keys())
    col = {cle: _colonne(entetes, cle) for cle in _ALIAS}
    if not col["matricule"] or not col["nom"]:
        raise ValueError(f"{chemin} doit contenir au moins les colonnes Matricule et Nom (trouvées : {entetes}).")
    classe: dict[str, Etudiant] = {}
    for ligne in lignes:
        matricule = (ligne.get(col["matricule"]) or "").strip()
        if not (len(matricule) == 7 and matricule.isdigit()):
            continue
        nom = (ligne.get(col["nom"]) or "").strip()
        prenom = (ligne.get(col["prenom"]) or "").strip() if col["prenom"] else ""
        if not prenom and "," in nom:
            nom, _, prenom = (p.strip() for p in nom.partition(","))
        classe[matricule] = Etudiant(
            id=matricule, nom=nom, prenom=prenom,
            groupe=(ligne.get(col["groupe"]) or "").strip() if col["groupe"] else "",
            github=(ligne.get(col["github"]) or "").strip() if col["github"] else "",
        )
    return classe


def etudiant_inconnu(identifiant: str, nom: str | None = None) -> Etudiant:
    """Étudiant hors liste (copie dont le matricule est absent ou sans liste de classe)."""
    return Etudiant(id=identifiant, nom=nom or identifiant)
