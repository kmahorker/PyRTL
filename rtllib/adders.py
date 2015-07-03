import sys
sys.path.append("..")
import pyrtl
from pyrtl import *
import libutils


def main():
    print "You should be looking at the test case folder"


def kogge_stone(a, b, cin=0):
    """
    Creates a Kogge-Stone adder given two inputs
    :param a, b: The two Wirevectors to add up (bitwidths don't need to match)
    :param cin: An optimal carry Wirevector or value
    :return: a Wirevector representing the output of the adder

    The Kogge-Stone adder is a fast tree-based adder with O(log(n))
    propagation delay, useful for performance critical designs. However,
    it has O(n log(n)) area usage, and large fan out.
    """
    a, b = libutils.match_bitwidth(a, b)

    prop_orig = a ^ b
    prop_bits = [i for i in prop_orig]
    gen_bits = [i for i in a & b]
    prop_dist = 1

    # creation of the carry calculation
    while prop_dist < len(a):
        for i in reversed(range(prop_dist, len(a))):
            prop_old = prop_bits[i]
            gen_bits[i] = gen_bits[i] | (prop_old & gen_bits[i - prop_dist])
            if i >= prop_dist*2:  # to prevent creating unnecessary nets and wires
                prop_bits[i] = prop_old & prop_bits[i - prop_dist]
        prop_dist *= 2

    # assembling the result of the addition
    gen_bits.insert(0, as_wires(cin))  # preparing the cin (and conveniently shifting the gen bits)
    return concat(*reversed(gen_bits)) ^ prop_orig


def one_bit_add(a, b, cin):
    assert len(a) == len(b) == 1  # len returns the bitwidth
    sum = a ^ b ^ cin
    cout = a & b | a & cin | b & cin
    return sum, cout


def ripple_add(a, b, cin=0):
    a, b = libutils.match_bitwidth(a, b)

    def ripple_add_partial(a, b, cin=0):  # this actually makes less s anc c blocks
        assert len(a) == len(b)
        if len(a) == 1:
            sumbits, cout = one_bit_add(a, b, cin)
        else:
            lsbit, ripplecarry = one_bit_add(a[0], b[0], cin)
            msbits, cout = ripple_add_partial(a[1:], b[1:], ripplecarry)
            sumbits = pyrtl.concat(msbits, lsbit)
        return sumbits, cout

    sumbits, cout = ripple_add_partial(a, b, cin)
    return concat(cout, sumbits)

def carrysave_adder(a, b, c):
    assert len(a) == len(b)
    partial_sum = a ^ b ^ c
    partial_shift = pyrtl.concat(0, partial_sum)
    shift_carry = (a | b) & (a | c) & (b | c)
    shift_carry_1 = pyrtl.concat(shift_carry, 0)
    sum_1, c_out = ripple_add(partial_shift, shift_carry_1, 0)
    sum = pyrtl.concat(c_out, sum_1)
    return sum


if __name__ == "__main__":
    main()
