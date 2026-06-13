import os

_package_dir = os.path.join(os.path.dirname(__file__), "interface")
__path__ = [_package_dir]

_nested_init = os.path.join(_package_dir, "__init__.py")
with open(_nested_init, "r", encoding="utf-8") as _handle:
    exec(compile(_handle.read(), _nested_init, "exec"), globals(), globals())
