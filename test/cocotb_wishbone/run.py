"""Standalone Cocotb runner, invoked from VS Code terminal (not Vivado Tcl)."""
import argparse
import os
import sys
import re
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WRAPPER = ROOT / "fpga" / "hdl" / "openeye_wb_openeye_top.sv"


def fix_generated_port_list(path):
    """Windows-safe fix for the Amir generator's trailing port comma.

    The original generator writes an output-port comma followed by a newline
    and then seeks backwards by two characters. On Windows text-mode CRLF
    translation can leave the comma in place. Only generated build files
    are passed to this function; original OpenEye RTL is never modified.
    """
    source = path.read_text(encoding="utf-8")
    repaired, replacements = re.subn(r",(?=[ \t]*\r?\n[ \t]*\);)", "", source, count=1)
    if replacements:
        path.write_text(repaired, encoding="utf-8", newline="\n")
        print(f"[FIX] Removed trailing port-list comma in generated {path.name}")


def copy_generated_for_sim(source_dir, destination):
    """Copy generated configuration into our own build dir, never patch the source."""
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("parameters.vh", "regmap_params.vh", "dma_storage.v"):
        shutil.copyfile(source_dir / name, destination / name)
    fix_generated_port_list(destination / "dma_storage.v")
    return destination


def generate_isolated_config(target):
    """Use THIS branch's generator; write files exclusively into test build dir."""
    sys.path.insert(0, str(ROOT / "src"))
    from open_eye.generator import create_regmap_params_vh_file
    from open_eye.open_eye_parameters import get_oep

    target.mkdir(parents=True, exist_ok=True)
    # These defaults mirror test/cocotb_fpga/Makefile on the Amir branch.
    defaults = {
        "CLUSTER_ROWS": "2", "CLUSTER_COLUMNS": "2", "NUM_GLB_IACT": "3",
        "NUM_GLB_PSUM": "4", "NUM_GLB_WGHT": "3", "RAM_CELLS": "8",
        "BRANCHES": "2", "DATA_PSUM_BITWIDTH": "20", "PARALLEL_MACS": "2",
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)
    create_regmap_params_vh_file(str(ROOT / "test" / "cocotb_fpga"), str(target), str(target), str(target))
    fix_generated_port_list(target / "dma_storage.v")
    oe = get_oep(serial=False)
    # Reproduce the branch's parameters.vh without the original helper's
    # deletion of 'demo/' or its os.utime() call on HDL/OpenEye_FPGA.v.
    content = (
        f"parameter CLUSTER_ROWS = {oe.Clusters_Y},\n"
        f"parameter NUM_GLB_IACT = {oe.NUM_GLB_IACT},\n"
        f"parameter NUM_GLB_PSUM = {oe.NUM_GLB_PSUM},\n"
        f"parameter NUM_GLB_WGHT = {oe.NUM_GLB_WGHT},\n"
        f"parameter RAM_CELLS = {oe.RAM_CELLS},\n"
        f"parameter BRANCHES = {oe.BRANCHES},\n"
    )
    (target / "parameters.vh").write_text(content, encoding="utf-8")
    return target


def run_tests():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("stub", "real-smoke", "real-vectors"), default="stub")
    parser.add_argument("--sim", choices=("icarus", "verilator"), default="icarus")
    parser.add_argument("--generated", type=Path, help="matching generated parameters.vh, regmap_params.vh, dma_storage.v")
    parser.add_argument("--vectors", type=Path, help="matching demo/layer_N_M with input/reference text")
    parser.add_argument("--step-limit", type=int, default=100000)
    parser.add_argument("--rx-depth", type=int, default=None, help="Override RX FIFO depth (e.g., 4 to reproduce the reported output mismatch)")
    parser.add_argument("--trace-rx", action="store_true", help="Trace real OpenEye DMA interface and FIFO transactions around backpressure")
    parser.add_argument("--capture-depth", type=int, default=0, help="Optional OpenEye output burst reservoir; e.g. 128 for the 128-word reference")
    args = parser.parse_args()
    try:
        from cocotb_test.simulator import run
    except ImportError:
        parser.error("Install prerequisites: python -m pip install cocotb cocotb-test pytest pyyaml")
    build = HERE / ".sim_build" / args.mode
    build.mkdir(parents=True, exist_ok=True)
    sources = [str(WRAPPER)]
    include_dirs = []
    defines = {}
    # Large RX FIFO for real vectors prevents output backpressure while diagnosing
    # reference mismatches. Stub and smoke keep the previously verified depth 4.
    parameters = {"TX_FIFO_DEPTH": 4, "RX_FIFO_DEPTH": 256 if args.mode == "real-vectors" else 4}
    if args.rx_depth is not None:
        if args.rx_depth < 2 or args.rx_depth > 4096:
            parser.error("--rx-depth must be between 2 and 4096")
        parameters["RX_FIFO_DEPTH"] = args.rx_depth
    if args.capture_depth < 0 or args.capture_depth > 4096:
        parser.error("--capture-depth must be between 0 and 4096")
    if args.capture_depth:
        parameters["RX_CAPTURE_DEPTH"] = args.capture_depth
    env = {
        "OE_STEP_LIMIT": str(args.step_limit),
        "OE_TRACE_RX": "1" if args.trace_rx else "0",
        "OE_CAPTURE_NO_STALL": "1" if args.capture_depth else "0",
    }
    if args.mode == "stub":
        sources.append(str(HERE / "openeye_dma_stub.sv"))
        test_module = "test_wb_stub"
        test_case = None
    else:
        if args.mode == "real-vectors":
            if not args.generated or not args.vectors:
                parser.error("real-vectors requires --generated and --vectors from the SAME Amir-branch test run")
            generated = args.generated.resolve()
            vectors = args.vectors.resolve()
            if not vectors.is_relative_to(generated):
                parser.error("--vectors must be inside --generated; prevents accidental cross-version vectors")
            env["OE_VECTOR_DIR"] = str(vectors)
            test_case = "real_dma_vectors"
        else:
            generated = generate_isolated_config(build / "generated")
            test_case = "real_hierarchy_smoke"
        required = ("parameters.vh", "regmap_params.vh", "dma_storage.v")
        missing = [name for name in required if not (generated / name).is_file()]
        if missing:
            parser.error(f"Missing generated files in {generated}: {', '.join(missing)}")
        if args.mode == "real-vectors":
            generated = copy_generated_for_sim(generated, build / "generated_for_sim")
        sources.extend(str(x) for x in sorted((ROOT / "hdl").glob("*.v")) if x.name != "dma_storage.v")
        sources.append(str(generated / "dma_storage.v"))
        include_dirs = [str(generated)]
        defines = {"NO_TRACE": "1"}
        test_module = "test_wb_real"
    # Cocotb 2.x filters fully qualified test names, e.g.
    # test_wb_real.real_hierarchy_smoke; accept either qualified or bare names.
    # Restrict the regression explicitly; real-smoke must not run real_dma_vectors.
    if test_case is not None:
        env["COCOTB_TEST_FILTER"] = rf"(^|\.){re.escape(test_case)}$"
    print(f"MODE: {args.mode} | RX_FIFO_DEPTH={parameters['RX_FIFO_DEPTH']} | RX_CAPTURE_DEPTH={args.capture_depth} | RTL branch: Amir | generated config: {include_dirs or 'stub only'}")
    run(
        simulator=args.sim,
        verilog_sources=sources,
        includes=include_dirs,
        defines=defines,
        compile_args=["-g2012"] if args.sim == "icarus" else [],
        toplevel="openeye_wb_openeye_top",
        module=test_module,
        parameters=parameters,
        sim_build=str(build / "sim"),
        python_search=[str(HERE)],
        extra_env=env,
        force_compile=True,
        waves=False,
    )


if __name__ == "__main__":
    run_tests()
