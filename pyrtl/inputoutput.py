"""
Helper functions for reading and writing hardware files.

Each of the functions in inputoutput take a block and a file descriptor.
The functions provided either read the file and update the Block
accordingly, or write information from the Block out to the file.
"""
from random import randint

import sys
import re
import collections

from . import core
from . import wire
from . import helperfuncs
from . import spice_templates


# -----------------------------------------------------------------
#            __       ___
#    | |\ | |__) |  |  |
#    | | \| |    \__/  |


def input_from_blif(blif, block=None, merge_io_vectors=True):
    """ Read an open blif file or string as input, updating the block appropriately

    Assumes the blif has been flattened and their is only a single module.
    Assumes that there is only one single shared clock and reset
    Assumes that output is generated by Yosys with formals in a particular order
    Ignores reset signal (which it assumes is input only to the flip flops)
    """

    import pyparsing
    from pyparsing import Word, Literal, OneOrMore, ZeroOrMore
    from pyparsing import Suppress, Group, Keyword

    block = core.working_block(block)

    if isinstance(blif, file):
        blif_string = blif.read()
    elif isinstance(blif, str):
        blif_string = blif
    else:
        raise core.PyrtlError('input_blif expecting either open file or string')

    def SKeyword(x):
        return Suppress(Keyword(x))

    def SLiteral(x):
        return Suppress(Literal(x))

    def twire(x):
        """ find or make wire named x and return it """
        s = block.get_wirevector_by_name(x)
        if s is None:
            s = wire.WireVector(bitwidth=1, name=x)
        return s

    # Begin BLIF language definition
    signal_start = pyparsing.alphas + '$:[]_<>\\\/'
    signal_middle = pyparsing.alphas + pyparsing.nums + '$:[]_<>\\\/.'
    signal_id = Word(signal_start, signal_middle)
    header = SKeyword('.model') + signal_id('model_name')
    input_list = Group(SKeyword('.inputs') + OneOrMore(signal_id))('input_list')
    output_list = Group(SKeyword('.outputs') + OneOrMore(signal_id))('output_list')

    cover_atom = Word('01-')
    cover_list = Group(ZeroOrMore(cover_atom))('cover_list')
    namesignal_list = Group(OneOrMore(signal_id))('namesignal_list')
    name_def = Group(SKeyword('.names') + namesignal_list + cover_list)('name_def')

    # asynchronous Flip-flop
    dffas_formal = (SLiteral('C=') + signal_id('C') +
                    SLiteral('R=') + signal_id('R') +
                    SLiteral('D=') + signal_id('D') +
                    SLiteral('Q=') + signal_id('Q'))
    dffas_keyword = SKeyword('$_DFF_PN0_') | SKeyword('$_DFF_PP0_')
    dffas_def = Group(SKeyword('.subckt') + dffas_keyword + dffas_formal)('dffas_def')

    # synchronous Flip-flop
    dffs_def = Group(SKeyword('.latch') +
                     signal_id('D') +
                     signal_id('Q') +
                     SLiteral('re') +
                     signal_id('C'))('dffs_def')
    command_def = name_def | dffas_def | dffs_def
    command_list = Group(OneOrMore(command_def))('command_list')

    footer = SKeyword('.end')
    model_def = Group(header + input_list + output_list + command_list + footer)
    model_list = OneOrMore(model_def)
    parser = model_list.ignore(pyparsing.pythonStyleComment)

    # Begin actually reading and parsing the BLIF file
    result = parser.parseString(blif_string, parseAll=True)
    # Blif file with multiple models (currently only handles one flattened models)
    assert(len(result) == 1)
    clk_set = set([])
    ff_clk_set = set([])

    def extract_inputs(model):
        start_names = [re.sub(r'\[([0-9]+)\]$', '', x) for x in model['input_list']]
        name_counts = collections.Counter(start_names)
        for input_name in name_counts:
            bitwidth = name_counts[input_name]
            if input_name == 'clk':
                clk_set.add(input_name)
            elif not merge_io_vectors or bitwidth == 1:
                block.add_wirevector(wire.Input(bitwidth=1, name=input_name))
            else:
                wire_in = wire.Input(bitwidth=bitwidth, name=input_name, block=block)
                for i in range(bitwidth):
                    bit_name = input_name + '[' + str(i) + ']'
                    bit_wire = wire.WireVector(bitwidth=1, name=bit_name, block=block)
                    bit_wire <<= wire_in[i]

    def extract_outputs(model):
        start_names = [re.sub(r'\[([0-9]+)\]$', '', x) for x in model['output_list']]
        name_counts = collections.Counter(start_names)
        for output_name in name_counts:
            bitwidth = name_counts[output_name]
            if not merge_io_vectors or bitwidth == 1:
                block.add_wirevector(wire.Output(bitwidth=1, name=output_name))
            else:
                wire_out = wire.Output(bitwidth=bitwidth, name=output_name, block=block)
                bit_list = []
                for i in range(bitwidth):
                    bit_name = output_name + '[' + str(i) + ']'
                    bit_wire = wire.WireVector(bitwidth=1, name=bit_name, block=block)
                    bit_list.append(bit_wire)
                wire_out <<= helperfuncs.concat(*bit_list)

    def extract_commands(model):
        # for each "command" (dff or net) in the model
        for command in model['command_list']:
            # if it is a net (specified as a cover)
            if command.getName() == 'name_def':
                extract_cover(command)
            # else if the command is a d flop flop
            elif command.getName() == 'dffas_def' or command.getName() == 'dffs_def':
                extract_flop(command)
            else:
                raise core.PyrtlError('unknown command type')

    def extract_cover(command):
        netio = command['namesignal_list']
        if len(command['cover_list']) == 0:
            output_wire = twire(netio[0])
            output_wire <<= wire.Const(0, bitwidth=1, block=block)  # const "FALSE"
        elif command['cover_list'].asList() == ['1']:
            output_wire = twire(netio[0])
            output_wire <<= wire.Const(1, bitwidth=1, block=block)  # const "TRUE"
        elif command['cover_list'].asList() == ['1', '1']:
            # Populate clock list if one input is already a clock
            if(netio[1] in clk_set):
                clk_set.add(netio[0])
            elif(netio[0] in clk_set):
                clk_set.add(netio[1])
            else:
                output_wire = twire(netio[1])
                output_wire <<= twire(netio[0])  # simple wire
        elif command['cover_list'].asList() == ['0', '1']:
            output_wire = twire(netio[1])
            output_wire <<= ~ twire(netio[0])  # not gate
        elif command['cover_list'].asList() == ['11', '1']:
            output_wire = twire(netio[2])
            output_wire <<= twire(netio[0]) & twire(netio[1])  # and gate
        elif command['cover_list'].asList() == ['1-', '1', '-1', '1']:
            output_wire = twire(netio[2])
            output_wire <<= twire(netio[0]) | twire(netio[1])  # or gate
        elif command['cover_list'].asList() == ['10', '1', '01', '1']:
            output_wire = twire(netio[2])
            output_wire <<= twire(netio[0]) ^ twire(netio[1])  # xor gate
        elif command['cover_list'].asList() == ['1-0', '1', '-11', '1']:
            output_wire = twire(netio[3])
            output_wire <<= (twire(netio[0]) & ~ twire(netio[2])) \
                | (twire(netio[1]) & twire(netio[2]))   # mux
        else:
            raise core.PyrtlError('Blif file with unknown logic cover set '
                                  '(currently gates are hard coded)')

    def extract_flop(command):
        if(command['C'] not in ff_clk_set):
            ff_clk_set.add(command['C'])

        # Create register and assign next state to D and output to Q
        regname = command['Q'] + '_reg'
        flop = wire.Register(bitwidth=1, name=regname)
        flop.next <<= twire(command['D'])
        flop_output = twire(command['Q'])
        flop_output <<= flop

    for model in result:
        extract_inputs(model)
        extract_outputs(model)
        extract_commands(model)


# ----------------------------------------------------------------
#    __       ___  __       ___
#   /  \ |  |  |  |__) |  |  |
#   \__/ \__/  |  |    \__/  |
#

def output_to_trivialgraph(file, block=None):
    """ Walk the block and output it in trivial graph format to the open file """

    block = core.working_block(block)
    nodes = {}
    edges = set([])
    edge_names = {}
    uid = [1]

    def add_node(x, label):
        nodes[x] = (uid[0], label)
        uid[0] = uid[0] + 1

    def add_edge(frm, to):
        if hasattr(frm, 'name') and not frm.name.startswith('tmp'):
            edge_label = frm.name
        else:
            edge_label = ''
        if frm not in nodes:
            frm = producer(frm)
        if to not in nodes:
            to = consumer(to)
        (frm_id, _) = nodes[frm]
        (to_id, _) = nodes[to]
        edges.add((frm_id, to_id))
        if edge_label:
            edge_names[(frm_id, to_id)] = edge_label

    def producer(w):
        """ return the node driving wire (or create it if undefined) """
        assert isinstance(w, wire.WireVector)
        for net in block.logic:
            for dest in net.dests:
                if dest is w:
                    return net
        add_node(w, '???')
        return w

    def consumer(w):
        """ return the node being driven by wire (or create it if undefined) """
        assert isinstance(w, wire.WireVector)
        for net in block.logic:
            for arg in net.args:
                if arg is w:
                    return net
        add_node(w, '???')
        return w

    # add all of the nodes
    for net in block.logic:
        label = str(net.op)
        label += str(net.op_param) if net.op_param is not None else ''
        add_node(net, label)
    for input in block.wirevector_subset(wire.Input):
        label = 'in' if input.name is None else input.name
        add_node(input, label)
    for output in block.wirevector_subset(wire.Output):
        label = 'out' if output.name is None else output.name
        add_node(output, label)
    for const in block.wirevector_subset(wire.Const):
        label = str(const.val)
        add_node(const, label)

    # add all of the edges
    for net in block.logic:
        for arg in net.args:
            add_edge(arg, net)
        for dest in net.dests:
            add_edge(net, dest)

    # print the actual output to the file
    for (id, label) in nodes.values():
        print(id, label, file=file)
    print('#', file=file)
    for (frm, to) in edges:
        print(frm, to, edge_names.get((frm, to), ''), file=file)


# ----------------------------------------------------------------
#         ___  __          __   __
#   \  / |__  |__) | |    /  \ / _`
#    \/  |___ |  \ | |___ \__/ \__>
#

def output_to_verilog(dest_file, block=None):
    """ Walk the block and output it in verilog format to the open file """

    block = core.working_block(block)
    _verilog_check_all_wirenames(block)
    _to_verilog_header(dest_file, block)
    _to_verilog_combinational(dest_file, block)
    _to_verilog_sequential(dest_file, block)
    _to_verilog_footer(dest_file, block)


def _verilog_vector_decl(w):
    return '' if len(w) == 1 else '[%d:0]' % (len(w) - 1)


def _verilog_vector_pow_decl(w):
    return '' if len(w) == 1 else '[0:%d]' % (2 ** len(w) - 1)


def _verilog_check_all_wirenames(block):
    verilog_reserved = \
        """always and assign automatic begin buf bufif0 bufif1 case casex casez cell cmos
        config deassign default defparam design disable edge else end endcase endconfig
        endfunction endgenerate endmodule endprimitive endspecify endtable endtask
        event for force forever fork function generate genvar highz0 highz1 if ifnone
        incdir include initial inout input instance integer join large liblist library
        localparam macromodule medium module nand negedge nmos nor noshowcancelledno
        not notif0 notif1 or output parameter pmos posedge primitive pull0 pull1
        pulldown pullup pulsestyle_oneventglitch pulsestyle_ondetectglitch remos real
        realtime reg release repeat rnmos rpmos rtran rtranif0 rtranif1 scalared
        showcancelled signed small specify specparam strong0 strong1 supply0 supply1
        table task time tran tranif0 tranif1 tri tri0 tri1 triand trior trireg unsigned
        use vectored wait wand weak0 weak1 while wire wor xnor xor
        """
    verilog_reserved_set = set(verilog_reserved.split())
    for w in block.wirevector_subset():
        if not re.match('[_A-Za-z][_a-zA-Z0-9\$]*$', w.name):
            raise core.PyrtlError('error, the wirevector name "%s"'
                                  ' is not a valid Verilog identifier' % w.name)
        if w.name in verilog_reserved_set:
            raise core.PyrtlError('error, the wirevector name "%s"'
                                  ' is a Verilog reserved keyword' % w.name)
        if len(w.name) >= 1024:
            raise core.PyrtlError('error, the wirevector name "%s" is too'
                                  ' long to be a Verilog id' % w.name)


def _to_verilog_header(file, block):
    io_list = [w.name for w in block.wirevector_subset((wire.Input, wire.Output))]
    io_list.append('clk')
    io_list_str = ', '.join(io_list)
    print('module toplevel(%s);' % io_list_str, file=file)

    inputs = block.wirevector_subset(wire.Input)
    outputs = block.wirevector_subset(wire.Output)
    registers = block.wirevector_subset(wire.Register)
    wires = block.wirevector_subset() - (inputs | outputs | registers)
    memory_nets = block.logic_subset(('m', '@'))
    memories = set()

    # Create a set of nets representitive of all memories (eliminating
    # duplicates caused by multiple ports).
    for m in memory_nets:
        if not any(m.op_param[0] == x.op_param[0] for x in memories):
            memories.add(m)

    for w in inputs:
        print('    input%s %s;' % (_verilog_vector_decl(w), w.name), file=file)
    print('    input clk;', file=file)
    for w in outputs:
        print('    output%s %s;' % (_verilog_vector_decl(w), w.name), file=file)
    print('', file=file)

    for w in registers:
        print('    reg%s %s;' % (_verilog_vector_decl(w), w.name), file=file)
    for w in wires:
        print('    wire%s %s;' % (_verilog_vector_decl(w), w.name), file=file)
    print('', file=file)

    for w in memories:
        if w.op == 'm':
            print('    reg%s mem_%s%s;' % (_verilog_vector_decl(w.dests[0]),
                                                    w.op_param[0],
                                                    _verilog_vector_pow_decl(w.args[0])), file=file)
        elif w.op == '@':
            print('    reg%s mem_%s%s;' % (_verilog_vector_decl(w.args[1]),
                                                    w.op_param[0],
                                                    _verilog_vector_pow_decl(w.args[0])), file=file)

    print('', file=file)

    # Generate the initial block for those memories that need it (such as ROMs).
    # FIXME: Right now, the memblock is the only place where those rom values are stored
    # which is bad form (it means the functionality of tne hardware is not completely
    # contained in "core".
    mems_with_initials = [w for w in memories if hasattr(w.op_param[1], 'initialdata')]
    for w in mems_with_initials:
        print('    initial begin', file=file)
        for i in range(2**len(w.args[0])):
            print("        mem_%s[%d]=%d'x%x" % (
                w.op_param[0], i, len(w), w.op_param[1]._get_read_data(i)), file=file)
        print('    end', file=file)
        print('', file=file)


def _to_verilog_combinational(file, block):
    for const in block.wirevector_subset(wire.Const):
            print('    assign %s = %d;' % (const.name, const.val), file=file)

    for net in block.logic:
        if net.op in set('w~'):  # unary ops
            opstr = '' if net.op == 'w' else net.op
            t = (net.dests[0].name, opstr, net.args[0].name)
            print('    assign %s = %s%s;' % t, file=file)
        elif net.op in '&|^+-*<>':  # binary ops
            t = (net.dests[0].name, net.args[0].name, net.op, net.args[1].name)
            print('    assign %s = %s %s %s;' % t, file=file)
        elif net.op == '=':
            t = (net.dests[0].name, net.args[0].name, net.args[1].name)
            print('    assign %s = %s == %s;' % t, file=file)
        elif net.op == 'x':
            # note that the argument order for 'x' is backwards from the ternary operator
            t = (net.dests[0].name, net.args[0].name, net.args[2].name, net.args[1].name)
            print('    assign %s = %s ? %s : %s;' % t, file=file)
        elif net.op == 'c':
            catlist = ', '.join([w.name for w in net.args])
            t = (net.dests[0].name, catlist)
            print('    assign %s = {%s};' % t, file=file)
        elif net.op == 's':
            catlist = ', '.join([net.args[0].name + '[%s]' % str(i) if len(net.args[0]) > 1
                                else net.args[0].name for i in net.op_param])
            t = (net.dests[0].name, catlist)
            print('    assign %s = {%s};' % t, file=file)
        elif net.op == 'r':
            pass  # do nothing for registers
        elif net.op == 'm':
            t = (net.dests[0].name, net.op_param[0], net.args[0].name)
            print('        assign %s = mem_%s[%s];' % t, file=file)
        elif net.op == '@':
            pass
        else:
            raise core.PyrtlInternalError
    print('', file=file)


def _to_verilog_sequential(file, block):
    print('    always @( posedge clk )', file=file)
    print('    begin', file=file)
    for net in block.logic:
        if net.op == 'r':
            t = (net.dests[0].name, net.args[0].name)
            print('        %s <= %s;' % t, file=file)
        elif net.op == '@':
            t = (net.args[2].name, net.op_param[0], net.args[0].name, net.args[1].name)
            print(('        if (%s) begin\n'
                            '                mem_%s[%s] <= %s;\n'
                            '        end') % t, file=file)
    print('    end', file=file)


def _to_verilog_footer(file, block):
    print('endmodule\n', file=file)


def output_verilog_testbench(file, simulation_trace=None, block=None):
    """Output a verilog testbanch for the block/inputs used in the simulation trace."""

    block = core.working_block(block)
    inputs = block.wirevector_subset(wire.Input)
    outputs = block.wirevector_subset(wire.Output)

    # Output header
    print('module tb();', file=file)

    # Declare all block inputs as reg
    print('    reg clk;', file=file)
    for w in inputs:
        print('    reg {:s} {:s};'.format(_verilog_vector_decl(w), w.name), file=file)

    # Declare all block outputs as wires
    for w in outputs:
        print('    wire {:s} {:s};'.format(_verilog_vector_decl(w), w.name), file=file)
    print(file=file)

    # Instantiate logic block
    io_list = [w.name for w in block.wirevector_subset((wire.Input, wire.Output))]
    io_list.append('clk')
    io_list_str = ['.{0:s}({0:s})'.format(w) for w in io_list]
    print('    toplevel block({:s});\n'.format(', '.join(io_list_str)), file=file)

    # Generate clock signal
    print('    always', file=file)
    print('        #0.5 clk = ~clk;\n', file=file)

    # Move through all steps of trace, writing out input assignments per cycle
    print('    initial begin', file=file)
    print('        $dumpfile ("waveform.vcd");', file=file)
    print('        $dumpvars;\n', file=file)
    print('        clk = 0;', file=file)

    for i in range(len(simulation_trace)):
        for w in inputs:
            print('        {:s} = {:s}{:d};'.format(
                w.name,
                "{:d}'d".format(len(w)),
                simulation_trace.trace[w][i]), file=file)
        print('\n        #2', file=file)

    # Footer
    print('        $finish;', file=file)
    print('    end', file=file)
    print('endmodule', file=file)


# ---------------------------------
#   _____ _____ _____ _____ ______
#  / ____|  __ \_   _/ ____|  ____|
# | (___ | |__) || || |    | |__
#  \___ \|  ___/ | || |    |  __|
#  ____) | |    _| || |____| |____
# |_____/|_|   |_____\_____|______|
#

# used to create intermediate nodes/wires
SPICE_NODES = 0
SPICE_FETS = 0


def _new_fet():
    global SPICE_FETS
    SPICE_FETS += 1
    return "M"+str(SPICE_FETS)


def _new_node():
    global SPICE_NODES
    SPICE_NODES += 1
    return "N"+str(SPICE_NODES)


def _render_nand(output_file, a, b, out):
    print(spice_templates.NAND_TEMPLATE.format(
        InputA=a, InputB=b, output=out, M1=_new_fet(), M2=_new_fet(),
        M3=_new_fet(), M4=_new_fet(), node=_new_node()
    ), file=output_file)


def output_to_spice(output_file=sys.stdout, block=None, sim_time="20", sim_min_step="1ms"):
    """
    Write SPICE model to output file.

    :param output_file: Opened file-like object.
    :return: None
    """
    working_block = core.working_block(block)
    operator_set = set('~&|^rwcsm@')

    # build alias table
    alias_table = {}    # alias table maps source wire name to their destination wire names
    for net in working_block.logic:
        if net.op == "w" and isinstance(net.dests[0], wire.Output):
            alias_table[net.args[0].name] = net.dests[0].name
        elif isinstance(net.args[0], wire.Input):
            alias_table[net.dests[0].name] = net.args[0].name

    # print >> output_file, repr(alias_table)

    # convenience function for alias table
    def alias(name):
        if name in iter(alias_table.keys()):
            return alias_table[name]
        return name

    # comment on top
    print("* SPICE export from PyRTL", file=output_file)

    # setup power source
    print("Vdd Vdd 0 5", file=output_file)

    for net in working_block.logic:
        # do some checks to make sure our working block is sane.
        if net.op not in operator_set:
            error_msg = "Illegal operator {} in block logic. ".format(str(net.op))
            error_msg += "Please synthesize/optimize design before exporting to SPICE."
            raise core.PyrtlError(error_msg)
        if len(net.args) > 2:
            error_msg = "Logic net `{}` has the wrong number of args ({}). "\
                .format(str(net), str(len(net.args)))
            error_msg += "Please synthesize/optimize design before exporting to SPICE."
            raise core.PyrtlError(error_msg)

        # start by processing inputs
        # TODO: deal with multiple inputs? This may be a select logicnet
        if isinstance(net.args[0], wire.Input):
            # for inputs, create a new voltage source to drive a new input
            input_wire = net.args[0]
            period = randint(2, 10)
            on = period/2
            print("V{name} {node} 0 PULSE(0 5 0 0 0 {on} {period})".format(
                name=alias(input_wire.name), node=alias(net.dests[0].name),
                on=str(on), period=str(period)), file=output_file)
        elif net.op == '&':
            # for AND, do NAND and invert it
            intermediate_node = _new_node()
            _render_nand(output_file,
                         alias(net.args[0].name), alias(net.args[1].name), intermediate_node)
            _render_nand(output_file,
                         intermediate_node, intermediate_node, alias(net.dests[0].name))
        elif net.op == "|":
            # handle OR; invert both inputs and put those through another NAND
            intermediate_a = _new_node()
            intermediate_b = _new_node()
            _render_nand(output_file,
                         alias(net.args[0].name), alias(net.args[0].name), intermediate_a)
            _render_nand(output_file,
                         alias(net.args[1].name), alias(net.args[1].name), intermediate_b)
            _render_nand(output_file,
                         intermediate_a, intermediate_b, alias(net.dests[0].name))
        elif net.op == "^":
            # XOR
            a_nand_b = _new_node()
            tim = _new_node()
            sherwood = _new_node()
            _render_nand(output_file, alias(net.args[0].name), alias(net.args[1].name), a_nand_b)
            _render_nand(output_file, alias(net.args[0].name), a_nand_b, tim)
            _render_nand(output_file, alias(net.args[1].name), a_nand_b, sherwood)
            _render_nand(output_file, tim, sherwood, alias(net.dests[0].name))
        elif net.op == 'w':
            # print >> output_file, "* {} is {}".format(net.dests[0].name, net.args[0].name)
            # do nothing for now?
            pass
        else:
            print(repr(net), file=output_file)

    # print footer
    print(".model NMOS NMOS", file=output_file)
    print(".model PMOS PMOS", file=output_file)
    print(".tran 0 {sim_time} 0 {sim_min_step}"\
        .format(sim_time=sim_time, sim_min_step=sim_min_step), file=output_file)
    print(".backanno", file=output_file)
    print(".end", file=output_file)
