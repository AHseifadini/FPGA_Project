# Run full OpenEye calculation using the supplied C headers

This add-on is for the existing **Amir-branch** Cocotb setup. It adds files only; it does not replace the previously passing `run.py`, wrapper, tests, or any original OpenEye RTL.

From the repository root in the VS Code PowerShell terminal:

```powershell
python test/cocotb_wishbone/run_header_vectors.py --prepare-only
python test/cocotb_wishbone/run_header_vectors.py
```

The script:
1. Parses `vector_headers/dma_stream.h` (5936 bytes / 742 words) and `dma_stream_out.h` (1024 bytes / 128 words).
2. Generates `parameters.vh`, `regmap_params.vh`, and `dma_storage.v` **using the currently checked-out Amir branch's own generator**, under `.sim_build/header_vectors/generated/`.
3. Checks the generated six design parameters against the supplied `vector_headers/parameters.vh`; aborts on any mismatch.
4. Converts little-endian C arrays into the repository's expected decimal input and binary reference text files.
5. Invokes the previously PASSed runner in `real-vectors` mode.

The output test is **not guaranteed to pass**. The supplied headers may have come from a different commit, model, or register-map revision even though the six parameters match. A failing output comparison indicates further investigation is required; it is not necessarily a wrapper defect.

**Safety:** All generated artifacts are under `test/cocotb_wishbone/.sim_build`. Nothing under `hdl/` is edited or touched. The wrapper and the original `run.py` are not overwritten by this add-on.
