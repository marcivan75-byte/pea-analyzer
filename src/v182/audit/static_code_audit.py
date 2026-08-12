from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import argparse
import ast
import json


@dataclass(frozen=True)
class Finding:
    severity:str
    code:str
    path:str
    line:int
    message:str


MUTABLE=(ast.List,ast.Dict,ast.Set)


def audit_file(path:Path)->list[Finding]:
    findings=[]
    try: tree=ast.parse(path.read_text(encoding="utf-8"),filename=str(path))
    except (OSError,UnicodeDecodeError,SyntaxError) as exc:
        return [Finding("HIGH","PARSE_ERROR",str(path),getattr(exc,"lineno",0) or 0,str(exc))]
    for node in ast.walk(tree):
        if isinstance(node,ast.ExceptHandler):
            if node.type is None:
                findings.append(Finding("HIGH","BARE_EXCEPT",str(path),node.lineno,"Bare except hides unexpected failures."))
            if len(node.body)==1 and isinstance(node.body[0],ast.Pass):
                findings.append(Finding("HIGH","SILENT_EXCEPT_PASS",str(path),node.lineno,"Exception is silently discarded."))
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
            defaults=list(node.args.defaults)+[x for x in node.args.kw_defaults if x is not None]
            for default in defaults:
                if isinstance(default,MUTABLE):
                    findings.append(Finding("HIGH","MUTABLE_DEFAULT",str(path),node.lineno,f"Mutable default in {node.name}."))
        if isinstance(node,ast.Compare):
            operands=[node.left,*node.comparators]
            if any(isinstance(x,ast.Constant) and x.value is None for x in operands) and any(isinstance(op,(ast.Eq,ast.NotEq)) for op in node.ops):
                findings.append(Finding("MEDIUM","EQ_NONE",str(path),node.lineno,"Use `is None` / `is not None`."))
    return findings


def run(root:Path,output:Path|None=None)->dict:
    files=sorted({*root.glob("src/**/*.py"),*root.glob("tests/**/*.py")}); findings=[]
    for path in files: findings.extend(audit_file(path))
    payload={"python_files":len(files),"high":sum(f.severity=="HIGH" for f in findings),"medium":sum(f.severity=="MEDIUM" for f in findings),"findings":[asdict(f) for f in findings]}
    if output:
        output.parent.mkdir(parents=True,exist_ok=True); output.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    return payload


def main():
    p=argparse.ArgumentParser(); p.add_argument("--root",default="."); p.add_argument("--output",default="outputs/audit/PYTHON_STATIC_AUDIT.json"); p.add_argument("--fail-high",action="store_true"); args=p.parse_args()
    payload=run(Path(args.root),Path(args.output)); print(json.dumps(payload,ensure_ascii=False,indent=2))
    if args.fail_high and payload["high"]: raise SystemExit(1)


if __name__=="__main__": main()
