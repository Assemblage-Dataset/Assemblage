# Assemblage

Assemblage is a distributed binary corpus discovery, generation, and archival tool built to provide high-quality labeled metadata for the purposes of building training data for machine learning applications of binary analysis and other applications (static / dynamic analysis, reverse engineering, etc...).  

You can now find our paper on [arxiv](https://arxiv.org/abs/2405.03991)  

## Cloud infrastructure support

We have run Assemblage over the course of several months within the research computing cluster at Syracuse University and Amazon Web Services. 

## Worker Requirement

This is the public repository of Assemblage, and it is hosts a general template for booting Assemblage on any cloud infrastructure. We will soon include stable/old versions we customized and ran on AWS, please check out beanches under name of fomat `{linux|windows}_{github|vcpkg}`. For example, code we used to generate Windows binaries from GitHub data will locate at branch naming `windows_github` (though the credentials are sanitized).

We provide Dockerfile and build script to build Docker images for Linux worker, and the Docker compose file can be used to specify the resource each worker can access.  
Due to the commercial license of the Wiundows, we only provide the boot script and environment specification for workers, locating at the [Windows readme](assemblage/windows/README.md)

Meanwhile, a brief introduction to the APIs is provided at this [link](assemblage/README.md).

## Dataset Availability

We include __only__ the subset of binaries for which permissive licenses can be ascertained. 
You can also read the [docs at this link](https://assemblagedocs.readthedocs.io/en/latest/dataset.html), and checkout our [data sheet](https://assemblage-dataset.net/assets/total-datasheet.pdf)

<del>Pdb files are too large to be included, but datasets with pdb files are also available upon request.</del>  
The dataset with pdb files are released and hosted on Hugging Face.

1.Windows GitHub dataset (67k, last updated: May 12th 2024, comes with function source code, address and comments):  
*   [SQLite databse (8.5G)](https://huggingface.co/datasets/changliu8541/Assemblage_PE/resolve/main/winpe_pdbs.sqlite.tar.xz)
*   [Binary dataset with pdb files (27G, ~200G inflated)](https://huggingface.co/datasets/changliu8541/Assemblage_PE/resolve/main/binaries.tar.xz)
*   [Binary dataset with pdb files (5G, ~10G inflated)](https://huggingface.co/datasets/changliu8541/Assemblage_PE/resolve/main/binaries_nopdb.tar.xz)

2.Windows vcpkg dataset (29k; comes with function address; source code and comments not included):

*   [SQLite database, 21G inflated](https://huggingface.co/datasets/changliu8541/Assemblage_vcpkgDLL/resolve/main/vcpkg.sqlite.tar.gz)
*   [Binary dataset with pdb files, 205G inflated](https://huggingface.co/datasets/changliu8541/Assemblage_vcpkgDLL/resolve/main/vcpkg.tar.xz)

3.Linux GitHub dataset (211k; only binary level information; function address, source code and comments not included):

*   [SQLite database (23M)](https://huggingface.co/datasets/changliu8541/Assemblage_LinuxELF/resolve/main/linux.sqlite.tar.xz)

*   Binary dataset (72G)
    *   [parta](https://huggingface.co/datasets/changliu8541/Assemblage_LinuxELF/resolve/main/binaries.tar.xz.partaa)
    *   [partb](https://huggingface.co/datasets/changliu8541/Assemblage_LinuxELF/resolve/main/binaries.tar.xz.partab)

<sub>The code in this repository is published under MIT license.</sub>
