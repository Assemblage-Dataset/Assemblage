import click
from dataset_utils import process, runcmd, filter_size, db_construct
import random
import os


@click.command()
@click.option('--data', help='The folder contains the data')
@click.option('--s3',  help='The S3 bucket path for the dataset')
@click.option('--dest', required=True, help='The destination folder for the data, will be created and overwritten.')
@click.option('-g', is_flag=True, help='Generate dataset, you need also need to provide other specs')
@click.option('--uppersize', type=int,  help='The upper binary size you want to filter in KB')
@click.option('--lowersize', type=int, help='The smallest binary size you want to filter in KB')
@click.option('--amount', type=int, help='The amount of binary files you want to get')
@click.option('--dbfile', help='The database file')
def main(data, s3, dest, g, uppersize, lowersize, amount, dbfile):
    """Assemblage Dataset Interface"""
    if g:
        assert data
        assert dest
        assert dbfile
        runcmd(f"rm -rf {dest}")
        runcmd(f"mkdir {dest}")
        filter_size(uppersize, lowersize, amount, data, dest)
        db_construct(dbfile, dest)
        return
    if data:
        runcmd(f"rm -rf {dest}")
        process(data, dest)
    elif s3:
        runcmd(f"mkdir {dest}")
        runcmd(f"aws s3 cp s3://assemblage-data/data/ ./{dest} --recursive")
        process(dest)


if __name__ == '__main__':
    main()
