# Assemblage

Assemblage is a distributed binary corpus discovery, generation, and archival tool built to provide high-quality labeled metadata for the purposes of building training data for machine learning applications of binary analysis and other applications (static / dynamic analysis, reverse engineering, etc...).  

You can now find our paper on [arxiv](https://arxiv.org/abs/2405.03991)  

## Deployment

This is the public repository of Assemblage, and it is hosts a general template for booting Assemblage on any cloud infrastructure. We will soon include stable/old versions we customized and ran on AWS, please check out beanches under name of fomat `{linux|windows}_{github|vcpkg}`. For example, code we used to generate Windows binaries from GitHub data will locate at branch naming `windows_github` (though the credentials are sanitized).

We provide Dockerfile and build script to build Docker images for Linux worker, and the Docker compose file can be used to specify the resource each worker can access. Due to the commercial license of the Windows, we only provide the boot script and environment specification for workers, locating at the [readme](assemblage/windows/README.md)

Meanwhile, a brief introduction to the APIs is provided at this [link](assemblage/README.md#workers-api-and-deployment), and deployment instructions can be found [here](https://assemblagedocs.readthedocs.io/en/latest/deployment.html)

## Dataset Availability

We include __only__ the subset of binaries for which permissive licenses can be ascertained, please checkout our [data sheet](https://assemblage-dataset.net/assets/total-datasheet.pdf)

For up to date info about dataset, you can visit the [docs at this link](https://assemblagedocs.readthedocs.io/en/latest/dataset.html), 

<sub>The code in this repository is published under MIT license.</sub>
