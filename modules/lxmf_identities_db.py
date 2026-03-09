# identity.py
import RNS
import LXMF
import os

class IdentityManager:
    def __init__(self, storagepath):
        self.identity = None
        self.storagepath = storagepath
        self.identitypath = f"{storagepath}/identity"
        
    def load_or_create(self):
        if os.path.isfile(self.identitypath):
            self.identity = RNS.Identity.from_file(self.identitypath)
        else:
            self.identity = RNS.Identity()
            self.identity.to_file(self.identitypath)
        return self.identity
    
    def get_hash(self):
        return self.identity.hash.hex() if self.identity else None