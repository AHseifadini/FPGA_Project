# OpenEye ↔ Wishbone verification (Amir branch)

This directory is a **new, isolated integration test** for `fpga/hdl/openeye_wb_openeye_top.sv`.
It does **not edit any original RTL under `hdl/`**, nor does it use Vivado project settings or write to `project_1.sim`.

## Required tools

- Python 3.10+ and `python -m pip install cocotb cocotb-test pytest pyyaml`
- Icarus Verilog (`iverilog` and `vvp` visible in PATH), or Verilator (`--sim verilator`)
- Run the commands from the **VS Code terminal**, not Vivado Tcl Console.
- The current working tree must be **branch `Amir`**.

## 1. Wrapper-only tests (start here)

From the repository root:

```powershell
python test/cocotb_wishbone/run.py --mode stub
```

This compiles *only* the new Wishbone wrapper and `openeye_dma_stub.sv`.
The Cocotb tests check ID, version, byte writes, one ACK per CYC/STB,
FIFO wrapping, backpressure, order/data integrity, counters and LAST.
**This mode proves the wrapper behavior with a stub; it does not test real OpenEye computation.**

## 2. Compile with actual OpenEye, using generated files of this branch

```powershell
python test/cocotb_wishbone/run.py --mode real-smoke
```

This generates `parameters.vh`, `regmap_params.vh`, and `dma_storage.v`
into `test/cocotb_wishbone/.sim_build/real-smoke/generated/` using the
**Amir branch's own** Python generator, and compiles the real files under `hdl/`.
No original source file is modified, touched, overwritten, or deleted.
The Python test reads the Wishbone ID/count registers. It is *not* a computation test.
If the original core fails to compile with its own generated files, the runner
fails without changing the RTL; investigate the original branch's source/generator compatibility.

## 3. Full functional stream + reference comparison (after obtaining matching vectors)

Original OpenEye tests in this **same working tree and commit** produce directories like:

```
test/.temp/<the-selected-original-test>/
    parameters.vh
    regmap_params.vh
    dma_storage.v
    demo/layer_0_0/dma_stream_input.txt
    demo/layer_0_0/dma_stream_ref.txt
```

From the VS Code terminal:

```powershell
python test/cocotb_wishbone/run.py --mode real-vectors --generated "test/.temp/<the-selected-original-test>" --vectors "test/.temp/<the-selected-original-test>/demo/layer_0_0"
```

The Python runner refuses to use vectors outside the specified generated
configuration directory, preventing the cross-version file mixing that caused
previous failures. The test checks *every 64-bit output word* and LAST. It
fails on a mismatch, extra word, missing word, or timeout. `--step-limit` can
increase the number of bus-service iterations, but cannot repair a stalled core.

**Compatibility note:** A matching generated configuration and vector pair from
this exact Amir branch is required. Do not use test vectors or `dma_storage.v`
from `DenLeb/OpenEye` or from `Documents/OpenEye` unless that is the same exact
source revision and configuration. The full functional test has not been run in
this delivery environment; do not claim end-to-end success before its own PASS.

## Wishbone register map (byte offsets)

- `0x00` ID: `0x4F50454E`
- `0x04` VERSION: `0x00010000`
- `0x08` CTRL (write, low byte enabled): bit0 enqueue TX, bit1 clear wrapper FIFOs/counters, bit2 pop RX
- `0x0C` STATUS: bit0 wrapper present, bit1 OpenEye input READY,
  bit2 TX empty, bit3 TX full, bit4 RX not empty, bit5 RX full,
  bit6 LAST received, bit7 TX overflow, bit8 reserved RX overflow
- `0x10`, `0x14`: TX low/high staging registers
- `0x18`, `0x1C`: RX low/high from current FIFO head
- `0x20`, `0x24`: TX/RX occupancy
- `0x28`, `0x2C`: accepted input and output word counters
- `0x30`: sticky error flags

CPU procedure: read STATUS and wait for TX-full=0; write LOW then HIGH then
CTRL=1. For output wait for RX-nonempty=1, read LOW and HIGH, then CTRL=4.
`CTRL=2` only clears the **wrapper**, not the accelerator; use only when idle.

Wishbone address input is a byte offset (bits `[6:2]` are decoded). With the
existing `axi_lite_to_wishbone.v`, use `WB_ADDR_SHIFT=0`; with NEORV32
Wishbone integration, verify the peripheral address mapping separately.

## Scope and reproducibility

- Tests explicitly use short, independent build directories per mode.
- The Wishbone+stub unit test is designed to be independent of neural network settings.
- The core's own DMA input register pipeline and READY behavior need verification
  in the real-vector test; no wrapper-only test can prove this automatically.
- Output reference is read as binary strings; inputs as decimal 64-bit words,
  matching the Amir branch's original stream-file writer.
