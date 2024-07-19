"""
Source code parsing code

1. Use universal-ctags
2. Parse function prototype, top comments, body comments, and function body

Rebecca Saul
Chang Liu
"""

import re
import sys
from subprocess import Popen, PIPE, STDOUT, TimeoutExpired


def parse_function(function_name, prefix_lines, start_line, end_line):
    """
    This function takes a string of c/cpp source code, a function name, and the start and end offsets of the function body,
    and returns the function prototype, return type, prototype decorators (e.g. static, __declspec(dllexport), etc.), the
    top comment block (if any), and the function body.
    """

    #body = code[start_offset: end_offset]
    #prefix = code[:start_offset]
    #prefix_lines = prefix.split('\n')

    if(len(prefix_lines) == 0):
        return None #body

    start_offset = end_line - start_line
    
    START = 1
    FOUND_FUNCTION_PROTOTYPE = 2
    FOUND_END_SINGLE_LINE_COMMENT_BLOCK = 3
    FOUND_END_MULTILINE_COMMENT_BLOCK = 4
    DONE = 5

    """If we've seen a blank noncomment line before the prototype we assume there are
    no more prototype decorators up the file. E.g.
    

            THIS_IS_NOT_A_DECORATTOR
            
            static
            int
            __stdcall
            foo(int, a, b){return 42;}
    """

    seen_blank_noncomment_line_before_prototype = False

    state=START
    
    idx = len(prefix_lines) - start_offset
    top_candidate_line_idx = idx
    
    while state != DONE:
        idx -= 1
        if idx < 0:
            if state == START:
                if function_name.split('::')[-1] != function_name:
                    return parse_function(function_name.split('::')[-1], prefix_lines, start_line, end_line)
                else:
                    state = DONE
            else:
                state = DONE

        elif state == START:
            # Back up from body to find function prototype
            if function_name + "(" in prefix_lines[idx]:
                state = FOUND_FUNCTION_PROTOTYPE
                top_candidate_line_idx = idx
        
        elif state == FOUND_FUNCTION_PROTOTYPE:

            curr_line = prefix_lines[idx].strip()
            if len(curr_line) > 0:
                if curr_line.startswith('//'):
                    state = FOUND_END_SINGLE_LINE_COMMENT_BLOCK
                elif curr_line.endswith('*/'):
                    state = FOUND_END_MULTILINE_COMMENT_BLOCK
                elif (curr_line[0] == '#') or (curr_line.split('//')[0].rstrip()[-1] in [';','}','{']):
                    # Ran into the previous function. We're done.
                    state = DONE
                else:

                    # Want to treat consecutive non-blank non-comment lines immediately before the
                    # prototype as a decorateor
                    if not seen_blank_noncomment_line_before_prototype:
                        top_candidate_line_idx = idx
                    # Skip lines that might be function decorators
                    pass

            else:
                # Skip blank lines

                # Record that we've seen a blank line before the prototype
                seen_blank_noncomment_line_before_prototype = True
                pass

        elif state == FOUND_END_SINGLE_LINE_COMMENT_BLOCK:
            curr_line = prefix_lines[idx].strip()
            if len(curr_line) == 0 or not curr_line.startswith('//'):
                top_candidate_line_idx = idx+1
                state = DONE
            else:
                # Continue backing up to find the beginning of the comment block
                pass

        elif state == FOUND_END_MULTILINE_COMMENT_BLOCK:
            curr_line = prefix_lines[idx].strip()
            if curr_line.startswith('/*'):
                top_candidate_line_idx = idx
                state = DONE
            else:
                # Continue backing up to find the beginning of the comment block
                pass

    if top_candidate_line_idx == len(prefix_lines):
        return None #body
    
    return '\n'.join(prefix_lines[top_candidate_line_idx:]) #+ body

def runcmd_ctags(cmd):
    stdout, stderr = None, None
    with Popen(cmd, shell=True, stdin=PIPE, stdout=PIPE, stderr=STDOUT, close_fds=True) as process:
        try:
            stdout, stderr = process.communicate(timeout=600)
        except TimeoutExpired:
            process.kill()
    return stdout, stderr, process.returncode

def get_functions(file):
    # USE universal-ctags
    out, err, status = runcmd_ctags("ctags --put-field-prefix -h='.h.H.hh.hpp.hxx.h++.inc.def.' --fields='{line}{end}' -o - "+ f'"{file}"')
    lines = out.decode('utf-8','ignore').splitlines()
    # print(out.decode('utf-8','ignore'))

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
    # Join function by end
    if len(functions) > 1:
        functions[0][1] = 0
        for i in range(1, len(functions)):
            if int(functions[i-1][2])+1 < int(functions[i][1]):
                functions[i][1] = int(functions[i-1][2])+1
    filecontent = [""]
    try:
        filecontent = open(file, 'r', encoding="utf-8", errors="ignore").readlines()
    except:
        pass
         
    for i, line in enumerate(filecontent):
        for function in functions:
            if i >= int(function[1])-1 and i <= int(function[2])-1:
                function[5].append(line)
                function[8][i] = line

    for function in functions:
        # function[5] = "".join(function[5])
        # start_offset = function[5].find(function[0])
        # if len(function[5])>0:
        #     for i in range(function[5].find(function[0]), len(function[5])):
        #         if function[5][i] == "{":
        #             start_offset = i
        #             break
        # else:
        #     start_offset = 0
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
        function[5] = "".join(function[5])
        function[9] = parse_function(function[0], function[5].splitlines(), int(function[10]), int(function[2]))
        if not function[9]:
            function[9] = ''
        function[4] = get_top_comments(function[9], function[0])
        function[6] = get_body_comments(function[9], function[0])
        function[7] = extract_function_prototype(function[9], function[0])
    return functions


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
