"""Cadre commun des scripts de correction (« recettes »).

Une recette ne contient que ce qui est propre au travail corrigé ; le cadre s'occupe de :

- la ligne de commande commune (dossier, groupes, échéance, --refaire, --seulement, --jobs…) ;
- le diagnostic des outils requis ;
- la liste de classe, la découverte des copies (dépôts git, archives, dossiers) ;
- l'échéance et les prolongations (commit retenu par étudiant), les exclusions ;
- l'extraction d'une copie de travail séparée (le clone n'est jamais modifié) ;
- le cache ``.correction/etat.sqlite`` : une copie inchangée n'est pas recorrigée ;
- le parallélisme par ressource ; les PDF par groupe, les notes CSV/PDF, ``ordre.csv``.

Exemple minimal ::

    from correcteur import Recette, Fiche, typst

    recette = Recette("SN1-TP1", version="2026.11.1", description="Correction du TP1")

    @recette.correction
    def corriger(copie, ctx):
        r = copie.python("racine.py", delai=ctx.args.timeout)
        return Fiche(typst.brut(r.sortie), note={"Total": 10})

    if __name__ == "__main__":
        recette.main()
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import time
import traceback
from concurrent.futures import as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from . import console, depots, diagnostic, echeance, git_info, notes, typst
from .classe import Etudiant, etudiant_inconnu, lire_classe, matricule_dans
from .etat import Entree, Etat
from .execution import Resultat, executer_python
from .ordonnanceur import Ordonnanceur

SANS_GROUPE = "tous"


@dataclass
class Fiche:
    """Résultat de la correction d'une copie."""

    typst: str
    note: dict | None = None
    donnees: Any = None
    conserver: bool = True  # False : ne pas mettre en cache (ex. build en échec, peut-être passager)


@dataclass
class Copie:
    """Une copie à corriger."""

    etudiant: Etudiant
    source: Path                 # clone git, archive ou dossier d'origine
    nature: str                  # "git", "archive" ou "dossier"
    dossier: Path | None = None  # copie de travail extraite (None si pas de commit avant l'échéance)
    cle: str = ""                # SHA du commit, empreinte de l'archive ou du dossier (clé de cache)
    sha: str | None = None       # commit corrigé (dépôts git)
    locale: bool = False         # copie du dossier de travail plutôt que du commit
    echeance: datetime | None = None
    reference: str | None = None
    commits_apres: list = field(default_factory=list)
    remarques: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.etudiant.id

    @property
    def groupe(self) -> str:
        return self.etudiant.groupe or SANS_GROUPE

    def historique(self, maximum: int | None = None) -> list[git_info.Commit] | None:
        """Commits jusqu'au commit corrigé (None si la copie n'est pas un dépôt git)."""
        if self.nature != "git":
            return None
        return git_info.historique(self.source, jusqua=None if self.locale else self.sha, maximum=maximum)

    def fichier(self, nom: str) -> Path | None:
        """Fichier dans la copie de travail (racine d'abord, sinon le moins profond)."""
        from .codepython import trouver
        return trouver(self.dossier, nom) if self.dossier else None

    def python(self, script: str | Path, *args: str, cwd: Path | None = None, **options) -> Resultat:
        """Exécute un script Python de la copie (voir :func:`correcteur.execution.executer_python`)."""
        return executer_python(script, *args, cwd=cwd or self.dossier, **options)


@dataclass
class Contexte:
    """Ce que la recette reçoit en plus de la copie."""

    recette: "Recette"
    args: argparse.Namespace
    etat: Etat
    sortie: Path
    ordonnanceur: Ordonnanceur | None = None
    outils: dict = field(default_factory=dict)
    partage: dict = field(default_factory=dict)  # rempli par la fonction de préparation


class Recette:
    def __init__(
        self,
        nom: str,
        version: str,
        description: str = "",
        *,
        outils: Iterable[str] = ("git",),
        source: str = "git",
        ressource: str = "cpu",
        delai: int = 15,
        taille: str = "8pt",
        marge: str = "8mm",
        saut: str = "odd",
        preambule: Iterable[str] = (),
        compact: bool = True,
        legende_notes: str = "",
        note_sur: float = 10,
        dossier_defaut: str | None = None,
        archives_par_groupe: str | None = None,
        prefixe_dossier: str = "",
        classe_defaut: str | Path | None = None,
    ):
        """
        source : "git" (un dépôt par sous-dossier), "archives" (.zip/.7z) ou "dossiers" (sans git).
        archives_par_groupe : motif de sous-dossier par groupe, ex. "intra-{groupe}".
        prefixe_dossier : préfixe retiré du nom de dossier pour nommer un étudiant hors liste.
        """
        self.nom = nom
        self.version = version
        self.description = description or nom
        self.outils = list(dict.fromkeys(["typst", *outils]))
        self.source = source
        self.ressource = ressource
        self.delai = delai
        self.taille = taille
        self.marge = marge
        self.saut = saut
        self.preambule = list(preambule)
        self.compact = compact
        self.legende_notes = legende_notes
        self.note_sur = note_sur
        self.dossier_defaut = dossier_defaut
        self.archives_par_groupe = archives_par_groupe
        self.prefixe_dossier = prefixe_dossier
        self.classe_defaut = classe_defaut
        self._corriger: Callable[[Copie, Contexte], Fiche | str | tuple] | None = None
        self._options: Callable[[argparse.ArgumentParser], None] | None = None
        self._preparer: Callable[[Contexte], None] | None = None
        self._options_propres: list[str] = []

    # ─── Décorateurs ─────────────────────────────────────────────────────────

    def correction(self, fonction):
        """fonction(copie, ctx) -> Fiche | str (Typst) | (str, note)."""
        self._corriger = fonction
        return fonction

    def options(self, fonction):
        """fonction(parser) : options propres à la recette (elles font partie de la clé de cache)."""
        self._options = fonction
        return fonction

    def preparation(self, fonction):
        """fonction(ctx) : appelée une fois avant les corrections (références, données partagées…)."""
        self._preparer = fonction
        return fonction

    # ─── Ligne de commande ───────────────────────────────────────────────────

    def analyseur(self) -> argparse.ArgumentParser:
        p = argparse.ArgumentParser(
            description=self.description,
            epilog="Attention : le code remis est exécuté sous votre compte ; le délai n'est pas un bac à sable.",
        )
        p.add_argument("dossier", nargs="?" if self.dossier_defaut else None, default=self.dossier_defaut,
                       help="Dossier des copies (un sous-dossier ou une archive par étudiant)"
                            + (f" ; défaut : {self.dossier_defaut}" if self.dossier_defaut else "") + ".")
        p.add_argument("groupes", nargs="*", help="Groupes à corriger (défaut : config, sinon tous).")
        g = p.add_argument_group("copies")
        g.add_argument("--classe", "--csv", dest="classe", type=Path, help="Liste de classe CSV/Excel.")
        g.add_argument("--echeance", help="Échéance ISO, ex. 2026-11-04T23:59 (défaut : config.toml).")
        g.add_argument("--sans-echeance", action="store_true", help="Ignore l'échéance : dernier commit.")
        g.add_argument("--reference", help="Référence git corrigée (défaut : origin/HEAD ou HEAD).")
        g.add_argument("--travail-local", action="store_true",
                       help="Corrige les fichiers du dossier de travail des clones (modifications non "
                            "committées comprises) plutôt que le commit à l'échéance.")
        g.add_argument("--seulement", nargs="+", metavar="ID", help="Ne corrige que ces matricules.")
        g.add_argument("--inclure-exclus", action="store_true", help="Corrige aussi les copies exclues.")
        g.add_argument("--etat", type=Path, help="Dossier où créer .correction (défaut : le dossier des copies).")
        x = p.add_argument_group("exécution")
        x.add_argument("--refaire", nargs="*", metavar="ID",
                       help="Ignore le cache (tous, ou seulement ces matricules).")
        x.add_argument("--jobs", "-j", type=int, help=f"Corrections en parallèle (défaut : {os.cpu_count()}).")
        x.add_argument("--timeout", type=int, default=self.delai, help=f"Délai par exécution, en s (défaut : {self.delai}).")
        x.add_argument("--sans-diagnostic", action="store_true", help="Saute le diagnostic machine.")
        s = p.add_argument_group("sortie")
        s.add_argument("--out", type=Path, help="Dossier de sortie (défaut : <dossier>/corrections-<recette>).")
        s.add_argument("--font-size", default=self.taille, help=f"Taille Typst (défaut : {self.taille}).")
        if self._options:
            avant = {a.dest for a in p._actions}
            self._options(p)
            self._options_propres = [a.dest for a in p._actions if a.dest not in avant]
        return p

    # ─── Session ─────────────────────────────────────────────────────────────

    def session(self, argv: list[str] | None = None) -> tuple[Contexte, list[Copie]]:
        """Analyse les arguments, prépare le contexte et découvre les copies (sans les corriger).

        Utilisé par :meth:`main` et par les outils interactifs (menus de build, IDE).
        """
        parser = self.analyseur()
        args = parser.parse_args(argv)
        racine = Path(args.dossier).expanduser().resolve()
        if not racine.is_dir():
            parser.error(f"{racine} n'est pas un dossier.")
        if not typst.TAILLE_RE.fullmatch(args.font_size):
            parser.error("--font-size doit être une taille Typst, par exemple 8pt.")
        etat = Etat(Path(args.etat).expanduser().resolve() if args.etat else racine)
        config = etat.config()
        args.racine = racine

        outils = {}
        if not args.sans_diagnostic:
            outils = diagnostic.verifier(self.outils)

        sortie = (args.out.expanduser().resolve() if args.out else racine / f"corrections-{self.nom}")
        sortie.mkdir(parents=True, exist_ok=True)
        ressources = dict(config.get("ressources", {}))
        if args.jobs:
            ressources["cpu"] = args.jobs
        ctx = Contexte(self, args, etat, sortie, Ordonnanceur(ressources), outils)

        classe = self._classe(args, config, racine, parser)
        groupes = list(dict.fromkeys(args.groupes or config.get("groupes", [])))
        if classe and groupes and not self.archives_par_groupe:  # sinon le dossier fait foi
            inconnus = sorted(set(groupes) - {e.groupe for e in classe.values()})
            if inconnus:
                parser.error(f"Groupe(s) absent(s) de la liste de classe : {', '.join(inconnus)}.")
        args.groupes = groupes

        texte_echeance = None if args.sans_echeance else (args.echeance or config.get("echeance"))
        args.echeances = echeance.Echeances.depuis(texte_echeance, config.get("prolongations"))
        args.reference = args.reference or config.get("reference")
        if args.echeances.echeance:
            console.info(f"Échéance : {args.echeances.echeance:%Y-%m-%d %H:%M %Z}"
                         + (f" ({len(args.echeances.prolongations)} prolongation(s))" if args.echeances.prolongations else ""))

        copies = self._decouvrir(racine, classe, groupes, ctx)
        if self._preparer:
            self._preparer(ctx)
        return ctx, copies

    def _classe(self, args, config, racine: Path, parser) -> dict[str, Etudiant]:
        """Liste de classe : --classe (relatif au dossier courant), sinon config.toml (relatif au
        dossier des copies), sinon le défaut de la recette."""
        if args.classe:
            chemin = Path(args.classe).expanduser().resolve()
        elif config.get("classe"):
            chemin = Path(config["classe"]).expanduser()
            chemin = chemin if chemin.is_absolute() else (racine / chemin).resolve()
        elif self.classe_defaut:
            chemin = Path(self.classe_defaut).expanduser().resolve()
        else:
            return {}
        try:
            return lire_classe(chemin)
        except (OSError, ValueError, RuntimeError) as e:
            parser.error(f"Liste de classe illisible : {e}")

    def _etudiant(self, nom: str, classe: dict[str, Etudiant]) -> Etudiant | None:
        matricule = matricule_dans(nom)
        if classe:
            return classe.get(matricule) if matricule else None
        ident = matricule or nom.removeprefix(self.prefixe_dossier)
        return etudiant_inconnu(ident, nom.removeprefix(self.prefixe_dossier))

    def _decouvrir(self, racine: Path, classe: dict[str, Etudiant], groupes: list[str], ctx: Contexte) -> list[Copie]:
        args, etat = ctx.args, ctx.etat
        exclusions = {} if args.inclure_exclus else etat.exclusions()
        seulement = set(args.seulement or [])
        travail = etat.dossier / "copies"
        trouvees: list[tuple[Etudiant, Path, str]] = []
        ignorees: list[str] = []

        if self.source == "archives":
            if self.archives_par_groupe and not groupes:
                avant, _, apres = self.archives_par_groupe.partition("{groupe}")
                groupes = sorted(p.name[len(avant):len(p.name) - len(apres)]
                                 for p in racine.glob(self.archives_par_groupe.format(groupe="*")) if p.is_dir())
                args.groupes = groupes
            dossiers =([racine / self.archives_par_groupe.format(groupe=g) for g in groupes]
                        if self.archives_par_groupe else [racine])
            for d in dossiers:
                if not d.is_dir():
                    console.attention(f"Dossier introuvable : {d}")
                    continue
                retenues, ecartees = depots.selectionner_archives(
                    depots.lister_archives(d), lambda a: matricule_dans(a.stem) or (None if classe else a.stem))
                ignorees += ecartees
                for ident, archive in retenues.items():
                    e = self._etudiant(archive.stem, classe)
                    if e is None:
                        e = etudiant_inconnu(ident)
                        ignorees.append(f"{archive.name} (matricule absent de la liste, corrigé quand même)")
                    if self.archives_par_groupe:
                        # Le dossier de dépôt fait foi pour le groupe ; on signale un désaccord avec la liste.
                        groupe = next(g for g in groupes if d.name == self.archives_par_groupe.format(groupe=g))
                        if e.groupe and e.groupe != groupe:
                            console.attention(f"{archive.name} : matricule {e.id} au groupe {e.groupe} "
                                              f"dans la liste, remis dans {d.name}.")
                        e = Etudiant(e.id, e.nom, e.prenom, groupe, e.github)
                    trouvees.append((e, archive, "archive"))
        else:
            for d in depots.lister_dossiers(racine):
                if d == ctx.sortie:
                    continue
                e = self._etudiant(d.name, classe)
                if e is None:
                    ignorees.append(f"{d.name} (matricule absent de la liste)")
                    continue
                nature = "git" if (d / ".git").exists() else "dossier"
                if self.source == "git" and nature != "git":
                    ignorees.append(f"{d.name} (pas un dépôt git)")
                    continue
                trouvees.append((e, d, nature))

        if ignorees:
            console.attention(f"{len(ignorees)} élément(s) ignoré(s) : " + "; ".join(ignorees))

        copies = []
        for e, source, nature in trouvees:
            if groupes and e.groupe not in groupes and not self.archives_par_groupe:
                continue
            if seulement and e.id not in seulement:
                continue
            if e.id in exclusions:
                console.info(f"Exclu : {e.nom_affiche} ({e.id}) — {exclusions[e.id]}")
                continue
            copies.append(Copie(e, source, nature, echeance=args.echeances.pour(e.id), reference=args.reference,
                                locale=bool(args.travail_local) and nature == "git"))
        copies.sort(key=lambda c: (c.groupe, c.etudiant.cle_tri))

        # Extraction des copies de travail (en parallèle : git archive / unzip sont indépendants).
        def extraire(copie: Copie) -> Copie:
            destination = travail / _nom_sur(copie.id)
            try:
                if copie.locale:
                    copie.cle = "local-" + depots.empreinte_dossier(copie.source)
                    temoin = destination / ".correction-dossier"
                    if not (temoin.is_file() and temoin.read_text().strip() == copie.cle):
                        depots.copier_dossier(copie.source, destination)
                        temoin.write_text(copie.cle + "\n")
                    copie.dossier = destination
                elif copie.nature == "git":
                    ref = copie.reference or echeance.reference_par_defaut(copie.source)
                    copie.reference = ref
                    sha = echeance.commit_a_echeance(copie.source, copie.echeance, ref)
                    if sha is None:
                        copie.remarques.append("aucun commit avant l'échéance" if copie.echeance else "dépôt vide")
                        copie.cle = "aucun-commit"
                        return copie
                    copie.cle = copie.sha = sha
                    copie.commits_apres = echeance.commits_apres(copie.source, sha, ref) if copie.echeance else []
                    copie.dossier = depots.exporter_commit(copie.source, sha, destination)
                elif copie.nature == "archive":
                    copie.cle = depots.empreinte_fichier(copie.source)
                    copie.dossier = depots.extraire_archive(copie.source, destination)
                else:
                    copie.cle = depots.empreinte_dossier(copie.source)
                    temoin = destination / ".correction-dossier"
                    if not (temoin.is_file() and temoin.read_text().strip() == copie.cle):
                        depots.copier_dossier(copie.source, destination)
                        temoin.write_text(copie.cle + "\n")
                    copie.dossier = destination
            except Exception as err:  # une copie illisible ne bloque pas les autres
                copie.remarques.append(f"extraction impossible : {err}")
                copie.cle = "erreur"
            return copie

        with Ordonnanceur({"cpu": min(8, os.cpu_count() or 1)}) as ordo:
            list(f.result() for f in ordo.carte(extraire, copies))
        retard = [c for c in copies if c.commits_apres]
        if retard:
            console.attention(f"{len(retard)} copie(s) avec des commits après l'échéance (ignorés) : "
                              + ", ".join(f"{c.etudiant.nom_affiche} ({len(c.commits_apres)})" for c in retard))
        console.info(f"{len(copies)} copie(s) à traiter.")
        return copies

    # ─── Correction ──────────────────────────────────────────────────────────

    def _options_cache(self, args) -> str:
        valeurs = {k: getattr(args, k, None) for k in self._options_propres}
        valeurs["timeout"] = args.timeout
        return Etat.empreinte_options(valeurs)

    def corriger_toutes(self, ctx: Contexte, copies: list[Copie]) -> dict[str, Entree]:
        if self._corriger is None:
            raise RuntimeError("La recette n'a pas de fonction @recette.correction.")
        args, etat = ctx.args, ctx.etat
        options = self._options_cache(args)
        if args.refaire is not None:
            n = etat.oublier(self.nom, args.refaire or None)
            console.info(f"Cache invalidé : {n} résultat(s).")

        resultats: dict[str, Entree] = {}
        a_faire = []
        for copie in copies:
            entree = etat.lire(self.nom, copie.id, copie.cle, self.version, options)
            if entree is not None and not images_manquantes(entree.fiche, ctx.sortie):
                resultats[copie.id] = entree
            else:
                a_faire.append(copie)
        if resultats:
            console.info(f"{len(resultats)} copie(s) déjà corrigée(s) (cache), {len(a_faire)} à corriger.")

        sans_cache: set[str] = set()

        def une(copie: Copie) -> Entree:
            debut = time.perf_counter()
            if copie.dossier is None:
                fiche = Fiche(self._fiche_vide(copie))
                statut = "vide"
            else:
                try:
                    fiche = _en_fiche(self._corriger(copie, ctx))
                    statut = "ok"
                    if not fiche.conserver:
                        sans_cache.add(copie.id)
                except Exception as err:
                    trace = traceback.format_exc(limit=-4)
                    fiche = Fiche(f"{typst.rouge(f'{copie.etudiant.nom_affiche} ({copie.id}) : erreur de correction : {err}')}"
                                  f"\n\n{typst.brut(trace)}")
                    statut = "erreur"
            if copie.commits_apres:
                fiche.typst = self._bandeau_retard(copie) + "\n" + fiche.typst
            return Entree(statut, fiche.typst, fiche.note, fiche.donnees, round(time.perf_counter() - debut, 2))

        total = len(a_faire)
        futurs = {ctx.ordonnanceur.soumettre(une, c, ressource=self.ressource): c for c in a_faire}
        try:
            for i, futur in enumerate(as_completed(futurs), 1):
                copie = futurs[futur]
                entree = futur.result()
                resultats[copie.id] = entree
                if entree.statut != "erreur" and copie.id not in sans_cache:  # les erreurs sont retentées au prochain lancement
                    etat.ecrire(self.nom, copie.id, copie.cle, self.version, options, entree)
                marque = {"ok": "", "vide": " — aucune copie", "erreur": " — ERREUR"}[entree.statut]
                (console.erreur if entree.statut == "erreur" else console.info)(
                    f"[{i}/{total}] [{copie.groupe}] {copie.etudiant.nom_affiche} ({copie.id}) {entree.duree:.1f} s{marque}")
        except KeyboardInterrupt:
            console.attention("Interrompu : les copies déjà corrigées sont conservées dans le cache.")
            ctx.ordonnanceur.fermer(annuler=True)
            raise SystemExit(130)
        return resultats

    def _fiche_vide(self, copie: Copie) -> str:
        e = copie.etudiant
        return (f'#text(size: 11pt, weight: "bold")[{typst.texte(f"{self.description} — {e.nom_affiche} — {e.id}")}]\n\n'
                + typst.rouge("Aucune copie à corriger : " + "; ".join(copie.remarques or ["introuvable"])))

    def _bandeau_retard(self, copie: Copie) -> str:
        n = len(copie.commits_apres)
        quand = copie.echeance.strftime("%d/%m %H:%M") if copie.echeance else ""
        derniers = ", ".join(f"{d[5:16].replace('T', ' ')} {m[:40]}" for _, d, m in copie.commits_apres[:3])
        return (f'#place(top + right, dx: 0mm, dy: -6mm)[#text(size: 6.5pt, fill: orange.darken(20%))['
                f'{typst.texte(f"{n} commit(s) après l’échéance ({quand}) ignoré(s) : {derniers}")}]]')

    # ─── Documents ───────────────────────────────────────────────────────────

    def assembler(self, ctx: Contexte, copies: list[Copie], resultats: dict[str, Entree]) -> list[Path]:
        args, sortie = ctx.args, ctx.sortie
        par_groupe: dict[str, list[Copie]] = {}
        for c in copies:
            par_groupe.setdefault(c.groupe, []).append(c)
        machine = diagnostic.resume_texte(ctx.outils) if ctx.outils else ""
        produits, echec = [], False
        for groupe, liste in par_groupe.items():
            fiches = [resultats[c.id].fiche or "" for c in liste if c.id in resultats]
            if not fiches:
                continue
            contenu = typst.document(
                fiches, genere_par=f"{self.nom} v{self.version} ({datetime.now():%Y-%m-%d %H:%M}) — {machine}",
                taille=args.font_size, marge=self.marge, saut=self.saut, preambule=self.preambule,
                compact=self.compact)
            try:
                pdf = typst.ecrire_et_compiler(contenu, sortie / f"groupe-{groupe}.typ", racine=sortie)
                produits.append(pdf)
                console.ok(f"[{groupe}] PDF : {pdf} ({len(fiches)} copies)")
            except typst.ErreurTypst as e:
                console.erreur(str(e))
                echec = True
            lignes_notes = [(c.etudiant, resultats[c.id].note) for c in liste
                            if c.id in resultats and resultats[c.id].note]
            if lignes_notes:
                chemin_csv = notes.ecrire_csv(sortie / f"groupe-{groupe}-notes.csv", lignes_notes)
                try:
                    pdf_notes = notes.page_notes(sortie / f"groupe-{groupe}-notes.pdf", groupe, lignes_notes,
                                                 self.legende_notes, self.note_sur)
                    console.ok(f"[{groupe}] Notes proposées : {pdf_notes} et {chemin_csv.name}")
                except typst.ErreurTypst as e:
                    console.erreur(str(e))
                    echec = True
        self._ecrire_ordre(sortie, copies)
        if echec:
            raise SystemExit(1)
        return produits

    @staticmethod
    def _ecrire_ordre(sortie: Path, copies: list[Copie]) -> None:
        """ordre.csv : ordre des fiches, pour parcourir les copies dans l'IDE (corr ouvrir)."""
        with (sortie / "ordre.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Matricule", "Nom", "Prenom", "Groupe", "Projet", "Source", "Cle"])
            for c in copies:
                w.writerow([c.id, c.etudiant.nom, c.etudiant.prenom, c.groupe,
                            str(c.dossier or ""), str(c.source), c.cle])

    def main(self, argv: list[str] | None = None) -> None:
        ctx, copies = self.session(argv)
        if not copies:
            console.erreur("Aucune copie à corriger.")
            raise SystemExit(1)
        try:
            resultats = self.corriger_toutes(ctx, copies)
            self.assembler(ctx, copies, resultats)
        finally:
            ctx.ordonnanceur.fermer()
            ctx.etat.fermer()
        erreurs = [cid for cid, e in resultats.items() if e.statut == "erreur"]
        if erreurs:
            console.erreur(f"{len(erreurs)} copie(s) en erreur : {', '.join(erreurs)}")
            raise SystemExit(1)
        console.ok(f"Terminé. Sorties dans {ctx.sortie}")


def images_manquantes(fiche: str, sortie: Path) -> bool:
    """Vrai si une fiche du cache cite une image absente de la sortie (autre --out, dossier vidé)."""
    return any(not (sortie / m).is_file() for m in re.findall(r'image\("([^"\\]+)"', fiche))


def _en_fiche(valeur) -> Fiche:
    if isinstance(valeur, Fiche):
        return valeur
    if isinstance(valeur, tuple):
        return Fiche(*valeur)
    return Fiche(str(valeur))


def _nom_sur(texte: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in texte)
