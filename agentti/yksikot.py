"""Yhteinen systemd-failed-listan kohinasuodatus (vain luku, ei ajeta komentoja)."""

# LXC-konteissa nama mountit ovat aina failed-tilassa (kohinaa, ei vika)
KOHINA = ("dev-mqueue.mount", "run-lock.mount", "tmp.mount")


def suodata_kohina(teksti):
    """Poistaa `systemctl list-units --failed` -tulosteesta tunnetut LXC-kohinarivit."""
    rivit = [r for r in teksti.splitlines() if not any(k in r for k in KOHINA)]
    return "\n".join(rivit).strip()
