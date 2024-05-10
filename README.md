# Assemblage

Assemblage is a distributed binary corpus discovery, generation, and archival tool built to provide high-quality labeled metadata for the purposes of building training data for machine learning applications of binary analysis and other applications (static / dynamic analysis, reverse engineering, etc...).

The code in this repository is published under MIT license.

## Cloud infrastructure support

We have run Assemblage over the course of several months within the research computing cluster at Syracuse University and Amazon Web Services. 

## Worker Requirement

This is the public repository of Assemblage, and it is hosts a general template for booting Assemblage on any cloud infrastructure. We will soon include stable/old versions we customized and ran on AWS, please check out beanches under name of fomat `{linux|windows}_{github|vcpkg}`. For example, code we used to generate Windows binaries from GitHub data will locate at branch naming `windows_github` (though the credentials are sanitized).

We provide Dockerfile and build script to build Docker images for Linux worker, and the Docker compose file can be used to specify the resource each worker can access.  
Due to the commercial license of the Wiundows, we only provide the boot script and environment specification for workers, locating at the [Windows readme](assemblage/windows/README.md)

Meanwhile, a brief introduction to the APIs is provided at this [link](assemblage/README.md).

## Dataset Availability

We include __only__ the subset of binaries for which permissive licenses can be ascertained. 

Pdb files are too large to be included, but datasets with pdb files are also available upon request.

1.Windows GitHub dataset (Processed to SQLite database, 62k, last updated: Apr 14th 2024):  
*   [SQLite databse (12G)](https://huggingface.co/datasets/changliu8541/Assemblage_PE/resolve/main/winpe.sqlite.zip)
*   [Binary dataset (7G)](https://huggingface.co/datasets/changliu8541/Assemblage_PE/resolve/main/binaries.zip)

2.Windows vcpkg dataset (Processed to SQLite database, 29k):

*   [SQLite database, 21G inflated](https://huggingface.co/datasets/changliu8541/Assemblage_vcpkgDLL/resolve/main/vcpkg.sqlite.tar.gz)
*   [Binary dataset with pdb files, 205G inflated](https://huggingface.co/datasets/changliu8541/Assemblage_vcpkgDLL/resolve/main/vcpkg.tar.xz)

3.Linux GitHub dataset (Processed to SQLite database, 211k):

*   SQLite database (23M):  
https://assemblage-lps.s3.us-west-1.amazonaws.com/public/feb15_linux_licensed.sqlite

*   Binary dataset (72G):  
https://assemblage-lps.s3.us-west-1.amazonaws.com/public/licensed_linux.zip

