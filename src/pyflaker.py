#!/usr/bin/env python3
# vim: set expandtab softtabstop=4 shiftwidth=4:
#-------------------------------------------------------------------------------
#--   PyFlaker - wrapper for 'pyflakes' and 'frosted'                         --
#--   Copyright (C) 2012-2022  Eric Fahlgren                                  --
#-------------------------------------------------------------------------------
"""
Customizations to the static analysis tool `frosted` (which replaced `pyflakes`
years ago, but now `frosted` is not being maintained, so we may switch back).

We inject several new features:

1) Addition of a ``pyflakes:ignore`` directive, for use with warnings that
   are benign and simply annoying.

2) Recognition of symbols that exist as part of the global environment
   when executing a lmpy file.

3) We suppress messages about ``from x import *`` inside __init__.py files.

4) Addition of a ``pyflakes:ignore_all(list,of,names)`` that allows you to
   define global symbols that are to be ignored when they would normally
   cause an `undefined name` error.  The directive must be placed on a
   line that would cause the first error, and contain no spaces.

   >>> mass = model.mass # pyflakes:ignore_all(model,submodel,ROOT)

   Use this sparingly, it is intended to be used only in files that are
   processed by ``exec`` and have a definite global context defined, but
   don't import the ignored symbols.

Whitespace is not significant in any of the above directives.
"""
#-------------------------------------------------------------------------------

import sys
import os

import glob
import fnmatch
import json

import ast
import re
from linecache import getlines

install_dir = os.path.dirname(os.path.realpath(__file__))  # Chase through any symbolic link to real path.

#-------------------------------------------------------------------------------

def parse_args():
    from argparse import ArgumentParser

    engines = 'frosted', 'flakes'

    parser = ArgumentParser()
    parser.add_argument("-v", "--verbose",    default=0,     action="count",       help="Increase the verbosity level each time you specify it.")
    parser.add_argument("-p", "--dprint",     default=False, action="store_true",  help="Scan for diagnostic print statements not in '__main__'.")
    parser.add_argument("-q", "--quiet",      default=False, action="store_true",  help="Suppress all the 'info' messages.")
    parser.add_argument("-S", "--summarize",  default=False, action="store_true",  help="Summarize the files scanned.")
    parser.add_argument("-s", "--showall",    default=False, action="store_true",  help="Show all messages, including those from suppressed symbols.")
    parser.add_argument("-u", "--use",        default=engines[0], choices=engines, help='Underlying lint engine to use.  Default: %(default)r.')
    args, extra = parser.parse_known_args()  # Peel off our own arguments, leave the rest for pyflakes or frosted.
    sys.argv = sys.argv[:1] + extra

    global P, C, M

    if args.use == "flakes":
        try:
            import pyflakes
        except ImportError:
            print("ERROR: You must 'pip install pyflakes' in order to use the --use=flakes option.", file=sys.stderr)
            raise SystemExit
        else:
            del pyflakes

        #print("Using pyflakes")
        import pyflakes.api      as P
        import pyflakes.checker  as C
        import pyflakes.messages as M

        class GetRidOfObject(M.Message):
            message = "'class %s(object...': get rid of legacy 'object' in class declaration"
            def __init__(self, filename, loc, name, thing):
                super().__init__(filename, loc)
                self.message_args = name
        M.GetRidOfObject = GetRidOfObject

        class NeedKwOnlyArgument(M.Message):
            message = "%s() needs kw-only argument(s): %s"
            def __init__(self, filename, loc, name, thing):
                super().__init__(filename, loc)
                self.message_args = name, thing
        M.NeedKwOnlyArgument = NeedKwOnlyArgument

        class TryExceptPass(M.Message):
            message = "try-except with call in body and AttributeError%s%s"
            def __init__(self, filename, loc, name, thing):
                super().__init__(filename, loc)
                self.message_args = name, thing
        M.TryExceptPass = TryExceptPass

        class name:
            """ Return the class name of the derived message type, as frosted does. """
            def __get__(self, instance, owner=None):
                return owner.__name__
        M.Message.name = name()

    elif args.use == "frosted":
        try:
            import frosted
        except ImportError:
            print("ERROR: You must 'pip install frosted' in order to use the --use=frosted option.", file=sys.stderr)
            raise SystemExit
        else:
            if frosted.__version__ < "1.5":
                print(f"WARNING: Your installed version of frosted ({frosted.__version__}) "
                       "is too old to support Python 3.7 or later, talk to Eric about 1.5.", file=sys.stderr)
            del frosted

        if args.verbose:
            # Need to pass it down to frosted, too.
            sys.argv.append("--verbose")

        #print("Using frosted")
        import frosted.main      as P  # pyflakes:ignore
        import frosted.checker   as C
        import frosted.messages  as M

        if hasattr(C.Checker, "handleChildren"):
            raise ImportError("Something has changed in frosted, check it out.")
        C.Checker.handleChildren = C.Checker.handle_children

        M.TryExceptPass  = M.MessageType("E999", "TryExceptPass",  "try-except with call in body and AttributeError")
        M.GetRidOfObject = M.MessageType("E999", "GetRidOfObject", "remove legacy 'object' from class bases")

    return args

cmd = parse_args()

#-------------------------------------------------------------------------------

class Ignore:
    """ Mechanism to ignore undefined symbols or classes of error message for
        various types of file, and also to ignore files that contain certain
        constructs (explicit "ignore me" tags, or SWIG-generated files).

        The symbols are ignored by file name matching, see the json file for
        the "files" list that defines glob-style wildcards for matching.

        Format of the json file is a single dict.  Section names are arbitrary,
        use nice names for human consumption.  There are three entries in each
        section, "files", "ignore_types" and "ignore_symbols".  The "files"
        entry is required, the "ignore_*" are both optional.  All are lists
        of string values.

        When an input file name is found to match against an entry in "files",
        then the ignore symbols and message types are appended for that file.
        Every section is processed for every file, so the ignore lists may be
        an accumulation from several sections.

        >>> {
        >>>     "Python": {
        >>>         "files":          [ "*.py" ],
        >>>         "ignore_types":   [ "ImportStarUsed" ],
        >>>         "ignore_symbols": [ "_", "A" ]
        >>>     }
        >>> }

        Finding the values of symbols to be added is easy: they're just
        the variable, function, method or class name that appears in the
        'undefined symbol' error message.  These are typically "built-ins"
        from frameworks other than base Python (see the "SCons" section of the
        ".pyflaker" config file).

        The names of types are harder as they are the linter's internal class
        name of the error message.  Run with '--verbose' and the name will be
        appended to the error message.  There may or may not be inconsistency
        between 'frosted' and 'pyflakes', although I haven't run into any yet,
        but I have not looked very hard either.

        TODO: Add more diagnostic output if 'cmd.verbose' is set.
    """
    def __init__(self):
        self._symbols = set()  # Reset at start of each file.
        self._files   = set()  # Accumulates as the run goes on, never reset.
        self._types   = set()  # Also reset for each file.

        self.symbol_table = None
        for config_name in glob.glob(os.path.join(install_dir, ".pyflaker")):
            with open(config_name) as config:
                self.symbol_table = json.load(config)
                break

    def symbol(self, symbol):
        """ Check if the symbol is among those to be ignored. """
        return symbol in self._symbols

    def file(self, filename):
        """ Check if the file name is among those to be ignored. """
        return filename in self._files

    def type(self, typename):
        """ Check if the message type name is in the ignore list. """
        return typename in self._types

    def reset_symbols(self, filename):
        """ Clear the symbols and set them according to file type.  Any
            directives in the file may extend the ignore set.
        """
        if self.symbol_table is None:
            return

        filename = os.path.basename(filename)  # Get the file name itself, no directory.

        self._symbols.clear()
        self._types.clear()
        for section, data in self.symbol_table.items():
            for match in data["files"]:
                if fnmatch.fnmatch(filename, match):
                    self._symbols.update(data.get("ignore_symbols", []))
                    self._types.update(data.get("ignore_types", []))

    def add_symbols(self, *symbols):
        """ Add one or more symbols to the ignore list. """
        self._symbols.update(symbols)

    def add_file(self, filename):
        self._files.add(filename)

ignore = Ignore()

#-------------------------------------------------------------------------------

from _string import formatter_parser as _parseFormat

def _parseExprs(self, string, lineno):
    for text, expression, formatSpec, conversion in _parseFormat(string):
        if expression:
            # Get the AST and pretend it's part of the local tree.
            expr = compile(expression, "<i-string>", "eval", ast.PyCF_ONLY_AST)
            for node in ast.walk(expr):
                if "lineno" in node._attributes:
                    node.lineno = lineno # So errors are reported at the true location.
            self.handleChildren(expr)
        if formatSpec:
            _parseExprs(self, formatSpec, lineno)

interpolatorFunctions = set([ # Manually maintained, hence quite fragile.
    "_stringInterpolater",
    "i",
    "_i",
    "formatFunction",
    "FF",
])

def handleIString(self, node, parent):
    """ Dissect the node to see if it contains i-string references. """
    if isinstance(parent, ast.Call):
        # Check if it's one of our _stringInterpolater derivatives.
        funcName = getattr(parent.func, "id", None) or getattr(parent.func, "attr", None)
        if funcName in interpolatorFunctions:
            _parseExprs(self, node.s.strip(), node.lineno)
#       elif "{" in node.s:
#           print(node.lineno, funcName, node.s)

#-------------------------------------------------------------------------------

def checkZipForStrict(self, node, parent):
    """ Verify that if the 'zip' function is used, that the call has an explicit
        'strict' keyword parameter.
    """
    if isinstance(node.func, ast.Name) and node.func.id == "zip":
        for kw in node.keywords:
            if kw.arg == "strict":
                break
        else:
            self.report(M.NeedKwOnlyArgument, node, "zip", "strict=True/False")

#-------------------------------------------------------------------------------

def checkDiagnosticPrint(self, node, parent):
    """ Scan for print statements that are not redirected using `file=`.
        Caller is responsible for determining if the print is inside a unit
        test or not.
    """
    if isinstance(node.func, ast.Name) and node.func.id in {"print", "xprint", "wprint", "dprint"}:
        for kw in node.keywords:
            if kw.arg == "file":
                break
        else:
            self.report(M.NeedKwOnlyArgument, node, "diagnostic print", "file=<not stdout>")

#-------------------------------------------------------------------------------

def checkSpuriousObject(self, node, parent):
    """ Look for classes that use legacy class definitions, 'class x(object):' """
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id == 'object':
            self.report(M.GetRidOfObject, node, node.name, "")

#-------------------------------------------------------------------------------

def containsACall(node):
    """ Scan the node and all subnodes for a function or method call. """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.Call):
            return True
    return False

def checkExceptPass(self, node, parent):
    """ Scan through the code for things that look grossly like this, usually
        attempting to do duck typing on 'obj' to see if it has a callable
        attribute (the name 'call' is used here, but it could be anything).

        >>> try:
        >>>     x = obj.call(y)
        >>> except AttributeError:
        >>>     pass

        This is particularly hard to debug when 'call' is successfully called,
        but then it generates an AttributeError somewhere deep in the call
        sequence (this has happened more than once).

        In case you wonder, above should be rewritten as below.

        >>> try:
        >>>     call = obj.call
        >>> except AttributeError:
        >>>     pass
        >>> else:
        >>>     x = call(y)
    """
    if isinstance(node, ast.Try):
        for handler in node.handlers:
            if len(handler.body) == 1 and isinstance(handler.body[0], ast.Pass):
                if isinstance(handler.type, ast.Name):
                    types = [handler.type]
                elif isinstance(handler.type, ast.Tuple):
                    types = handler.type.elts
                else:  # ast.Attribute or something that's almost certainly not 'AttributeError'.
                    types = []

                for type in types:
                    if isinstance(type, ast.Name) and type.id == "AttributeError":
                        break
                else:
                    return

                for item in node.body:
                    if containsACall(item):
                        self.report(M.TryExceptPass, node, "", "")
                        break

#-------------------------------------------------------------------------------

def _getStr(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Constant):
        return node.value
    return ""

def init(self, *args, **kwds):
    """ Note that after the call to base __init__ has returned, we have
        "self.filename", but by then it's too late as the file has already
        been processed.  Hence, we jump through a hoop here.
    """
    filename = kwds.get('filename') or args[1]  # flakes uses kwd, frosted uses args.
    ignore.reset_symbols(filename)  # This is what must occur before base.__init__.

    self.main_lines = 1, 0
    baseInit(self, *args, **kwds)

def set_main(self, node):
    self.main_lines = node.lineno, node.end_lineno

def in_main(self, node):
    """ TODO Move this to the config file settings. """
    if self.filename.endswith(".lmpy"):
        return True
    return self.main_lines[0] <= node.lineno <= self.main_lines[1]

funclines = 0  # TODO make a 'Stats' class or something.
funccount = 0
funcmax   = 0
funcname  = ""

def handleNode(self, node, parent):
    if node is None:
        return None

    if   isinstance(node, ast.Str):
        handleIString(self, node, parent)
    elif isinstance(node, ast.Call):
        checkZipForStrict(self, node, parent)
        if cmd.dprint and not self.in_main(node):
            checkDiagnosticPrint(self, node, parent)
    elif isinstance(node, ast.Try):
        checkExceptPass(self, node, parent)
    elif isinstance(node, ast.ClassDef):
        checkSpuriousObject(self, node, parent)

    elif isinstance(node, ast.If) and node.col_offset == 0:
        # Super sloppy detection of 'if __name__ == "something"' where "something"
        # is main or LifeMOD_testing, which we assume contains a unit test.
        test = node.test
        if isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq):
            lft = _getStr(test.left)
            rgt = _getStr(test.comparators[0])  # If ops has length 1, guarantees at least one comparator.
            if lft == "__name__" and rgt in {"__main__", "LifeMOD_testing"}:
                self.set_main(node)

    elif isinstance(node, ast.FunctionDef):
        global funclines, funccount, funcmax, funcname
        length = node.end_lineno - node.lineno
        if length > funcmax:
            funcmax  = length
            funcname = f"{self.filename}:{node.lineno}:{node.name}".replace("\\", "/")
#       if length > 80:
#           self.filename = self.filename.replace("\\", "/")
#           print(f" {length:4} {node.name:32}  {node.lineno:5} {self.filename}")
        funclines += length
        funccount += 1
#       print(funclines, funccount, funcmax, funclines/funccount)

    return baseHandleNode(self, node, parent)

baseInit             = C.Checker.__init__  # pyflakes:ignore_all(P,C,M)
baseHandleNode       = C.Checker.handleNode
C.Checker.__init__   = init
C.Checker.handleNode = handleNode
C.Checker.set_main   = set_main
C.Checker.in_main    = in_main

#-------------------------------------------------------------------------------

def report(self, messageClass, *args, **kwds):
    """ The body of the original method is copied wholesale here, so no need to
        redirect at the end.
    """
    self.filename = self.filename.replace("\\", "/")

    try:
        kwds["verbose"] = self.settings.get("verbose")
    except AttributeError:
        # Above only applies when using 'frosted'.
        pass
    message = messageClass(self.filename, *args, **kwds)

    if cmd.verbose and messageClass.name not in message.message:
        try:
            # pyflakes
            message.message = f'{message.message} ({messageClass.name})'
        except AttributeError:
            # frosted uses a namedtuple
            message = message._replace(message=f'{message.message} ({messageClass.name})')

    if messageClass is M.UndefinedName and ignore.symbol(args[1]):
        return

    if cmd.showall:
        # A override to allow display of all messages, irrespective of any
        # other settings or options.
        self.messages.append(message)
        return

    if cmd.use == "frosted":
        # Use of real 'flakes' bypasses these checks because the structure of
        # its message classes is grossly different than those in 'frosted'.

        error_code = messageClass.error_code
        if (   error_code[:2] + "00"          in self.ignore_errors
            or error_code                     in self.ignore_errors
            or str(messageClass.error_number) in self.ignore_errors
        ):
            return

        if message.lineno in self.ignore_lines:
            return

    if ignore.file(self.filename):
        return

    if ignore.type(messageClass.name):
        # Typical use is to suppress "ImportStarUsed" messages in __init__.py.
        return

    lineidx = args[0].lineno - 1
    lines   = getlines(self.filename)

    if "This file was automatically generated " "by SWIG" in lines[0]:
        ignore.add_file(self.filename)
        if not cmd.quiet:
            print(f"{self.filename}:1: SWIG-generated file, contents ignored")
        return

    # Scan the source file for comments containing, but replace 'S' with '$':
    #  SChecks: no-static-analysis S
    for lno, line in enumerate(lines, 1):
        if re.search(r"\$Checks:[^\$]*\bno-static-analysis\b[^\$]*\$", line):
            ignore.add_file(self.filename)
            if not cmd.quiet:
                print(f"{self.filename}:{lno}: Static analysis explicitly suppressed")
            return

    line = lines[lineidx]
    if re.search(r"\bpylint\s*:\s*disable\s*=\s*(.*)", line):
        # We ignore 'pylint:disable' comments here so we can add a hook
        # to show specific ones, if it seems appropriate.
        #print('>'*40, match.group(1))
        return

    if re.search(r"\bpyflakes\s*:\s*ignore_all\s*\(", line):
        names = re.split(r"pyflakes\s*:\s*ignore_all\s*\(\s*", line)[-1]
        names = re.split(r"\s*\)", names)[0]
        names = re.split(r"\s*,\s*", names)
        ignore.add_symbols(*names)

    if messageClass is M.UndefinedName and ignore.symbol(args[1]):
        return

    self.messages.append(message)

C.Checker.report = report

#-------------------------------------------------------------------------------

if __name__ == "__main__":
    if cmd.summarize:
        import atexit
        def summarize():
            if funccount == 0:
                print("No functions found to summarize")
            else:
                print(f"{funclines = }, {funccount = }, {funclines/funccount = :.1f}, longest function = {funcmax} lines @ {funcname}")
        atexit.register(summarize)

    P.main()

#-------------------------------------------------------------------------------
