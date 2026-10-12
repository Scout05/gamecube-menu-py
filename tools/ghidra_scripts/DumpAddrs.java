// Create (if needed) and decompile functions at the given addresses.
// args: <out_file> <addr1> <addr2> ...
// @category IPL
import ghidra.app.cmd.function.CreateFunctionCmd;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import java.io.FileWriter;

public class DumpAddrs extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] a = getScriptArgs();
        var space = currentProgram.getAddressFactory().getDefaultAddressSpace();
        var listing = currentProgram.getListing();
        var fm = currentProgram.getFunctionManager();
        DecompInterface d = new DecompInterface();
        d.openProgram(currentProgram);
        try (FileWriter w = new FileWriter(a[0])) {
            for (int i = 1; i < a.length; i++) {
                Address ad = space.getAddress(Long.parseLong(a[i].replace("0x", ""), 16));
                Function f = fm.getFunctionAt(ad);
                if (f == null) {
                    f = fm.getFunctionContaining(ad);
                    if (f != null && !f.getEntryPoint().equals(ad)) f = null;
                }
                if (f == null) {
                    listing.clearCodeUnits(ad, ad.add(3), false);
                    disassemble(ad);
                    new CreateFunctionCmd(ad).applyTo(currentProgram, monitor);
                    f = fm.getFunctionAt(ad);
                }
                w.write("// ==== " + ad + "\n");
                if (f == null) { w.write("// create failed\n\n"); continue; }
                DecompileResults r = d.decompileFunction(f, 60, monitor);
                w.write(r != null && r.decompileCompleted() ? r.getDecompiledFunction().getC() : "// decompile failed\n");
                w.write("\n");
            }
        }
        println("done");
    }
}
