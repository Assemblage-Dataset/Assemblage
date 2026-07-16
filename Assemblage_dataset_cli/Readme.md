# Assemblage Dataset CLI

This repository holds the dataset construction tool for Assemblage

## Table schema

```
Table schema
    +=========+      +=========+      +===========+
    |binaries |      |functions|      |    rvas   |
    |=========|      |=========|      |===========|
    |   *id   |<-+   |   *id   |<--+  |    *id    |
    |---------|  |   |---------|   |  |-----------|
    |file_name|  +-- |binary_id|   +--|function_id|
    |---------|  |   |---------|   |  |-----------|
    | platform|  |   |  name   |   |  |   start   |
    |---------|  |   |---------|   |  |-----------|
    |   ...   |  |   |  hash   |   |  |    end    |
    +=========+  |   +=========+   |  +===========+
                 |                 |  
                 |   +=========+   |  +===========+
                 |   |  pdbs   |   |  |  lines    |
                 |   |=========|   |  |===========|
                 |   |   id    |   |  |    id     |
                 |   |---------|   |  |-----------|
                 +---|binary_id|   +--|function_id|
                     |---------|      |-----------|
                     |file_name|      |source_code|
                     +=========+      +===========+

* indicates indexing column
                    
```

## CLI commands

Currently this CLI supports 3 commands

1.  Unzip the zips in one folder to the destination folder called unzipped_folder:
```
python cli.py --data zip_folder --dest unzipped_folder
```

2.  Generate serialized SQLite database 
```
python cli.py -g --data unzipped_folder --dbfile some.sqlite --functions --rvas --lines --pdbs
```

where --functions --rvas --lines --pdbs are flags to include the function, eva, lines and pdns information

3.  Legacy, will be deprecated soon: Add license for each reposotory:
```
python cli.py --addlicense --dbfile some.sqlite
```
