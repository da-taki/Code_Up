import re
from typing import Dict, List, Optional, Tuple


_COUNT_WORDS = {1: "once", 2: "twice", 3: "three times", 4: "four times", 5: "five times"}
_NUM_WORDS = {0: "zero", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
              6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}

UNKNOWN_CONCEPT = "__unknown_concept__"
IDENTITY_QUERY = "__identity__"
NON_CODE_QUERY = "__non_code__"

GROUNDED_KINDS = {"quotes", "colon"}


def _entry(means: str, example: str, note: str) -> str:
    return f"{means}\n\nExample:\n{example}\n\nBeginner note: {note}"


_CATALOG: Dict[str, Dict[str, object]] = {
    "print": {
        "aliases": ["print", "print statement"],
        "message": _entry(
            "print is how Python shows text or values on the screen.",
            'print("Hello")',
            "print does not ask the user for information. It only shows output.",
        ),
    },
    "input": {
        "aliases": ["input", "input statement"],
        "message": _entry(
            "input is how Python asks the user to type a value while the program is running.",
            'name = input("Enter name: ")\nprint(name)',
            "input receives information from the user, while print shows information to the user.",
        ),
    },
    "range": {
        "aliases": ["range"],
        "message": _entry(
            "range is how Python makes a simple sequence of numbers, often for a loop.",
            "for number in range(3):\n    print(number)",
            "range(3) gives 0, 1, and 2, not 1, 2, and 3.",
        ),
    },
    "len": {
        "aliases": ["len", "length"],
        "message": _entry(
            "len tells Python to count how many items are inside something.",
            'len("CodeUp") gives 6',
            "len works on strings, lists, tuples, dictionaries, and many other containers.",
        ),
    },
    "type": {
        "aliases": ["type"],
        "message": _entry(
            "type means Python tells you what kind of value something is.",
            "type(5) gives int",
            "type is useful for learning, but beginners usually do not need it in every program.",
        ),
    },
    "int": {
        "aliases": ["int", "integer"],
        "message": _entry(
            "int means a whole number, and int() can convert a value into a whole number.",
            'age = int("16")',
            "int cannot convert text like hello because that is not a whole number.",
        ),
    },
    "float": {
        "aliases": ["float", "decimal", "decimal number"],
        "message": _entry(
            "float means a number that can have a decimal point.",
            'marks = float("85.5")',
            "use float for decimal values like marks, money, or measurements.",
        ),
    },
    "str": {
        "aliases": ["str", "string", "strings", "text"],
        "message": _entry(
            "string means text in Python.",
            'name = "CodeUp"',
            "strings usually go inside quotes so Python knows they are text.",
        ),
    },
    "bool": {
        "aliases": ["bool", "boolean", "booleans", "true false"],
        "message": _entry(
            "bool means a value that is either True or False.",
            "is_pass = score >= 50",
            "conditions in if statements usually produce boolean values.",
        ),
    },
    "list": {
        "aliases": ["list", "lists"],
        "message": _entry(
            "A list stores several values in one ordered group.",
            "scores = [80, 90, 75]\nprint(scores[0])",
            "Python list positions start at 0, so scores[0] means the first item.",
        ),
    },
    "dict": {
        "aliases": ["dict", "dictionary", "dictionaries", "dictionary object"],
        "message": _entry(
            "dictionary means a collection that stores values under named keys.",
            'student = {"name": "Asha", "marks": 90}',
            "use a dictionary when labels like name or marks make the data easier to understand.",
        ),
    },
    "tuple": {
        "aliases": ["tuple", "tuples"],
        "message": _entry(
            "tuple means an ordered group of values that usually stays fixed.",
            "point = (3, 4)",
            "tuples use parentheses and are handy when grouped values should not change.",
        ),
    },
    "set": {
        "aliases": ["set", "sets"],
        "message": _entry(
            "set means a collection that keeps only unique items.",
            'colors = {"red", "green", "red"}',
            "a set removes duplicates and does not promise a fixed order.",
        ),
    },
    "sum": {
        "aliases": ["sum"],
        "message": _entry(
            "sum means Python adds the numbers in a group.",
            "sum([2, 3, 4]) gives 9",
            "sum needs numeric values; it is not for joining normal text.",
        ),
    },
    "min": {
        "aliases": ["min", "minimum"],
        "message": _entry(
            "min means Python finds the smallest value.",
            "min([8, 3, 5]) gives 3",
            "min works on many comparable values, including numbers and strings.",
        ),
    },
    "max": {
        "aliases": ["max", "maximum"],
        "message": _entry(
            "max means Python finds the largest value.",
            "max([8, 3, 5]) gives 8",
            "max compares values, so the items should be the same kind of thing.",
        ),
    },
    "sorted": {
        "aliases": ["sorted", "sort"],
        "message": _entry(
            "sorted means Python makes a new ordered version of a collection.",
            "sorted([3, 1, 2]) gives [1, 2, 3]",
            "sorted returns a new value; it does not change the original list.",
        ),
    },
    "enumerate": {
        "aliases": ["enumerate"],
        "message": _entry(
            "enumerate means Python gives each item a counter while you loop.",
            "for index, name in enumerate(names):\n    print(index, name)",
            "enumerate is useful when you need both the position and the item.",
        ),
    },
    "zip": {
        "aliases": ["zip"],
        "message": _entry(
            "zip means Python pairs items from two or more collections.",
            "zip(names, scores)",
            "zip stops when the shortest collection runs out of items.",
        ),
    },
    "open": {
        "aliases": ["open"],
        "message": _entry(
            "open means Python opens a file so your program can read or write it.",
            'with open("notes.txt") as file:\n    text = file.read()',
            "use with when opening files so Python closes the file safely.",
        ),
    },
    "append": {
        "aliases": ["append", "list append", "append method"],
        "message": _entry(
            "append means adding one new item to the end of a list.",
            "scores.append(95)",
            "append changes the existing list instead of making a new list.",
        ),
    },
    "split": {
        "aliases": ["split", "str.split", "string split", "split method"],
        "message": _entry(
            "split means breaking a string into a list of smaller strings.",
            '"red blue".split() gives ["red", "blue"]',
            "split is often used to separate words or comma-separated input.",
        ),
    },
    "strip": {
        "aliases": ["strip", "str.strip", "string strip", "strip method"],
        "message": _entry(
            "str.strip means removing extra spaces or line breaks from the start and end of text.",
            '"  hi  ".strip() gives "hi"',
            "strip does not remove spaces from the middle of the text.",
        ),
    },
    "replace": {
        "aliases": ["replace", "str.replace", "string replace", "replace method"],
        "message": _entry(
            "replace means making a new string with some text swapped for other text.",
            '"cat".replace("c", "h") gives "hat"',
            "strings do not change in place; replace gives you a new string.",
        ),
    },
    "join": {
        "aliases": ["join", "str.join", "string join", "join method"],
        "message": _entry(
            "join means combining strings with a separator between them.",
            '", ".join(["A", "B"]) gives "A, B"',
            "join expects strings, so convert numbers to text first if needed.",
        ),
    },
    "for_loop": {
        "aliases": ["for loop", "for loops", "loop", "loops"],
        "message": _entry(
            "A for loop repeats the same block of code once for each value.",
            "for number in range(3):\n    print(number)",
            "the indented line is inside the loop, so it runs again and again.",
        ),
    },
    "while_loop": {
        "aliases": ["while loop", "while loops"],
        "message": _entry(
            "while loop means repeating code as long as a condition stays true.",
            "while count < 3:\n    print(count)\n    count = count + 1",
            "make sure something changes inside the loop, or it may never stop.",
        ),
    },
    "if_statement": {
        "aliases": ["if statement", "if statements", "condition", "conditions", "conditional", "conditionals"],
        "message": _entry(
            "An if statement lets Python choose whether to run code based on a condition.",
            "if score >= 50:\n    print(\"Pass\")",
            "the indented line runs only when the condition is true.",
        ),
    },
    "variable": {
        "aliases": ["variable", "variables"],
        "message": _entry(
            "A variable is a name that stores a value for later.",
            "score = 10\nprint(score)",
            "the variable name is on the left, and the stored value is on the right.",
        ),
    },
    "function": {
        "aliases": ["function", "functions"],
        "message": _entry(
            "A function is a named set of instructions that you can run when needed.",
            "def greet():\n    print(\"Hello\")\ngreet()",
            "defining a function saves the instructions; calling it makes them run.",
        ),
    },
    "parameter": {
        "aliases": ["parameter", "parameters"],
        "message": _entry(
            "parameter means a name in a function definition that receives a value.",
            "def greet(name):\n    print(name)",
            "the value passed into the function is called an argument.",
        ),
    },
    "argument": {
        "aliases": ["argument", "arguments"],
        "message": _entry(
            "argument means the actual value you give to a function call.",
            'greet("Asha")',
            "in def greet(name), name is the parameter, and Asha is the argument.",
        ),
    },
    "return": {
        "aliases": ["return", "return value", "return values", "return statement"],
        "message": _entry(
            "return means a function sends a result back to the code that called it.",
            "def add(a, b):\n    return a + b",
            "print shows output, but return gives a value back to the program.",
        ),
    },
    "class": {
        "aliases": ["class", "classes"],
        "message": _entry(
            "class means a blueprint for creating objects.",
            "class Dog:\n    pass",
            "classes group data and actions that belong together.",
        ),
    },
    "object": {
        "aliases": ["object", "objects", "instance", "instances"],
        "message": _entry(
            "object means a real value created from a class.",
            "dog = Dog()",
            "a class is the blueprint, and an object is one thing made from that blueprint.",
        ),
    },
    "method": {
        "aliases": ["method", "methods"],
        "message": _entry(
            "method means a function that belongs to an object or class.",
            "name.strip()",
            "methods are called with a dot after the value they work on.",
        ),
    },
    "module": {
        "aliases": ["module", "modules"],
        "message": _entry(
            "module means a Python file or library you can reuse in another program.",
            "import math\nprint(math.sqrt(9))",
            "modules help keep code organized and reusable.",
        ),
    },
    "package": {
        "aliases": ["package", "packages"],
        "message": _entry(
            "package means a folder of Python modules grouped together.",
            "import requests",
            "packages are often installed with pip before you import them.",
        ),
    },
    "import": {
        "aliases": ["import", "imports", "importing", "import statement"],
        "message": _entry(
            "import means bringing code from a module into your program.",
            "import math\nprint(math.sqrt(16))",
            "imports usually go near the top of the file.",
        ),
    },
    "from_import": {
        "aliases": ["from import", "from import statement", "from math import"],
        "message": _entry(
            "from import means bringing one specific name from a module into your program.",
            "from math import sqrt\nprint(sqrt(16))",
            "use plain import when you want the module name to stay visible, like math.sqrt.",
        ),
    },
    "pip": {
        "aliases": ["pip"],
        "message": _entry(
            "pip means the common tool for installing Python packages.",
            "pip install requests",
            "install only packages you trust, especially on shared or school computers.",
        ),
    },
    "recursion": {
        "aliases": ["recursion", "recursive", "recursive function", "recursive functions"],
        "message": _entry(
            "recursion means a function calls itself to solve a smaller version of the same problem.",
            "def countdown(n):\n    if n == 0:\n        return\n    countdown(n - 1)",
            "recursion needs a stopping point called a base case.",
        ),
    },
    "lambda": {
        "aliases": ["lambda", "lambda function"],
        "message": _entry(
            "lambda means a small unnamed function written in one expression.",
            "double = lambda x: x * 2",
            "beginners can usually use def first because it is easier to read.",
        ),
    },
    "list_comprehension": {
        "aliases": ["list comprehension", "list comprehensions"],
        "message": _entry(
            "list comprehension means making a new list in one compact line.",
            "squares = [n * n for n in range(3)]",
            "write a normal for loop first if the compact form feels confusing.",
        ),
    },
    "dictionary_comprehension": {
        "aliases": ["dictionary comprehension", "dict comprehension", "dictionary comprehensions"],
        "message": _entry(
            "dictionary comprehension means making a new dictionary in one compact line.",
            "squares = {n: n * n for n in range(3)}",
            "it is like a list comprehension, but it creates key and value pairs.",
        ),
    },
    "try_except": {
        "aliases": ["try except", "try and except", "try/except", "exception handling", "error handling"],
        "message": _entry(
            "try except means Python tries risky code and handles an error if one happens.",
            "try:\n    age = int(text)\nexcept ValueError:\n    print(\"Use a number\")",
            "use try except for errors you expect and can recover from.",
        ),
    },
    "exception": {
        "aliases": ["exception", "exceptions", "try except", "try and except", "try/except",
                    "exception handling", "error handling"],
        "message": _entry(
            "exception means an error that happens while a program is running.",
            "ValueError happens when int(\"hi\") cannot make a number",
            "exceptions can be handled with try and except so the program does not crash.",
        ),
    },
    "syntax_error": {
        "aliases": ["syntax error", "syntax errors"],
        "message": _entry(
            "syntax error means Python could not understand the code's grammar.",
            "if score > 50\n    print(\"Pass\")",
            "look for missing colons, quotes, parentheses, or misspelled Python words.",
        ),
    },
    "indentation_error": {
        "aliases": ["indentation error", "indentation errors"],
        "message": _entry(
            "indentation error means Python expected different spaces at the start of a line.",
            "if True:\nprint(\"Hi\")",
            "lines inside if statements, loops, and functions must be indented consistently.",
        ),
    },
    "type_error": {
        "aliases": ["type error", "type errors", "typeerror"],
        "message": _entry(
            "type error means Python got the wrong kind of value for an operation.",
            '"age: " + 16 causes a TypeError',
            "convert values first, like str(16), when mixing text and numbers.",
        ),
    },
    "value_error": {
        "aliases": ["value error", "value errors", "valueerror"],
        "message": _entry(
            "value error means the value has the right general type but an unusable value.",
            'int("hello") raises ValueError',
            "check user input before converting it to int or float.",
        ),
    },
    "index_error": {
        "aliases": ["index error", "index errors", "indexerror"],
        "message": _entry(
            "index error means a list or string position does not exist.",
            "scores = [80]\nprint(scores[2])",
            "remember that Python positions start at 0.",
        ),
    },
    "key_error": {
        "aliases": ["key error", "key errors", "keyerror"],
        "message": _entry(
            "key error means a dictionary does not have the key you asked for.",
            'student = {"name": "Asha"}\nprint(student["marks"])',
            "check the key spelling or use get when a key might be missing.",
        ),
    },
    "file_handling": {
        "aliases": ["file handling", "files", "file io", "file input output"],
        "message": _entry(
            "file handling means reading from or writing to files with Python.",
            'with open("notes.txt") as file:\n    text = file.read()',
            "use with so Python closes the file safely after the block finishes.",
        ),
    },
    "with_statement": {
        "aliases": ["with statement", "with"],
        "message": _entry(
            "with statement means Python manages a resource for a block of code.",
            'with open("notes.txt") as file:\n    text = file.read()',
            "with is commonly used for files because it closes them automatically.",
        ),
    },
    "decorator": {
        "aliases": ["decorator", "decorators"],
        "message": _entry(
            "decorator means code that wraps a function to add behavior around it.",
            "@timer\ndef run():\n    print(\"Go\")",
            "decorators are advanced, so beginners can first learn normal functions well.",
        ),
    },
    "generator": {
        "aliases": ["generator", "generators"],
        "message": _entry(
            "generator means a function that produces values one at a time.",
            "def count():\n    yield 1\n    yield 2",
            "generators can save memory because they do not build every value at once.",
        ),
    },
    "yield": {
        "aliases": ["yield"],
        "message": _entry(
            "yield means a generator sends out one value and can continue later.",
            "yield number",
            "yield is like return for generators, but the function can resume afterward.",
        ),
    },
    "async": {
        "aliases": ["async", "async function"],
        "message": _entry(
            "async means a function can pause while waiting for something and let other work continue.",
            "async def fetch_data():\n    return data",
            "async is advanced; learn normal functions before async functions.",
        ),
    },
    "await": {
        "aliases": ["await"],
        "message": _entry(
            "await means wait for an async operation to finish without blocking everything.",
            "result = await fetch_data()",
            "await only works inside async functions.",
        ),
    },
    "comment": {
        "aliases": ["comment", "comments", "hash comment"],
        "message": _entry(
            "comment means a note in code that Python ignores when running.",
            "# This explains the next line",
            "use comments to explain why code exists, not every tiny step.",
        ),
    },
    "indentation": {
        "aliases": ["indentation", "indent", "indenting", "four spaces"],
        "message": _entry(
            "indentation means spaces at the start of a line that show which block it belongs to.",
            "if score >= 50:\n    print(\"Pass\")",
            "Python uses indentation to understand loops, if statements, functions, and classes.",
        ),
    },
    "inheritance": {
        "aliases": ["inheritance", "parent class", "child class", "subclass", "superclass", "inherit"],
        "message": _entry(
            "inheritance means one class builds on a parent class.",
            "class Dog(Animal):\n    pass",
            "inheritance can reuse code, but beginners should keep class designs simple.",
        ),
    },
    "big_o": {
        "aliases": ["big o", "big-o", "big o notation", "time complexity", "space complexity",
                    "algorithm complexity", "algorithmic complexity"],
        "message": _entry(
            "time complexity means describing how the work grows as the input gets bigger.",
            "checking every item in a list is often O(n)",
            "Big O helps compare approaches, but clear working code comes first for beginners.",
        ),
    },
    "oop": {
        "aliases": ["object oriented programming", "object-oriented programming", "oop"],
        "message": _entry(
            "object-oriented programming means organizing code around objects and classes.",
            "class Student:\n    pass",
            "OOP is useful for larger programs, but small scripts can stay simple.",
        ),
    },
    "__init__": {
        "aliases": ["__init__", "init", "dunder init"],
        "message": _entry(
            "__init__ means the setup method that runs when a new object is created.",
            "class Dog:\n    def __init__(self, name):\n        self.name = name",
            "__init__ stores starting values for the object.",
        ),
    },
}


_DANGEROUS_BUILTINS: Dict[str, str] = {
    "eval": _entry(
        "eval means Python runs text as Python code.",
        'eval("2 + 3") gives 5',
        "avoid eval in beginner projects because it can run unsafe code.",
    ),
    "exec": _entry(
        "exec means Python runs a larger string as Python code.",
        'exec("print(5)")',
        "avoid exec in beginner projects because it can run unsafe code.",
    ),
    "compile": _entry(
        "compile means Python prepares code text before running it.",
        'compile("print(5)", "demo", "exec")',
        "beginners rarely need compile, and it can be unsafe with untrusted text.",
    ),
    "globals": _entry(
        "globals means Python returns the program's global names.",
        "globals()",
        "beginners usually do not need globals because it can make code hard to understand.",
    ),
    "locals": _entry(
        "locals means Python returns names available in the current local area.",
        "locals()",
        "beginners usually do not need locals because normal variables are clearer.",
    ),
    "setattr": _entry(
        "setattr means Python sets an attribute by name.",
        'setattr(obj, "name", "Asha")',
        "beginners should usually use normal dot assignment when possible.",
    ),
    "delattr": _entry(
        "delattr means Python deletes an attribute by name.",
        'delattr(obj, "name")',
        "be careful because deleting attributes can break later code.",
    ),
    "__import__": _entry(
        "__import__ means Python's low-level import function.",
        '__import__("math")',
        "use normal import statements in beginner code.",
    ),
    "breakpoint": _entry(
        "breakpoint means Python pauses a program for debugging.",
        "breakpoint()",
        "use it only when you intentionally want an interactive debugging pause.",
    ),
}


_BUILTIN_FALLBACKS: Dict[str, str] = {
    "abs": _entry("abs means Python finds a number's distance from zero.", "abs(-5) gives 5",
                  "abs is useful when you need a positive size or difference."),
    "all": _entry("all means every value in a group must be true.", "all([True, True]) gives True",
                  "all returns False as soon as one value is false."),
    "any": _entry("any means at least one value in a group is true.", "any([False, True]) gives True",
                  "any is useful for checking if one match exists."),
    "ascii": _entry("ascii means Python shows a text-safe representation using ASCII characters.",
                    'ascii("hi")', "beginners rarely need ascii in normal programs."),
    "bin": _entry("bin means Python converts a number to binary text.", "bin(5) gives 0b101",
                  "binary is base two, using only 0 and 1."),
    "bytes": _entry("bytes means a sequence of raw byte values.", 'bytes("hi", "utf-8")',
                    "beginners usually work with strings before bytes."),
    "callable": _entry("callable means Python checks whether something can be called like a function.",
                       "callable(print) gives True", "functions and classes are common callable values."),
    "chr": _entry("chr means Python turns a number into a Unicode character.", "chr(65) gives A",
                  "ord does the reverse of chr."),
    "dir": _entry("dir means Python lists names available on an object.", "dir(str)",
                  "dir is helpful for exploring, but the output can be long."),
    "divmod": _entry("divmod means Python gives division result and remainder together.",
                     "divmod(7, 3) gives (2, 1)", "it is a shortcut for quotient and remainder."),
    "filter": _entry("filter means Python keeps items that pass a test.", "filter(str.isalpha, items)",
                     "beginners may find a normal for loop easier to read."),
    "format": _entry("format means Python converts a value into formatted text.", 'format(3.14159, ".2f")',
                     "f-strings are often easier for beginners."),
    "frozenset": _entry("frozenset means a set that cannot be changed.", "frozenset([1, 2, 2])",
                        "use set first unless you specifically need an unchangeable set."),
    "getattr": _entry("getattr means Python reads an attribute by name.", 'getattr(obj, "name")',
                      "normal dot access is clearer when you know the attribute name."),
    "hasattr": _entry("hasattr means Python checks whether an object has an attribute.", 'hasattr(obj, "name")',
                      "it returns True or False."),
    "hash": _entry("hash means Python makes a number used for fast lookup.", 'hash("CodeUp")',
                   "hash values are mainly an internal detail for dictionaries and sets."),
    "hex": _entry("hex means Python converts a number to hexadecimal text.", "hex(15) gives 0xf",
                  "hexadecimal is base sixteen."),
    "id": _entry("id means Python shows an object's identity number while the program runs.", "id(name)",
                 "id is mostly for debugging and learning about objects."),
    "isinstance": _entry("isinstance means Python checks whether a value is a certain type.", "isinstance(5, int)",
                         "it returns True or False."),
    "issubclass": _entry("issubclass means Python checks whether one class inherits from another.",
                         "issubclass(Dog, Animal)", "it is mainly used with classes."),
    "iter": _entry("iter means Python gets an iterator from something you can loop over.", "iter([1, 2, 3])",
                   "beginners usually use for loops directly."),
    "map": _entry("map means Python applies a function to each item.", "map(str, [1, 2, 3])",
                  "a for loop or list comprehension is often easier to read."),
    "next": _entry("next means Python asks an iterator for its next value.", "next(iterator)",
                   "for loops call next behind the scenes."),
    "oct": _entry("oct means Python converts a number to octal text.", "oct(8) gives 0o10",
                  "octal is base eight and is uncommon in beginner programs."),
    "ord": _entry("ord means Python turns one character into its Unicode number.", "ord('A') gives 65",
                  "chr does the reverse of ord."),
    "pow": _entry("pow means Python raises a number to a power.", "pow(2, 3) gives 8",
                  "2 ** 3 is the common beginner-friendly form."),
    "repr": _entry("repr means Python returns a developer-style text representation of a value.", 'repr("hi")',
                   "repr is useful for debugging because it shows quotes and escapes."),
    "reversed": _entry("reversed means Python loops over items from the end back to the start.",
                       "list(reversed([1, 2, 3])) gives [3, 2, 1]", "it does not change the original list."),
    "round": _entry("round means Python rounds a number.", "round(3.14159, 2) gives 3.14",
                    "round can be helpful for displaying decimal results."),
    "slice": _entry("slice means Python describes a start, stop, and step for selecting part of a sequence.",
                    "text[1:4]", "beginners usually write slice syntax directly with square brackets."),
    "super": _entry("super means Python lets a child class call behavior from a parent class.",
                    "super().__init__()", "super is useful with inheritance, which is an intermediate topic."),
    "vars": _entry("vars means Python returns an object's stored attributes as a dictionary.", "vars(student)",
                   "beginners usually access attributes directly with dot notation."),
}

_ALLOWED_BUILTINS = set(_CATALOG) | set(_BUILTIN_FALLBACKS)


_MODULE_FALLBACKS: Dict[str, str] = {
    "math.sqrt": _entry("math.sqrt means the square root function from the math module.",
                        "math.sqrt(16) gives 4.0", "import math before using math.sqrt."),
    "math.floor": _entry("math.floor means rounding a number down to a whole number.",
                         "math.floor(3.9) gives 3", "floor always moves down, not just toward zero."),
    "math.ceil": _entry("math.ceil means rounding a number up to a whole number.",
                        "math.ceil(3.1) gives 4", "ceil always moves up."),
    "math.pow": _entry("math.pow means raising a number to a power using the math module.",
                       "math.pow(2, 3) gives 8.0", "the ** operator is often simpler for beginners."),
    "math.sin": _entry("math.sin means the sine function from math.", "math.sin(0) gives 0.0",
                       "math.sin uses radians, not degrees."),
    "math.cos": _entry("math.cos means the cosine function from math.", "math.cos(0) gives 1.0",
                       "math.cos uses radians, not degrees."),
    "math.pi": _entry("math.pi means the value of pi from the math module.", "math.pi is about 3.14159",
                      "math.pi is a value, not a function call."),
    "random.randint": _entry("random.randint means choosing a random whole number in a range.",
                             "random.randint(1, 6)", "both end numbers can be chosen."),
    "random.choice": _entry("random.choice means picking one random item from a collection.",
                            "random.choice(names)", "the collection must not be empty."),
    "random.shuffle": _entry("random.shuffle means rearranging a list into random order.",
                             "random.shuffle(cards)", "shuffle changes the original list."),
    "statistics.mean": _entry("statistics.mean means calculating the average of numbers.",
                              "statistics.mean([80, 90, 100]) gives 90", "import statistics before using it."),
    "statistics.median": _entry("statistics.median means finding the middle value.",
                                "statistics.median([1, 9, 10]) gives 9", "median can be better than average when values are uneven."),
    "json.loads": _entry("json.loads means turning JSON text into Python data.",
                         'json.loads("{\\"name\\": \\"Asha\\"}")', "only load JSON from sources you trust and expect."),
    "json.dumps": _entry("json.dumps means turning Python data into JSON text.",
                         'json.dumps({"name": "Asha"})', "JSON is a common text format for sharing data."),
    "datetime.date": _entry("datetime.date means a date value with year, month, and day.",
                            "datetime.date(2026, 7, 3)", "date stores a calendar day, not a time of day."),
    "datetime.datetime": _entry("datetime.datetime means a date and time value together.",
                                "datetime.datetime(2026, 7, 3, 9, 30)", "datetime includes both the day and the clock time."),
}

_TRIGGERS = [
    ("quotes", ["why do we use quotes", "why are there quotes", "why are the quotes there",
                "what are the quotes for", "what do quotes do", "why use quotes", "why the quotes",
                "why quotes", "why is there a quote", "what do the quotes mean"]),
    ("indentation", ["why indentation", "why do we indent", "why is this indented",
                     "why is this line indented", "what is indentation", "why the indentation",
                     "why four spaces", "why is it indented"]),
    ("colon", ["why do we need a colon", "why do we need colon", "why the colon",
               "what is the colon for"]),
    ("range", ["what does range", "what is range", "why range", "what does the range",
               "explain range"]),
    ("print", ["what does print mean", "what does print do", "what is print",
               "what does the print do", "what does print"]),
    ("variable", ["what is a variable", "what does variable mean", "what is variable",
                  "what does a variable do", "what's a variable"]),
]

_UNKNOWN_CONCEPT_MESSAGE = (
    "I do not have a prepared explanation for that Python topic yet. I can explain beginner "
    "Python topics like print, input, loops, lists, functions, classes, errors, recursion, "
    "or file handling.")

_IDENTITY_MESSAGE = (
    "I am CodeUp, a voice-first Python learning environment. I can help you create, run, debug, "
    "understand, and export Python code.")

_NON_CODE_MESSAGE = (
    "I am focused on Python learning here. Try asking about print, input, loops, lists, "
    "functions, classes, errors, or file handling.")

_IDENTITY_RE = re.compile(
    r"^(?:hey|ok|okay|so)?[,\s]*(?:"
    r"who\s+are\s+you|who\s+is\s+this|what\s+are\s+you|what\s+is\s+codeup|"
    r"what(?:'s| is)\s+your\s+name|whats\s+your\s+name|"
    r"are\s+you\s+(?:a\s+|an\s+)?(?:robot|human|real|ai|bot|person|chatgpt|gpt)|"
    r"introduce\s+yourself|tell\s+me\s+about\s+yourself"
    r")\s*\??$", re.IGNORECASE)

_NON_CODE_RE = re.compile(
    r"^(?:hey|ok|okay|so)?[,\s]*(?:"
    r"what\s+time\s+is\s+it|what(?:'s| is)\s+the\s+time|"
    r"what\s+day\s+is\s+it|what(?:'s| is)\s+(?:the\s+|today'?s\s+)?date|what(?:'s| is)\s+today|"
    r"what(?:'s| is)\s+the\s+weather|how(?:'s| is)\s+the\s+weather|"
    r"how\s+are\s+you(?:\s+doing)?|how\s+is\s+it\s+going|"
    r"are\s+you\s+(?:working|there|awake|ok|okay|online|ready|alive|listening)|"
    r"is\s+(?:this|it)\s+working|do\s+you\s+work"
    r")\s*\??$", re.IGNORECASE)

_NON_PYTHON_TOPICS = {
    "anime", "laptop", "computer", "phone", "capital of france", "france",
    "weather", "cricket", "football", "movie", "music", "recipe",
}

_CODE_REF_RE = re.compile(
    r"\b(this|these|that|those|my\s+code|my\s+program|the\s+code|the\s+program|"
    r"current\s+code|the\s+output|the\s+error|this\s+line|the\s+line|here)\b", re.IGNORECASE)

_WEAK_DEFER_WORDS = {
    "it", "again", "structure", "outline", "code", "program", "file", "line",
    "output", "error", "everything", "this", "that", "these", "those", "here",
}

_CONCEPT_DISPLAY = {
    "big_o": "time complexity",
    "oop": "object-oriented programming",
    "for_loop": "for loop",
    "while_loop": "while loop",
    "if_statement": "if statement",
    "from_import": "from import",
    "list_comprehension": "list comprehension",
    "dictionary_comprehension": "dictionary comprehension",
    "try_except": "try except",
    "syntax_error": "syntax error",
    "indentation_error": "indentation error",
    "type_error": "type error",
    "value_error": "value error",
    "index_error": "index error",
    "key_error": "key error",
    "file_handling": "file handling",
    "with_statement": "with statement",
}

_PYTHON_RELATED_WORDS = {
    "python", "py", "code", "programming", "program", "script", "syntax", "error",
    "function", "method", "class", "object", "module", "package", "loop", "list",
    "dictionary", "tuple", "set", "string", "integer", "float", "boolean", "decorator",
    "generator", "iterator", "dataclass", "typing", "annotation", "variable", "exception",
}

_ALIASES: Dict[str, str] = {}
for _kind, _data in _CATALOG.items():
    for _alias in _data.get("aliases", []):
        _ALIASES[str(_alias)] = _kind
_ALIASES["argument"] = "parameter"
_ALIASES["arguments"] = "parameter"
for _name in _BUILTIN_FALLBACKS:
    _ALIASES.setdefault(_name, _name)
for _name in _DANGEROUS_BUILTINS:
    _ALIASES.setdefault(_name, _name)
for _name in _MODULE_FALLBACKS:
    _ALIASES.setdefault(_name, _name)

_ALIASES_BY_LEN = sorted(_ALIASES, key=len, reverse=True)

# Compatibility for older tests/imports that inspect these names.
_CONCEPTS = {kind: str(data["message"]) for kind, data in _CATALOG.items()}
_BEGINNER_CONCEPTS = {
    kind: _CONCEPTS[kind]
    for kind in ("print", "input", "range", "for_loop", "variable", "function", "if_statement", "list")
}
_CONCEPT_ALIASES = {kind: list(data.get("aliases", [])) for kind, data in _CATALOG.items()}
_CONCEPT_BY_ALIAS = dict(_ALIASES)
_DEFER_SET = set()

_DEFINITIONAL_FORM_RE = re.compile(
    r"^(?:hey|ok|okay|so|um|hmm)?[,\s]*"
    r"(?:"
    r"what(?:'s| is| are| was)\s+(?:a\s+|an\s+|the\s+)?(?P<a>.+?)"
    r"|what\s+do(?:es)?\s+(?:a\s+|an\s+|the\s+)?(?P<b>.+?)\s+do"
    r"|what\s+do(?:es)?\s+(?:a\s+|an\s+|the\s+)?(?P<c>.+?)\s+mean"
    r"|define\s+(?:a\s+|an\s+|the\s+)?(?P<d>.+?)"
    r")\s*$",
    re.IGNORECASE,
)

_WEAK_FORM_RE = re.compile(
    r"^(?:hey|ok|okay|so|um|hmm)?[,\s]*"
    r"(?:"
    r"explain\s+(?:to\s+me\s+)?(?:what\s+(?:a\s+|an\s+|the\s+)?)?(?:a\s+|an\s+|the\s+)?(?P<a>.+?)"
    r"|tell\s+me\s+(?:more\s+)?about\s+(?:a\s+|an\s+|the\s+)?(?P<b>.+?)"
    r"|how\s+do(?:es)?\s+(?:a\s+|an\s+|the\s+)?(?P<c>.+?)\s+work"
    r"|why\s+(?:use|do\s+we\s+use|would\s+i\s+use)\s+(?:a\s+|an\s+|the\s+)?(?P<d>.+?)"
    r"|teach\s+me\s+(?:about\s+)?(?:a\s+|an\s+|the\s+)?(?P<e>.+?)"
    r")\s*$",
    re.IGNORECASE,
)


def _simple_text(text: str) -> str:
    return " ".join(str(text or "").lower().strip().rstrip(".!?").split())


def _extract_topic(rx, text: str) -> Optional[str]:
    match = rx.match(text)
    if not match:
        return None
    topic = next((group for group in match.groupdict().values() if group), "") or ""
    topic = re.sub(r"\s+in\s+python$", "", topic.strip(), flags=re.IGNORECASE)
    topic = topic.strip(" ?.!,'\"")
    return topic or None


def _normalize_plural_word(word: str) -> str:
    plural_map = {
        "loops": "loop",
        "lists": "list",
        "dicts": "dict",
        "dictionaries": "dictionary",
        "tuples": "tuple",
        "sets": "set",
        "strings": "string",
        "classes": "class",
        "objects": "object",
        "methods": "method",
        "modules": "module",
        "packages": "package",
        "functions": "function",
        "parameters": "parameter",
        "arguments": "argument",
        "exceptions": "exception",
        "decorators": "decorator",
        "generators": "generator",
        "comments": "comment",
        "errors": "error",
    }
    return plural_map.get(word, word)


def normalize_topic(topic: str) -> str:
    text = str(topic or "").strip().lower()
    text = text.replace("'", "")
    text = re.sub(r"\bdunder\s+init\b", "__init__", text)
    text = text.replace("try/except", "try except").replace("big-o", "big o")
    text = re.sub(r"[^a-z0-9_.\s]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    words = text.split()
    words = [word for word in words if word not in {"a", "an", "the"}]
    while len(words) > 1 and words[-1] in {"function", "functions", "method", "methods", "concept", "concepts", "topic"}:
        words.pop()
    words = [_normalize_plural_word(word) for word in words]
    return " ".join(words).strip()


def extract_concept_topic(text: str) -> Optional[str]:
    t = _simple_text(text)
    topic = _extract_topic(_DEFINITIONAL_FORM_RE, t)
    if topic is not None:
        return topic
    return _extract_topic(_WEAK_FORM_RE, t)


def _is_non_python_topic(topic: str) -> bool:
    normalized = normalize_topic(topic)
    if not normalized:
        return False
    if normalized in _NON_PYTHON_TOPICS:
        return True
    if "capital" in normalized and "france" in normalized:
        return True
    return False


def is_python_related_topic(topic: str) -> bool:
    normalized = normalize_topic(topic)
    if not normalized or _is_non_python_topic(normalized):
        return False
    if normalized in _ALIASES:
        return True
    words = set(re.findall(r"[a-z_]+", normalized))
    return bool(words & _PYTHON_RELATED_WORDS or "." in normalized or normalized.startswith("__"))


def classify_non_code_query(text: str) -> Optional[str]:
    t = _simple_text(text)
    if not t:
        return None
    if _IDENTITY_RE.match(t):
        return IDENTITY_QUERY
    if _NON_CODE_RE.match(t):
        return NON_CODE_QUERY
    topic = extract_concept_topic(t)
    if topic and _is_non_python_topic(topic):
        return NON_CODE_QUERY
    return None


def non_code_answer(kind: str) -> str:
    if kind == IDENTITY_QUERY:
        return _IDENTITY_MESSAGE
    if kind == NON_CODE_QUERY:
        return _NON_CODE_MESSAGE
    return _UNKNOWN_CONCEPT_MESSAGE


def concept_label(kind: str) -> str:
    kind = str(kind or "").strip()
    if not kind or kind in {UNKNOWN_CONCEPT, IDENTITY_QUERY, NON_CODE_QUERY}:
        return ""
    return _CONCEPT_DISPLAY.get(kind, kind.replace("_", " "))


def _weak_command_target(topic: str) -> bool:
    t = (topic or "").lower()
    if _CODE_REF_RE.search(t):
        return True
    return bool(set(re.findall(r"[a-z]+", t)) & _WEAK_DEFER_WORDS)


def _lookup_concept(topic: str) -> Optional[str]:
    raw = str(topic or "").strip()
    normalized = normalize_topic(raw)
    if not normalized:
        return None
    if _CODE_REF_RE.search(raw):
        return None
    if _is_non_python_topic(normalized):
        return None
    if normalized in _ALIASES:
        return _ALIASES[normalized]
    for alias in _ALIASES_BY_LEN:
        if "." in alias or alias.startswith("__"):
            continue
        if re.search(rf"\b{re.escape(alias)}\b", normalized):
            return _ALIASES[alias]
    return UNKNOWN_CONCEPT


def classify_concept_question(text: str) -> Optional[str]:
    t = _simple_text(text)
    if not t:
        return None
    topic = _extract_topic(_DEFINITIONAL_FORM_RE, t)
    if topic is not None:
        return _lookup_concept(topic)
    topic = _extract_topic(_WEAK_FORM_RE, t)
    if topic is not None:
        kind = _lookup_concept(topic)
        if kind not in (None, UNKNOWN_CONCEPT):
            return kind
        if kind is None:
            return None
        return None if _weak_command_target(topic) else UNKNOWN_CONCEPT
    for kind, phrases in _TRIGGERS:
        for phrase in phrases:
            if t == phrase or t.startswith(phrase + " "):
                return kind
    return None


def _first_string_literal(code: str) -> Optional[str]:
    match = re.search(r"""(['"])(.*?)\1""", code or "")
    if match and match.group(2).strip():
        return match.group(2).strip()
    return None


def _first_range_count(code: str) -> Optional[int]:
    match = re.search(r"\brange\s*\(\s*(\d+)", code or "")
    return int(match.group(1)) if match else None


def _number_list(n: int) -> str:
    items = [str(i) for i in range(n)]
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + ", and " + items[-1]


def answer_concept(kind: str, code: str = "") -> Tuple[str, List[str]]:
    code = code or ""
    if kind == "quotes":
        literal = _first_string_literal(code)
        if literal:
            return (f"Quotes tell Python that {literal} is text, not a variable name.",
                    [literal, "text", "variable"])
        return ("Quotes tell Python that the words inside are text, not a variable name.",
                ["text", "variable"])
    if kind == "colon":
        return ("A colon starts a block, and the indented lines below belong to it.",
                ["colon", "block"])
    if kind == UNKNOWN_CONCEPT:
        return (_UNKNOWN_CONCEPT_MESSAGE, [])
    if kind in _CATALOG:
        return (str(_CATALOG[kind]["message"]), [])
    if kind in _DANGEROUS_BUILTINS:
        return (_DANGEROUS_BUILTINS[kind], [])
    if kind in _BUILTIN_FALLBACKS:
        return (_BUILTIN_FALLBACKS[kind], [])
    if kind in _MODULE_FALLBACKS:
        return (_MODULE_FALLBACKS[kind], [])
    return ("", [])
