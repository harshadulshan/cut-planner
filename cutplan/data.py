"""
Benchmark instances, downloaded on first use from pinned GitHub commits and checked by SHA-256.

* Bars: BPPLIB (Delorme, Iori & Martello 2018), github.com/mdelorme2/BPPLIB: Falkenauer, Scholl, Waescher, Schwerin
  and Hard28, 1,615 bin packing instances, every one solved to optimality in the literature. The archives are RAR;
  extracting them needs the optional `libarchive-c` package (and the libarchive library), or extract them yourself
  into data_cache/bpp/<archive name>/.
* Sheets: the CLASS instances of Berkey & Wang (1987) and Martello & Vigo (1998), 500 two-dimensional bin packing
  instances, from github.com/Oscar-Oliveira/OR-Datasets (MIT licence).
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from collections import Counter

from .bars import Bar, BarJob, Piece
from .sheets import Panel, Sheet, SheetJob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "data_cache")
BPPLIB = "https://raw.githubusercontent.com/mdelorme2/BPPLIB/fc3aa784a3b14282fc591d710f056abc1b1451c3/Instances/Benchmarks/"
ARCHIVES = {
    "1_Falkenauer": "e09f6ed02addc00f21db98229c17e1bdc0e42d201a3a799598594057a5a85316",
    "2_Scholl": "b1b0dd2c4f641256a5a5638fc5a5eb0819ac3f2da9baf1be0cad87176e5d1971",
    "3_Wäscher": "61c703fb5a76fa3f74e4be5baad60a1ace57a11a73833843fba33f9575cfeb88",
    "4_Schwerin": "3c7d0a5d291cf842669cbca5d0b5249e6c0ec22476c4e0c9d9dace35571b7d59",
    "5_Hard28": "cee6c3417a5fd34d69da1142898e70720f32a4b37c2e555e9cc07aae83104fec",
}
# optimal number of bins of every instance (BPPLIB Solutions.xlsx: best lower bound = best upper bound)
OPTIMAL = {k: v[1] for k, v in json.load(open(os.path.join(os.path.dirname(__file__), "bpp_optimal.json"))).items()}
CLASS_URL = ("https://raw.githubusercontent.com/Oscar-Oliveira/OR-Datasets/e2e151cf241ab97d860f657cbfe515232e7096d7/"
             "Cutting-and-Packing/2D/Datasets/CLASS/json/")
CLASS_SHA = json.load(open(os.path.join(os.path.dirname(__file__), "class_sha256.json")))
FAMILIES = {"1_Falkenauer": "Falkenauer", "2_Scholl": "Scholl", "3_Wäscher": "Wäscher", "4_Schwerin": "Schwerin",
            "5_Hard28": "Hard28"}


def _download(url, path, sha):
    if not (os.path.exists(path) and os.path.getsize(path) > 0):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with urllib.request.urlopen(url, timeout=120) as r, open(path + ".part", "wb") as f:
            f.write(r.read())
        os.replace(path + ".part", path)
    raw = open(path, "rb").read()
    if hashlib.sha256(raw).hexdigest() != sha:
        os.remove(path)
        raise ValueError(f"{os.path.basename(path)}: the download does not match its checksum")
    return raw


def _extract(archive, folder):
    """Regular files only, by base name (archives are data, not trusted paths)."""
    import libarchive
    os.makedirs(folder, exist_ok=True)
    with libarchive.file_reader(archive) as arc:
        for entry in arc:
            if not entry.isfile:
                continue
            base = os.path.basename(entry.pathname.replace("\\", "/"))
            if base and not base.startswith("."):
                with open(os.path.join(folder, base), "wb") as f:
                    for block in entry.get_blocks():
                        f.write(block)


def bpp_files():
    """{family: [paths]} of the 1,615 bar instances, downloading and extracting them if needed."""
    out = {}
    for name, sha in ARCHIVES.items():
        folder = os.path.join(CACHE, "bpp", name)
        if not (os.path.isdir(folder) and os.listdir(folder)):
            arc = os.path.join(CACHE, "bpp", name + ".rar")
            _download(BPPLIB + urllib.request.quote(name) + ".rar", arc, sha)
            try:
                _extract(arc, folder)
            except ImportError:
                raise RuntimeError("extracting the BPPLIB archives needs `pip install libarchive-c` "
                                   f"(or extract {arc} into {folder} yourself)") from None
        out[FAMILIES[name]] = sorted(os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(".txt"))
    return out


def read_bpp(path) -> BarJob:
    """BPPLIB format: number of items, capacity, one weight per line."""
    v = [int(float(x)) for x in open(path).read().split()]
    c, w = v[1], v[2:2 + v[0]]
    cnt = Counter(w)
    return BarJob([Piece(str(k), float(k), q) for k, q in sorted(cnt.items(), reverse=True)], [Bar("bin", float(c))],
                  name=os.path.basename(path))


def class_instance(k: int) -> SheetJob:
    """CLASS instance k (1-500); panels may not turn (the classic oriented two-dimensional bin packing)."""
    fname = f"{k}.json"
    raw = _download(CLASS_URL + fname, os.path.join(CACHE, "class", fname), CLASS_SHA[fname])
    d = json.loads(raw)
    o = d["Objects"][0]
    panels = [Panel(f"{it['Length']}x{it['Height']}", float(it["Length"]), float(it["Height"]), int(it["Demand"]),
                    rotate=False) for it in d["Items"]]
    return SheetJob(panels, [Sheet("bin", float(o["Length"]), float(o["Height"]))], name=d["Name"])
