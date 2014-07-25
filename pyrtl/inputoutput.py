import sys
from pyrtl import *

#-----------------------------------------------------------------
#            __       ___ 
#    | |\ | |__) |  |  |  
#    | | \| |    \__/  |  
                             

def read_block_as_blif( block, blif_file ):
    """ Read an open blif file as input, updating the block appropriately 
    
        Assumes the blif has been flattened and their is only a single module.
        Assumes that there is only one single shared clock and reset
        Assumes that output is generated by Yosys with formals in a particular order
        Ignores reset signal (which it assumes is input only to the flip flops)
    """

    from pyparsing import Word, Literal, infixNotation, OneOrMore, ZeroOrMore
    from pyparsing import oneOf, Suppress, Group, Optional, Keyword

    def SKeyword(x): return Suppress(Keyword(x))
    def SLiteral(x): return Suppress(Literal(x))
    def twire(x):
        """ find or make wire named x and return it """
        s = block.get_wire_by_name(x)
        if s == None:
            s = Wire(block,x)
        return s

    # Begin BLIF language definition
    signal_start = pyparsing.alphas + '$:[]_<>\\\/'
    signal_middle = pyparsing.alphas + pyparsing.nums + '$:[]_<>\\\/.'
    signal_id = Word( signal_start, signal_middle )
    header = SKeyword('.model') + signal_id('model_name')
    input_list = Group(SKeyword('.inputs') + OneOrMore(signal_id))('input_list')
    output_list = Group(SKeyword('.outputs') + OneOrMore(signal_id))('output_list')

    cover_atom = Word( '01-' )
    cover_list = Group( ZeroOrMore(cover_atom) )('cover_list')
    namesignal_list = Group( OneOrMore(signal_id) )('namesignal_list')
    name_def = Group( SKeyword('.names') + namesignal_list + cover_list )('name_def')

    # asynchronous Flip-flop
    dffas_formal = SLiteral('C=')+signal_id('C') + SLiteral('D=')+signal_id('D') \
        + SLiteral('Q=')+signal_id('Q') + SLiteral('R=')+signal_id('R')
    dffas_def = Group( SKeyword('.subckt') + (SKeyword('$_DFF_PN0_') | SKeyword('$_DFF_PP0_')) + dffas_formal )('dffas_def')
    # synchronous Flip-flop
    dffs_def = Group( SKeyword('.latch') + signal_id('D') + signal_id('Q') + SLiteral('re') + signal_id('C') )('dffs_def')

    command_def = name_def | dffas_def | dffs_def
    command_list = Group( OneOrMore(command_def) )('command_list')

    footer = SKeyword('.end')
    model_def = Group( header + input_list + output_list + command_list + footer )
    model_list = OneOrMore( model_def )
    parser = model_list.ignore(pyparsing.pythonStyleComment)


    # Begin actually reading and parsing the BLIF file
    result = parser.parseString( f.read(), parseAll=True )
    assert( len(result)==1 ) # Blif file with multiple models (currently only handles one flattened models)
    clk_set = {}

    def extract_inputs(model):
        for input_name in model['input_list']:
            if input_name == 'clk':
                clk_set.add(input_name)
            else:
                block.add_wire( InputWire(block,input_name) )

    def extract_outputs(model):
        for output_name in model['output_list']:
            block.add_wire( OutputWire(block,output_name) )

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
                 raise BlifFormatError('unknown command type')

    def extract_cover(command):
        netio = command['namesignal_list']
        if len(command['cover_list']) == 0:
            twire(netio[0]) <= ConstWire(self, 0)  # const "FALSE"
        elif command['cover_list'].asList() == ['1']:
            twire(netio[0]) <= ConstWire(self, 1)  # const "TRUE"
        elif command['cover_list'].asList() == ['1','1']:
            #Populate clock list if one input is already a clock
            if(netio[1] in clk_set):
                clk_set.add(netio[0])
            elif(netio[0] in clk_set):
                clk_set.add(netio[1])
            else:
                twire(netio[1]) <= twire(netio[0]) # simple wire
        elif command['cover_list'].asList() == ['0','1']:
            twire(netio[1]) <= ~ twire(netio[0]) # not gate
        elif command['cover_list'].asList() == ['11','1']:
            twire(netio[2]) <= twire(netio[0]) & twire(netio[1]) # and gate
        elif command['cover_list'].asList() == ['1-','1','-1','1']:
            twire(netio[2]) <= twire(netio[0]) | twire(netio[1]) # or gate
        elif command['cover_list'].asList() == ['10','1','01','1']:
            twire(netio[2]) <= (twire(netio[0]) & ~twire(netio[1])) \
                | (~twire(netio[0]) & twire(netio[1])) # xor gate
        elif command['cover_list'].asList() == ['1-0', '1', '-11', '1']:
            twire(netio[3]) <= (twire(netio[0]) & ~ twire(netio[2]) ) \
                | (twire(netio[1]) & twire(netio[2]))  # mux
        else:
            raise BlifFormatError('Blif file with unknown logic cover set (currently gates are hard coded)')

    def extract_flop(command):
        if(command['C'] not in ff_clk_set):
            ff_clk_set.add(command['C'])
            tlog.verbose_status('    Inferring %s as a clock signal'%command['C'])

        #Create register and assign next state to D and output to Q
        flop = Register(self,command['Q'] + '_reg')
        flop.next <= twire(command['D'])
        twire(command['Q']) <= flop

    for model in result:
        extract_inputs(model)
        extract_outputs(model)
        extract_commands(model)



#-----------------------------------------------------------------
#    __       ___  __       ___ 
#   /  \ |  |  |  |__) |  |  |  
#   \__/ \__/  |  |    \__/  |  
#                             


def output_block_as_trivialgraph( block, file )
    """ Walk the block and output it in trivial graph format to the open file """

    uid = 1
    nodes = {}
    edges = set([])
    edge_names = {}

    def add_node(x, label):
        nodes[x] = (uid, label)
        uid += 1

    def add_edge(frm, to):
        if hasattr(frm, 'name') and not frm.name.startswith('tmp'):
            edge_label = frm.name
        else:
            edge_label = ''
        if frm not in nodes:
            frm = producer(frm)
        if to not in self.nodes:
            to = consumer(to)
        (frm_id, _) = nodes[frm]
        (to_id, _) = nodes[to]
        edges.add((frm_id, to_id))
        if edge_label:
            edge_names[(frm_id, to_id)] = edge_label

    def producer(self, wire):
        """ return the node driving wire (or create it if undefined) """
        assert isinstance(wire, WireVector)
        for net in sorted(block.logic):
            for dest in sorted(net.dests):
                if dest == wire:
                    return net
        add_node(wire, '???')
        return wire

    def consumer(self, wire):
        """ return the node being driven by wire (or create it if undefined) """
        assert isinstance(wire, WireVector)
        for net in sorted(self.block.logic):
            for arg in sorted(net.args):
                if arg == wire:
                    return net
        self.add_node(wire, '???')
        return wire

    # add all of the nodes
    for net in sorted(block.logic):
        label = str(net.op)
        label += str(net.op_param) if net.op_param is not None else ''
        add_node(net, label)
    for input in sorted(block.wirevector_subset(Input)):
        label = 'in' if input.name is None else input.name
        add_node(input, label)
    for output in sorted(block.wirevector_subset(Output)):
        label = 'out' if output.name is None else output.name
        add_node(output, label)
    for const in sorted(block.wirevector_subset(Const)):
        label = str(const.val)
        add_node(const, label)

    # add all of the edges
    for net in sorted(block.logic):
        for arg in sorted(net.args):
            add_edge(arg, net)
        for dest in sorted(net.dests):
            add_edge(net, dest)

    # print the actual output to the file
    for (id, label) in sorted(self.nodes.values()):
        print >> file, id, label
    print >> file, '#'
    for (frm, to) in sorted(self.edges):
        print >> file, frm, to, self.edge_names.get((frm, to), '')

