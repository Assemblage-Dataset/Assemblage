jTrans is made available on GitHub here: https://github.com/vul337/jTrans/tree/main
and comes with a descriptive README. Our code (and documentation) is intended to complement, not
replace, that work. Therefore, we only provide and discuss our supplemental code for extending
jTrans to Windows data. Readers should also familiarize themselves with the original jTrans
repository before running experiments with jTrans.

# jTrans Assemblage Adoption Code

1. This code transforms Assemblage Windows data to jTrans's input format. It includes the following files:

```
.
├── README.md
├── base_assemblage_pe.py
├── jTrans_Assemblage_preprocess.ipynb
├── pairdata_assemblage_pe.py
├── eval.ipynb
└── process_pe.py
```

2. Please first clone the jTrans source code to local machine. jTrans is made available at: https://github.com/vul337/jTrans/.  
The jTrans directory structure is shown below. Please put the files 

*   `base_assemblage_pe.py` and `pairdata_assemblage_pe.py`in the `datautils/util` folder  
*   `process_pe.py` and `jTrans_Assemblage_preprocess.ipynb` in the `datautils` folder

Also, update the `dbfile` variable in `base_assemblage_pe.py` to the SQLite database path, so you can read the function info from the SQLite database.

```
x < - y:  Do NOT delete the original file x, copy the file y along with original file x

.
├── LICENSE
├── README.md
├── data.py
├── datautils
│   ├── README.md
│   ├── clean.sh
│   ├── dataset
│   │   └──...
│   ├── playdata.py
│   ├── process.py  <- process_pe.py
│   ├── run.py <- jTrans_Assemblage_preprocess.ipynb
│   └── util      
│       ├── base.py <- base_assemblage_pe.py,
│       └── pairdata.py <- pairdata_assemblage_pe.py
├── eval_save.py
├── fasteval.py
├── figures
│   └── poolsizecompare.png
├── finetune.py
├── jtrans_tokenizer
│   ├── special_tokens_map.json
│   ├── tokenizer_config.json
│   └── vocab.txt
├── readidadata.py
└── tokenizer.py

6 directories, 24 files

```

3. Uncompress the Assemblage data into a folder that is easy to access.

4. Set up the jTrans environment, and follow the instructions in `jTrans_Assemblage_preprocess.ipynb`. 
This will flatten the Assemblage files into one folder, which the ida script will run over. You can also use our multiprocessing version 
to speed up the IDA processing (the naming convention is little different, so please change process.py if you use their IDA script).

5. After running the notebook, you will see a folder containing binaries, with a structure like this:

```
flatten_dir
    |-- 1001
    |     |- a.exe
    |     |- a.pdb
    |
    |-- 1002
    |     |- b.exe
    |     |- b.pdb
    ...
```

6. If you also use our IDA script to process the data, you will get a `extract` folder under
 `datautils`, which is the input data to jTrans. 
 
7. We also provides `eval.ipynb` to evaluate the data, which also comes from the provided `eval_save.py`. 
It is only recommended to use this notebook if your GPU has large memory, otherwise please use author's code following their instructions. To replicate our results, please set POOLSIZE=10000 at evaluation time.
