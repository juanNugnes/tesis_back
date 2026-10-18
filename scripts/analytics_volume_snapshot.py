"""Respaldo/verificacion de un volumen detenido, ejecutado en un helper Docker.

Montar /state y /backup. backup/verify requieren /state de solo lectura;
restore requiere un volumen nuevo y vacio. No imprime contenidos del volumen.
"""

import argparse
import hashlib
import json
from pathlib import Path
import tarfile


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else hashlib.sha256(stream.read()).hexdigest()


def inventory(root):
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise RuntimeError("No se admiten enlaces en este respaldo")
        stat = path.stat()
        entry = {"mode": stat.st_mode & 0o7777, "uid": stat.st_uid, "gid": stat.st_gid}
        if path.is_file():
            entry.update(size=stat.st_size, sha256=digest(path))
        elif not path.is_dir():
            raise RuntimeError("Tipo de archivo no admitido")
        result[path.relative_to(root).as_posix()] = entry
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("backup", "verify", "restore", "fingerprint"))
    args = parser.parse_args()
    root, backup = Path("/state"), Path("/backup")
    archive, manifest = backup / "historical.tgz", backup / "historical-manifest.json"
    if args.phase == "fingerprint":
        print(json.dumps(inventory(root), sort_keys=True))
        return
    if args.phase == "backup":
        if archive.exists() or manifest.exists():
            raise RuntimeError("Respaldo ya existente")
        before = inventory(root)
        with tarfile.open(archive, "x:gz") as tar:
            tar.add(root, arcname=".")
        if inventory(root) != before:
            raise RuntimeError("El origen cambio durante el respaldo")
        with manifest.open("x", encoding="utf-8") as stream:
            json.dump({"archive_sha256": digest(archive), "entries": before}, stream, indent=2)
    saved = json.loads(manifest.read_text(encoding="utf-8"))
    if digest(archive) != saved["archive_sha256"]:
        raise RuntimeError("Hash del archivo de respaldo incorrecto")
    if args.phase == "restore":
        if any(root.iterdir()):
            raise RuntimeError("La restauracion exige un volumen vacio")
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar.getmembers():
                target = (root / member.name).resolve()
                if not target.is_relative_to(root.resolve()) or not (member.isfile() or member.isdir()):
                    raise RuntimeError("Entrada de archivo no admitida")
            tar.extractall(root)
    elif args.phase in ("backup", "verify"):
        with tarfile.open(archive, "r:gz") as tar:
            archived = {}
            for member in tar.getmembers():
                if member.isfile():
                    archived[member.name.removeprefix("./")] = hashlib.sha256(tar.extractfile(member).read()).hexdigest()
            expected = {name: entry["sha256"] for name, entry in saved["entries"].items() if "sha256" in entry}
            if archived != expected:
                raise RuntimeError("El contenido del archivo no coincide con el manifiesto")
    if inventory(root) != saved["entries"]:
        raise RuntimeError("El volumen no coincide con el respaldo")
    print(json.dumps({"phase": args.phase, "verified": True, "entries": len(saved["entries"]), "archive_sha256": saved["archive_sha256"]}))


if __name__ == "__main__":
    main()
