"""Snapshot-asiakas: pyytaa Proxmox-isantaa ottamaan CT115:n snapshotin (pakotettu ssh-komento).

Ajetaan CT115:ssa. Isannan /root/snapshot-kutsu.py sallii vain 'tila' (vain luku), 'listaa' ja 'snapshot ennen-<nimi>'.

  python3 -m agentti.snapshot_kutsu listaa
  python3 -m agentti.snapshot_kutsu ota ennen-halytin

Asetukset: SNAPSHOT_HOST (oletus 192.168.1.114), SNAPSHOT_KEY (oletus /root/.ssh/snapshot_key),
SNAPSHOT_USER (oletus root). Ei salaisuuksia: vain avaintiedoston polku.
"""
import json
import os
import re
import subprocess
import sys

NIMI_RE = re.compile(r"^ennen-[a-z0-9][a-z0-9-]{1,40}$")


class SnapshotVirhe(Exception):
    pass


def _ssh_perus():
    avain = os.environ.get("SNAPSHOT_KEY", "/root/.ssh/snapshot_key")
    isanta = os.environ.get("SNAPSHOT_HOST", "192.168.1.114")
    kayttaja = os.environ.get("SNAPSHOT_USER", "root")
    return ["ssh", "-i", avain, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "-o", "IdentitiesOnly=yes", "%s@%s" % (kayttaja, isanta)]


def _suorita(komento_osa, aikaraja=330):
    try:
        r = subprocess.run(_ssh_perus() + [komento_osa], capture_output=True, timeout=aikaraja)
    except subprocess.TimeoutExpired:
        raise SnapshotVirhe("snapshot-pyynto ylitti aikarajan (%d s)" % aikaraja)
    ulos = r.stdout.decode("utf-8", "replace")
    try:
        tulos = json.loads(ulos)
    except ValueError:
        raise SnapshotVirhe("ei JSON-vastausta (koodi %s): %s" % (
            r.returncode, (r.stderr.decode("utf-8", "replace").strip() or ulos)[-300:]))
    if not isinstance(tulos, dict):
        raise SnapshotVirhe("odottamaton vastaus")
    return tulos


def listaa():
    return _suorita("listaa")


def tila():
    """Isannan ja konttien tila tekstina (vain luku, isannan pakotettu komento 'tila')."""
    tulos = _suorita("tila", aikaraja=180)
    if not tulos.get("ok"):
        raise SnapshotVirhe(str(tulos.get("virhe", "?")))
    return str(tulos.get("tila", ""))


def ota(nimi):
    if not NIMI_RE.match(nimi):
        raise SnapshotVirhe("nimen pitaa olla muotoa ennen-<a-z0-9-> (8-47 merkkia)")
    return _suorita("snapshot " + nimi)


def main(argv=None):
    a = sys.argv[1:] if argv is None else argv
    try:
        if a == ["tila"]:
            print(tila())
            return 0
        if a == ["listaa"]:
            tulos = listaa()
        elif len(a) == 2 and a[0] == "ota":
            tulos = ota(a[1])
        else:
            print("kaytto: python -m agentti.snapshot_kutsu listaa | ota ennen-<nimi>")
            return 2
    except SnapshotVirhe as e:
        print("VIRHE: " + str(e))
        return 1
    if not tulos.get("ok"):
        print("EI ONNISTUNUT: " + str(tulos.get("virhe", "?")))
        return 1
    print(("snapshot otettu: " + tulos["snapshot"]) if "snapshot" in tulos
          else "snapshotit: " + ", ".join(tulos.get("snapshotit", [])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
