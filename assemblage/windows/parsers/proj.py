"""Visual C++ project file."""

import xml.etree.ElementTree as ET
import re
import string

__all__ = ['Project', 'parse']

_MS_BUILD_NAMESPACE = "http://schemas.microsoft.com/developer/msbuild/2003"
_REGEX_CONFIG_CONDITION = re.compile(
    r"""'\$\(Configuration\)\|\$\(Platform\)'=='(\w+)\|(\w+)'""")


def _parse_config_condition(condition):
    return _REGEX_CONFIG_CONDITION.match(condition).groups()


def _matches_platform_configuration(condition, platform, configuration):
    (p, c) = _parse_config_condition(condition)
    return (platform == "All Configurations"
            or p == platform) and (configuration == "All Configurations"
                                   or c == configuration)


# ET.register_namespace doesn't exist before python 2.7
try:
    _register_namespace = ET.register_namespace
except AttributeError:

    def _register_namespace(prefix, uri):
        ET._namespace_map[uri] = prefix


class Project(object):
    """Visual C++ project file (.vcxproj)."""

    def __init__(self, filename):
        """Create a Project instance for project file *filename*."""
        _register_namespace('', _MS_BUILD_NAMESPACE)
        self.filename = filename
        try:
            self.xml = ET.parse(filename)
        except:
            raise FileNotFoundError(f"Can't find xml file {filename}")

    def configuration_type(self):
        """Project type (Application, StaticLibrary or DynamicLibrary)."""
        return self.__property_group_item_for_config('All Configurations',
                                                     'All Configurations',
                                                     'Configuration',
                                                     'ConfigurationType')

    def configurations(self,
                       platform='All Configurations',
                       configuration='All Configurations'):
        """List available configurations for this project as list of tuples (config, platform)"""
        item_groups = self.xml.findall("./{" + _MS_BUILD_NAMESPACE +
                                       "}ItemGroup")
        config_groups = (
            item_group for item_group in item_groups
            if item_group.attrib.get('Label', None) == 'ProjectConfigurations')
        config_group = next(config_groups, None)
        confignplat = []
        for config_item in config_group:
            item_config = config_item.find("./{" + _MS_BUILD_NAMESPACE +
                                           "}Configuration").text
            item_platform = config_item.find("./{" + _MS_BUILD_NAMESPACE +
                                             "}Platform").text
            if (platform == 'All Configurations' or item_platform
                    == platform) and (configuration == 'All Configurations'
                                      or item_config == configuration):
                confignplat.append((item_config, item_platform))
        return confignplat

    def source_files(self):
        """List source files in project."""
        return [
            c.attrib['Include']
            for c in self.xml.findall(".//{" + _MS_BUILD_NAMESPACE +
                                      "}ClCompile") if 'Include' in c.attrib
        ]

    def include_files(self):
        """List include files in project."""
        return [
            c.attrib['Include']
            for c in self.xml.findall(".//{" + _MS_BUILD_NAMESPACE +
                                      "}ClInclude") if 'Include' in c.attrib
        ]

    def __item_groups_for_config(self, platform, configuration):
        groups = self.xml.findall("./{" + _MS_BUILD_NAMESPACE +
                                  "}ItemDefinitionGroup")
        return list(
            filter(
                lambda g: _matches_platform_configuration(
                    g.attrib['Condition'], platform, configuration), groups))

    def __item_group_item_for_config(self, platform, configuration,
                                     subgroup_name, item_name):
        groups = self.__item_groups_for_config(platform, configuration)
        if len(groups) == 0:
            return None
        item = groups[0].find("{" + _MS_BUILD_NAMESPACE + "}" + subgroup_name +
                              "/{" + _MS_BUILD_NAMESPACE + "}" + item_name)
        return item.text if item is not None else None

    def __set_item_group_items_for_config(self, platform, configuration,
                                          subgroup_name, item_name, val):
        groups = self.__item_groups_for_config(platform, configuration)
        for group in groups:
            subgroup = group.find("{" + _MS_BUILD_NAMESPACE + "}" +
                                  subgroup_name)
            if subgroup is None:
                continue
            item = subgroup.find("{" + _MS_BUILD_NAMESPACE + "}" + item_name)
            if val is None:
                # remove the node to get "inherit from project defaults"
                if item is not None:
                    subgroup.remove(item)
            else:
                if item is None:
                    item = ET.SubElement(
                        subgroup, "{" + _MS_BUILD_NAMESPACE + "}" + item_name)
                item.text = val

    def __property_group_item_for_config(self, platform, configuration, label,
                                         item_name):
        property_groups = self.xml.findall("./{" + _MS_BUILD_NAMESPACE +
                                           "}PropertyGroup")
        matching_groups = (group for group in property_groups
                           if group.attrib.get('Label', None) == label)
        for group in matching_groups:
            if 'Condition' not in group.attrib or _matches_platform_configuration(
                    group.attrib['Condition'], platform, configuration):
                items = group.findall("{" + _MS_BUILD_NAMESPACE + "}" +
                                      item_name)
                for item in items:
                    if item is not None:
                        if 'Condition' not in item.attrib or _matches_platform_configuration(
                                item.attrib['Condition'], platform,
                                configuration):
                            return item.text
        return ""

    def __set_property_group_items_for_config(self, platform, configuration,
                                              label, item_name, val):
        if platform == "All Configurations" or configuration == "All Configurations":
            # Gets too hairy to handle wild cards in the code below when new items need to be created
            # so ensure that we always call with specific configs.
            for config in self.configurations(platform, configuration):
                self.__set_property_group_items_for_config(
                    config[0], config[1], label, item_name, val)
        else:
            property_groups = self.xml.findall("./{" + _MS_BUILD_NAMESPACE +
                                               "}PropertyGroup")
            label_matching_groups = (
                group for group in property_groups
                if group.attrib.get('Label', None) == label)
            condition_matching_groups = (
                g for g in label_matching_groups if
                'Condition' not in g.attrib or _matches_platform_configuration(
                    g.attrib['Condition'], platform, configuration))
            group = next(condition_matching_groups, None)
            if group is not None:
                group_condition = group.attrib.get('Condition', None)
                # In some files there are multiple group nodes each with a condition and on other files
                # (ones imported from earlier versions of Visual Studio maybe?) there is a single group node
                # and each child has a condition.
                if group_condition is None:
                    # No condition on group, must be conditions on the items
                    items = group.findall("{" + _MS_BUILD_NAMESPACE + "}" +
                                          item_name)
                    item = next(
                        (item
                         for item in items if _matches_platform_configuration(
                             item.attrib['Condition'], platform, configuration)
                         ), None)
                    if val is None:
                        if item is not None:
                            # remove the node to get "inherit from project defaults"
                            group.remove(item)
                    else:
                        if item is None:
                            item = ET.SubElement(
                                group,
                                "{" + _MS_BUILD_NAMESPACE + "}" + item_name)
                            item.attrib[
                                'Condition'] = "'$(Configuration)|$(Platform)'=='{0}|{1}'".format(
                                    configuration, platform)
                        item.text = val
                else:
                    # condition on group so none on items
                    item = group.find("{" + _MS_BUILD_NAMESPACE + "}" +
                                      item_name)
                    if val is None:
                        if item is not None:
                            # remove the node to get "inherit from project defaults"
                            group.remove(item)
                    else:
                        if item is None:
                            item = ET.SubElement(
                                group,
                                "{" + _MS_BUILD_NAMESPACE + "}" + item_name)
                    item.text = val

    def additional_link_dependencies(self, platform, configuration):
        """List libraries linked to by this project"""
        link_deps = self.__item_group_item_for_config(
            platform, configuration, "Link", "AdditionalDependencies")
        return link_deps.split(';') if link_deps is not None else None

    def additional_include_directories(self, platform, configuration):
        """List additional include directories for this project"""
        includes = self.__item_group_item_for_config(
            platform, configuration, "ClCompile",
            "AdditionalIncludeDirectories")
        return includes.split(';') if includes is not None else None

    def set_additional_include_directories(self, platform, configuration,
                                           additional_includes):
        """Set additional include directories for this project"""

        dirs = ";".join(
            additional_includes) if additional_includes is not None else None
        self.__set_item_group_items_for_config(platform, configuration,
                                               "ClCompile",
                                               "AdditionalIncludeDirectories",
                                               dirs)

    def output_file(self, platform, configuration):
        """Get output file name for this project"""
        group_name = "Lib" if self.configuration_type(
        ) == "StaticLibrary" else "Link"
        return self.__item_group_item_for_config(platform, configuration,
                                                 group_name, "OutputFile")

    def set_output_file(self, platform, configuration, output_file_name):
        """Set output file name for this project"""
        group_name = "Lib" if self.configuration_type(
        ) == "StaticLibrary" else "Link"
        self.__set_item_group_items_for_config(platform, configuration,
                                               group_name, "OutputFile",
                                               output_file_name)

    def output_directory(self, platform, configuration):
        """Output directory for this project"""
        # outdir is in an unlabeled PropertyGroup
        try:
            return self.__property_group_item_for_config(
                platform, configuration, None, 'OutDir')
        except:
            return None

    def set_output_directory(self, platform, configuration, output_directory):
        """Set output directory for this project"""
        try:
            self.__set_property_group_items_for_config(platform, configuration,
                                                       None, 'OutDir',
                                                       output_directory)
            return True
        except AttributeError:
            return False

    def program_database_file(self, platform, configuration):
        """Path to program database file."""
        return self.__item_group_item_for_config(platform, configuration,
                                                 "Link", "ProgramDatabaseFile")

    def set_program_database_file(self, platform, configuration, pdb_path):
        """Set path to program database file."""
        return self.__set_item_group_items_for_config(platform, configuration,
                                                      "Link",
                                                      "ProgramDatabaseFile",
                                                      pdb_path)

    def set_optimization(self,
                         optimization,
                         platform="All Configurations",
                         configuration="All Configurations"):
        optimization_mode = ""
        if "O2" in optimization:
            optimization_mode = "MaxSpeed"
        elif "O1" in optimization:
            optimization_mode = "MinSpace"
        elif "Ox" in optimization:
            optimization_mode = "Full"
            self.set_whole_program_optimization("true")
        else:
            optimization_mode = ""
        item_name = "ClCompile"
        label = "Optimization"
        property_groups = self.xml.findall("./{" + _MS_BUILD_NAMESPACE +
                                           "}ItemDefinitionGroup")
        for group in property_groups:
            items = group.findall("{" + _MS_BUILD_NAMESPACE + "}" + item_name)
            for item in items:
                subitem = item.find("{" + _MS_BUILD_NAMESPACE + "}" + label)
                try:
                    subitem.text = optimization_mode
                except AttributeError:
                    self.__set_item_group_items_for_config(
                        platform, configuration, "ClCompile", label,
                        optimization_mode)
        if optimization_mode!= "":
            item_name = "ClCompile"
            label = "BasicRuntimeChecks"
            property_groups = self.xml.findall("./{" + _MS_BUILD_NAMESPACE +
                                            "}ItemDefinitionGroup")
            for group in property_groups:
                items = group.findall("{" + _MS_BUILD_NAMESPACE + "}" + item_name)
                for item in items:
                    subitem = item.find("{" + _MS_BUILD_NAMESPACE + "}" + label)
                    try:
                        subitem.text = "Default"
                    except AttributeError:
                        self.__set_item_group_items_for_config(
                            platform, configuration, "ClCompile", label,
                            "Default")


    def set_favorsizeorspeed(self,
                             optimization,
                             platform="All Configurations",
                             configuration="All Configurations"):
        optimization_mode = ""
        if "Ot" in optimization:
            optimization_mode = "Speed"
        elif "Os" in optimization:
            optimization_mode = "Size"
        else:
            optimization_mode = ""
        item_name = "ClCompile"
        label = "FavorSizeOrSpeed"
        property_groups = self.xml.findall("./{" + _MS_BUILD_NAMESPACE +
                                           "}ItemDefinitionGroup")
        for group in property_groups:
            items = group.findall("{" + _MS_BUILD_NAMESPACE + "}" + item_name)
            for item in items:
                subitem = item.find("{" + _MS_BUILD_NAMESPACE + "}" + label)
                try:
                    subitem.text = optimization_mode
                except AttributeError:
                    self.__set_item_group_items_for_config(
                        platform, configuration, "ClCompile", label,
                        optimization_mode)

    def get_favorsizeorspeed(self,
                             platform="All Configurations",
                             configuration="All Configurations"):
        item_name = "ClCompile"
        label = "FavorSizeOrSpeed"
        property_groups = self.xml.findall("./{" + _MS_BUILD_NAMESPACE +
                                           "}ItemDefinitionGroup")
        for group in property_groups:
            items = group.findall("{" + _MS_BUILD_NAMESPACE + "}" + item_name)
            for item in items:
                subitem = item.find("{" + _MS_BUILD_NAMESPACE + "}" + label)
                return subitem.text

    def general_set(self,
                    label,
                    upper_label,
                    value,
                    platform="All Configurations",
                    configuration="All Configurations"):
        property_groups = self.xml.findall("./{" + _MS_BUILD_NAMESPACE +
                                           "}ItemDefinitionGroup")
        for group in property_groups:
            items = group.findall("{" + _MS_BUILD_NAMESPACE + "}" +
                                  upper_label)
            for item in items:
                subitem = item.find("{" + _MS_BUILD_NAMESPACE + "}" + label)
                try:
                    subitem.text = value
                except AttributeError:
                    self.__set_item_group_items_for_config(
                        platform, configuration, upper_label, label, value)

    def general_get(self,
                    label,
                    upper_label,
                    platform="All Configurations",
                    configuration="All Configurations"):
        property_groups = self.xml.findall("./{" + _MS_BUILD_NAMESPACE +
                                           "}ItemDefinitionGroup")
        for group in property_groups:
            items = group.findall("{" + _MS_BUILD_NAMESPACE + "}" +
                                  upper_label)
            for item in items:
                subitem = item.find("{" + _MS_BUILD_NAMESPACE + "}" + label)
                return subitem.text

    def set_inlinefunctionexpansion(self,
                                    value,
                                    platform="All Configurations",
                                    configuration="All Configurations"):
        if "ob1" in value.lower():
            value = "OnlyExplicitInline"
        elif "ob2" in value.lower():
            value = "AnySuitable"
        elif "obd" in value.lower():
            value = "Disabled"
        else:
            value = ""
        self.general_set("InlineFunctionExpansion", "ClCompile", value)

    def get_inlinefunctionexpansion(self,
                                    platform="All Configurations",
                                    configuration="All Configurations"):
        return self.general_get("InlineFunctionExpansion", "ClCompile",
                                platform, configuration)

    def get_optimization(self):
        item_name = "ClCompile"
        label = "Optimization"
        property_groups = self.xml.findall("./{" + _MS_BUILD_NAMESPACE +
                                           "}ItemDefinitionGroup")
        for group in property_groups:
            items = group.findall("{" + _MS_BUILD_NAMESPACE + "}" + item_name)
            for item in items:
                subitem = item.find("{" + _MS_BUILD_NAMESPACE + "}" + label)
                if subitem.text:
                    return subitem.text
        return "Disabled"

    def enable_intrinsicfunctions(self):
        self.general_set("IntrinsicFunctions", "ClCompile", "true")

    def disable_intrinsicfunctions(self):
        self.general_set("IntrinsicFunctions", "ClCompile", "false")

    def get_intrinsicfunctions(self,
                               platform="All Configurations",
                               configuration="All Configurations"):
        return self.general_get("IntrinsicFunctions",
                                "ClCompile",
                                platform=platform,
                                configuration=configuration)

    def get_whole_program_optimization(self):
        return self.__property_group_item_for_config(
            "All Configurations", "All Configurations", "Configuration",
            'WholeProgramOptimization')

    def set_whole_program_optimization(self,
                                       flag,
                                       platform="All Configurations",
                                       configuration="All Configurations"):
        # self.__set_property_group_items_for_config(platform, configuration, None, 'LinkIncremental', string_value)
        # return self.__set_item_group_items_for_config("All Configurations", "All Configurations", "Configuration", 'PlatformToolset', version)
        try:
            property_groups = self.xml.findall("./{" + _MS_BUILD_NAMESPACE +
                                               "}PropertyGroup")
            matching_groups = (
                group for group in property_groups
                if group.attrib.get('Label', None) == "Configuration")
            for group in matching_groups:
                if 'Condition' not in group.attrib or _matches_platform_configuration(
                        group.attrib['Condition'], platform, configuration):
                    items = group.findall("{" + _MS_BUILD_NAMESPACE + "}" +
                                          "WholeProgramOptimization")
                    for item in items:
                        if item is not None:
                            if 'Condition' not in item.attrib or _matches_platform_configuration(
                                    item.attrib['Condition'], platform,
                                    configuration):
                                item.text = str(flag).lower()
        except:
            return self.__set_item_group_items_for_config(
                platform, configuration, "Configuration",
                'WholeProgramOptimization',
                str(flag).lower())

    def get_toolset_version(self):
        """Build tool version for this project"""
        # outdir is in an unlabeled PropertyGroup
        try:
            property_groups = self.xml.findall("./{" + _MS_BUILD_NAMESPACE +
                                               "}PropertyGroup")
            matching_groups = (
                group for group in property_groups
                if group.attrib.get('Label', None) == "Configuration")
            for group in matching_groups:
                if 'Condition' not in group.attrib or _matches_platform_configuration(
                        group.attrib['Condition'], "All Configurations",
                        "All Configurations"):
                    items = group.findall("{" + _MS_BUILD_NAMESPACE + "}" +
                                          "PlatformToolset")
                    for item in items:
                        if item is not None:
                            if 'Condition' not in item.attrib or _matches_platform_configuration(
                                    item.attrib['Condition'],
                                    "All Configurations",
                                    "All Configurations"):
                                if item.text:
                                    return item.text
        except:
            return self.__property_group_item_for_config("All Configurations",
                                                        "All Configurations",
                                                        "Configuration",
                                                        'PlatformToolset')

    def set_toolset_version(self, version):
        # self.__set_property_group_items_for_config(platform, configuration, None, 'LinkIncremental', string_value)
        # return self.__set_item_group_items_for_config("All Configurations", "All Configurations", "Configuration", 'PlatformToolset', version)
        try:
            property_groups = self.xml.findall("./{" + _MS_BUILD_NAMESPACE +
                                               "}PropertyGroup")
            matching_groups = (
                group for group in property_groups
                if group.attrib.get('Label', None) == "Configuration")
            for group in matching_groups:
                if 'Condition' not in group.attrib or _matches_platform_configuration(
                        group.attrib['Condition'], "All Configurations",
                        "All Configurations"):
                    items = group.findall("{" + _MS_BUILD_NAMESPACE + "}" +
                                          "PlatformToolset")
                    for item in items:
                        if item is not None:
                            if 'Condition' not in item.attrib or _matches_platform_configuration(
                                    item.attrib['Condition'],
                                    "All Configurations",
                                    "All Configurations"):
                                item.text = version
            self.__set_item_group_items_for_config(
                "All Configurations", "All Configurations", "PropertyGroup",
                'PlatformToolset', version)
        except:
            self.__set_item_group_items_for_config(
                "All Configurations", "All Configurations", "PropertyGroup",
                'PlatformToolset', version)

    def debug_information_format(self, platform, configuration):
        """Program database format (ProgramDatabase, EditAndContinue or OldStyle)."""
        return self.__item_group_item_for_config(platform, configuration,
                                                 "ClCompile",
                                                 "DebugInformationFormat")

    def set_debug_information_format(self, platform, configuration,
                                     information_format):
        """Set program database format (ProgramDatabase, EditAndContinue or OldStyle)."""
        return self.__set_item_group_items_for_config(
            platform, configuration, "ClCompile", "DebugInformationFormat",
            information_format)

    def enable_incremental_linking(self, platform, configuration):
        """Whether or not incremental linking is enabled."""
        string_value = self.__property_group_item_for_config(
            platform, configuration, None, 'LinkIncremental')
        if string_value is None:
            return None
        elif string_value.lower() == 'true':
            return True
        else:
            return False

    def set_enable_incremental_linking(self, platform, configuration,
                                       link_incremental):
        """Set whether or not incremental linking is enabled."""
        string_value = None if link_incremental is None else str(
            link_incremental).lower()
        self.__set_property_group_items_for_config(platform, configuration,
                                                   None, 'LinkIncremental',
                                                   string_value)

    def write(self, filename=None):
        """Save project file."""
        filename = filename or self.filename
        self.xml.write(filename,
                       xml_declaration=True,
                       encoding='utf-8',
                       method="xml")


def parse(filename):
    """Parse project file filename and return Project instance."""
    return Project(filename)
