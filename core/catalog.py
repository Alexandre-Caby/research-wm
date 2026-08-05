"""Seed catalog: the hand-curated models, transcribed from the README.

Open discovery ingests far beyond this list. The catalog exists to keep the
provenance trail — which models were audited, which were excluded and why.
"""

import re

CATALOG = "catalog"
UNDECIDED = "undecided"
WATCH = "watch"
EXCLUDED = "excluded"

CATALOG_SEED = (
    {"model_id": "abot-world", "display_name": "ABot-World", "org": "amap-cvlab",
     "repo_full_name": "amap-cvlab/ABot-World", "catalog_status": CATALOG,
     "note": "existence confirmed, pass of 2026-07-18"},
    {"model_id": "alaya-world", "display_name": "AlayaWorld", "org": "AlayaLab",
     "repo_full_name": "AlayaLab/AlayaWorld", "catalog_status": CATALOG,
     "note": "existence confirmed, pass of 2026-07-18"},
    {"model_id": "dreamx-world", "display_name": "DreamX-World", "org": "AMAP-ML",
     "repo_full_name": "AMAP-ML/DreamX-World", "catalog_status": CATALOG,
     "note": "existence confirmed, pass of 2026-07-18"},
    {"model_id": "hy-world", "display_name": "HY-World", "org": "Tencent-Hunyuan",
     "repo_full_name": "Tencent-Hunyuan/HY-World", "catalog_status": CATALOG,
     "note": "never pin a version: releases already move past 2.0"},
    {"model_id": "gamma-world", "display_name": "Gamma-World", "org": "nv-tlabs",
     "repo_full_name": "nv-tlabs/Gamma-World", "catalog_status": CATALOG, "note": ""},
    {"model_id": "pid", "display_name": "PiD", "org": "nv-tlabs",
     "repo_full_name": "nv-tlabs/PiD", "catalog_status": CATALOG, "note": ""},
    {"model_id": "physforge", "display_name": "PhysForge", "org": "HKU-MMLab",
     "repo_full_name": "HKU-MMLab/PhysForge", "catalog_status": CATALOG,
     "note": "ICML 2026"},
    {"model_id": "cosmos", "display_name": "NVIDIA Cosmos", "org": "nvidia-cosmos",
     "repo_full_name": "nvidia-cosmos/cosmos-predict2.5", "catalog_status": CATALOG,
     "note": "cookbook repositories in the same org are collected by the org scan"},
    {"model_id": "v-jepa-2", "display_name": "V-JEPA 2", "org": "facebookresearch",
     "repo_full_name": "facebookresearch/jepa", "catalog_status": CATALOG,
     "note": "added 2026-08-01, never audited individually"},
    {"model_id": "dreamer-v3", "display_name": "DreamerV3", "org": "danijar",
     "repo_full_name": "danijar/dreamerv3", "catalog_status": CATALOG,
     "note": "added 2026-08-01, carried from the original CDC, never re-evaluated"},
    {"model_id": "dreamer-v3-torch", "display_name": "DreamerV3 (PyTorch port)",
     "org": "NM512", "repo_full_name": "NM512/dreamerv3-torch",
     "catalog_status": CATALOG, "note": "unformalized training-sourcing backlog"},
    {"model_id": "commavq", "display_name": "commaVQ", "org": "commaai",
     "repo_full_name": "commaai/commavq", "catalog_status": CATALOG,
     "note": "unformalized training-sourcing backlog"},
    {"model_id": "genesis", "display_name": "Genesis", "org": "Genesis-Embodied-AI",
     "repo_full_name": "Genesis-Embodied-AI/Genesis", "catalog_status": UNDECIDED,
     "note": "in the original CDC, dropped from the live list with no recorded reason"},
    {"model_id": "gymnasium", "display_name": "Gymnasium", "org": "Farama-Foundation",
     "repo_full_name": "Farama-Foundation/Gymnasium", "catalog_status": UNDECIDED,
     "note": "in the original CDC, dropped from the live list with no recorded reason"},
    {"model_id": "reactive-gwm", "display_name": "ReactiveGWM", "org": "INV-WZQ",
     "repo_full_name": "INV-WZQ/ReactiveGWM", "catalog_status": EXCLUDED,
     "note": "CC BY-NC 4.0 plus a Capcom ROM dependency; excluded by name"},
)

WATCH_ONLY = (
    "Google Genie", "Panoworld", "Arbord 3D", "Tri-splat", "Perception DLM",
    "AlphaEvolve", "Seedance 2.5", "Scope", "Omni Contact",
)

_WATCH_NOTE = "closed or not self-hostable: literature only, no code to collect"

WATCH_SEED = tuple(
    {"model_id": re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"),
     "display_name": name, "org": None, "repo_full_name": None,
     "catalog_status": WATCH, "note": _WATCH_NOTE}
    for name in WATCH_ONLY
)


SCAN_ORGS = tuple(dict.fromkeys(
    entry["org"] for entry in CATALOG_SEED
    if entry["org"] and entry["catalog_status"] != EXCLUDED
))

EXCLUDED_REPOS = frozenset(
    entry["repo_full_name"] for entry in CATALOG_SEED
    if entry["catalog_status"] == EXCLUDED
)
