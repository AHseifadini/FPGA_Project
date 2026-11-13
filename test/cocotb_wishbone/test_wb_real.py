"""Real OpenEye tests. No RTL modification, no dummy output and no fake PASS."""
import os
from collections import deque
from pathlib import Path

import cocotb
from cocotb.triggers import ClockCycles, FallingEdge, Timer
from wb_helpers import (
    Wishbone, ID, VERSION, STATUS, TX_COUNT, RX_COUNT, WORDS_SENT,
    WORDS_RECV, ERROR,
)


def _words(path, binary=False):
    result = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        try:
            result.append(int(value, 2 if binary else 10))
        except ValueError as exc:
            raise AssertionError(f"{path}:{line_number} is not valid {'binary' if binary else 'decimal'}: {value}") from exc
        assert 0 <= result[-1] < (1 << 64), f"64-bit DMA word out of range: {path}:{line_number}"
    return result


async def monitor_rx_dma(dut, snapshot, references):
    """Non-invasive instrumentation: sample signals 1 ns after each falling edge.

    The signals settle well before the next rising edge, when the wrapper
    actually decides whether to push a DMA output into its RX FIFO.
    Does not drive anything, alter the DUT, or modify expected values.
    """
    prior_stall = None
    prior_ready = None
    cycle = 0
    while True:
        await FallingEdge(dut.wb_clk_i)
        await Timer(1, unit="ns")
        cycle += 1
        try:
            ready = int(dut.oe_ready_dma_i.value)
            valid = int(dut.oe_enable_dma_o.value)
            # OpenEye may drive X on the data/last bus before VALID is asserted.
            # Do not stop tracing solely because inactive data is unresolved.
            data = int(dut.oe_data_dma_o.value) if valid else 0
            last = int(dut.oe_last_data_o.value) if valid else 0
            occupancy = int(dut.rx_count.value)
            pop = int(dut.rx_pop.value)
        except (AttributeError, ValueError) as exc:
            snapshot["events"].append(f"TRACE UNAVAILABLE: {exc}")
            return

        if prior_stall is not None:
            previous_data, previous_last, previous_cycle = prior_stall
            if not valid or data != previous_data or last != previous_last:
                msg = (f"PROTOCOL VIOLATION: valid/data/last changed while stalled; "
                       f"cycle={previous_cycle}->{cycle}, "
                       f"previous=0x{previous_data:016x}/last={previous_last}, "
                       f"now valid={valid} data=0x{data:016x}/last={last}")
                snapshot["violations"].append(msg)
                snapshot["events"].append(msg)
        prior_stall = (data, last, cycle) if valid and not ready else None

        if ready != prior_ready:
            snapshot["events"].append(
                f"cycle={cycle:5d} READY {prior_ready}->{ready} "
                f"valid={valid} rx_count={occupancy} rx_pop={pop} "
                f"data=0x{data:016x}")
            prior_ready = ready

        if valid and not ready:
            snapshot["stall_cycles"] += 1
        if valid and ready:
            index = len(snapshot["accepted"])
            snapshot["accepted"].append((cycle, data))
            if index < len(references) and data != references[index]:
                snapshot["events"].append(
                    f"cycle={cycle:5d} RAW MISMATCH raw_accepted[{index}] "
                    f"got=0x{data:016x} expected=0x{references[index]:016x} "
                    f"rx_count={occupancy} rx_pop={pop}")
            if index < 16 or (index % 16 == 0):
                snapshot["events"].append(
                    f"cycle={cycle:5d} raw_accepted[{index}]="
                    f"0x{data:016x} rx_count={occupancy} rx_pop={pop}")


def report_rx_trace(dut, snapshot, mismatch_index):
    accepted = snapshot["accepted"]
    dut._log.error("RX diagnostic: %d raw output handshakes captured; %d stalled-valid violations",
                   len(accepted), len(snapshot["violations"]))
    for i in range(max(0, mismatch_index-6), min(len(accepted), mismatch_index+10)):
        cycle, data = accepted[i]
        dut._log.error("RAW OpenEye accept[%d] at sample cycle=%d: 0x%016x", i, cycle, data)
    dut._log.error("Recent READY/handshake trace (last 45 events):")
    for event in list(snapshot["events"])[-45:]:
        dut._log.error("  %s", event)
    if mismatch_index < len(accepted):
        dut._log.error("Compare RAW OpenEye accept[%d] against failed Wishbone read[%d]",
                       mismatch_index, mismatch_index)
    else:
        dut._log.error("RAW output handshake monitor has not reached failed Wishbone index yet")


@cocotb.test()
async def real_hierarchy_smoke(dut):
    wb = Wishbone(dut)
    await wb.reset()
    assert await wb.read(ID) == 0x4F50454E
    assert await wb.read(VERSION) == 0x00010000
    assert await wb.read(TX_COUNT) == 0
    assert await wb.read(RX_COUNT) == 0
    assert await wb.read(ERROR) == 0
    dut._log.info("PASS: Real OpenEye compiled and Wishbone register smoke test completed")
    dut._log.info("NOTE: no computation is tested by real_hierarchy_smoke")


@cocotb.test()
async def real_dma_vectors(dut):
    directory = os.environ.get("OE_VECTOR_DIR")
    assert directory, "OE_VECTOR_DIR must point to a matching generated demo/layer_X_Y folder"
    directory = Path(directory)
    inputs = _words(directory / "dma_stream_input.txt")
    references = _words(directory / "dma_stream_ref.txt", binary=True)
    assert inputs, "No input vectors found"
    assert references, "No output references found"
    dut._log.info("Real DMA test: %d input and %d expected output words", len(inputs), len(references))
    wb = Wishbone(dut)
    await wb.reset()
    assert await wb.read(ID) == 0x4F50454E
    tracing = os.environ.get("OE_TRACE_RX") == "1"
    snapshot = {"accepted": [], "events": deque(maxlen=1000), "violations": [], "stall_cycles": 0}
    if tracing:
        cocotb.start_soon(monitor_rx_dma(dut, snapshot, references))
        dut._log.info("RX diagnostic enabled: sampling raw DMA before FIFO")
    sent, received = 0, []
    step_limit = int(os.environ.get("OE_STEP_LIMIT", "100000"))
    for step in range(step_limit):
        status = await wb.read(STATUS)
        if sent < len(inputs) and not (status & (1 << 3)):
            await wb.enqueue(inputs[sent])
            sent += 1
            if sent % 128 == 0 or sent == len(inputs):
                dut._log.info("Input queued: %d/%d", sent, len(inputs))
        if await wb.read(RX_COUNT):
            result = await wb.dequeue()
            index = len(received)
            if index >= len(references):
                raise AssertionError(f"Unexpected extra output[{index}]: 0x{result:016x}")
            expected = references[index]
            if result != expected:
                if tracing:
                    report_rx_trace(dut, snapshot, index)
                raise AssertionError(
                    f"OpenEye output[{index}] mismatch: got 0x{result:016x}, "
                    f"expected 0x{expected:016x}"
                )
            received.append(result)
            if len(received) % 16 == 0 or len(received) == len(references):
                dut._log.info("Compared outputs: %d/%d", len(received), len(references))
        if sent == len(inputs) and len(received) == len(references) and await wb.read(TX_COUNT) == 0:
            # Require the LAST marker for a complete layer response.
            assert await wb.read(STATUS) & (1 << 6), "Expected last_data marker not seen"
            assert await wb.read(WORDS_SENT) == len(inputs)
            assert await wb.read(WORDS_RECV) == len(references)
            assert await wb.read(ERROR) == 0
            if tracing and os.environ.get("OE_CAPTURE_NO_STALL") == "1":
                assert snapshot["stall_cycles"] == 0, (
                    f"Output capture buffer was too small: "
                    f"{snapshot['stall_cycles']} stalled-valid cycles. "
                    "Choose a buffer large enough for the entire output burst."
                )
                assert len(snapshot["accepted"]) == len(references), (
                    "DMA raw acceptance count differs from reference output length"
                )
                dut._log.info("PASS: No output READY stalls; %d raw OpenEye words accepted", len(snapshot["accepted"]))
            dut._log.info("PASS: REAL OpenEye %d words in, %d reference words matched", len(inputs), len(references))
            return
        await ClockCycles(dut.wb_clk_i, 1)
    dut._log.error(
        "TIMEOUT: queued=%d/%d compared=%d/%d tx_count=%d rx_count=%d words_sent=%d words_recv=%d status=0x%x",
        sent, len(inputs), len(received), len(references), await wb.read(TX_COUNT),
        await wb.read(RX_COUNT), await wb.read(WORDS_SENT), await wb.read(WORDS_RECV), await wb.read(STATUS),
    )
    try:
        dut._log.error("OpenEye internal FSM state: %d", int(dut.openeye_inst.debug_fsm_current_state.value))
    except (AttributeError, ValueError):
        pass
    if tracing:
        report_rx_trace(dut, snapshot, len(received))
    raise AssertionError("Real OpenEye computation did not produce the full reference stream")
