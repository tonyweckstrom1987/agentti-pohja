"""CHANGELOG.md:n rinnakkaiset lisaykset eivat saa konfliktoida (.gitattributes: merge=union)."""
import subprocess
import tempfile
import unittest
from pathlib import Path

JUURI = Path(__file__).resolve().parent.parent


def git(kansio, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=kansio, capture_output=True, text=True,
    )


class ChangelogUnionTesti(unittest.TestCase):
    def test_rinnakkaiset_rivit_sailyvat_ilman_konfliktia(self):
        with tempfile.TemporaryDirectory() as tmp:
            k = Path(tmp)
            git(k, "init", "-q", "-b", "main")
            (k / ".gitattributes").write_bytes((JUURI / ".gitattributes").read_bytes())
            loki = k / "CHANGELOG.md"
            loki.write_text("## Unreleased\n- vanha\n", encoding="utf-8")
            git(k, "add", ".")
            git(k, "commit", "-qm", "alku")
            git(k, "checkout", "-qb", "pr")
            loki.write_text("## Unreleased\n- pr-rivi\n- vanha\n", encoding="utf-8")
            git(k, "commit", "-qam", "pr")
            git(k, "checkout", "-q", "main")
            loki.write_text("## Unreleased\n- main-rivi\n- vanha\n", encoding="utf-8")
            git(k, "commit", "-qam", "main")

            tulos = git(k, "merge", "--no-edit", "pr")

            self.assertEqual(tulos.returncode, 0, tulos.stdout + tulos.stderr)
            sisalto = loki.read_text(encoding="utf-8")
            for rivi in ("- pr-rivi", "- main-rivi", "- vanha"):
                self.assertIn(rivi, sisalto)
            self.assertNotIn("<<<<<<<", sisalto)


if __name__ == "__main__":
    unittest.main()
