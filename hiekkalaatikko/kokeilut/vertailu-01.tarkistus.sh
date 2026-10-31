#!/usr/bin/env bash
# Ajetaan kloonissa. aja-kokeilu.sh on juuri kirjoittanut testitiedoston uudelleen isannalta (agentin mahdollinen muokkaus ei vaikuta).
set -eu
cd /root/kokeilu
python3 -m unittest test_tehtava
