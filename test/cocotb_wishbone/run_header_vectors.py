"""Run real Wishbone/OpenEye Cocotb vectors from the supplied C headers.

Only writes to test/cocotb_wishbone/.sim_build/. Does not alter original RTL.
This script expects the existing, previously smoke-tested run.py on Amir branch.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HEADERS = HERE / "vector_headers"


def scalar(text: str, name: str) -> int:
    match = re.search(rf"\bint\s+{re.escape(name)}\s*=\s*(\d+)\s*;", text)
    if not match:
        raise ValueError(f"Cannot find integer declaration: {name}")
    return int(match.group(1))


def int_array(text: str, name: str) -> list[int]:
    match = re.search(
        rf"\bint\s+{re.escape(name)}\s*\[\s*(\d+)\s*\]\s*=\s*\{{([^}}]*)\}}\s*;",
        text,
        flags=re.DOTALL,
    )
    if not match:
        raise ValueError(f"Cannot find integer array: {name}")
    values = [int(piece.strip(), 0) for piece in match.group(2).split(",") if piece.strip()]
    if len(values) != int(match.group(1)):
        raise ValueError(f"Length mismatch in {name}")
    return values


def byte_array(text: str, name: str) -> bytes:
    match = re.search(
        rf"\b(?:u8|uint8_t|unsigned\s+char)\s+{re.escape(name)}\s*\[\s*(\d+)\s*\]\s*=\s*\{{([^}}]*)\}}\s*;",
        text,
        flags=re.DOTALL,
    )
    if not match:
        raise ValueError(f"Cannot find byte array: {name}")
    parts = [piece.strip() for piece in match.group(2).split(",") if piece.strip()]
    if not all(re.fullmatch(r"0[xX][0-9a-fA-F]{1,2}", p) for p in parts):
        raise ValueError(f"Unexpected byte syntax in {name}")
    result = bytes(int(part, 16) for part in parts)
    if len(result) != int(match.group(1)):
        raise ValueError(f"Byte count mismatch for {name}: declared {match.group(1)}, found {len(result)}")
    if len(result) % 8:
        raise ValueError(f"{name} length is not a multiple of 8 bytes")
    return result


def parameters(text: str) -> dict[str, int]:
    return {key: int(value) for key, value in re.findall(r"\bparameter\s+(\w+)\s*=\s*(\d+)\s*,", text)}


def words_little_endian(data: bytes):
    for index in range(0, len(data), 8):
        yield int.from_bytes(data[index : index + 8], "little")


def prepare() -> tuple[Path, Path, int, int]:
    # Only the current branch's own generator is used; no borrowed RTL/header copies.
    from run import generate_isolated_config

    ih = (HEADERS / "dma_stream.h").read_text(encoding="utf-8")
    oh = (HEADERS / "dma_stream_out.h").read_text(encoding="utf-8")
    ph = (HEADERS / "parameters.vh").read_text(encoding="utf-8")

    num_layers = scalar(ih, "n_layers")
    lengths = int_array(ih, "dma_input_stream_lengths")
    input_bytes = byte_array(ih, "dma_input_stream")
    ref_bytes = byte_array(oh, "dma_output_reference")
    out_length = scalar(oh, "dma_output_length")
    if num_layers != 1 or len(lengths) != 1:
        raise ValueError("This script expects a single layer; multi-layer requires a different protocol driver")
    if sum(lengths) != len(input_bytes) or out_length != len(ref_bytes):
        raise ValueError("Declared DMA byte lengths do not match the C arrays")

    generated = (HERE / ".sim_build" / "header_vectors" / "generated").resolve()
    generate_isolated_config(generated)
    wanted = parameters(ph)
    created = parameters((generated / "parameters.vh").read_text(encoding="utf-8"))
    required = {"CLUSTER_ROWS", "NUM_GLB_IACT", "NUM_GLB_PSUM", "NUM_GLB_WGHT", "RAM_CELLS", "BRANCHES"}
    if set(wanted) != required or {key: created.get(key) for key in required} != wanted:
        raise ValueError(f"Provided parameters.vh differs from branch-generated parameters: supplied={wanted}, generated={created}")

    vector_dir = generated / "demo" / "layer_0_0"
    vector_dir.mkdir(parents=True, exist_ok=True)
    input_words = list(words_little_endian(input_bytes))
    reference_words = list(words_little_endian(ref_bytes))
    (vector_dir / "dma_stream_input.txt").write_text(
        "".join(f"{w}\n" for w in input_words), encoding="utf-8"
    )
    (vector_dir / "dma_stream_ref.txt").write_text(
        "".join(f"{w:064b}\n" for w in reference_words), encoding="utf-8"
    )
    print(f"[OK] C headers converted: {len(input_words)} input and {len(reference_words)} reference words (64 bits)", flush=True)
    print(f"[OK] Uploaded parameters.vh matches all six generated parameters on this branch", flush=True)
    print("[NOTE] Matching parameters alone does not prove that the C headers share the same RTL/regmap revision.", flush=True)
    return generated, vector_dir, len(input_words), len(reference_words)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true", help="Validate headers and stage vectors without simulation")
    parser.add_argument("--sim", choices=("icarus", "verilator"), default="icarus")
    parser.add_argument("--step-limit", type=int, default=250000)
    args = parser.parse_args()
    generated, vectors, _nin, _nout = prepare()
    if args.prepare_only:
        print("[OK] Preparation successful (no simulation launched)")
        return
    cmd = [
        sys.executable,
        str(HERE / "run.py"),
        "--mode", "real-vectors",
        "--generated", str(generated),
        "--vectors", str(vectors),
        "--sim", args.sim,
        "--step-limit", str(args.step_limit),
    ]
    print("[RUN] Starting full OpenEye computation via Wishbone", flush=True)
    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
