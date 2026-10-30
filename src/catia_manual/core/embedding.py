"""Pinned CPU embedding model. No Qt, Torch, remote inference or implicit downloads."""

import hashlib
import json
import threading
from pathlib import Path

from catia_manual.core.tasks import exclusive_file

MODEL_ID = "multilingual-e5-small-int8"
REVISION = "761b726dd34fb83930e26aab4e9ac3899aa1fa78"
REPOSITORY = "Xenova/multilingual-e5-small"
FILES = {
    "onnx/model_quantized.onnx": (
        118308185,
        "f80102d3f2a1229f387d3c81909990d8945513e347b0eab049f7de3c6f98c193",
    ),
    "tokenizer.json": (
        17082730,
        "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39",
    ),
}
SPEC = {
    "schema_version": 1,
    "model": MODEL_ID,
    "repository": REPOSITORY,
    "original_model": "intfloat/multilingual-e5-small",
    "license": "MIT",
    "revision": REVISION,
    "dimension": 384,
    "pooling": "attention-mean-l2",
    "max_tokens": 512,
    "query_prefix": "query: ",
    "passage_prefix": "passage: ",
    "files": FILES,
}
FINGERPRINT = hashlib.sha256(json.dumps(SPEC, sort_keys=True).encode()).hexdigest()


class Cancelled(Exception):
    pass


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled("任务已暂停，可继续")


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def model_status(directory, *, verify=False):
    directory = Path(directory)
    for name, (size, digest) in FILES.items():
        path = directory / name
        if not path.is_file() or path.stat().st_size != size:
            return {"ready": False, "reason": "请下载模型或选择完整模型目录"}
        if verify and file_hash(path) != digest:
            return {"ready": False, "reason": "模型校验失败，请重新下载"}
    return {"ready": True, "reason": "模型文件已就绪", "fingerprint": FINGERPRINT}


def download_model(directory, *, progress=None, cancel=None, client=None):
    """Explicit download; resume .part files, validate bytes, publish each file atomically."""
    import httpx

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / ".install.lock"
    with exclusive_file(lock, "模型正在安装，请等待当前任务完成"):
        session = client or httpx.Client(follow_redirects=True, timeout=60)
        try:
            total, complete = sum(v[0] for v in FILES.values()), 0
            for name, (size, digest) in FILES.items():
                check_cancel(cancel)
                path = directory / name
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.is_file() and path.stat().st_size == size and file_hash(path) == digest:
                    complete += size
                    continue
                partial = path.with_suffix(path.suffix + ".part")
                offset = partial.stat().st_size if partial.exists() else 0
                if offset > size:
                    partial.unlink()
                    offset = 0
                if offset < size:
                    headers = {"Range": f"bytes={offset}-"} if offset else {}
                    url = f"https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/{name}"
                    with session.stream("GET", url, headers=headers) as response:
                        response.raise_for_status()
                        if response.status_code == 206:
                            if not response.headers.get("Content-Range", "").startswith(
                                f"bytes {offset}-"
                            ):
                                raise ValueError("下载续传范围不一致，请重试")
                        else:
                            offset = 0
                        with partial.open("ab" if offset else "wb") as stream:
                            for chunk in response.iter_bytes(256 * 1024):
                                check_cancel(cancel)
                                offset += len(chunk)
                                if offset > size:
                                    raise ValueError("下载文件超过预期大小")
                                stream.write(chunk)
                                if progress:
                                    progress({"processed": complete + offset, "total": total})
                if partial.stat().st_size != size or file_hash(partial) != digest:
                    partial.unlink(missing_ok=True)
                    raise ValueError("下载文件校验失败，请重试")
                partial.replace(path)
                complete += size
            (directory / "model-info.json").write_text(
                json.dumps(SPEC, ensure_ascii=False, indent=2), "utf-8"
            )
            if progress:
                progress({"processed": total, "total": total})
        finally:
            if client is None:
                session.close()
    return model_status(directory, verify=True)


class E5Embedder:
    fingerprint = FINGERPRINT
    dimension = 384
    spec = SPEC

    def __init__(self, directory, threads=2):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        directory = Path(directory)
        status = model_status(directory, verify=True)
        if not status["ready"]:
            raise ValueError(status["reason"])
        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, min(int(threads), 8))
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(directory / "onnx/model_quantized.onnx"),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self.tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=512)
        self.tokenizer.enable_padding(pad_id=1, pad_token="<pad>")
        self.lock = threading.Lock()

    def encode(self, texts, *, query=False):
        import numpy as np

        if not texts:
            return np.empty((0, self.dimension), dtype=np.float32)
        prefix = "query: " if query else "passage: "
        with self.lock:
            tokens = self.tokenizer.encode_batch([prefix + text for text in texts])
            ids = np.asarray([t.ids for t in tokens], dtype=np.int64)
            mask = np.asarray([t.attention_mask for t in tokens], dtype=np.int64)
            inputs = {"input_ids": ids, "attention_mask": mask, "token_type_ids": ids * 0}
            hidden = self.session.run(
                None, {value.name: inputs[value.name] for value in self.session.get_inputs()}
            )[0]
        vectors = (hidden * mask[..., None]).sum(1) / mask.sum(1)[:, None]
        vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
        if vectors.shape != (len(texts), self.dimension) or not np.isfinite(vectors).all():
            raise ValueError("模型未生成有效向量")
        return vectors.astype(np.float32)


_models = {}
_model_lock = threading.Lock()


def make_embedder(config):
    directory = Path(config.get("model_dir", "data/search-models/e5-small")).resolve()
    stamp = tuple(
        (directory / name).stat().st_mtime_ns if (directory / name).is_file() else 0
        for name in FILES
    )
    key = (str(directory), int(config.get("threads", 2)), stamp)
    with _model_lock:
        if key not in _models:
            model = E5Embedder(directory, key[1])
            _models.clear()
            _models[key] = model
        return _models[key]
