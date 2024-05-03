"""
Deprecated ftp server

Yihao Sun
"""

from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer
import logging

FTP_ADDR = ('disasm', 10086)
FTP_DEFAULT_USER = "assemblage"
FTP_DEFAULT_PWD = "assemblage"

logging.getLogger('pyftpdlib').setLevel(logging.WARNING)

class AssemblageFtpSever:
    """ a FTP server for slog """
    def __init__(self, base_dir) -> None:
        self.base_dir = base_dir
        self.authorizer = DummyAuthorizer()
        self.authorizer.add_user(FTP_DEFAULT_USER, FTP_DEFAULT_PWD, self.base_dir,
                                 perm='elradfmwMT')
        self.authorizer.add_anonymous(base_dir)
        handler = FTPHandler
        handler.authorizer = self.authorizer
        handler.passive_ports = [47173]
        self.server = FTPServer(FTP_ADDR, handler)
        self.server.max_cons = 256
        self.server.max_cons_per_ip = 5

    def start(self):
        """ boot ftp server """
        print(f"FTP server at {FTP_ADDR}...")
        self.server.serve_forever()

    def add_user(self, username, pwd):
        """ add a user to ftp """
        self.authorizer.add_user(username, pwd, self.base_dir, perm='elradfmwMT')
