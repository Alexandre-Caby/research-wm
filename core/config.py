import os

_ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
TOPIC = os.path.basename(_ROOT_DIR).replace("research-", "")

STORAGE_DIR = os.path.join(_ROOT_DIR, "storage")
RAW_DIR = os.path.join(STORAGE_DIR, "1_raw_data")
REGISTRY_DIR = os.path.join(STORAGE_DIR, "2_register_data")
EXP_DIR = os.path.join(STORAGE_DIR, "3_exploitable_data")
VECTOR_DIR = os.path.join(STORAGE_DIR, "4_vector_data")

PAPERS_DIR = os.path.join(EXP_DIR, "papers")
CODE_DIR = os.path.join(EXP_DIR, "code")

DB_PATH = os.path.join(REGISTRY_DIR, f"{TOPIC}_registry.db")
LANCE_DIR = os.path.join(VECTOR_DIR, "lance")

def ensure_storage_dirs() -> None:
    for path in (STORAGE_DIR, RAW_DIR, REGISTRY_DIR, EXP_DIR, VECTOR_DIR,
                 PAPERS_DIR, CODE_DIR, LANCE_DIR):
        os.makedirs(path, exist_ok=True)

ensure_storage_dirs()


def _escapes_lake(relative: str) -> bool:
    return relative == os.pardir or relative.startswith(os.pardir + os.sep)


def rel_path(path: str | None) -> str | None:
    """Convert to a storage-relative path; raises (C3) instead of silently letting a `..` escape survive a lake move."""
    if not path:
        return None
    relative = (
        os.path.relpath(os.path.abspath(path), os.path.abspath(STORAGE_DIR))
        if os.path.isabs(path)
        else os.path.normpath(path)
    )
    if _escapes_lake(relative):
        raise ValueError(f"path lies outside the lake, cannot be stored: {path!r}")
    return relative


def abs_path(path: str | None) -> str | None:
    """Resolve a storage-relative path against the lake; raises on absolute/escaping input instead of letting `os.path.join` fail the C3 boundary open."""
    if not path:
        return None
    if os.path.isabs(path):
        raise ValueError(f"registry paths are storage-relative, got absolute: {path!r}")
    if _escapes_lake(os.path.normpath(path)):
        raise ValueError(f"path escapes the lake: {path!r}")
    return os.path.join(STORAGE_DIR, path)


DEFAULT_CONTACT_EMAIL = "research@example.org"
EMAIL_CONTACT = os.environ.get("WM_CONTACT_EMAIL", DEFAULT_CONTACT_EMAIL)
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
HF_TOKEN = os.environ.get("HF_TOKEN", "")

TEXT_MODEL = "BAAI/bge-m3"
TEXT_DIM = 1024
EMBED_BATCH = 64

QUALITY_THRESHOLD = 0.6

CHUNK_SIZE_WORDS = 180
CHUNK_OVERLAP_WORDS = 30
MIN_TRAILING_CHUNK_WORDS = 20

WEIGHT_EXTENSIONS = {
    ".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".onnx", ".gguf",
    ".h5", ".msgpack", ".npz", ".tflite", ".engine", ".plan",
    ".pb", ".pkl", ".params", ".pdparams", ".caffemodel",
}


def device() -> str:
    import torch
    return "cuda" if torch.cuda.is_available() else "cpu"


_WM_ANCHOR = '("world model" OR "world models" OR "latent dynamics" OR "video prediction" OR "neural simulator")'

KEYWORD_BLOCKS: dict[str, str] = {
    "world_models": (
        '("world model" OR "world models" OR "learned simulator" OR "neural simulator" '
        'OR "generative world model" OR "interactive world model")'
    ),
    "video_generation": (
        '("video generation" OR "video prediction" OR "video diffusion" '
        'OR "long-horizon video" OR "controllable video synthesis") '
        f'AND {_WM_ANCHOR}'
    ),
    "latent_dynamics": (
        '("latent dynamics" OR "recurrent state space model" OR "RSSM" '
        'OR "latent state space" OR "dynamics model" OR "forward model")'
    ),
    "model_based_rl": (
        '("model-based reinforcement learning" OR "Dreamer" OR "imagination-based planning" '
        'OR "learned dynamics policy" OR "planning in latent space")'
    ),
    "embodied_robotics": (
        '("embodied agent" OR "robot learning" OR "vision-language-action" '
        'OR "manipulation policy" OR "sim-to-real") '
        f'AND {_WM_ANCHOR}'
    ),
    "physics_reasoning": (
        '("physics-informed" OR "intuitive physics" OR "physical reasoning" '
        'OR "physically plausible generation" OR "differentiable simulation")'
    ),
    "tokenizers": (
        '("visual tokenizer" OR "video tokenizer" OR "VQ-VAE" OR "discrete latent" '
        'OR "vector quantization") '
        f'AND {_WM_ANCHOR}'
    ),
    "self_supervised_video": (
        '("JEPA" OR "joint embedding predictive architecture" OR "masked video modeling" '
        'OR "self-supervised video representation")'
    ),
    "autonomous_driving": (
        '("driving world model" OR "autonomous driving simulation" OR "closed-loop simulation" '
        'OR "driving scene generation")'
    ),
    "benchmarks_eval": (
        '("benchmark" OR "evaluation protocol" OR "rollout fidelity" OR "long-horizon consistency") '
        f'AND {_WM_ANCHOR}'
    ),
}

ARXIV_CATEGORIES = ("cs.LG", "cs.CV", "cs.RO", "cs.AI")

GITHUB_TOPIC_QUERIES = (
    "world model",
    "video world model",
    "latent dynamics",
    "model-based reinforcement learning",
    "neural simulator",
    "world simulator",
)

CODE_EXTENSIONS = {
    ".py", ".cpp", ".cc", ".h", ".hpp", ".cu", ".cuh", ".js", ".ts", ".tsx",
    ".rs", ".go", ".java", ".sh", ".yaml", ".yml", ".toml", ".cfg", ".ini",
    ".md", ".rst", ".txt", ".json",
}

EXCLUDE_DIRS = {
    "tests", "test", "third_party", "vendor", "node_modules", "build", "dist",
    "assets", "data", "checkpoints", "__pycache__", ".git", ".github", "docs/_build",
}
EXCLUDE_FILE_PATTERNS = ("_pb2.py", ".min.js", "package-lock.json", "yarn.lock", "poetry.lock")

MAX_TEXT_FILE_BYTES = 1_000_000
MAX_TARBALL_BYTES = 300_000_000

CODE_CHUNK_MAX_LINES = 120
CODE_CHUNK_MIN_LINES = 5
