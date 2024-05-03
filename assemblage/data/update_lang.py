from assemblage.protobufs.assemblage_pb2 import Repo
from sqlalchemy import select, update, create_engine, func
from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import desc
from sqlalchemy.sql.selectable import Select
from assemblage.data.object import RepoDO, Status
from tqdm import tqdm
import datetime

class UpdateLang:
    def __init__(self, addr):
        self.engine = create_engine(addr, echo=False)

    def write2db(self):
        repo = []
        lang = []

        csvfile = open('repos_lang.csv')
        line = "init"
        print("Readling file")
        while line:
            try:
                line = csvfile.readline()
                line=lione.strip()
                row = line.split(",")
                if len(row) !=3: # corrupted line
                    continue
                repo.append(row[0])
                lang.append(row[2])
            except: # corrupted line
                pass
        csvfile.close()
        print(len(repo),"repos")
        print(len(lang),"lang")
        assert(len(repo)==len(build_systems))
        # repoid and build_type loaded

        ''' update the build/clone status of a repo for one build option '''
        input("Write into db?")

        for i in range(int(len(repo)/10000)+1):
            with Session(self.engine) as session:
                for j in range(10000):
                    index = i*10000+j
                    if index >= len(repo):
                        session.commit()
                        print("END")

                    repoid = repo[index]
                    lang_col = lang[index]

                    repo_val = {'updated_at': datetime.datetime.utcnow()}
                    repo_val['language'] = lang_col
                    rec = update(RepoDO).values(**repo_val).where(RepoDO._id == repoid)
                    session.execute(rec)
                print(f"Commit {i} out of {(int(len(repo)/10000)+1)}")
                session.commit()
            
    def stop(self):
        self.engine.dispose()
