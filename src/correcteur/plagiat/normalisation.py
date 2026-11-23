"""Normalisation du code en jetons, par langage.

Les identifiants deviennent ``V``, les nombres ``N``, les chaînes ``S`` : renommer les
variables ou changer les messages ne change pas la suite de jetons. Les mots-clés et
opérateurs sont conservés. Chaque jeton garde sa ligne d'origine pour l'affichage.
"""

from __future__ import annotations

import io
import keyword
import re
import tokenize
from dataclasses import dataclass
from pathlib import Path

LANGAGES = {
    ".py": "python",
    ".kt": "kotlin", ".kts": "kotlin",
    ".java": "java",
    ".dart": "dart",
    ".cs": "csharp",
    ".js": "js", ".ts": "js", ".jsx": "js", ".tsx": "js",
    ".c": "c", ".h": "c", ".cpp": "c", ".hpp": "c",
}

MOTS_CLES = {
    "kotlin": "as break class continue do else false for fun if in interface is null object package return super "
              "this throw true try typealias typeof val var when while by catch constructor finally get init "
              "override private protected public internal open abstract sealed data enum companion suspend "
              "lateinit inline const",
    "java": "abstract assert boolean break byte case catch char class const continue default do double else enum "
            "extends final finally float for if implements import instanceof int interface long new package "
            "private protected public return short static super switch this throw throws try void while var "
            "null true false",
    "dart": "abstract as async await break case catch class const continue default do dynamic else enum extends "
            "factory final finally for if implements import in is late new null required return static super "
            "switch this throw true false try var void while with yield",
    "csharp": "abstract as base bool break byte case catch char class const continue decimal default do double else "
              "enum false finally float for foreach if in int interface internal is long namespace new null "
              "object out override private protected public readonly ref return static string struct switch "
              "this throw true try using var virtual void while async await",
    "js": "async await break case catch class const continue default do else export extends false finally for "
          "function if import in instanceof let new null return super switch this throw true try typeof var "
          "void while yield",
    "c": "auto break case char const continue default do double else enum extern float for goto if int long "
         "return short signed sizeof static struct switch typedef union unsigned void while",
}
_MOTS_CLES = {lang: set(mots.split()) for lang, mots in MOTS_CLES.items()}

_C_JETON = re.compile(r'''
    (?P<comm>//[^\n]*|/\*.*?\*/)
  | (?P<chaine>"""(?:.|\n)*?"""|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')
  | (?P<nombre>\b\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?[fFlLdDuU]?\b|\b0[xX][0-9a-fA-F_]+\b)
  | (?P<ident>[A-Za-z_$][\w$]*)
  | (?P<op>==|!=|<=|>=|&&|\|\||\+\+|--|->|=>|::|\?\.|\?:|!!|\.\.|[-+*/%=<>!&|^~?:;,.(){}\[\]@#])
  | (?P<espace>\s+)
''', re.VERBOSE | re.DOTALL)
_IMPORT_RE = re.compile(r"^\s*(import|package|using|#include)\b.*$", re.MULTILINE)


@dataclass(frozen=True)
class Jeton:
    valeur: str
    ligne: int


def langage(chemin: str | Path) -> str | None:
    return LANGAGES.get(Path(chemin).suffix.lower())


def jetons_python(source: str) -> list[Jeton]:
    jetons: list[Jeton] = []
    try:
        for t in tokenize.generate_tokens(io.StringIO(source).readline):
            if t.type in (tokenize.COMMENT, tokenize.NL, tokenize.ENCODING, tokenize.ENDMARKER):
                continue
            if t.type == tokenize.NAME:
                v = t.string if keyword.iskeyword(t.string) else "V"
            elif t.type == tokenize.NUMBER:
                v = "N"
            elif t.type == tokenize.STRING or t.type in _FSTRING:
                v = "S"
            elif t.type == tokenize.NEWLINE:
                v = ";"
            elif t.type == tokenize.INDENT:
                v = "{"
            elif t.type == tokenize.DEDENT:
                v = "}"
            else:
                v = t.string
            if jetons and v == "S" and jetons[-1].valeur == "S":
                continue  # f-strings découpées en plusieurs jetons
            jetons.append(Jeton(v, t.start[0]))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return jetons_c(source, "python")
    return _sans_imports_python(jetons)


_FSTRING = {getattr(tokenize, n) for n in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END") if hasattr(tokenize, n)}


def _sans_imports_python(jetons: list[Jeton]) -> list[Jeton]:
    sortie, i = [], 0
    while i < len(jetons):
        if jetons[i].valeur in ("import", "from") and (i == 0 or jetons[i - 1].valeur in (";", "{", "}")):
            while i < len(jetons) and jetons[i].valeur != ";":
                i += 1
            i += 1
            continue
        sortie.append(jetons[i])
        i += 1
    return sortie


def jetons_c(source: str, lang: str) -> list[Jeton]:
    """Langages à accolades (Kotlin, Java, Dart, C#, JS, C) : commentaires et imports retirés."""
    source = _IMPORT_RE.sub(lambda m: "\n" * m.group(0).count("\n"), source)
    mots = _MOTS_CLES.get(lang, set())
    jetons, ligne = [], 1
    for m in _C_JETON.finditer(source):
        texte = m.group(0)
        genre = m.lastgroup
        if genre == "ident":
            jetons.append(Jeton(texte if texte in mots else "V", ligne))
        elif genre == "nombre":
            jetons.append(Jeton("N", ligne))
        elif genre == "chaine":
            jetons.append(Jeton("S", ligne))
        elif genre == "op":
            jetons.append(Jeton(texte, ligne))
        ligne += texte.count("\n")
    return jetons


def jetons(source: str, lang: str) -> list[Jeton]:
    return jetons_python(source) if lang == "python" else jetons_c(source, lang)


def sans_commentaires(source: str, lang: str) -> str:
    """Code sans commentaires ni lignes vides (affichage côte à côte)."""
    if lang == "python":
        try:
            morceaux = []
            for t in tokenize.generate_tokens(io.StringIO(source).readline):
                if t.type != tokenize.COMMENT:
                    morceaux.append(t)
            source = tokenize.untokenize(morceaux)
        except (tokenize.TokenError, IndentationError, SyntaxError):
            pass
    else:
        source = re.sub(r"//[^\n]*|/\*.*?\*/", "", source, flags=re.DOTALL)
    return "\n".join(l.rstrip() for l in source.splitlines() if l.strip())
