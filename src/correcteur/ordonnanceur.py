"""Parallélisme selon les ressources.

Chaque tâche déclare la ressource qu'elle occupe ; un sémaphore par ressource borne
le nombre de tâches simultanées :

=================  =========================  =========================================
Ressource          Défaut                     Usage
=================  =========================  =========================================
``cpu``            nombre de cœurs            exécution Python, analyse statique, plagiat
``flutter``        1                          builds Flutter (verrous du pub cache, RAM)
``gradle``         1                          builds Gradle (démon partagé)
``appareil:<id>``  1                          install/run adb sur un émulateur
``humain``         1                          inspection interactive
=================  =========================  =========================================

``Pipeline`` combine un fond de tâches (builds) et une boucle interactive qui attend,
dans l'ordre, que la tâche de la copie courante soit terminée : le build de N+1 tourne
pendant que vous inspectez N.
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any, Callable, Iterable, Iterator, TypeVar

T = TypeVar("T")

DEFAUTS = {"cpu": os.cpu_count() or 1, "flutter": 1, "gradle": 1, "humain": 1}


class Ressources:
    """Sémaphores nommés, créés à la demande."""

    def __init__(self, limites: dict[str, int] | None = None):
        self.limites = {**DEFAUTS, **(limites or {})}
        self._semaphores: dict[str, threading.BoundedSemaphore] = {}
        self._verrou = threading.Lock()

    def semaphore(self, nom: str) -> threading.BoundedSemaphore:
        with self._verrou:
            if nom not in self._semaphores:
                limite = self.limites.get(nom, 1 if ":" in nom else self.limites["cpu"])
                self._semaphores[nom] = threading.BoundedSemaphore(max(1, int(limite)))
            return self._semaphores[nom]

    @contextmanager
    def prendre(self, *noms: str) -> Iterator[None]:
        """Occupe une ou plusieurs ressources (toujours dans le même ordre : pas d'interblocage)."""
        sems = [self.semaphore(n) for n in sorted(set(noms))]
        for s in sems:
            s.acquire()
        try:
            yield
        finally:
            for s in reversed(sems):
                s.release()


class Ordonnanceur:
    """Pool de fils d'exécution + ressources. ``soumettre(f, ressource="cpu")`` → Future."""

    def __init__(self, limites: dict[str, int] | None = None, fils: int | None = None):
        self.ressources = Ressources(limites)
        total = fils or max(sum(v for k, v in self.ressources.limites.items() if k != "humain"), 2)
        self._pool = ThreadPoolExecutor(max_workers=total)

    def soumettre(self, fonction: Callable[..., T], *args: Any, ressource: str | Iterable[str] = "cpu",
                  **kwargs: Any) -> Future:
        noms = (ressource,) if isinstance(ressource, str) else tuple(ressource)

        def tache():
            with self.ressources.prendre(*noms):
                return fonction(*args, **kwargs)

        return self._pool.submit(tache)

    def carte(self, fonction: Callable[[Any], T], elements: Iterable[Any], ressource: str = "cpu") -> list[Future]:
        return [self.soumettre(fonction, e, ressource=ressource) for e in elements]

    def fermer(self, annuler: bool = False) -> None:
        self._pool.shutdown(wait=not annuler, cancel_futures=annuler)

    def __enter__(self) -> "Ordonnanceur":
        return self

    def __exit__(self, *exc) -> None:
        self.fermer(annuler=exc[0] is not None)


class Pipeline:
    """Tâches de fond (builds) + parcours interactif dans l'ordre.

    >>> with Pipeline(copies, preparer, ressource="flutter") as p:   # doctest: +SKIP
    ...     for copie, resultat in p:          # attend le build de la copie courante
    ...         menu(copie, resultat)
    """

    def __init__(self, elements: list[Any], preparer: Callable[[Any], Any], ressource: str = "cpu",
                 limites: dict[str, int] | None = None, avance: int | None = None):
        self.elements = elements
        self._ordo = Ordonnanceur(limites)
        self._preparer = preparer
        self._ressource = ressource
        self._futurs: dict[int, Future] = {}
        self._avance = avance  # None : tout soumettre d'un coup
        self.index = 0

    def _soumettre_jusqua(self, fin: int) -> None:
        for i in range(min(fin, len(self.elements))):
            if i not in self._futurs:
                self._futurs[i] = self._ordo.soumettre(self._preparer, self.elements[i], ressource=self._ressource)

    def resultat(self, index: int) -> Any:
        self._soumettre_jusqua(index + 1 + (self._avance if self._avance is not None else len(self.elements)))
        return self._futurs[index].result()

    def pret(self, index: int) -> bool:
        f = self._futurs.get(index)
        return f is not None and f.done()

    def __enter__(self) -> "Pipeline":
        self._soumettre_jusqua(len(self.elements) if self._avance is None else self._avance + 1)
        return self

    def __exit__(self, *exc) -> None:
        self._ordo.fermer(annuler=True)

    def __iter__(self):
        while 0 <= self.index < len(self.elements):
            i = self.index
            yield self.elements[i], self.resultat(i)
            if self.index == i:
                self.index += 1
