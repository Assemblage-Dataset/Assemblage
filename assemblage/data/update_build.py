from assemblage.protobufs.assemblage_pb2 import Repo
from sqlalchemy import select, update, create_engine, func
from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import desc
from sqlalchemy.sql.selectable import Select
from assemblage.data.object import RepoDO, Status
from assemblage.analyze.analyze import *
from tqdm import tqdm
import datetime


class UpdateMakeSys:
    def __init__(self, addr):
        self.engine = create_engine(addr, echo=False)
        self.analyze = Analyze(addr)

    def write2db(self):
        # print("Please run within Docker")
        # input("Write into db?")

        repo = []
        build_systems = []

        with open('repos_files.csv') as csvfile:
            line = "init"
            print("Readling file")
            while line:
                try:
                    line = csvfile.readline()
                    row = line.split(",")
                    if len(row) != 3:  # corrupted line
                        continue
                    repo.append(row[0])
                    build_tool = self.analyze.analyze_buildsys(row[2].split(";"))
                    build_systems.append(build_tool)
                    # print(row[2],build_tool)
                    # time.sleep(0.5)
                except:  # corrupted line
                    pass
        print(len(repo), "repos")
        print(len(build_systems), "build_sys")
        assert (len(repo) == len(build_systems))
        # repoid and build_type loaded

        ''' update the build/clone status of a repo for one build option '''
        input("Write into db?")

        with Session(self.engine) as session:
            for i in tqdm(range(len(repo))):
                repoid = repo[i]
                build_system = build_systems[i]
                repo_val = {'updated_at': datetime.datetime.utcnow()}
                repo_val['build_system'] = build_system
                rec = update(RepoDO).values(**repo_val).where(RepoDO._id == repoid)
                session.execute(rec)
                # print(i,repoid,build_system,"written")
            session.commit()
