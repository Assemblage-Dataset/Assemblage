import click
from dataset_utils import process, runcmd, db_construct, update_license
import random
import os

@click.command()
@click.option('--data', help='The folder contains the data')
@click.option('--s3',  is_flag=True, help='The S3 bucket path for the dataset')
@click.option('--dest', help='The destination folder for the data, will be created and overwritten.')
@click.option('-g', is_flag=True, help='Generate dataset, you need also need to provide other specs')
@click.option('--dbfile', help='The database file')
@click.option('-f', is_flag=True, help='Filter the data folder')
@click.option('--uppersize', help='Maximum size of binary file')
@click.option('--lowersize', help='Minimum size of binary file')
@click.option('--amount', help='Files to be processed')
@click.option('--lines', is_flag=True, help='Store lines information in the database')
@click.option('--functions', is_flag=True, help='Store lines information in the database')
@click.option('--rvas', is_flag=True, help='Store RVA information in the database')
@click.option('--pdbs', is_flag=True, help='Store PDB file, takes up additional space')
@click.option('--inplace', is_flag=True, help='Delete zip file while processing')
@click.option('--nopdb', is_flag=True, help='Delete pdb file while processing')
# Todo: merge legacy operations to one command
@click.option('--addlicense', is_flag=True, help='Update license information in database')



def main(data, s3, dest, g, dbfile, f, uppersize, lowersize, amount, lines, functions, rvas, pdbs, inplace, addlicense, nopdb):
    """Assemblage Dataset Interface"""
    if g:
        assert data
        assert dbfile
        db_construct(dbfile, data, lines, functions, rvas, pdbs)
    elif addlicense:
        assert dbfile
        update_license(dbfile)
    elif data:
        process(data, dest, inplace, nopdb)


if __name__ == '__main__':
    main()