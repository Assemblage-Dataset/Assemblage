import re

from subprocess import Popen, PIPE, STDOUT, TimeoutExpired
from assemblage.worker.parse_function import parse_function
import os
def runcmd_ctags(cmd):
    stdout, stderr = None, None
    with Popen(cmd, shell=True, stdin=PIPE, stdout=PIPE, stderr=STDOUT, close_fds=True) as process:
        try:
            stdout, stderr = process.communicate(timeout=600)
        except TimeoutExpired:
            process.kill()
    return stdout, stderr, process.returncode



def get_functions(file):
    # ONLY USE universal-ctags
    out, err, status = runcmd_ctags("ctags --put-field-prefix --fields='{line}{end}' -o - "+ f'"{file}"')
    lines = out.decode('utf-8','ignore').splitlines()
    functions = []
    for line in lines:
        parts = line.split()
        try:
            # 0: name, 1: startline, 2: endline, 3: def, 4: top comments, 5: body, 6: body comment, 
            # 7: prototype, 8: line to code 9: Mark's code extracted function body 10: start line (unchanged)
            functionname = parts.pop(0)
            if functionname.startswith("__anon"):
                continue
            functionname = functionname.replace("/^", "").replace("$/;", "")
            functionname = functionname.split("(")[0]
            sourcefile = parts.pop(0)
            endline = parts.pop().split(":")[-1]
            startline = parts.pop().split(":")[-1]
            if endline.isdigit() and startline.isdigit():
                functions.append([functionname, startline, endline, " ".join(parts), [], [], [], [], {}, "", startline])
        except Exception as e:
            print("CTAGS err 32", e)
    functions.sort(key=lambda x: int(x[2]))
    for f in functions:
        f[1] = int(f[1])
        f[2] = int(f[2])

    if len(functions) > 1:
        functions[0][1] = 0
        for i in range(1, len(functions)):
            if int(functions[i-1][2])+1 < int(functions[i][1]):
                functions[i][1] = int(functions[i-1][2])+1
    filecontent = [""]*100000
    try:
        filecontent = open(file, 'r', encoding="utf-8", errors="ignore").readlines()
    except:
        pass

    lines = list(filecontent)
    n = len(lines)
    for f in functions:
        start, end = f[1], f[2]
        s, e = max(1, start)-1, min(n, end)
        chunk = lines[s:e]
        f[5].extend(chunk)
        if isinstance(f[8], list) and len(f[8]) >= n:
            f[8][s:e] = chunk
        else:
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
        function[7] = extract_function_prototype(function[9], function[0])
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
