import importlib.util
import sys
from pathlib import Path


def test(path_lib):
    tl = ToolLoader(path_lib)
    print(tl.get_info())
    print(tl.get_exec("pubmed_search"))

class ToolLoader:

    def __init__(self, lib_path):
        folder = Path(lib_path)
        self.modules = {}

        for path in sorted(folder.glob("*.py")):
            if path.name.startswith("_"):          # skip __init__.py, _private.py
                continue

            name = path.stem
            spec = importlib.util.spec_from_file_location(name, path)
            if spec is None:
                raise ValueError("Error loading module")

            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module             # lets the file import itself / be found later
            spec.loader.exec_module(module)        # actually runs the file

            if not "META_INF" in vars(module):
                raise TypeError(f"Missing META_INF in {name}")
            if not module.META_INF['function']['name'] == name :
                 raise TypeError(f"Name in META_INF {module.META_INF['function']['name']} must match {name}")

            if not "run" in vars(module):
                raise TypeError(f"Missing run in {name}")

            self.modules[name] = {"info":
                module.META_INF,
                "function" : module.run
                }


    def get_info(self):
        infs = []
        for m in self.modules.values():
            infs.append(m["info"])
        return infs


    def get_exec(self, name):
        if not name in self.modules:
            print("No Tool named " + name)
            return None
        return self.modules[name]["function"]
