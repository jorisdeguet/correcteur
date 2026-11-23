"""Commande ``corr`` : outils communs à toutes les corrections.

    corr doctor [outil …]                      diagnostic machine
    corr sync ORG PREFIXE DOSSIER              clone / fetch des dépôts d'une organisation GitHub
    corr statut DOSSIER                        copies, exclusions, échéance, cache
    corr echeance DOSSIER DATE                 fixe l'échéance (config.toml)
    corr prolonger DOSSIER MATRICULE +1j       prolongation individuelle
    corr exclure DOSSIER MATRICULE [--raison]  exclusion persistante
    corr inclure DOSSIER MATRICULE
    corr plagiat DOSSIER [options]             similarité entre copies (rapport HTML)
    corr ouvrir SORTIE [--ide pycharm]         parcourt les copies dans l'IDE (ordre.csv)
"""

from __future__ import annotations

import argparse
import csv
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

from . import __version__, console, depots, diagnostic, echeance, ide
from .etat import Etat


def _doctor(args) -> int:
    resultats = diagnostic.diagnostiquer(args.outils or None)
    diagnostic.afficher(resultats)
    return 0


def _sync(args) -> int:
    racine = Path(args.dossier).expanduser().resolve()
    clones, maj, erreurs = depots.synchroniser(args.org, args.prefixe, racine)
    etat = Etat(racine)
    for d in depots.lister_depots(racine):
        try:
            import subprocess
            shas = subprocess.run(["git", "-C", str(d), "rev-list", "--all"], capture_output=True, text=True,
                                  timeout=60).stdout.split()
            etat.observer(d.name, shas)
        except Exception:
            pass
    console.ok(f"{len(clones)} cloné(s), {len(maj)} mis à jour, {len(erreurs)} en erreur.")
    return 1 if erreurs else 0


def _statut(args) -> int:
    racine = Path(args.dossier).expanduser().resolve()
    etat = Etat(racine)
    config = etat.config()
    from rich.table import Table
    console.info(f"Dossier : {racine}")
    console.info(f"Configuration : {etat.chemin_config}")
    console.info(f"Échéance : {config.get('echeance', '(aucune : dernier commit)')}")
    for m, v in config.get("prolongations", {}).items():
        console.info(f"  prolongation {m} : {v}")
    for m, v in config.get("exclusions", {}).items():
        console.info(f"  exclu {m} : {v}")
    table = Table(title="Cache des corrections")
    for col in ("Recette", "Copies", "ok", "vide", "Dernière"):
        table.add_column(col)
    par_recette: dict[str, list] = {}
    for recette, etudiant, statut, quand in etat.resume():
        par_recette.setdefault(recette, []).append((statut, quand))
    for recette, lignes in par_recette.items():
        table.add_row(recette, str(len(lignes)), str(sum(s == "ok" for s, _ in lignes)),
                      str(sum(s == "vide" for s, _ in lignes)),
                      datetime.fromtimestamp(max(q for _, q in lignes)).strftime("%Y-%m-%d %H:%M"))
    console.console.print(table)
    depots_git = depots.lister_depots(racine)
    if depots_git:
        echeances = echeance.Echeances.depuis(config.get("echeance"), config.get("prolongations"))
        apres = 0
        for d in depots_git:
            from .classe import matricule_dans
            ref = config.get("reference") or echeance.reference_par_defaut(d)
            sha = echeance.commit_a_echeance(d, echeances.pour(matricule_dans(d.name) or d.name), ref)
            if echeances.echeance and echeance.commits_apres(d, sha, ref):
                apres += 1
        console.info(f"{len(depots_git)} dépôt(s) git ; {apres} avec des commits après l'échéance.")
    return 0


def _echeance(args) -> int:
    etat = Etat(Path(args.dossier).expanduser().resolve())
    date = echeance.lire_date(args.date)
    etat.modifier_config(echeance=args.date)
    console.ok(f"Échéance : {date:%Y-%m-%d %H:%M:%S %Z}")
    return 0


def _prolonger(args) -> int:
    etat = Etat(Path(args.dossier).expanduser().resolve())
    echeance.appliquer_prolongation(datetime.now().astimezone(), args.valeur)  # validation
    etat.prolonger(args.matricule, args.valeur)
    console.ok(f"Prolongation enregistrée : {args.matricule} → {args.valeur}")
    return 0


def _exclure(args) -> int:
    etat = Etat(Path(args.dossier).expanduser().resolve())
    for m in args.matricules:
        etat.exclure(m, args.raison)
    console.ok(f"Exclu(s) : {', '.join(args.matricules)} (persisté dans {etat.chemin_config})")
    return 0


def _inclure(args) -> int:
    etat = Etat(Path(args.dossier).expanduser().resolve())
    for m in args.matricules:
        console.info(f"{m} : {'réinclus' if etat.inclure(m) else 'n’était pas exclu'}")
    return 0


def _ouvrir(args) -> int:
    ordre = Path(args.sortie).expanduser().resolve()
    if ordre.is_dir():
        ordre = ordre / "ordre.csv"
    if not ordre.is_file():
        console.erreur(f"{ordre} introuvable : lancez d'abord la correction.")
        return 1
    with ordre.open(encoding="utf-8", newline="") as f:
        lignes = [l for l in csv.DictReader(f) if l.get("Projet")]
    depart = 0
    if args.depuis:
        ids = [l["Matricule"] for l in lignes]
        if args.depuis not in ids:
            console.erreur(f"Matricule {args.depuis} absent de {ordre}.")
            return 1
        depart = ids.index(args.depuis)

    def ouvrir(ligne):
        projet = Path(ligne["Projet"])
        if args.ide == "pycharm":
            from .codepython import fichiers_questions, racine_projet
            projet = racine_projet(projet)
            if args.sdk:
                ide.definir_sdk_pycharm(projet, args.sdk)
            fichiers = [p for _, ps in sorted(fichiers_questions(projet).items()) for p in ps]
        else:
            fichiers = []
        ide.ouvrir(args.ide, projet, fichiers)

    ide.parcourir(lignes, lambda i, l: f"{l['Prenom']} {l['Nom']} — {l['Matricule']} — groupe {l['Groupe']}\n  {l['Projet']}",
                  ouvrir, depart=depart)
    return 0


def _plagiat(argv: list[str]) -> int:
    from .plagiat.commande import principal
    return principal(argv)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "plagiat":
        return _plagiat(argv[1:])
    p = argparse.ArgumentParser(prog="corr", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"correcteur {__version__}")
    sous = p.add_subparsers(dest="commande", required=True)

    s = sous.add_parser("doctor", help="Diagnostic machine (versions des outils).")
    s.add_argument("outils", nargs="*", help=f"Outils à vérifier (défaut : tous : {', '.join(diagnostic.TOUS)}).")
    s.set_defaults(f=_doctor)

    s = sous.add_parser("sync", help="Clone ou met à jour (fetch) les dépôts ORG/PREFIXE* dans DOSSIER.")
    s.add_argument("org"), s.add_argument("prefixe"), s.add_argument("dossier")
    s.set_defaults(f=_sync)

    s = sous.add_parser("statut", help="État du dossier de copies.")
    s.add_argument("dossier")
    s.set_defaults(f=_statut)

    s = sous.add_parser("echeance", help="Fixe l'échéance du dossier (ex. 2026-11-04T23:59).")
    s.add_argument("dossier"), s.add_argument("date")
    s.set_defaults(f=_echeance)

    s = sous.add_parser("prolonger", help="Prolongation individuelle (+1j, +36h ou date).")
    s.add_argument("dossier"), s.add_argument("matricule"), s.add_argument("valeur")
    s.set_defaults(f=_prolonger)

    s = sous.add_parser("exclure", help="Exclut des copies de toutes les corrections du dossier.")
    s.add_argument("dossier"), s.add_argument("matricules", nargs="+")
    s.add_argument("--raison", default="")
    s.set_defaults(f=_exclure)

    s = sous.add_parser("inclure", help="Annule une exclusion.")
    s.add_argument("dossier"), s.add_argument("matricules", nargs="+")
    s.set_defaults(f=_inclure)

    s = sous.add_parser("ouvrir", help="Parcourt les copies dans l'IDE, dans l'ordre des PDF.")
    s.add_argument("sortie", help="Dossier de sortie d'une correction (contient ordre.csv).")
    s.add_argument("--ide", choices=["pycharm", "studio", "code"], default="pycharm")
    s.add_argument("--depuis", help="Matricule par lequel commencer.")
    s.add_argument("--sdk", default="", help="Interpréteur PyCharm à imposer au projet (.idea).")
    s.set_defaults(f=_ouvrir)

    sous.add_parser("plagiat", help="Similarité entre copies (corr plagiat --help).")

    args = p.parse_args(argv)
    try:
        return args.f(args) or 0
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
