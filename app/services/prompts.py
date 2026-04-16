"""Loader for prompt templates from the `prompts/` directory."""

from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


def load_prompt(name: str) -> str:
    """Read a prompt file from prompts/ directory. Accepts name with or without extension."""
    candidates = [PROMPTS_DIR / name]
    if "." not in name:
        candidates += [PROMPTS_DIR / f"{name}.md", PROMPTS_DIR / f"{name}.txt"]
    for path in candidates:
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    raise FileNotFoundError(f"Prompt not found: {name} (looked in {PROMPTS_DIR})")
