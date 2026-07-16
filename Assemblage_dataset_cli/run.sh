# rm -rf sample sample.sqlite samplezips
# mkdir samplezips
# cd samplezips
# aws s3 cp s3://assemblage-data/lps_finilized/ ./ --recursive
# cd ..
python cli.py --data samplezips --dest sample
python cli.py -g --data sample --dbfile sample.sqlite --functions --lines --rvas
rm -rf 1.txt && python test_source_code_recovery.py >>1.txt