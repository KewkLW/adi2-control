import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.listing.*;
import java.io.*;
public class DecompAll extends GhidraScript {
  public void run() throws Exception {
    String out = getScriptArgs()[0];
    PrintWriter pw = new PrintWriter(new FileWriter(out));
    DecompInterface d = new DecompInterface(); d.openProgram(currentProgram);
    for (Function f : currentProgram.getFunctionManager().getFunctions(true)) {
      if (f.isExternal() || f.isThunk()) continue;
      DecompileResults r = d.decompileFunction(f, 60, monitor);
      pw.println("// ===== " + f.getName() + " @ " + f.getEntryPoint());
      if (r != null && r.getDecompiledFunction() != null) pw.println(r.getDecompiledFunction().getC());
    }
    pw.close();
  }
}
