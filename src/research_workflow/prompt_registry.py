from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .assets import asset_file


DEFAULT_PROMPT_PATH = asset_file("docs/claim_verification_prompts.md")


@dataclass(slots=True)
class PromptTemplate:
    name: str
    text: str

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "text": self.text}


def load_claim_verification_prompts(path: str | Path = DEFAULT_PROMPT_PATH) -> dict[str, PromptTemplate]:
    text = Path(path).read_text(encoding="utf-8")
    prompts: dict[str, PromptTemplate] = {}
    current_name = ""
    current_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if current_name:
                prompts[current_name] = PromptTemplate(current_name, "\n".join(current_lines).strip())
            current_name = line[3:].strip()
            current_lines = []
            continue
        if current_name:
            current_lines.append(line)
    if current_name:
        prompts[current_name] = PromptTemplate(current_name, "\n".join(current_lines).strip())
    return prompts


def get_claim_verification_prompt(name: str, path: str | Path = DEFAULT_PROMPT_PATH) -> str:
    prompts = load_claim_verification_prompts(path)
    if name not in prompts:
        available = ", ".join(sorted(prompts))
        raise KeyError(f"Unknown claim verification prompt: {name}. Available: {available}")
    return prompts[name].text
