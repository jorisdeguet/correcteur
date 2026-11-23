"""Flutter / Android / examens : logique pure, sans SDK."""

import json
import socket

from correcteur import android, flutter
from correcteur.examen import Examen, executer_question


def projet(dossier, pubspec="dependencies:\n  flutter:\n    sdk: flutter\n"):
    (dossier / "android" / "app" / "src" / "main").mkdir(parents=True)
    (dossier / "lib").mkdir()
    (dossier / "pubspec.yaml").write_text(pubspec, encoding="utf-8")
    return dossier


def test_projets_ignore_les_dossiers_generes(tmp_path):
    projet(tmp_path / "app")
    projet(tmp_path / "app" / "build" / "copie")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "pubspec.yaml").write_text("name: dart_pur\n", encoding="utf-8")
    assert flutter.projets(tmp_path) == [tmp_path / "app"]


def test_emulateurs_hors_commentaires(tmp_path):
    p = projet(tmp_path / "app")
    (p / "lib" / "main.dart").write_text(
        "// x.useAuthEmulator('a', 1);\n/* y.useStorageEmulator() */\n"
        "final u = 'http://exemple'; FirebaseFirestore.instance.useFirestoreEmulator('10.0.2.2', 8080);",
        encoding="utf-8")
    assert flutter.emulateurs_utilises(p) == ["firestore"]


def test_dart_defines_de_la_configuration_dev(tmp_path):
    p = projet(tmp_path / "app")
    (p / ".vscode").mkdir()
    (p / ".vscode" / "launch.json").write_text(
        '{ // jsonc\n"configurations": [{"name": "DEV (phone)", "args": ["--dart-define=ENV=tel"]},'
        '{"name": "DEV", "args": ["--dart-define", "ENV=android_dev",]},]}', encoding="utf-8")
    assert flutter.dart_defines_dev(tmp_path, p)[0] == ["--dart-define=ENV=android_dev"]


def test_ports_firebase_json_prioritaire(tmp_path):
    fb = tmp_path / "firebase.json"
    fb.write_text(json.dumps({"emulators": {"firestore": {"port": 8085}, "ui": {"port": 4000}}}), encoding="utf-8")
    assert flutter.ports_emulateurs(fb, ["auth", "firestore"]) == {"firestore": 8085, "auth": 9099}


def test_build_reutilise_par_temoin(tmp_path):
    p = projet(tmp_path / "app")
    f = flutter.apk(p)
    f.parent.mkdir(parents=True)
    f.write_bytes(b"apk")
    (p / ".correction-build").write_text(json.dumps({"release": False, "defines": []}), encoding="utf-8")
    assert flutter.construire_apk(p).etape == "build déjà fait"


def test_port_ouvert():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        assert flutter.port_ouvert(s.getsockname()[1])


def test_package_et_activite(tmp_path):
    a = projet(tmp_path / "app") / "android"
    (a / "app" / "build.gradle.kts").write_text('namespace = "com.ns"\napplicationId = "com.exemple.app"\n', encoding="utf-8")
    (a / "app" / "src" / "main" / "AndroidManifest.xml").write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android"><application>'
        '<activity android:name=".MainActivity"><intent-filter>'
        '<action android:name="android.intent.action.MAIN"/><category android:name="android.intent.category.LAUNCHER"/>'
        "</intent-filter></activity></application></manifest>", encoding="utf-8")
    assert android.package_et_activite(a) == ("com.exemple.app", ".MainActivity")


def test_examen(tmp_path):
    (tmp_path / "question_3.py").write_text("print('Résultat : 2')\n", encoding="utf-8")
    (tmp_path / "question_4.py").write_text("while True: pass\n", encoding="utf-8")
    ex = Examen("T", {3: ("critère",)}, {3: "Résultat : 2"}, {3: 2, 4: 2}, colonnes=((3,), (4,)))
    q = ex.executer(tmp_path, 3)
    assert q[3][0]["identical"] and not q[3][0]["crashed"]
    assert q[4][0]["crashed"] and "délai" in q[4][0]["status"]


def test_statut_de_question(tmp_path):
    f = tmp_path / "question_5.py"
    f.write_text("raise SystemExit(3)\n", encoding="utf-8")
    assert executer_question(f, 5)[2] == "Code de retour : 3"
