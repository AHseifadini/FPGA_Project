`timescale 1 ns / 1 ps

// AXI4-Lite slave to Wishbone Classic master bridge.
//
// Notes:
// - Supports one outstanding AXI transaction at a time.
// - AXI burst transfers are not supported; this is for AXI4-Lite.
// - Wishbone STALL is not used. Connect to Wishbone Classic slaves that hold
//   ACK/ERR until CYC/STB completes a single transfer.
// - AXI addresses are byte addresses. For word-addressed Wishbone slaves, set
//   WB_ADDR_SHIFT to 2 for 32-bit data and reduce C_WB_ADDR_WIDTH as needed.
module axi_lite_to_wishbone #(
    parameter integer C_AXI_DATA_WIDTH = 32,
    parameter integer C_AXI_ADDR_WIDTH = 6,
    parameter integer C_WB_ADDR_WIDTH  = C_AXI_ADDR_WIDTH,
    parameter integer WB_ADDR_SHIFT    = 0
) (
    input wire s_axi_aclk,
    input wire s_axi_aresetn,

    input wire [C_AXI_ADDR_WIDTH-1 : 0] s_axi_awaddr,
    input wire [2 : 0] s_axi_awprot,
    input wire s_axi_awvalid,
    output wire s_axi_awready,

    input wire [C_AXI_DATA_WIDTH-1 : 0] s_axi_wdata,
    input wire [(C_AXI_DATA_WIDTH/8)-1 : 0] s_axi_wstrb,
    input wire s_axi_wvalid,
    output wire s_axi_wready,

    output reg [1 : 0] s_axi_bresp,
    output reg s_axi_bvalid,
    input wire s_axi_bready,

    input wire [C_AXI_ADDR_WIDTH-1 : 0] s_axi_araddr,
    input wire [2 : 0] s_axi_arprot,
    input wire s_axi_arvalid,
    output wire s_axi_arready,

    output reg [C_AXI_DATA_WIDTH-1 : 0] s_axi_rdata,
    output reg [1 : 0] s_axi_rresp,
    output reg s_axi_rvalid,
    input wire s_axi_rready,

    output reg [C_WB_ADDR_WIDTH-1 : 0] wb_adr_o,
    output reg [C_AXI_DATA_WIDTH-1 : 0] wb_dat_o,
    input wire [C_AXI_DATA_WIDTH-1 : 0] wb_dat_i,
    output reg [(C_AXI_DATA_WIDTH/8)-1 : 0] wb_sel_o,
    output reg wb_we_o,
    output reg wb_cyc_o,
    output reg wb_stb_o,
    input wire wb_ack_i,
    input wire wb_err_i
);

    localparam [1 : 0] AXI_RESP_OKAY   = 2'b00;
    localparam [1 : 0] AXI_RESP_SLVERR = 2'b10;

    localparam [2 : 0] ST_IDLE       = 3'd0;
    localparam [2 : 0] ST_WAIT_WRITE = 3'd1;
    localparam [2 : 0] ST_WB_WRITE   = 3'd2;
    localparam [2 : 0] ST_WRITE_RESP = 3'd3;
    localparam [2 : 0] ST_WB_READ    = 3'd4;
    localparam [2 : 0] ST_READ_RESP  = 3'd5;

    reg [2 : 0] state;

    reg [C_WB_ADDR_WIDTH-1 : 0] awaddr_hold;
    reg [C_AXI_DATA_WIDTH-1 : 0] wdata_hold;
    reg [(C_AXI_DATA_WIDTH/8)-1 : 0] wstrb_hold;
    reg aw_seen;
    reg w_seen;

    wire write_collect_state;
    wire aw_hs;
    wire w_hs;
    wire ar_hs;

    assign write_collect_state = (state == ST_IDLE) || (state == ST_WAIT_WRITE);

    assign s_axi_awready = s_axi_aresetn && write_collect_state && !aw_seen;
    assign s_axi_wready  = s_axi_aresetn && write_collect_state && !w_seen;

    // Write has priority when read and write channels are offered together.
    assign s_axi_arready = s_axi_aresetn &&
                           (state == ST_IDLE) &&
                           !s_axi_awvalid &&
                           !s_axi_wvalid;

    assign aw_hs = s_axi_awvalid && s_axi_awready;
    assign w_hs  = s_axi_wvalid && s_axi_wready;
    assign ar_hs = s_axi_arvalid && s_axi_arready;

    wire unused_axi_prot;
    assign unused_axi_prot = &{1'b0, s_axi_awprot, s_axi_arprot};

    function [C_WB_ADDR_WIDTH-1 : 0] axi_to_wb_addr;
        input [C_AXI_ADDR_WIDTH-1 : 0] axi_addr;
        integer bit_index;
        begin
            axi_to_wb_addr = {C_WB_ADDR_WIDTH{1'b0}};
            for (bit_index = 0; bit_index < C_WB_ADDR_WIDTH; bit_index = bit_index + 1) begin
                if ((bit_index + WB_ADDR_SHIFT) < C_AXI_ADDR_WIDTH) begin
                    axi_to_wb_addr[bit_index] = axi_addr[bit_index + WB_ADDR_SHIFT];
                end
            end
        end
    endfunction

    always @(posedge s_axi_aclk) begin
        if (!s_axi_aresetn) begin
            state <= ST_IDLE;

            awaddr_hold <= {C_WB_ADDR_WIDTH{1'b0}};
            wdata_hold <= {C_AXI_DATA_WIDTH{1'b0}};
            wstrb_hold <= {(C_AXI_DATA_WIDTH/8){1'b0}};
            aw_seen <= 1'b0;
            w_seen <= 1'b0;

            s_axi_bresp <= AXI_RESP_OKAY;
            s_axi_bvalid <= 1'b0;
            s_axi_rdata <= {C_AXI_DATA_WIDTH{1'b0}};
            s_axi_rresp <= AXI_RESP_OKAY;
            s_axi_rvalid <= 1'b0;

            wb_adr_o <= {C_WB_ADDR_WIDTH{1'b0}};
            wb_dat_o <= {C_AXI_DATA_WIDTH{1'b0}};
            wb_sel_o <= {(C_AXI_DATA_WIDTH/8){1'b0}};
            wb_we_o <= 1'b0;
            wb_cyc_o <= 1'b0;
            wb_stb_o <= 1'b0;
        end else begin
            if (aw_hs) begin
                awaddr_hold <= axi_to_wb_addr(s_axi_awaddr);
                aw_seen <= 1'b1;
            end

            if (w_hs) begin
                wdata_hold <= s_axi_wdata;
                wstrb_hold <= s_axi_wstrb;
                w_seen <= 1'b1;
            end

            case (state)
                ST_IDLE: begin
                    if (aw_hs || w_hs) begin
                        state <= ST_WAIT_WRITE;
                    end else if (ar_hs) begin
                        wb_adr_o <= axi_to_wb_addr(s_axi_araddr);
                        wb_dat_o <= {C_AXI_DATA_WIDTH{1'b0}};
                        wb_sel_o <= {(C_AXI_DATA_WIDTH/8){1'b1}};
                        wb_we_o <= 1'b0;
                        wb_cyc_o <= 1'b1;
                        wb_stb_o <= 1'b1;
                        state <= ST_WB_READ;
                    end
                end

                ST_WAIT_WRITE: begin
                    if (aw_seen && w_seen) begin
                        wb_adr_o <= awaddr_hold;
                        wb_dat_o <= wdata_hold;
                        wb_sel_o <= wstrb_hold;
                        wb_we_o <= 1'b1;
                        wb_cyc_o <= 1'b1;
                        wb_stb_o <= 1'b1;

                        aw_seen <= 1'b0;
                        w_seen <= 1'b0;
                        state <= ST_WB_WRITE;
                    end
                end

                ST_WB_WRITE: begin
                    if (wb_ack_i || wb_err_i) begin
                        wb_cyc_o <= 1'b0;
                        wb_stb_o <= 1'b0;
                        wb_we_o <= 1'b0;

                        s_axi_bresp <= wb_err_i ? AXI_RESP_SLVERR : AXI_RESP_OKAY;
                        s_axi_bvalid <= 1'b1;
                        state <= ST_WRITE_RESP;
                    end
                end

                ST_WRITE_RESP: begin
                    if (s_axi_bready) begin
                        s_axi_bvalid <= 1'b0;
                        s_axi_bresp <= AXI_RESP_OKAY;
                        state <= ST_IDLE;
                    end
                end

                ST_WB_READ: begin
                    if (wb_ack_i || wb_err_i) begin
                        wb_cyc_o <= 1'b0;
                        wb_stb_o <= 1'b0;

                        s_axi_rdata <= wb_dat_i;
                        s_axi_rresp <= wb_err_i ? AXI_RESP_SLVERR : AXI_RESP_OKAY;
                        s_axi_rvalid <= 1'b1;
                        state <= ST_READ_RESP;
                    end
                end

                ST_READ_RESP: begin
                    if (s_axi_rready) begin
                        s_axi_rvalid <= 1'b0;
                        s_axi_rresp <= AXI_RESP_OKAY;
                        state <= ST_IDLE;
                    end
                end

                default: begin
                    state <= ST_IDLE;
                    wb_cyc_o <= 1'b0;
                    wb_stb_o <= 1'b0;
                    wb_we_o <= 1'b0;
                    s_axi_bvalid <= 1'b0;
                    s_axi_rvalid <= 1'b0;
                    aw_seen <= 1'b0;
                    w_seen <= 1'b0;
                end
            endcase
        end
    end

endmodule
