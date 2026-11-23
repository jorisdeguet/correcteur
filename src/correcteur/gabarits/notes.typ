// Notes proposées d'un groupe. Données : notes-donnees.json (voir correcteur.notes).
#let d = json("donnees.json")
#set page(paper: "us-letter", margin: 8mm, flipped: true)
#set text(font: "Libertinus Serif", size: 8pt, lang: "fr")

#let fmt(v) = if type(v) == float or type(v) == int {
  let r = calc.round(v, digits: 2)
  str(r).replace(".", ",")
} else { str(v) }

#text(size: 12pt, weight: "bold")[#d.titre]

#text(size: 7pt, fill: luma(80))[#d.legende]

#let colonnes = d.colonnes
#let numeriques = colonnes.filter(c => c != "Remarques")
#table(
  columns: (auto, auto) + numeriques.map(_ => auto) + colonnes.filter(c => c == "Remarques").map(_ => 1fr),
  inset: 2.5pt,
  stroke: .3pt,
  align: (x, y) => if x < 2 or colonnes.at(x - 2, default: "") == "Remarques" { left } else { center },
  fill: (x, y) => if y > 0 and calc.even(y) { luma(240) },
  table.header([*Nom*], [*Matricule*], ..colonnes.map(c => [*#c*])),
  ..d.lignes.map(l => (
    [#l.nom], [#l.matricule],
    ..colonnes.map(c => {
      let v = l.notes.at(c, default: "")
      if c == "Total" { [*#fmt(v)*] } else if c == "Remarques" { text(size: 6.5pt)[#v] } else { [#fmt(v)] }
    }),
  )).flatten(),
  [*Moyenne*], [],
  ..colonnes.map(c => if c in d.moyennes { [*#fmt(d.moyennes.at(c))*] } else { [] }),
)
