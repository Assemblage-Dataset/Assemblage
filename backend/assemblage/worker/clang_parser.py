import re

from subprocess import Popen, PIPE, STDOUT, TimeoutExpired
from assemblage.worker.parse_function import parse_function
import os

from clang.cindex import Config, Index, CursorKind
import time

# Configure libclang path
Config.set_library_path(r"C:\Program Files\LLVM\bin")

CLTIMEOUT = 3600

def extract_functions(file_path):
    STARTTIME = time.time()
    if not os.path.exists(file_path):
        return []
    index = Index.create()
    tu = index.parse(file_path)
    functions = []
    
    def traverse(node):
        if node.is_definition() and node.kind not in [CursorKind.ENUM_CONSTANT_DECL]:
            # Extract function prototype
            prototype = " ".join(t.spelling for t in node.get_tokens() 
                                if t.kind.name != "PUNCTUATION" or t.spelling != '{')
            prototype = prototype.split('{')[0].strip()
            functions.append({
                "name": node.spelling,
                "start": node.extent.start.line,
                "end": node.extent.end.line,
                "prototype": prototype,
                "node.kind": node.kind.name
            })
        for child in node.get_children():
            traverse(child, depth-1)
    
    traverse(tu.cursor)
    filelines = len(open(file_path).readlines())
    # drop lines larger than file contents
    functions = [f for f in functions if f['end'] <= filelines]
    functions = [f for f in functions if f['start'] <= filelines]
    return functions


def get_functions(file):
    functions = []
    for func in extract_functions(file):
        # 0: name, 1: startline, 2: endline, 3: def, 4: top comments, 5: body, 6: body comment, 
        # 7: prototype, 8: line to code 9: Mark's code extracted function body 10: start line (unchanged)
        functionname = func['name'].replace("()", "")
        endline = func['end']
        startline = func['start']
        prototype = func['prototype']
        functions.append([functionname, startline, endline, "", [], [], [], prototype, {}, "", startline])

    filecontent = [""]*100000
    try:
        filecontent = open(file, 'r', encoding="utf-8", errors="ignore").readlines()
    except:
        pass

    lines = list(filecontent)  # one pass
    n = len(lines)

    for f in functions:
        start, end = f[1], f[2]
        s, e = max(1, start)-1, min(n, end)
        chunk = lines[s:e]
        f[5].extend(chunk)
        if isinstance(f[8], list) and len(f[8]) >= n:
            f[8][s:e] = chunk
        else:
            # Fallback: set per-line
            for idx, line in enumerate(chunk, start=s):
                f[8][idx] = line


    for function in functions:
        lines_to_remove = []
        for source_line_potential in function[5]:
            if "#" in source_line_potential\
                or "#if" in source_line_potential\
                or "#pragma" in source_line_potential\
                or ("}" in source_line_potential and ";" in source_line_potential)\
                :
                lines_to_remove.append(source_line_potential)
            else:
                break
        for line in lines_to_remove:
            function[5].remove(line)
        function[3] = "".join(filecontent[int(function[1])-1:int(function[2])])
        function[5] = "".join(function[5])
        function[9] = parse_function(function[0], function[5].splitlines(), int(function[10]), int(function[2]))
        if not function[9]:
            function[9] = ''
        function[4] = get_top_comments(function[9], function[0])
        function[6] = get_body_comments(function[9], function[0])
    function_dedup = {}
    for function in functions:
        if function[0] in function_dedup:
            already_existied = function_dedup[function[0]]
            # 0: name, 1: startline, 2: endline, 3: def, 4: top comments, 5: body, 6: body comment, 
            # 7: prototype, 8: line to code 9: Mark's code extracted function body 10: start line (unchanged)
            already_existied[1] = min(int(function[1]),  int(already_existied[1]))
            already_existied[2] = max(int(function[2]),  int(already_existied[2]))
            already_existied[3] += function[3]
            already_existied[4] += function[4]
            already_existied[5] += function[5]
            already_existied[6] += function[6]
            already_existied[7] += function[7]
            for linenum in function[8]:
                already_existied[8][linenum] = function[8][linenum]
            already_existied[9] += function[9]
            already_existied[10] += function[10]
        else:
            function_dedup[function[0]] = function
    return list(function_dedup.values())


def get_top_comments(s, function_name):
    comments = []
    multiple_line_comment = 0

    for line in s.splitlines():

        # Found function name, exit
        if re.search(rf"{function_name}\s*\(", line):
            break
        
        # Exclude preprocessor directives
        if "#include" in line:
            comments=[]
        # Start of multiple line comment
        if "/*" in line:
            multiple_line_comment = 1
            comments.append(line)
        # Single line comment
        elif "//" in line:
            comments.append(line)
        # End of multiple line comment
        elif "*/" in line:
            multiple_line_comment = 0
            comments.append(line)
        elif multiple_line_comment:
            comments.append(line)
        if "*/" in line:
            multiple_line_comment = 0
    while "" in comments:
        comments.remove("")
    return "\n".join(comments)


def get_body_comments(s, function_name):
    comments = []
    started = 0
    multiple_line_comment = 0
    for line in s.splitlines():
        # Found function name, exit
        if re.search(rf"{function_name}\s*\(", line):
            started = 1
        # Exclude preprocessor directives
        if "#include" in line:
            comments=[]
        # Start of multiple line comment
        if started and "/*" in line:
            multiple_line_comment = 1
            comments.append(line)
        # Single line comment
        elif started and "//" in line:
            comments.append(line)
        # End of multiple line comment
        elif started and "*/" in line:
            multiple_line_comment = 0
            comments.append(line)
        elif started and multiple_line_comment:
            comments.append(line)
        if "*/" in line:
            multiple_line_comment = 0
    while "" in comments:
        comments.remove("")
    return "\n".join(comments)

def extract_function_prototype(s, function_name):
    lines = s.splitlines()
    
    while "" in lines:
        lines.remove("")

    if len(lines)==0:
        return ""
    startline = 0
    endline = len(lines)
    def_line_number = 0
    for i, line in enumerate(lines):
        if re.search(rf"{function_name}\s*\(", line) and "//" not in line:
            startline = i
            endline = i
            def_line_number = i
            break
    for i in range(def_line_number-1, -1, -1):
        if "//" in lines[i]\
                or "/*" in lines[i]\
                or "*/" in lines[i]\
                or "#" in lines[i]\
                or ";" in lines[i]\
                or "}" in lines[i]:
            startline = i
            break
    for i in range(def_line_number, len(lines)):
        if ")" in lines[i]:
            endline = i
            break
    # print("***", startline, lines[0])
    if startline==0 and 1 not in [x in lines[0] for x in ["//", "/*", "*/"]]:
        startline = -1
    if startline==def_line_number:
        startline = -1
    if startline==endline:
        return lines[def_line_number]

    return "\n".join(lines[startline+1:endline+1])
