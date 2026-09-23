r"""Convert the Bangla speech model to ONNX so EchoInk can run it without NeMo.

EchoInk runs every speech model through ONNX Runtime and has no PyTorch or NeMo
dependency. The Bangla model (speaklar/speaklar_stt_bn_fastconformer, a NeMo
FastConformer CTC checkpoint) is only published in NeMo format, so it has to be
converted once, in an environment that has NeMo. Docker is the easiest way; from
the repository root:

    docker run --rm -v "<models dir>:/models" -v "$PWD/scripts:/scripts:ro" python:3.11 \
        bash -c "pip install torch==2.7.1 --index-url https://download.pytorch.org/whl/cpu \
            && pip install 'nemo_toolkit[asr]==3.0.0' torch==2.7.1 onnxruntime \
            && python /scripts/export_bangla_model.py \
                --out /models/speaklar-bn-fastconformer-onnx"

<models dir> is %APPDATA%\echoink\models on Windows and ~/.config/echoink/models
elsewhere. EchoInk loads the model on its next start.
"""

import argparse
import json
from pathlib import Path

REPO_ID = "speaklar/speaklar_stt_bn_fastconformer"
REVISION = "8773966e4a0b6389be318e42ebc84b957c71067e"
CHECKPOINT = "speaklar_stt_bn_fastconformer.nemo"

NOTICE = f"""\
Bangla speech model: https://huggingface.co/{REPO_ID} (revision {REVISION})
Author: Munzur ul Mamun (speaklar.com)
License: CC BY-NC 4.0 (non-commercial use only), separately from EchoInk's MIT code.
Converted from the NeMo checkpoint to ONNX by EchoInk's scripts/export_bangla_model.py;
the weights are unchanged.
"""


def _load_wav_16k(path: str):
    import librosa

    audio, _ = librosa.load(path, sr=16000, mono=True)
    return audio


def _verify(model, onnx_path: Path, wav_path: str) -> None:
    """Check that the ONNX graph decodes a clip exactly like NeMo does."""
    import onnxruntime as ort
    import torch

    audio = _load_wav_16k(wav_path)
    reference = model.transcribe([wav_path], verbose=False)[0]
    reference = getattr(reference, "text", reference)

    with torch.no_grad():
        features, lengths = model.preprocessor(
            input_signal=torch.from_numpy(audio)[None], length=torch.tensor([len(audio)])
        )
    session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    (logprobs,) = session.run(
        ["logprobs"], {"audio_signal": features.numpy(), "length": lengths.numpy()}
    )
    blank = logprobs.shape[-1] - 1
    ids, previous = [], blank
    for token in logprobs[0].argmax(axis=-1).tolist():
        if token != previous and token != blank:
            ids.append(token)
        previous = token
    exported = model.tokenizer.ids_to_text(ids)

    print(f"NeMo: {reference}")
    print(f"ONNX: {exported}")
    if reference.strip() != exported.strip():
        raise SystemExit("ONNX export does not match NeMo output")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, help="output directory for the ONNX model")
    parser.add_argument("--nemo", help="local .nemo file (default: download the pinned revision)")
    parser.add_argument("--verify-wav", help="optional clip to compare NeMo and ONNX output on")
    args = parser.parse_args()

    import nemo.collections.asr as nemo_asr
    import torch
    from huggingface_hub import hf_hub_download

    checkpoint = args.nemo or hf_hub_download(REPO_ID, CHECKPOINT, revision=REVISION, token=False)
    model = nemo_asr.models.ASRModel.restore_from(checkpoint, map_location=torch.device("cpu"))
    model.eval()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    onnx_path = out / "model.onnx"
    model.export(str(onnx_path))

    # onnx-asr vocabulary format: "<token> <id>" per line, CTC blank last.
    vocab = [*model.tokenizer.vocab, "<blk>"]
    with open(out / "vocab.txt", "w", encoding="utf-8") as f:
        f.writelines(f"{token} {i}\n" for i, token in enumerate(vocab))

    config = {
        "model_type": "nemo-conformer-ctc",
        "features_size": int(model.cfg.preprocessor.features),
        "subsampling_factor": int(model.cfg.encoder.subsampling_factor),
    }
    (out / "config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    (out / "NOTICE.md").write_text(NOTICE, encoding="utf-8")

    if args.verify_wav:
        _verify(model, onnx_path, args.verify_wav)
    print(f"Exported {REPO_ID} to {out}")


if __name__ == "__main__":
    main()
