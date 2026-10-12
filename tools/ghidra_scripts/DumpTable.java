// Create functions for every pointer in a handler table and decompile them.
// args: <table_addr_hex> <count> <out_file>
// @category IPL
import ghidra.app.cmd.function.CreateFunctionCmd;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import java.io.FileWriter;

public class DumpTable extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] a = getScriptArgs();
        long table = Long.parseLong(a[0].replace("0x", ""), 16);
        int count = Integer.parseInt(a[1]);
        var space = currentProgram.getAddressFactory().getDefaultAddressSpace();
        var mem = currentProgram.getMemory();
        var listing = currentProgram.getListing();
        var fm = currentProgram.getFunctionManager();
        DecompInterface d = new DecompInterface();
        d.openProgram(currentProgram);
        try (FileWriter w = new FileWriter(a[2])) {
            for (int i = 0; i < count; i++) {
                long p = mem.getInt(space.getAddress(table + 4L * i)) & 0xFFFFFFFFL;
                w.write(String.format("// ==== entry %d -> %08x\n", i, p));
                if (p < 0x81300000L || p >= 0x81384000L) { w.write("// (not code)\n\n"); continue; }
                Address ad = space.getAddress(p);
                Function f = fm.getFunctionAt(ad);
                if (f == null) {
                    listing.clearCodeUnits(ad, ad.add(3), false);
                    disassemble(ad);
                    new CreateFunctionCmd(ad).applyTo(currentProgram, monitor);
                    f = fm.getFunctionAt(ad);
                }
                if (f == null) { w.write("// create failed\n\n"); continue; }
                DecompileResults r = d.decompileFunction(f, 60, monitor);
                w.write(r != null && r.decompileCompleted() ? r.getDecompiledFunction().getC() : "// decompile failed\n");
                w.write("\n");
            }
        }
        println("done");
    }
}
