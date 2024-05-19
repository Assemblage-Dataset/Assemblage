# Windows worker configs

### Environment configs
    1. NEED 3 dir in PATH:

        `{INSTALLPATH}\Git\bin`
        For sh exec

        `{INSTALLPATH}\Microsoft Visual Studio\2019\Community\MSBuild\Current\Bin`
        The msbuild path

        `{INSTALLPATH}\7z`
        The 7z bin path for compressing files

    2. 
        Install different versions of Visual Studio, you might need MSDN subscription
        Install components (SDKs, Tools...) in Visual Studion to increase success rate

### Pdb json format
    The binary folder `pdbinfo.json` follows following format:

    '''

    {
        "Platform": "x86" or "x64",
        "Build_mode": "Debug" or "Release",
        "Source_url": Github repo link,
        "Toolset_version" : Visual Studio tool set version,
        "Optimization" : "Od/O1/O2",
        "Binary_info_list":
          [
             {
                  "file": binary_file,
                  "functions":[
                      {
                            "function_name": function_name,
                            "intersect_ratio": injected debug pin ratio,
                            "source_file": source_file_name,
                            "function_info":[
                                {
                                   "rva_start": rva start address,
                                   "rva_end": rva end address,
                                   "debug_ratio" : injected debug pin ratio,
                                }
                            ],
                            "lines":[
                                {
                                   "line_number" : line number,
                                   "rva": RVA address,
                                   "length": length of line in binary,
                                   ("source_file": source file path)
                               }
                           ],
                      },
                  ]       
             },
          ]
    }
    '''