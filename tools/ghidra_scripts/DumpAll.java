// Decompile every function in the BS2 code range to one C file per 0x4000-byte bucket.
// Also seeds functions at all bl targets / stwu prologues first, then decompiles.
// @category IPL
import ghidra.app.cmd.function.CreateFunctionCmd;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import java.io.File;
import java.io.FileWriter;
import java.util.TreeMap;

public class DumpAll extends GhidraScript {
    static final long LO = 0x81300000L, HI = 0x81384000L;

    @Override
    public void run() throws Exception {
        String out = getScriptArgs().length > 0 ? getScriptArgs()[0] : "decomp";
        new File(out).mkdirs();
        FunctionManager fm = currentProgram.getFunctionManager();
        var mem = currentProgram.getMemory();
        var space = currentProgram.getAddressFactory().getDefaultAddressSpace();

        // seed: bl targets inside the code range
        int seeded = 0;
        for (long a = LO; a < HI; a += 4) {
            int w = mem.getInt(space.getAddress(a));
            if ((w >>> 26) == 18 && (w & 3) == 1) {
                int li = w & 0x03FFFFFC;
                if ((li & 0x02000000) != 0) li |= 0xFC000000;
                long t = (a + li) & 0xFFFFFFFFL;
                if (t >= LO && t < HI && fm.getFunctionAt(space.getAddress(t)) == null
                        && fm.getFunctionContaining(space.getAddress(t)) == null) {
                    disassemble(space.getAddress(t));
                    new CreateFunctionCmd(space.getAddress(t)).applyTo(currentProgram, monitor);
                    seeded++;
                }
            }
        }
        println("seeded " + seeded);

        DecompInterface d = new DecompInterface();
        d.openProgram(currentProgram);
        TreeMap<Long, StringBuilder> buckets = new TreeMap<>();
        int n = 0;
        for (Function f : fm.getFunctions(true)) {
            long a = f.getEntryPoint().getOffset();
            if (a < LO || a >= HI) continue;
            DecompileResults r = d.decompileFunction(f, 60, monitor);
            StringBuilder sb = buckets.computeIfAbsent(a & ~0x3FFFL, k -> new StringBuilder());
            sb.append("// ==== ").append(f.getName()).append(" @ ").append(f.getEntryPoint()).append("\n");
            if (r != null && r.decompileCompleted()) sb.append(r.getDecompiledFunction().getC());
            else sb.append("// decompile failed\n");
            sb.append("\n");
            n++;
        }
        for (var e : buckets.entrySet()) {
            try (FileWriter w = new FileWriter(new File(out, String.format("bs2_%08x.c", e.getKey())))) {
                w.write(e.getValue().toString());
            }
        }
        println("decompiled " + n);
    }
}
