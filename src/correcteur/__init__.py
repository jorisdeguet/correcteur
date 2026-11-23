"""correcteur : primitives pour corriger des travaux de programmation.

Voir README.md. Points d'entrée principaux :

- :class:`correcteur.Recette` : cadre d'un script de correction ;
- :mod:`correcteur.typst` : rapports PDF ;
- :mod:`correcteur.execution` : exécution bornée multi-OS ;
- :mod:`correcteur.plagiat` : similarité entre copies ;
- la commande ``corr`` (doctor, sync, statut, exclure, plagiat, ouvrir…).
"""

__version__ = "0.1.1"

from .classe import Etudiant, lire_classe  # noqa: E402
from .execution import Resultat, executer, executer_python  # noqa: E402
from .recette import Contexte, Copie, Fiche, Recette  # noqa: E402

__all__ = ["Contexte", "Copie", "Etudiant", "Fiche", "Recette", "Resultat", "executer", "executer_python",
           "lire_classe", "__version__"]
