
from assemblage.worker.disasm import Disassembler, AvailableDisassembler

def test_disasm():
    disasm = Disassembler(AvailableDisassembler.DDISASM)
    disasm.disasm("assemblage/test/telegramtranslit.exe", "./")

if __name__ == "__main__":
    test_disasm()
