`timescale 1ns / 1ps
// Wishbone Classic 32-bit slave + 64-bit DMA streaming FIFO bridge.
// This is a NEW integration module; it does not modify OpenEye RTL.
// Local addresses are byte offsets.
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: Seifadini
// 
// Create Date: 07.08.2026 17:10:23
// Design Name: 
// Module Name: openeye_wb_openeye_top
// Project Name: FPGA_Openeye_NeoRV
// Target Devices: 
// Tool Versions: 
// Description: 
// 
// Dependencies: 
// 
// Revision:
// Revision 0.01 - File Created
// Additional Comments:
// 
//////////////////////////////////////////////////////////////////////////////////
module openeye_wb_openeye_top #(
    parameter integer WB_ADDR_WIDTH = 32,
    parameter integer TX_FIFO_DEPTH = 16,
    parameter integer RX_FIFO_DEPTH = 128
)(
    // -------------------------------------------------
    // Clock and reset
    // -------------------------------------------------
    input  logic                     wb_clk_i,
    input  logic                     wb_rst_i,

    // -------------------------------------------------
    // Wishbone Slave Interface
    // -------------------------------------------------
    input  logic [WB_ADDR_WIDTH-1:0] wb_adr_i,
    input  logic [31:0]              wb_dat_i,
    output logic [31:0]              wb_dat_o,

    input  logic [3:0]               wb_sel_i,
    input  logic                     wb_we_i,
    input  logic                     wb_cyc_i,
    input  logic                     wb_stb_i,

    output logic                     wb_ack_o
);

    // -------------------------------------------------
    // Register map, byte addresses
    //
    // 0x00 : ID          RO  = 0x4F50454E = "OPEN"
    // 0x04 : VERSION     RO  = 0x00010000
    //
    // 0x08 : CTRL        WO
    //        bit 0 = PUSH_TX_WORD
    //        bit 1 = CLEAR_ALL
    //        bit 2 = RX_POP
    //
    // 0x0C : STATUS      RO
    //        bit 0  = block ready
    //        bit 1  = OpenEye ready for input
    //        bit 2  = TX FIFO empty
    //        bit 3  = TX FIFO full
    //        bit 4  = RX FIFO valid / not empty
    //        bit 5  = RX FIFO full
    //        bit 6  = last output seen
    //        bit 7  = TX overflow
    //        bit 8  = RX overflow
    //
    // 0x10 : TX_LOW      RW
    // 0x14 : TX_HIGH     RW
    // 0x18 : RX_LOW      RO
    // 0x1C : RX_HIGH     RO
    // 0x20 : TX_COUNT    RO
    // 0x24 : RX_COUNT    RO
    // 0x28 : WORDS_SENT  RO
    // 0x2C : WORDS_RECV  RO
    // 0x30 : ERROR       RO
    // -------------------------------------------------

    localparam logic [31:0] REG_ID_VALUE      = 32'h4F50_454E; // "OPEN"
    localparam logic [31:0] REG_VERSION_VALUE = 32'h0001_0000;

    localparam logic [4:0] ADDR_ID          = 5'h00; // 0x00
    localparam logic [4:0] ADDR_VERSION     = 5'h01; // 0x04
    localparam logic [4:0] ADDR_CTRL        = 5'h02; // 0x08
    localparam logic [4:0] ADDR_STATUS      = 5'h03; // 0x0C
    localparam logic [4:0] ADDR_TX_LOW      = 5'h04; // 0x10
    localparam logic [4:0] ADDR_TX_HIGH     = 5'h05; // 0x14
    localparam logic [4:0] ADDR_RX_LOW      = 5'h06; // 0x18
    localparam logic [4:0] ADDR_RX_HIGH     = 5'h07; // 0x1C
    localparam logic [4:0] ADDR_TX_COUNT    = 5'h08; // 0x20
    localparam logic [4:0] ADDR_RX_COUNT    = 5'h09; // 0x24
    localparam logic [4:0] ADDR_WORDS_SENT  = 5'h0A; // 0x28
    localparam logic [4:0] ADDR_WORDS_RECV  = 5'h0B; // 0x2C
    localparam logic [4:0] ADDR_ERROR       = 5'h0C; // 0x30

    localparam integer TX_PTR_WIDTH   = (TX_FIFO_DEPTH <= 1) ? 1 : $clog2(TX_FIFO_DEPTH);
    localparam integer RX_PTR_WIDTH   = (RX_FIFO_DEPTH <= 1) ? 1 : $clog2(RX_FIFO_DEPTH);

    localparam integer TX_COUNT_WIDTH = $clog2(TX_FIFO_DEPTH + 1);
    localparam integer RX_COUNT_WIDTH = $clog2(RX_FIFO_DEPTH + 1);

    localparam logic [TX_COUNT_WIDTH-1:0] TX_DEPTH_VALUE = TX_FIFO_DEPTH;
    localparam logic [RX_COUNT_WIDTH-1:0] RX_DEPTH_VALUE = RX_FIFO_DEPTH;

    // -------------------------------------------------
    // Wishbone internal
    // -------------------------------------------------

    logic wishbone_request;
    logic wishbone_new_request;
    logic request_active;

    logic [4:0] register_index;

    assign wishbone_request     = wb_cyc_i && wb_stb_i;
    assign wishbone_new_request = wishbone_request && !request_active;

    always_comb begin
        register_index = wb_adr_i[6:2]; // byte addressing: 0x00, 0x04, 0x08, ...
    end

    // -------------------------------------------------
    // Control commands
    // -------------------------------------------------

    logic cmd_push_tx;
    logic cmd_clear_all;
    logic cmd_rx_pop;

    assign cmd_push_tx   = wishbone_new_request && wb_we_i && wb_sel_i[0] &&
                           (register_index == ADDR_CTRL) && wb_dat_i[0];

    assign cmd_clear_all = wishbone_new_request && wb_we_i && wb_sel_i[0] &&
                           (register_index == ADDR_CTRL) && wb_dat_i[1];

    assign cmd_rx_pop    = wishbone_new_request && wb_we_i && wb_sel_i[0] &&
                           (register_index == ADDR_CTRL) && wb_dat_i[2];

    // -------------------------------------------------
    // TX registers written by CPU
    // -------------------------------------------------

    logic [31:0] tx_low_reg;
    logic [31:0] tx_high_reg;

    // -------------------------------------------------
    // TX FIFO: CPU -> OpenEye
    // -------------------------------------------------

    logic [63:0] tx_fifo_mem [0:TX_FIFO_DEPTH-1];

    logic [TX_PTR_WIDTH-1:0]   tx_wr_ptr;
    logic [TX_PTR_WIDTH-1:0]   tx_rd_ptr;
    logic [TX_COUNT_WIDTH-1:0] tx_count;

    logic tx_empty;
    logic tx_full;
    logic tx_push;
    logic tx_send;

    logic tx_overflow_flag;

    assign tx_empty = (tx_count == 0);
    assign tx_full  = (tx_count == TX_DEPTH_VALUE);

    // A full FIFO can accept a new word when the head leaves in this cycle.
    assign tx_push  = cmd_push_tx && (!tx_full || tx_send);

    // -------------------------------------------------
    // RX FIFO: OpenEye -> CPU
    // -------------------------------------------------

    logic [63:0] rx_fifo_mem [0:RX_FIFO_DEPTH-1];

    logic [RX_PTR_WIDTH-1:0]   rx_wr_ptr;
    logic [RX_PTR_WIDTH-1:0]   rx_rd_ptr;
    logic [RX_COUNT_WIDTH-1:0] rx_count;

    logic rx_empty;
    logic rx_full;
    logic rx_push;
    logic rx_pop;

    logic [63:0] rx_current_word;

    logic last_seen_flag;
    logic rx_overflow_flag; // reserved; normal READY backpressure is not overflow

    assign rx_empty = (rx_count == 0);
    assign rx_full  = (rx_count == RX_DEPTH_VALUE);

    assign rx_pop   = cmd_rx_pop && !rx_empty;

    assign rx_current_word =
        (!rx_empty) ? rx_fifo_mem[rx_rd_ptr] : 64'h0000_0000_0000_0000;

    // -------------------------------------------------
    // OpenEye DMA-style internal signals
    // -------------------------------------------------

    logic [63:0] oe_data_dma_i;
    logic        oe_enable_dma_i;
    logic        oe_ready_dma_o;

    logic [63:0] oe_data_dma_o;
    logic        oe_enable_dma_o;
    logic        oe_ready_dma_i;
    logic        oe_last_data_o;

    assign oe_ready_dma_i = !rx_full || rx_pop;

    assign rx_push = oe_enable_dma_o && oe_ready_dma_i;

    // Present the current FIFO head together with VALID.
    // Advance only on a same-cycle valid/ready handshake.
    assign oe_data_dma_i   = tx_empty ? 64'h0 : tx_fifo_mem[tx_rd_ptr];
    assign oe_enable_dma_i = !tx_empty;
    assign tx_send = oe_enable_dma_i && oe_ready_dma_o;

    // -------------------------------------------------
    // Counters
    // -------------------------------------------------

    logic [31:0] words_sent_count;
    logic [31:0] words_recv_count;

    integer byte_index;

    // -------------------------------------------------
    // Main sequential logic
    // -------------------------------------------------

    always_ff @(posedge wb_clk_i) begin
        if (wb_rst_i) begin
            wb_ack_o          <= 1'b0;
            request_active    <= 1'b0;

            tx_low_reg        <= 32'h0000_0000;
            tx_high_reg       <= 32'h0000_0000;

            tx_wr_ptr         <= '0;
            tx_rd_ptr         <= '0;
            tx_count          <= '0;

            rx_wr_ptr         <= '0;
            rx_rd_ptr         <= '0;
            rx_count          <= '0;

            words_sent_count  <= 32'h0000_0000;
            words_recv_count  <= 32'h0000_0000;

            last_seen_flag    <= 1'b0;
            tx_overflow_flag  <= 1'b0;
            rx_overflow_flag  <= 1'b0;
        end
        else begin
            // Default pulses
            wb_ack_o        <= 1'b0;
            // -------------------------------------------------
            // Wishbone ACK
            // -------------------------------------------------
            if (wishbone_new_request) begin
                wb_ack_o       <= 1'b1;
                request_active <= 1'b1;
            end

            if (!wishbone_request) begin
                request_active <= 1'b0;
            end

            // -------------------------------------------------
            // CLEAR resets wrapper bookkeeping ONLY, not the OpenEye engine.
    // Use during idle/reset, never while DMA traffic is active.
    // Clear command
            // -------------------------------------------------
            if (cmd_clear_all) begin
                tx_wr_ptr         <= '0;
                tx_rd_ptr         <= '0;
                tx_count          <= '0;

                rx_wr_ptr         <= '0;
                rx_rd_ptr         <= '0;
                rx_count          <= '0;

                words_sent_count  <= 32'h0000_0000;
                words_recv_count  <= 32'h0000_0000;

                last_seen_flag    <= 1'b0;
                tx_overflow_flag  <= 1'b0;
                rx_overflow_flag  <= 1'b0;

                // TX valid/data are combinationally driven from FIFO head.
                // CLEAR_ALL should be used only while OpenEye is idle.
            end
            else begin

                // -------------------------------------------------
                // CPU writes TX_LOW / TX_HIGH
                // -------------------------------------------------
                if (wishbone_new_request && wb_we_i &&
                    register_index == ADDR_TX_LOW) begin

                    for (byte_index = 0; byte_index < 4; byte_index = byte_index + 1) begin
                        if (wb_sel_i[byte_index]) begin
                            tx_low_reg[byte_index*8 +: 8]
                                <= wb_dat_i[byte_index*8 +: 8];
                        end
                    end
                end

                if (wishbone_new_request && wb_we_i &&
                    register_index == ADDR_TX_HIGH) begin

                    for (byte_index = 0; byte_index < 4; byte_index = byte_index + 1) begin
                        if (wb_sel_i[byte_index]) begin
                            tx_high_reg[byte_index*8 +: 8]
                                <= wb_dat_i[byte_index*8 +: 8];
                        end
                    end
                end

                // -------------------------------------------------
                // Push one 64-bit word from TX_LOW/TX_HIGH into TX FIFO
                // -------------------------------------------------
                if (cmd_push_tx && tx_full && !tx_send) begin
                    tx_overflow_flag <= 1'b1;
                end

                if (tx_push) begin
                    tx_fifo_mem[tx_wr_ptr] <= {tx_high_reg, tx_low_reg};
                    tx_wr_ptr              <= (tx_wr_ptr == TX_FIFO_DEPTH-1) ? '0 : tx_wr_ptr + 1'b1;
                end

                // -------------------------------------------------
                // Send one word from TX FIFO to OpenEye when ready
                // -------------------------------------------------
                if (tx_send) begin
                    tx_rd_ptr       <= (tx_rd_ptr == TX_FIFO_DEPTH-1) ? '0 : tx_rd_ptr + 1'b1;
                    words_sent_count <= words_sent_count + 1'b1;
                end

                // Update TX FIFO count
                case ({tx_push, tx_send})
                    2'b10: tx_count <= tx_count + 1'b1;
                    2'b01: tx_count <= tx_count - 1'b1;
                    default: tx_count <= tx_count;
                endcase

                // -------------------------------------------------
                // Receive one 64-bit word from OpenEye into RX FIFO
                // -------------------------------------------------
                // Holding VALID while READY=0 is normal backpressure,
                // not an RX overflow. The upstream DMA must honor READY.

                if (rx_push) begin
                    rx_fifo_mem[rx_wr_ptr] <= oe_data_dma_o;
                    rx_wr_ptr              <= (rx_wr_ptr == RX_FIFO_DEPTH-1) ? '0 : rx_wr_ptr + 1'b1;
                    words_recv_count       <= words_recv_count + 1'b1;

                    if (oe_last_data_o) begin
                        last_seen_flag <= 1'b1;
                    end
                end

                // -------------------------------------------------
                // Pop one word from RX FIFO after CPU has read it
                // -------------------------------------------------
                if (rx_pop) begin
                    rx_rd_ptr <= (rx_rd_ptr == RX_FIFO_DEPTH-1) ? '0 : rx_rd_ptr + 1'b1;
                end

                // Update RX FIFO count
                case ({rx_push, rx_pop})
                    2'b10: rx_count <= rx_count + 1'b1;
                    2'b01: rx_count <= rx_count - 1'b1;
                    default: rx_count <= rx_count;
                endcase
            end
        end
    end

    // -------------------------------------------------
    // Wishbone read mux
    // -------------------------------------------------

    always_comb begin
        case (register_index)

            ADDR_ID: begin
                wb_dat_o = REG_ID_VALUE;
            end

            ADDR_VERSION: begin
                wb_dat_o = REG_VERSION_VALUE;
            end

            ADDR_CTRL: begin
                wb_dat_o = 32'h0000_0000;
            end

            ADDR_STATUS: begin
                wb_dat_o = {
                    23'h000000,
                    rx_overflow_flag,  // bit 8
                    tx_overflow_flag,  // bit 7
                    last_seen_flag,    // bit 6
                    rx_full,           // bit 5
                    !rx_empty,         // bit 4
                    tx_full,           // bit 3
                    tx_empty,          // bit 2
                    oe_ready_dma_o,    // bit 1
                    1'b1               // bit 0
                };
            end

            ADDR_TX_LOW: begin
                wb_dat_o = tx_low_reg;
            end

            ADDR_TX_HIGH: begin
                wb_dat_o = tx_high_reg;
            end

            ADDR_RX_LOW: begin
                wb_dat_o = rx_current_word[31:0];
            end

            ADDR_RX_HIGH: begin
                wb_dat_o = rx_current_word[63:32];
            end

            ADDR_TX_COUNT: begin
                wb_dat_o = {{(32-TX_COUNT_WIDTH){1'b0}}, tx_count};
            end

            ADDR_RX_COUNT: begin
                wb_dat_o = {{(32-RX_COUNT_WIDTH){1'b0}}, rx_count};
            end

            ADDR_WORDS_SENT: begin
                wb_dat_o = words_sent_count;
            end

            ADDR_WORDS_RECV: begin
                wb_dat_o = words_recv_count;
            end

            ADDR_ERROR: begin
                wb_dat_o = {
                    30'h0000_0000,
                    rx_overflow_flag,
                    tx_overflow_flag
                };
            end

            default: begin
                wb_dat_o = 32'hDEAD_BEEF;
            end

        endcase
    end

    // -------------------------------------------------
    // Real OpenEye instance
    // -------------------------------------------------
    // IMPORTANT:
    // This assumes your OpenEye_FPGA module uses these exact ports.
    // If your OpenEye_FPGA declaration has additional ports or different
    // reset naming, adjust only this instance connection.
    // -------------------------------------------------

    OpenEye_FPGA openeye_inst (
        .clk_i          (wb_clk_i),
        .rst_ni         (~wb_rst_i),

        .data_dma_i     (oe_data_dma_i),
        .enable_dma_i   (oe_enable_dma_i),
        .ready_dma_o    (oe_ready_dma_o),

        .data_dma_o     (oe_data_dma_o),
        .enable_dma_o   (oe_enable_dma_o),
        .ready_dma_i    (oe_ready_dma_i),
        .last_data_o    (oe_last_data_o)
    );

endmodule