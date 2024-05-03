import os
import shutil
import hashlib
import glob
import threading
import time

'''
Deprecated proxy cloner
'''

from flask import Flask, request


REPO_HOME = "/var/www/html"
if not os.path.isdir(REPO_HOME):
    os.makedirs(REPO_HOME)

app = Flask(__name__)

@app.route("/")
def hello_world():
    return REPO_HOME

@app.route("/delete", methods=["GET"])
def delete():
    zip_url = request.args.get('zip_url')
    zip_path = f"{REPO_HOME}/{zip_url}"
    if os.path.exists(zip_path):
        os.remove(zip_path)
        return "0"
    else:
        return "1"

@app.route("/clone", methods=["GET"])
def clone():
    # check disk avaliable first
    usage = shutil.disk_usage(REPO_HOME)
    if usage.free < 4000000:
        shutil.rmtree(REPO_HOME)
        os.mkdir(REPO_HOME)
    repo_url = request.args.get('repo_url')
    print(repo_url)
    delete_outdated(REPO_HOME)
    auth = request.args.get('auth')
    assert auth == "?"
    url_hash = hashlib.md5(repo_url.encode()).hexdigest()
    repo_tmp_path = f"{REPO_HOME}/{url_hash}"
    repo_zip = f"{REPO_HOME}/{url_hash}.zip"
    if os.path.exists(repo_zip):
        return "0"
    res = os.system(f"git clone --depth 1 {repo_url} {repo_tmp_path} && cd {repo_tmp_path} && zip -r {repo_zip} ./ && rm -rf {repo_tmp_path}")
    return str(res)


def delete_outdated(dir, interval=60):
    for f in glob.glob(REPO_HOME+"/*"):
        print(f)
        if abs(time.time() - os.path.getmtime(f)) > interval:
            try:
                os.remove(f)
                os.unlink(f)
            except Exception as err:
                print(err)

if __name__ == "__main__":
    print(REPO_HOME)
    app.run(debug=True, host='0.0.0.0')
