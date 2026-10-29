import hashlib
import json
import re
import threading
from functools import lru_cache
from pathlib import Path

INFERENCE_LOCK = threading.RLock()


@lru_cache(maxsize=4)
def load_model(directory: str, revision: str):
    import ctranslate2
    import sentencepiece

    folder = Path(directory)
    tokenizer = sentencepiece.SentencePieceProcessor(model_file=str(folder / "sentencepiece.model"))
    translator = ctranslate2.Translator(
        str(folder / "model"), device="cpu", compute_type="int8", inter_threads=1, intra_threads=4
    )
    metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    return tokenizer, translator, metadata.get("target_prefix", "")


class LocalTranslationProvider:
    """CPU-only inference using explicitly installed Argos SentencePiece/CT2 packages."""

    def __init__(self, model_dir: Path):
        self.model_dir = model_dir.resolve()
        manifest = self.model_dir / "manifest.json"
        self.revision = (
            hashlib.sha256(manifest.read_bytes()).hexdigest()[:16]
            if (manifest.is_file())
            else "missing"
        )
        self.name = f"local-argos-v2:{self.model_dir}:{self.revision}"

    def translate(self, text: str, target: str) -> str:
        from catia_manual.core.translation import GLOSSARY

        target = "zh" if target.lower().startswith("zh") else target
        source = "zh" if target == "en" else "en"
        if (source, target) not in {("en", "zh"), ("zh", "en")}:
            raise ValueError("本机模型支持英文与中文互译")
        term = text.strip().lower()
        if target == "zh":
            variants = [term]
            if term.endswith("s"):
                variants.append(term[:-1])
            if term.endswith("es"):
                variants.append(term[:-2])
            for variant in variants:
                if variant in GLOSSARY:
                    return GLOSSARY[variant]
        folder = self.model_dir / f"{source}_{target}"
        if not (folder / "model" / "model.bin").is_file():
            raise FileNotFoundError(
                "未找到离线模型，请在设置中选择模型目录，或切换到其他翻译服务。"
            )
        with INFERENCE_LOCK:
            tokenizer, translator, prefix = load_model(str(folder), self.revision)
            paragraphs = []
            for paragraph in text.splitlines():
                if not paragraph.strip():
                    paragraphs.append("")
                    continue
                sentences = re.split(r"(?<=[.!?。！？])\s+", paragraph.strip())
                batches = []
                for sentence in sentences:
                    tokens = tokenizer.encode(sentence, out_type=str)
                    batches.extend(
                        tokens[start : start + 160] for start in range(0, len(tokens), 160)
                    )
                results = translator.translate_batch(
                    batches,
                    target_prefix=[[prefix]] * len(batches) if prefix else None,
                    beam_size=4,
                    replace_unknowns=True,
                    max_decoding_length=512,
                    length_penalty=0.2,
                )
                translated = []
                for result in results:
                    value = tokenizer.decode(result.hypotheses[0]).replace("▁", " ")
                    if prefix and value.startswith(prefix):
                        value = value[len(prefix) :]
                    translated.append(value.strip())
                paragraphs.append(("" if target == "zh" else " ").join(translated))
            return "\n".join(paragraphs)
