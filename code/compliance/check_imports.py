"""AST import audit over the files INSIDE code.zip, cross-checked against stdlib."""
import ast, sys, zipfile, json
from pathlib import Path
ROOT = Path("/Users/gamagarcia/Desktop/BUY-OR-WAIT")
z = zipfile.ZipFile(ROOT/"code.zip")
py = [n for n in z.namelist() if n.endswith(".py")]
std = set(sys.stdlib_module_names)
local_roots = {"code","contracts","loader","main","finance","decision","extraction",
               "evaluation","analysis","audit","release","riesgo","forensics","patterns","cache","compliance"}
ext = {}
allmods = {}
syntax_err = []
for n in sorted(py):
    src = z.read(n).decode("utf-8")
    try:
        tree = ast.parse(src, filename=n)
    except SyntaxError as e:
        syntax_err.append((n,str(e))); continue
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                top = a.name.split(".")[0]
                allmods.setdefault(top,set()).add(n)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue  # relative -> local
            if node.module:
                top = node.module.split(".")[0]
                allmods.setdefault(top,set()).add(n)
for m,files in sorted(allmods.items()):
    if m in std or m in local_roots:
        continue
    ext[m] = sorted(files)
print("py files in zip:", len(py))
print("syntax errors:", syntax_err)
print("distinct top-level imports:", len(allmods))
print("stdlib/local OK:", sorted(m for m in allmods if m in std or m in local_roots))
print()
print("NON-STDLIB / NON-LOCAL IMPORTS:", json.dumps(ext, indent=2) if ext else "NONE")
# network modules present?
net = {m:sorted(allmods[m]) for m in ("socket","urllib","http","requests","httpx","ssl","ftplib","smtplib","telnetlib","xmlrpc","asyncio") if m in allmods}
print()
print("NETWORK-CAPABLE STDLIB IMPORTS:", json.dumps(net, indent=2) if net else "NONE")
