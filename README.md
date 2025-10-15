# Assemblage

Assemblage is a distributed binary corpus discovery, generation, and archival tool built to provide high-quality labeled metadata for the purposes of building training data for machine learning applications of binary analysis and other applications (static / dynamic analysis, reverse engineering, etc...).  

You can now find our paper on [arxiv](https://arxiv.org/abs/2405.03991)  

## Deployment and Dataset Availability

A brief introduction to the APIs and deployment can be found [here](https://assemblagedocs.readthedocs.io/)

We include __**only**__ the subset of binaries for which permissive licenses can be ascertained, please checkout our [data sheet](https://assemblage-dataset.net/assets/total-datasheet.pdf).   
For up to date info and download, please visit the [dataset page](https://assemblagedocs.readthedocs.io/en/latest/dataset.html)

<sub>The code in this repository is published under MIT license.</sub>


Use separate ENV variable file for each container. 
Also, in the compose file, specify the type - Coordinator,Scraper,Builder

## ENVIRONMENT VARIABLES FOR coordinator.secrets.env
```
DB_HOST=assemblage-db (database container name)
POSTGRES_DATABASE=assemblage
POSTGRES_USER=assemblage
POSTGRES_PASSWORD=<password>
DB_PORT=5432
```
## ENVIRONMENT VARIABLES FOR scraper.secrets.env
```
GITHUB_TOKEN=<github_pat_token>
```
## ENVIRONEMNT VARIABLES FOR builder.secrets.env
```
SAVE_ASSEMBLY=true
```

## If using a builder or scraper on a distributed host also add
```
MQ_HOST=<mq_host>
MQ_PORT=<mq_port>
```
You will also need to expose the rabbitmq port. TODO: add proper authentication