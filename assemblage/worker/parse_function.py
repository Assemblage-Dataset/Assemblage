"""

This code tests a function to parse c/cpp source code for the full source of a specified function, including the prototype,
any prototype decorators, and the top comment for the function. See the doc string for parse_function for more details.
"""

import sys

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
                    return parse_function(function_name.split('::')[-1], prefix_lines)
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
