"""
A parser for Visual Studio Solution File.
Modified from https://github.com/jhandley/pyvcproj/blob/master/vcproj/solution.py under Unlicense licence
"""
import re
import codecs

__all__ = ['Solution', 'parse']


class SolutionFileError(Exception):
    pass


_REGEX_PROJECT_FILE = re.compile(
    r'Project\("\{([^\}]+)\}"\)[\s=]+"([^\"]+)",\s"(.+proj)", "(\{[^\}]+\})"')
_REGEX_END_PROJECT = re.compile(r"""\s*EndProject""")
_REGEX_PROJECT_DEPENDENCIES_SECTION = re.compile(
    r"""\s*ProjectSection\((\w+)\) = postProject""")
_REGEX_END_PROJECT_SECTION = re.compile(r"""\s*EndProjectSection""")
_REGEX_END_GLOBAL_SECTION = re.compile(r"""\s*EndGlobalSection""")
_REGEX_DEPENDENCY = re.compile(
    r"""\s*(\{[A-Za-z0-9-]+\})\s*=\s*(\{[A-Za-z0-9-]+\})""")
_REGEX_CONFIG_START = re.compile(r"""\s*GlobalSection\(SolutionConfigurationPlatforms\)\s*\=\s*preSolution\s*""")

class Solution(object):
    """Visual C++ solution file (.sln)."""

    def __init__(self, filename):
        """Create a Solution instance for solution file *name*."""
        self.filename = filename
        self.projects = []
        with open(self.filename, 'rb') as f:
            line = f.readline()
            while line:
                line = f.readline().decode('utf-8')
                if line.startswith("Project"):
                    match = _REGEX_PROJECT_FILE.match(line)
                    if match:
                        self.projects.append(
                            Solution.__read_project(match.groups(), f))
                    # else:
                    #     print('No MATCH: {0}'.format(line))
                elif line.startswith("Global"):
                    self.globals = Solution.__read_global(f)

    @staticmethod
    def __read_project(project, f):
        dependencies = []
        while True:
            line = f.readline().decode('utf-8')
            if line is None:
                raise SolutionFileError("Missing end project")
            if _REGEX_END_PROJECT.match(line):
                break
            if _REGEX_PROJECT_DEPENDENCIES_SECTION.match(line):
                dependencies = Solution.__read_dependencies(f)
        return project + (dependencies,)

    @staticmethod
    def __read_dependencies(f):
        dependencies = []
        while True:
            line = f.readline().decode('utf-8')
            if line is None:
                raise SolutionFileError("Missing end dependencies section")
            if _REGEX_END_PROJECT_SECTION.match(line):
                break
            match = _REGEX_DEPENDENCY.match(line)
            if match:
                dependencies.append(match.group(1))
        return dependencies

    @staticmethod
    def __read_global(f):
        result = ""
        while True:
            line = f.readline().decode('utf-8')
            if line is None:
                raise SolutionFileError("Missing end global")
            if line.startswith("EndGlobal"):
                break
            result += line
        return result


    def set_config(self, platform, buildmode, filename=None):
        # Quite hard coded to meet the need
        started = 0
        configs = []
        f = open(self.filename, "rb")
        while True:
            line = f.readline().decode('utf-8-sig')
            if started and _REGEX_END_GLOBAL_SECTION.match(line):
                break
            if started:
                configs.append(line.split("=")[0].strip())
            started = _REGEX_CONFIG_START.match(line) or started
        if f"{buildmode}|{platform}" in configs:
            return
        target_str = configs[0]
        replace_str = f"{buildmode}|{platform}"
        f = open(self.filename, "r", encoding="utf-8-sig")
        lines = f.readlines()
        lines = [x.replace(target_str, replace_str) for x in lines]
        with open(self.filename, "w", encoding="utf-8-sig") as f:
            f.write("".join(lines))


    def project_files(self):
        """List project files (.vcxproj.) in solution."""
        return list(map(lambda p: p[2], self.projects))

    def project_names(self):
        """List project files (.vcxproj.) in solution."""
        return list(map(lambda p: p[1], self.projects))

    def project_names_files(self):
        """List project files (.vcxproj.) in solution."""
        return list(map(lambda p: (p[1], p[2]), self.projects))

    def dependencies(self, project_name):
        """List names of projects dependent on project *project_name*"""
        project = self.__project_from_name(project_name)
        if not project:
            raise SolutionFileError(
                "Can't find project with name " + project_name)
        return map(lambda d: self.__project_from_id(d)[1], project[4])

    def set_dependencies(self, project_name, dependencies):
        """Set names of projects dependent on project *project_name* to *dependencies*"""
        project = self.__project_from_name(project_name)
        if not project:
            raise SolutionFileError(
                "Can't find project with name " + project_name)
        index = self.projects.index(project)
        self.projects[index] = project[0:4] + \
            (map(lambda d: self.__project_from_name(d)[3], dependencies),)

    def __project_from_name(self, project_name):
        return next((p for p in self.projects if p[1] == project_name), None)

    def __project_from_id(self, project_id):
        projs = list(filter(lambda p: p[3] == project_id, self.projects))
        return projs[0]

    def write(self, filename=None):
        """Save solution file."""
        filename = filename or self.filename
        with codecs.open(filename, "wb", "utf-8-sig") as f:
            f.write(
                "\r\nMicrosoft Visual Studio Solution File, Format Version 11.00\r\n")
            f.write("# Visual Studio 2010\r\n")
            for project in self.projects:
                f.write("Project(\"{{{0}}}\") = \"{1}\", \"{2}\", \"{3}\"\r\n".format(
                    *project[0:4]))
                dependencies = project[4]
                if dependencies:
                    f.write(
                        "\tProjectSection(ProjectDependencies) = postProject\r\n")
                    for d in dependencies:
                        f.write("\t\t{0} = {0}\r\n".format(d))
                    f.write("\tEndProjectSection\r\n")
                f.write("EndProject\r\n")
            f.write("Global\r\n")
            f.write(self.globals)
            f.write("EndGlobal\r\n")


def parse(filename):
    """Parse solution file filename and return Solution instance."""
    return Solution(filename)
