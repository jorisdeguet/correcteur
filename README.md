# correcteur

Primitives pour corriger des travaux de programmation (Python, Flutter, Android…) :
copies à l'échéance, exécution bornée, cache entre les sessions, parallélisme par ressource,
rapports PDF Typst, détection de similarité. Fonctionne à l'identique sous macOS (Apple Silicon),
Windows et Linux.

## Installation

Seul [uv](https://docs.astral.sh/uv/) est nécessaire (il installe Python au besoin) :

```sh
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh        # ou : brew install uv
# Windows (PowerShell)
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Un script de correction déclare la librairie en en-tête ([PEP 723](https://peps.python.org/pep-0723/)) :

```python
# /// script
# requires-python = ">=3.11"
# dependencies = ["correcteur @ git+https://github.com/jorisdeguet/correcteur@v0.1.1"]
# ///
```

puis se lance avec `uv run correctionTP2.py …` : uv installe la version épinglée dans un cache,
sans toucher au Python du système. Une vieille correction épinglée sur `@v0.1.1` continue de
fonctionner quand la librairie évolue.

La commande `corr` (outils communs) :

```sh
uv tool install git+https://github.com/jorisdeguet/correcteur      # une fois
uv tool install -e ~/Documents/GitHub/correcteur                   # ou, en développement
```

## Commande `corr`

| Commande | Rôle |
|---|---|
| `corr doctor [outil…]` | Versions de git, gh, uv, typst, java, flutter, dart, adb (+ appareils), firebase, node, mvn, dotnet, IDE ; cœurs, mémoire, disque |
| `corr sync ORG PREFIXE DOSSIER` | `gh repo clone` des nouveaux dépôts, `git fetch` des autres (le dossier de travail n'est pas modifié) |
| `corr echeance DOSSIER 2026-11-04T23:59` | Échéance du dossier |
| `corr prolonger DOSSIER 2538179 +1j` | Prolongation individuelle (`+1j`, `+36h`, `+90min` ou date) |
| `corr exclure DOSSIER 6309202 --raison "abandon"` | Exclusion persistante, pour toutes les corrections du dossier |
| `corr statut DOSSIER` | Échéance, prolongations, exclusions, cache, commits en retard |
| `corr plagiat DOSSIER --motifs '*.py' --depart ../TP1-Depart` | Rapport HTML de similarité |
| `corr ouvrir DOSSIER/corrections-X --ide pycharm` | Parcourt les copies dans l'IDE, dans l'ordre des PDF |

## Le dossier `.correction/`

Créé à côté des copies, jamais dans un dépôt étudiant :

```
.correction/
  config.toml    échéance, prolongations, exclusions, groupes, liste de classe, ressources
  etat.sqlite    cache : (recette, étudiant) → commit/archive, version, fiche, note
  copies/<id>/   copie de travail extraite au commit de l'échéance
  empreintes/    cache du plagiat
```

- Une copie est recorrigée seulement si son commit (ou son archive), la version de la recette
  ou ses options ont changé. `--refaire [ID…]` force la reprise ; Ctrl-C conserve ce qui est fait.
- Le code étudiant s'exécute dans `copies/<id>` : le clone n'est jamais modifié (harnais,
  données, builds restent hors du dépôt).
- **Dates de commit** : elles sont fixées par la machine de l'étudiant et peuvent être falsifiées.
  Les commits postérieurs à l'échéance sont signalés en haut de la fiche ; `corr sync` note
  en plus la date de première observation de chaque commit.

## Écrire une recette

```python
from correcteur import Fiche, Recette, typst

recette = Recette("SN1-TP1", version="2026.11.1", description="TP1 — racine.py",
                  outils=["git"], delai=15)

@recette.options
def options(p):
    p.add_argument("--autocorrecteur", default="autocorrecteur.py")

@recette.correction
def corriger(copie, ctx):
    r = copie.python("racine.py", delai=ctx.args.timeout)
    return Fiche(typst.brut(r.sortie), note={"Q1": 1, "Total": 1, "Remarques": ""})

if __name__ == "__main__":
    recette.main()
```

Options communes : `dossier [groupes…] --classe --echeance --sans-echeance --reference
--seulement ID… --inclure-exclus --refaire [ID…] -j N --timeout --out --font-size`.
Sorties dans `<dossier>/corrections-<recette>/` : `groupe-<g>.pdf` (une feuille par étudiant,
recto verso), `groupe-<g>-notes.csv` / `.pdf` si la recette propose des notes, `ordre.csv`.

## Parallélisme par ressource

Chaque recette déclare la ressource qu'elle occupe (`Recette(..., ressource="flutter")`) ;
un sémaphore par ressource borne la concurrence. Défauts : `cpu` = nombre de cœurs,
`flutter` = 1, `gradle` = 1, `appareil:<id>` = 1, `humain` = 1. Réglable dans `config.toml` :

```toml
[ressources]
flutter = 2
```

`ordonnanceur.Pipeline` fait tourner les builds en fond pendant l'inspection interactive
(le build de la copie N+1 avance pendant que vous regardez N).

## Plagiat

Empreintes par *winnowing* (l'algorithme de MOSS) sur des jetons normalisés (identifiants,
nombres et chaînes remplacés ; commentaires et imports retirés) pour Python, Kotlin, Java,
Dart, C#, JS et C. L'index inversé donne les paires candidates en un passage ; la plus longue
séquence commune est calculée par tableau de suffixes (`pydivsufsort`, en C) sur ces paires
seulement. Le code de départ (`--depart`) et les fragments présents chez plus de la moitié
des copies sont ignorés.

## Avertissement

Le code des étudiants est exécuté sous votre compte. Les délais et la mise à mort des groupes
de processus empêchent les boucles infinies, pas les actions malveillantes.
