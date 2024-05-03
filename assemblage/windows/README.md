# Windows worker config

## Environment Setup

Please install different versions of Visual Studio, you might need MSDN subscription, and install components (SDKs, Tools...) in Visual Studion to increase success rate


Also, add 3 directory to PATH:

1.  `{INSTALLPATH}\Git\bin`  : for shell exec

2.  `{INSTALLPATH}\Microsoft Visual Studio\2019\Community\MSBuild\Current\Bin` : the msbuild path in Visual Studio installation path.  
__Please note, path may vary if you are installing Professional/Enterprise or different version__

3.  `{INSTALLPATH}\7z` : the 7z path for file compression 


## Pdb JSON output format

The binary folder `pdbinfo.json` follows following format, it is not nmecessary for you to use it, as the dataset generation tool will process it for you.


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
